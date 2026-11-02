# -*- coding: utf-8 -*-
"""
xAgent.core.mcp_client —— 外部 MCP Server（Streamable HTTP）客户端管理。

职责：
  - 读取当前项目目录下的 .mcp.json，解析 mcpServers 连接信息
  - 用 MCP Python SDK 建立 Streamable HTTP 会话，调用 list_tools() / call_tool()
  - 保存「暴露给 LLM 的工具名 -> (Server, 原始工具名)」映射
  - 单个 Server 连接失败时只记录警告，不影响其它 Server 与本地工具

线程模型：MCP SDK 全部是 async API，这里用一个后台线程跑独立事件循环，
对外暴露同步方法，供现有同步的 ToolService 直接调用。
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import json
import os
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import anyio

MCP_CONFIG_FILE = ".mcp.json"
CONNECT_TIMEOUT = 15.0
CALL_TIMEOUT = 120.0

_SUPPORTED_TRANSPORTS = ("streamable-http", "streamable_http", "http")


class MCPClientError(RuntimeError):
    """MCP 客户端错误（消息已整理为面向用户的文本）。"""


# ---------- 配置 ----------

@dataclass
class MCPServerConfig:
    """一个 MCP Server 的连接信息。"""

    name: str
    url: str
    headers: Dict[str, str] = field(default_factory=dict)


def load_mcp_config(
    project_root: str | Path | None = None,
) -> Tuple[List[MCPServerConfig], List[str]]:
    """
    读取 <project_root>/.mcp.json。

    返回 (配置列表, 警告列表)；文件不存在时返回 ([], [])。
    仅支持 Streamable HTTP，其它 transport 会被跳过并记录警告。
    """
    root = Path(project_root or os.getcwd())
    path = root / MCP_CONFIG_FILE
    warnings: List[str] = []
    if not path.is_file():
        return [], warnings

    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return [], [f"读取 {MCP_CONFIG_FILE} 失败：{exc}"]

    servers = raw.get("mcpServers") if isinstance(raw, dict) else None
    if not isinstance(servers, dict):
        return [], [f"{MCP_CONFIG_FILE} 缺少 mcpServers 配置"]

    configs: List[MCPServerConfig] = []
    for name, conf in servers.items():
        if not isinstance(conf, dict):
            warnings.append(f"MCP Server '{name}' 配置格式无效，已跳过")
            continue
        transport = str(
            conf.get("type") or conf.get("transport") or "streamable-http"
        ).lower()
        if transport not in _SUPPORTED_TRANSPORTS:
            warnings.append(
                f"MCP Server '{name}' 使用不支持的传输方式 '{transport}'，"
                "当前仅支持 Streamable HTTP"
            )
            continue
        url = conf.get("url") or conf.get("httpUrl") or conf.get("http_url")
        if not isinstance(url, str) or not url.strip():
            warnings.append(f"MCP Server '{name}' 缺少 url，已跳过")
            continue
        headers = conf.get("headers")
        if not isinstance(headers, dict):
            headers = {}
        configs.append(
            MCPServerConfig(
                name=str(name),
                url=url.strip(),
                headers={str(k): str(v) for k, v in headers.items()},
            )
        )
    return configs, warnings


# ---------- 异步事件循环线程 ----------

class _AsyncRunner:
    """在后台线程里维护一个事件循环，把 async 调用桥接成同步调用。"""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._started = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="xagent-mcp-loop", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._started.set()
        self._loop.run_forever()

    def submit(self, coro, timeout: float = CONNECT_TIMEOUT) -> Any:
        if not self._started.wait(3):
            raise RuntimeError("MCP 事件循环线程启动失败")
        done: concurrent.futures.Future = concurrent.futures.Future()

        async def _await_and_report() -> None:
            # 独立捕获结果，避免 run_coroutine_threadsafe 把任务取消误报为
            # concurrent.futures.CancelledError，从而丢失真实错误
            try:
                result = await coro
            except BaseException as exc:  # 含 asyncio/anyio 的取消异常
                if not done.done():
                    done.set_exception(_as_caller_error(exc))
            else:
                if not done.done():
                    done.set_result(result)

        chained = asyncio.run_coroutine_threadsafe(_await_and_report(), self._loop)
        chained.add_done_callback(
            lambda f: None if f.cancelled() else f.exception()
        )
        try:
            return done.result(timeout)
        except concurrent.futures.TimeoutError as exc:
            done.cancel()
            raise RuntimeError(f"请求超时（{timeout:g}s）") from exc

    def stop(self) -> None:
        if self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._loop.stop)


def _format_error(exc: BaseException) -> str:
    """把 SDK 可能抛出的 ExceptionGroup 展开成可读文本。"""
    if isinstance(exc, BaseExceptionGroup):
        parts = [_format_error(e) for e in exc.exceptions]
        return "; ".join(p for p in parts if p)
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _reason(exc: BaseException) -> str:
    """面向用户的错误原因：已整理过的异常不再重复类型名前缀。"""
    if isinstance(exc, MCPClientError):
        return str(exc)
    return _format_error(exc)


def _as_caller_error(exc: BaseException) -> BaseException:
    """
    把事件循环线程里的异常转换成调用方可以理解的异常。

    anyio 在连接失败/取消作用域退出时会取消宿主任务，导致真实的
    ConnectError 被 asyncio.CancelledError 覆盖；这里优先回溯它的
    上下文，尽量还原底层原因。
    """
    if isinstance(exc, asyncio.CancelledError):
        context = exc.__context__
        if isinstance(context, BaseException) and not isinstance(
            context, asyncio.CancelledError
        ):
            return RuntimeError(_format_error(context))
        return RuntimeError("连接被中断（MCP Server 未响应或无法建立连接）")
    if isinstance(exc, Exception):
        return exc
    return RuntimeError(_format_error(exc))


def _pick_open_error(
    original: BaseException, close_error: Optional[BaseException]
) -> BaseException:
    """选择更有信息量的异常：关闭上下文时的 SDK 错误通常包含真实原因。"""
    if close_error is None:
        return original
    if isinstance(close_error, asyncio.CancelledError):
        return original
    if isinstance(original, asyncio.CancelledError):
        return close_error
    if isinstance(close_error, BaseExceptionGroup):
        return close_error
    return original


def _to_jsonable(value: Any) -> Any:
    """把 SDK 的 pydantic 模型 / 嵌套结构转成 JSON 可序列化对象。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return _to_jsonable(value.model_dump())
        except Exception:
            pass
    return str(value)


def _content_text(result: Any) -> str:
    """拼接 CallToolResult.content 中的文本/资源/图片占位说明。"""
    parts: List[str] = []
    for item in getattr(result, "content", None) or []:
        kind = getattr(item, "type", None)
        if kind == "text":
            parts.append(str(getattr(item, "text", "")))
        elif kind == "image":
            data = getattr(item, "data", "") or ""
            mime = getattr(item, "mime_type", "image")
            parts.append(f"[图片 {mime}，base64 长度 {len(str(data))}]")
        elif kind == "audio":
            data = getattr(item, "data", "") or ""
            mime = getattr(item, "mime_type", "audio")
            parts.append(f"[音频 {mime}，base64 长度 {len(str(data))}]")
        elif kind == "resource":
            resource = getattr(item, "resource", None)
            text = getattr(resource, "text", None)
            if text is not None:
                parts.append(str(text))
            else:
                parts.append(json.dumps(_to_jsonable(resource), ensure_ascii=False))
        else:
            parts.append(json.dumps(_to_jsonable(item), ensure_ascii=False))
    return "\n".join(p for p in parts if p)


def convert_call_result(server: str, tool: str, result: Any) -> Dict[str, Any]:
    """
    MCP CallToolResult -> xAgent 工具结果 dict。

    文本内容放 content，结构化数据放 structured_content，错误信息放 error。
    """
    content = _content_text(result)
    structured = _to_jsonable(getattr(result, "structured_content", None))
    is_error = bool(getattr(result, "is_error", False))

    payload: Dict[str, Any] = {
        "server": server,
        "tool": tool,
        "content": content,
    }
    if structured not in (None, {}, []):
        payload["structured_content"] = structured

    if is_error:
        payload["success"] = False
        payload["error"] = content or f"MCP 工具 {server}/{tool} 返回错误"
    else:
        payload["success"] = True
        payload["message"] = content or f"MCP 工具 {server}/{tool} 执行成功"
    return payload


# ---------- 客户端管理器 ----------

@dataclass
class _ServerState:
    config: MCPServerConfig
    stack: AsyncExitStack
    session: Any
    tools: List[Any] = field(default_factory=list)


class MCPClientManager:
    """管理多个 MCP Server 会话，并对工具名做 Server 内映射。"""

    def __init__(self, project_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root or os.getcwd())
        self.configs: List[MCPServerConfig] = []
        self.warnings: List[str] = []
        self._runner: Optional[_AsyncRunner] = None
        self._servers: Dict[str, _ServerState] = {}
        self._bindings: Dict[str, Tuple[str, str]] = {}
        self._initialized = False
        self._init_lock = threading.Lock()
        self._closed = False

    # ---------- 初始化 ----------

    def initialize(self) -> None:
        """读取配置并连接所有 Server；幂等，可重复调用。"""
        with self._init_lock:
            if self._initialized:
                return
            self._initialized = True

        configs, warnings = load_mcp_config(self.project_root)
        self.configs = configs
        self.warnings.extend(warnings)
        if not configs:
            return

        try:
            self._runner = _AsyncRunner()
        except Exception as exc:
            self.warnings.append(f"MCP 客户端初始化失败：{_reason(exc)}")
            return

        for cfg in configs:
            try:
                state = self._runner.submit(self._open_server(cfg), CONNECT_TIMEOUT)
            except Exception as exc:
                self.warnings.append(
                    f"MCP Server '{cfg.name}'（{cfg.url}）连接失败：{_reason(exc)}"
                )
                continue
            self._servers[cfg.name] = state

    async def _open_server(self, cfg: MCPServerConfig) -> _ServerState:
        from mcp import ClientSession
        from mcp.client.streamable_http import (
            create_mcp_http_client,
            streamable_http_client,
        )

        stack = AsyncExitStack()
        try:
            http_client = create_mcp_http_client(headers=cfg.headers or None)
            await stack.enter_async_context(http_client)
            streams = await stack.enter_async_context(
                streamable_http_client(cfg.url, http_client=http_client)
            )
            read_stream, write_stream = streams[0], streams[1]
            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            # SDK 内部基于 anyio cancel scope，用 asyncio.wait_for 会吞掉真实错误
            with anyio.fail_after(CONNECT_TIMEOUT):
                await session.initialize()
                result = await session.list_tools()
            return _ServerState(
                config=cfg,
                stack=stack,
                session=session,
                tools=list(getattr(result, "tools", None) or []),
            )
        except BaseException as exc:
            # 关闭上下文时 SDK 的 TaskGroup 才会抛出真实原因（如 ConnectError），
            # 而原异常往往只是 anyio 的取消包装，因此优先采用关闭阶段的信息
            close_error: Optional[BaseException] = None
            try:
                await stack.aclose()
            except BaseException as close_exc:
                close_error = close_exc
            reason = _pick_open_error(exc, close_error)
            # 统一转成普通异常，避免 anyio 的取消机制把真实原因覆盖成取消
            raise MCPClientError(_format_error(_as_caller_error(reason))) from None

    # ---------- 查询 ----------

    def connected_servers(self) -> List[str]:
        return list(self._servers.keys())

    def server_config(self, server: str) -> Optional[MCPServerConfig]:
        state = self._servers.get(server)
        return state.config if state else None

    def server_tools(self, server: str) -> List[Any]:
        state = self._servers.get(server)
        return list(state.tools) if state else []

    def bind(self, exposed_name: str, server: str, tool_name: str) -> None:
        """记录 LLM 看到的工具名与 (Server, 原始工具名) 的映射。"""
        self._bindings[exposed_name] = (server, tool_name)

    def bindings(self) -> Dict[str, Tuple[str, str]]:
        return dict(self._bindings)

    # ---------- 调用 ----------

    def call_tool(self, exposed_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        binding = self._bindings.get(exposed_name)
        if binding is None:
            return {
                "success": False,
                "error": f"未知的 MCP 工具: {exposed_name}",
            }
        server, tool_name = binding
        state = self._servers.get(server)
        if state is None or self._runner is None:
            return {
                "success": False,
                "error": f"MCP Server '{server}' 当前不可用",
            }
        try:
            result = self._runner.submit(
                self._call_with_timeout(state.session, tool_name, arguments or {}),
                CALL_TIMEOUT + 2,
            )
        except Exception as exc:
            return {
                "success": False,
                "error": (
                    f"MCP 工具调用失败（{server}/{tool_name}）：{_reason(exc)}"
                ),
            }
        return convert_call_result(server, tool_name, result)

    @staticmethod
    async def _call_with_timeout(session: Any, tool_name: str, arguments: Dict[str, Any]) -> Any:
        with anyio.fail_after(CALL_TIMEOUT):
            return await session.call_tool(tool_name, arguments=arguments)

    # ---------- 关闭 ----------

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        runner = self._runner
        if runner is None:
            return
        for state in list(self._servers.values()):
            try:
                runner.submit(self._close_stack(state.stack), 5.0)
            except Exception:
                pass
        self._servers.clear()
        try:
            runner.stop()
        except Exception:
            pass

    @staticmethod
    async def _close_stack(stack: AsyncExitStack) -> None:
        await stack.aclose()


# ---------- 全局单例 ----------

_MANAGER: Optional[MCPClientManager] = None
_MANAGER_LOCK = threading.Lock()


def get_mcp_manager(project_root: str | Path | None = None) -> MCPClientManager:
    """获取全局 MCP 管理器（首次调用时按启动目录创建）。"""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = MCPClientManager(project_root)
            atexit.register(_MANAGER.shutdown)
        return _MANAGER


def reset_mcp_manager() -> None:
    """重置单例（测试用）。"""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is not None:
            try:
                _MANAGER.shutdown()
            except Exception:
                pass
            _MANAGER = None
