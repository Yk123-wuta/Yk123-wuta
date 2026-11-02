# -*- coding: utf-8 -*-
"""MCPClient 单元测试：配置解析、结果转换、动态注册。"""

import json
import tempfile
import unittest
from pathlib import Path

from mcp import types

from xAgent.core import ToolService
from xAgent.core.mcp_client import load_mcp_config, convert_call_result
from xAgent.core.registry import (
    TOOL_META, TOOL_FUNCS, register_tool, unregister_tool,
    _sanitize_tool_name, _mcp_parameters,
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

    def test_unsupported_transport_and_missing_url(self):
        with self._write(json.dumps({
            "mcpServers": {
                "stdio": {"type": "stdio", "command": "python"},
                "nourl": {"type": "http"},
            }
        })) as tmp:
            configs, warnings = load_mcp_config(tmp)
            self.assertEqual(configs, [])
            self.assertEqual(len(warnings), 2)

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


if __name__ == "__main__":
    unittest.main()
