# -*- coding: utf-8 -*-
"""MCPClient 单元测试：配置解析、结果转换、动态注册。"""

import asyncio
import json
import tempfile
import threading
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mcp import types

from xAgent.core import ToolService
from xAgent.core.mcp_client import (
    MCPServerConfig, MCPClientManager, _ServerState, _ServerStatus,
    is_connection_error, load_mcp_config, convert_call_result,
)
from xAgent.core.registry import (
    TOOL_META, TOOL_FUNCS, register_tool, unregister_tool,
    _sanitize_tool_name, _mcp_parameters,
    _register_server_tools, _unregister_server_tools,
)
from xAgent.core.types import ToolInfo


class TestMcpConfig(unittest.TestCase):
    def _write(self, content: str) -> tempfile.TemporaryDirectory:
        tmp = tempfile.TemporaryDirectory()
        Path(tmp.name, ".mcp.json").write_text(content, encoding="utf-8")
        return tmp

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            configs, warnings = load_mcp_config(tmp)
            self.assertEqual(configs, [])
            self.assertEqual(warnings, [])

    def test_valid_streamable_http(self):
        with self._write(json.dumps({
            "mcpServers": {
                "PeiHospital": {
                    "type": "streamable-http",
                    "url": "http://127.0.0.1:8090/mcp",
                }
            }
        })) as tmp:
            configs, warnings = load_mcp_config(tmp)
            self.assertEqual(len(configs), 1)
            self.assertEqual(configs[0].name, "PeiHospital")
            self.assertEqual(configs[0].url, "http://127.0.0.1:8090/mcp")
            self.assertEqual(warnings, [])

    def test_stdio_config_parsed(self):
        with self._write(json.dumps({
            "mcpServers": {
                "local": {
                    "type": "stdio",
                    "command": "python",
                    "args": ["-m", "pet_hospital_mcp"],
                    "env": {"PET_API": "http://127.0.0.1:8080"},
                    "cwd": "C:/work",
                }
            }
        })) as tmp:
            configs, warnings = load_mcp_config(tmp)
            self.assertEqual(warnings, [])
            self.assertEqual(len(configs), 1)
            cfg = configs[0]
            self.assertEqual(cfg.transport, "stdio")
            self.assertEqual(cfg.command, "python")
            self.assertEqual(cfg.args, ["-m", "pet_hospital_mcp"])
            self.assertEqual(cfg.env, {"PET_API": "http://127.0.0.1:8080"})
            self.assertEqual(cfg.cwd, "C:/work")
            self.assertEqual(cfg.target, "stdio: python -m pet_hospital_mcp")

    def test_stdio_missing_command_skipped(self):
        with self._write(json.dumps({
            "mcpServers": {"broken": {"type": "stdio", "args": ["-m"]}}
        })) as tmp:
            configs, warnings = load_mcp_config(tmp)
            self.assertEqual(configs, [])
            self.assertEqual(len(warnings), 1)
            self.assertIn("command", warnings[0])

    def test_unsupported_transport_and_missing_url(self):
        with self._write(json.dumps({
            "mcpServers": {
                "sse": {"type": "sse", "url": "http://127.0.0.1:8090/sse"},
                "nourl": {"type": "http"},
            }
        })) as tmp:
            configs, warnings = load_mcp_config(tmp)
            self.assertEqual(configs, [])
            self.assertEqual(len(warnings), 2)
            self.assertTrue(any("不支持" in warning for warning in warnings))

    def test_invalid_json(self):
        with self._write("{not json") as tmp:
            configs, warnings = load_mcp_config(tmp)
            self.assertEqual(configs, [])
            self.assertIn("读取", warnings[0])


class TestMcpResult(unittest.TestCase):
    def test_text_result(self):
        result = types.CallToolResult(
            content=[types.TextContent(type="text", text="hello")],
            is_error=False,
        )
        payload = convert_call_result("srv", "tool", result)
        self.assertTrue(payload["success"])
        self.assertIn("hello", payload["content"])

    def test_structured_result(self):
        result = types.CallToolResult(
            content=[types.TextContent(type="text", text="ok")],
            structured_content={"total": 3, "items": [1, 2, 3]},
            is_error=False,
        )
        payload = convert_call_result("srv", "tool", result)
        self.assertEqual(payload["structured_content"]["total"], 3)
        self.assertEqual(payload["structured_content"]["items"], [1, 2, 3])

    def test_error_result(self):
        result = types.CallToolResult(
            content=[types.TextContent(type="text", text="boom")],
            is_error=True,
        )
        payload = convert_call_result("srv", "tool", result)
        self.assertFalse(payload["success"])
        self.assertIn("boom", payload["error"])


class TestMcpNaming(unittest.TestCase):
    def test_sanitize_name(self):
        self.assertEqual(_sanitize_tool_name("Pei Hospital MCP"), "Pei_Hospital_MCP")
        self.assertEqual(_sanitize_tool_name(""), "mcp_tool")
        self.assertLessEqual(len(_sanitize_tool_name("x" * 100)), 64)

    def test_schema_to_parameters(self):
        schema = {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "搜索词"},
                "page": {"type": "integer", "description": "页码"},
            },
            "required": ["q"],
        }
        params = _mcp_parameters(schema)
        self.assertIn("必需", params["q"])
        self.assertIn("可选", params["page"])


class TestDynamicRegistration(unittest.TestCase):
    def test_register_and_call_external_tool(self):
        calls = {}

        def fake_tool(**kwargs):
            calls.update(kwargs)
            return {"success": True, "message": "ok", "echo": kwargs}

        info = ToolInfo(
            name="tmp_mcp_tool",
            description="临时 MCP 工具",
            parameters={"input": "object: 参数（必需）"},
            input_schema={
                "type": "object",
                "properties": {"input": {"type": "object"}},
                "required": ["input"],
            },
        )
        self.assertTrue(register_tool("tmp_mcp_tool", info, fake_tool))
        try:
            result = ToolService.call(
                "tmp_mcp_tool", input={"page": 1, "nested": {"x": [1]}}
            )
            self.assertTrue(result.success)
            self.assertEqual(calls["input"]["page"], 1)
            # 外部工具参数按 JSON Schema 原样传递，不做字符串化
            self.assertEqual(calls["input"]["nested"], {"x": [1]})
        finally:
            unregister_tool("tmp_mcp_tool")
            self.assertNotIn("tmp_mcp_tool", TOOL_META)
            self.assertNotIn("tmp_mcp_tool", TOOL_FUNCS)

    def test_register_duplicate_is_rejected(self):
        info = ToolInfo(name="read_file", description="dup")
        self.assertFalse(register_tool("read_file", info, lambda **kw: {}))


class TestConnectionErrorDetection(unittest.TestCase):
    def test_connection_errors(self):
        self.assertTrue(is_connection_error("httpx.ConnectError: connection refused"))
        self.assertTrue(is_connection_error(RuntimeError("连接被中断")))
        self.assertTrue(is_connection_error("Broken pipe"))
        self.assertTrue(is_connection_error("RemoteProtocolError: disconnected"))

    def test_non_connection_errors(self):
        self.assertFalse(is_connection_error("参数错误: path is required"))
        self.assertFalse(is_connection_error(RuntimeError("MCP 工具返回错误")))


class _FakeStack:
    async def aclose(self) -> None:
        pass


class _FakeRunner:
    """在测试中用 asyncio.run 执行协程，可注入固定异常模拟调用失败。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def submit(self, coro, timeout: float = 15.0):
        if self.error is not None:
            coro.close()
            raise self.error
        return asyncio.run(coro)

    def stop(self) -> None:
        pass


def _fake_tools(*names: str):
    return [
        SimpleNamespace(name=name, description=f"{name} 工具", input_schema={})
        for name in names
    ]


class _FakeSession:
    """模拟 mcp.ClientSession（异步上下文管理器 + initialize/list_tools）。"""

    def __init__(self, read_stream=None, write_stream=None) -> None:
        self.read_stream = read_stream
        self.write_stream = write_stream
        self.initialized = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def initialize(self):
        self.initialized = True

    async def list_tools(self):
        return SimpleNamespace(tools=_fake_tools("ping", "pong"))


class TestStdioTransport(unittest.TestCase):
    """D5：stdio 传输使用子进程 stdin/stdout，与 HTTP 分支共存。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = MCPClientManager(self.tmp.name)

    def tearDown(self) -> None:
        self.manager.shutdown()
        self.tmp.cleanup()

    def test_open_server_uses_stdio_client(self):
        captured = {}

        @asynccontextmanager
        async def fake_stdio(params):
            captured["params"] = params
            yield ("read-stream", "write-stream")

        cfg = MCPServerConfig(
            name="local",
            transport="stdio",
            command="python",
            args=["-m", "pet_hospital_mcp"],
            env={"PET_API": "http://127.0.0.1:8080"},
            cwd=self.tmp.name,
        )
        with mock.patch("mcp.client.stdio.stdio_client", fake_stdio), \
                mock.patch("mcp.ClientSession", _FakeSession):
            state = asyncio.run(self.manager._open_server(cfg))

        self.assertEqual(state.config.transport, "stdio")
        self.assertEqual([tool.name for tool in state.tools], ["ping", "pong"])
        self.assertTrue(state.session.initialized)
        self.assertEqual(captured["params"].command, "python")
        self.assertEqual(captured["params"].args, ["-m", "pet_hospital_mcp"])
        self.assertEqual(captured["params"].cwd, self.tmp.name)

    def test_http_transport_still_default(self):
        cfg = MCPServerConfig(name="remote", url="http://127.0.0.1:8090/mcp")
        self.assertEqual(cfg.transport, "http")
        self.assertEqual(cfg.target, "http://127.0.0.1:8090/mcp")


class TestReconnect(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = MCPClientManager(self.tmp.name)
        self.manager.reconnect_delays = (0.05, 0.1)

    def tearDown(self) -> None:
        self.manager.shutdown()
        self.tmp.cleanup()

    def _fake_state(self, name: str, tools=("ping",)) -> _ServerState:
        return _ServerState(
            config=MCPServerConfig(name=name, url="http://127.0.0.1:1/mcp"),
            stack=_FakeStack(),
            session=object(),
            tools=_fake_tools(*tools),
        )

    def test_reconnect_recovers_with_backoff(self):
        """前两次失败后成功，事件回调依次收到重试/失败/恢复通知。"""
        events: list[tuple[str, str]] = []
        done = threading.Event()

        def on_event(event: str, server: str, detail: str = "") -> None:
            events.append((event, server))
            if event == "connected":
                done.set()

        self.manager.set_event_callback(on_event)
        calls = {"n": 0}

        async def fake_open(cfg):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("ConnectError: connection refused")
            return self._fake_state(cfg.name, tools=("ping", "pong"))

        self.manager._runner = _FakeRunner()
        self.manager._open_server = fake_open
        self.manager._statuses["A"] = _ServerStatus(
            config=MCPServerConfig(name="A", url="http://127.0.0.1:1/mcp"),
            state="reconnecting",
        )
        self.manager._schedule_reconnect("A")

        self.assertTrue(done.wait(3), f"未在预期时间内恢复：{events}")
        self.assertEqual(events.count(("connected", "A")), 1)
        self.assertIn(("reconnecting", "A"), events)
        self.assertIn(("reconnect_failed", "A"), events)

        snapshot = self.manager.server_states()[0]
        self.assertEqual(snapshot["state"], "connected")
        self.assertEqual(snapshot["attempt"], 0)
        self.assertEqual(snapshot["tools"], ["ping", "pong"])
        self.assertEqual(self.manager._reconnect_timers, {})

    def test_failed_server_does_not_affect_others(self):
        """一个 Server 持续失败时，另一个已连接的 Server 保持可用。"""
        self.manager._runner = _FakeRunner()
        self.manager._open_server = None

        async def always_fail(cfg):
            raise RuntimeError("ConnectError: refused")

        self.manager._open_server = always_fail
        self.manager._statuses["A"] = _ServerStatus(
            config=MCPServerConfig(name="A", url="http://127.0.0.1:1/mcp"),
            state="reconnecting",
        )
        self.manager._statuses["B"] = _ServerStatus(
            config=MCPServerConfig(name="B", url="http://127.0.0.1:2/mcp"),
            state="connected",
        )
        self.manager._servers["B"] = self._fake_state("B", tools=("ping",))

        self.manager._schedule_reconnect("A")
        time.sleep(0.25)
        self.manager._reconnect_timers.get("A")  # 仍在重试中被清理后重新排期

        states = {st["name"]: st for st in self.manager.server_states()}
        self.assertEqual(states["B"]["state"], "connected")
        self.assertEqual(states["B"]["tools"], ["ping"])
        self.assertNotEqual(states["A"]["state"], "connected")
        self.assertIn("A", states["A"]["name"])

    def test_call_tool_connection_error_triggers_reconnect(self):
        self.manager.reconnect_delays = (5.0,)
        self.manager._runner = _FakeRunner(
            error=RuntimeError("httpx.ConnectError: connection refused")
        )
        self.manager._open_server = None
        self.manager._statuses["A"] = _ServerStatus(
            config=MCPServerConfig(name="A", url="http://127.0.0.1:1/mcp"),
            state="connected",
        )
        self.manager._servers["A"] = self._fake_state("A")
        self.manager._bindings["A__ping"] = ("A", "ping")

        result = self.manager.call_tool("A__ping", {})

        self.assertFalse(result["success"])
        self.assertIn("自动重连", result["error"])
        self.assertEqual(self.manager._statuses["A"].state, "reconnecting")
        self.assertEqual(self.manager._servers, {})
        self.assertIn("A", self.manager._reconnect_timers)

    def test_server_states_reports_retry_countdown(self):
        self.manager._statuses["A"] = _ServerStatus(
            config=MCPServerConfig(name="A", url="http://127.0.0.1:1/mcp"),
            state="reconnecting",
            attempt=2,
            last_error="ConnectError",
            retry_in=2.0,
            next_retry_at=time.monotonic() + 2.0,
        )
        snapshot = self.manager.server_states()[0]
        self.assertEqual(snapshot["state"], "reconnecting")
        self.assertEqual(snapshot["attempt"], 2)
        self.assertEqual(snapshot["last_error"], "ConnectError")
        self.assertGreater(snapshot["retry_in"], 0.5)
        self.assertLessEqual(snapshot["retry_in"], 2.0)


class _FakeManager:
    def __init__(self, servers) -> None:
        self.servers = servers
        self.bindings: dict = {}

    def server_tools(self, server: str):
        return list(self.servers.get(server, []))

    def bind(self, exposed: str, server: str, tool_name: str) -> None:
        self.bindings[exposed] = (server, tool_name)

    def unbind(self, exposed: str) -> None:
        self.bindings.pop(exposed, None)


class TestMcpToolRegistration(unittest.TestCase):
    def tearDown(self) -> None:
        _unregister_server_tools("A")
        _unregister_server_tools("B")

    def test_reconnect_reregisters_without_duplicates(self):
        manager = _FakeManager({"A": _fake_tools("ping")})
        self.assertEqual(_register_server_tools(manager, "A"), ["ping"])
        # 重复注册：先清理旧工具，不应产生重复项
        self.assertEqual(_register_server_tools(manager, "A"), ["ping"])
        self.assertEqual(list(TOOL_META).count("ping"), 1)

        # 服务恢复后工具列表变化：旧工具失效，新工具可用
        manager.servers["A"] = _fake_tools("pong")
        self.assertEqual(_register_server_tools(manager, "A"), ["pong"])
        self.assertNotIn("ping", TOOL_FUNCS)
        self.assertIn("pong", TOOL_FUNCS)
        self.assertEqual(manager.bindings, {"pong": ("A", "pong")})

    def test_servers_are_isolated_and_conflicts_prefixed(self):
        manager = _FakeManager({
            "A": _fake_tools("read_file"),
            "B": _fake_tools("ping"),
        })
        self.assertEqual(_register_server_tools(manager, "A"), ["A__read_file"])
        self.assertEqual(_register_server_tools(manager, "B"), ["ping"])

        _unregister_server_tools("A")
        self.assertNotIn("A__read_file", TOOL_FUNCS)
        self.assertIn("ping", TOOL_FUNCS)


if __name__ == "__main__":
    unittest.main()
