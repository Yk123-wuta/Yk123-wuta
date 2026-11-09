# -*- coding: utf-8 -*-
"""
xAgent.core.mcp_client —— 外部 MCP Server（Streamable HTTP / stdio）客户端管理。

职责：
  - 读取当前项目目录下的 .mcp.json，解析 mcpServers 连接信息
  - 用 MCP Python SDK 建立会话：Streamable HTTP（url）或 stdio（本地子进程管道）
  - 调用 list_tools() / call_tool()
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
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import anyio

from .config import load_settings

_SUPPORTED_TRANSPORTS = ("streamable-http", "streamable_http", "http")
_STDIO_TRANSPORTS = ("stdio",)


class MCPClientError(RuntimeError):
    """MCP 客户端错误（消息已整理为面向用户的文本）。"""


# ---------- 配置 ----------

@dataclass
class MCPServerConfig:
    """一个 MCP Server 的连接信息（HTTP 用 url，stdio 用 command/args）。"""

    name: str
    url: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    transport: str = "http"
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None

    @property
    def target(self) -> str:
        """供日志与 UI 展示的连接目标。"""
        if self.transport == "stdio":
            parts = [self.command, *self.args]
            return "stdio: " + " ".join(part for part in parts if part)
        return self.url


def load_mcp_config(
    project_root: str | Path | None = None,
) -> Tuple[List[MCPServerConfig], List[str]]:
    """
    读取 <project_root>/.mcp.json。

    返回 (配置列表, 警告列表)；文件不存在时返回 ([], [])。
    支持 Streamable HTTP（url）与 stdio（command + args）两种传输，
    其它 transport 会被跳过并记录警告。
    """
    root = Path(project_root or os.getcwd())
    settings = load_settings(str(root))
    path = root / settings.mcp_config_file
    warnings: List[str] = []
    if not path.is_file():
        return [], warnings

    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return [], [f"读取 {settings.mcp_config_file} 失败：{exc}"]

    servers = raw.get("mcpServers") if isinstance(raw, dict) else None
    if not isinstance(servers, dict):
        return [], [f"{settings.mcp_config_file} 缺少 mcpServers 配置"]

    configs: List[MCPServerConfig] = []
    for name, conf in servers.items():
        if not isinstance(conf, dict):
            warnings.append(f"MCP Server '{name}' 配置格式无效，已跳过")
            continue
        transport = str(
            conf.get("type") or conf.get("transport") or "streamable-http"
        ).lower()

        # ---------- stdio：本地子进程管道 ----------
        if transport in _STDIO_TRANSPORTS:
            command = conf.get("command")
            if not isinstance(command, str) or not command.strip():
                warnings.append(f"MCP Server '{name}' 使用 stdio 但缺少 command，已跳过")
                continue
            raw_args = conf.get("args")
            if isinstance(raw_args, (list, tuple)):
                args = [str(item) for item in raw_args]
            elif isinstance(raw_args, str):
                args = [raw_args]
            else:
                args = []
            raw_env = conf.get("env")
            env = (
                {str(k): str(v) for k, v in raw_env.items()}
                if isinstance(raw_env, dict) else {}
            )
            cwd = conf.get("cwd")
            configs.append(
                MCPServerConfig(
                    name=str(name),
                    transport="stdio",
                    command=command.strip(),
                    args=args,
                    env=env,
                    cwd=str(cwd) if isinstance(cwd, str) and cwd.strip() else None,
                )
            )
            continue

        # ---------- HTTP：Streamable HTTP ----------
        if transport not in _SUPPORTED_TRANSPORTS:
            warnings.append(
                f"MCP Server '{name}' 使用不支持的传输方式 '{transport}'，"
                "当前支持 Streamable HTTP 与 stdio"
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
                transport="http",
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

    def submit(self, coro, timeout: float = 15.0) -> Any:
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


# 连接类错误特征：命中后触发后台自动重连，而不是当作普通工具错误返回
_CONNECTION_ERROR_MARKERS = (
    "连接被中断",
    "连接失败",
    "请求超时",
    "ConnectError",
    "ConnectionError",
    "ConnectionReset",
    "ConnectionRefused",
    "BrokenResource",
    "ClosedResource",
    "EndOfStream",
    "RemoteProtocol",
    "disconnected",
    "Broken pipe",
    "EOF",
)


def is_connection_error(text: BaseException | str) -> bool:
    """判断异常/错误文本是否属于连接类问题。"""
    message = _reason(text) if isinstance(text, BaseException) else str(text)
    lowered = message.lower()
    return any(
        marker.lower() in lowered for marker in _CONNECTION_ERROR_MARKERS
    )


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


@dataclass
class _ServerStatus:
    """一个 Server 的连接状态（供后台重连与 TUI 展示）。"""

    config: MCPServerConfig
    state: str = "connecting"      # connecting / connected / reconnecting
    attempt: int = 0               # 已失败的重连次数
    last_error: str = ""
    retry_in: float = 0.0          # 距离下次重试的秒数（0 表示正在尝试）
    next_retry_at: float = 0.0     # time.monotonic() 时间点


class MCPClientManager:
    """管理多个 MCP Server 会话，并对工具名做 Server 内映射。"""

    def __init__(self, project_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root or os.getcwd())
        settings = load_settings(str(self.project_root))
        self.connect_timeout = settings.mcp_connect_timeout
        self.call_timeout = settings.mcp_call_timeout
        self.reconnect_delays = tuple(settings.mcp_reconnect_delays) or (1.0, 2.0, 5.0, 10.0, 30.0)
        self.configs: List[MCPServerConfig] = []
        self.warnings: List[str] = []
        self._runner: Optional[_AsyncRunner] = None
        self._servers: Dict[str, _ServerState] = {}
        self._statuses: Dict[str, _ServerStatus] = {}
        self._bindings: Dict[str, Tuple[str, str]] = {}
        self._reconnect_timers: Dict[str, threading.Timer] = {}
        self._on_event: Optional[Callable[[str, str, str], None]] = None
        self._lock = threading.RLock()
        self._initialized = False
        self._init_lock = threading.Lock()
        self._closed = False

    # ---------- 初始化 ----------

    def set_event_callback(
        self, callback: Optional[Callable[[str, str, str], None]]
    ) -> None:
        """
        注册状态回调 callback(event, server, detail)。

        event: connected / lost / reconnecting / reconnect_failed
        """
        self._on_event = callback

    def initialize(self) -> None:
        """读取配置并连接所有 Server；失败的 Server 转入后台自动重连。"""
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
            status = _ServerStatus(config=cfg)
            with self._lock:
                self._statuses[cfg.name] = status
            try:
                state = self._runner.submit(
                    self._open_server(cfg), self.connect_timeout
                )
            except Exception as exc:
                reason = _reason(exc)
                self._mark_failed(cfg.name, reason, notify="reconnect_failed")
                self.add_warning(
                    f"MCP Server '{cfg.name}'（{cfg.target}）连接失败：{reason}，"
                    "已转入后台自动重连"
                )
                self._schedule_reconnect(cfg.name)
                continue
            with self._lock:
                self._servers[cfg.name] = state
                status.state = "connected"
                status.attempt = 0
                status.last_error = ""
                status.retry_in = 0.0

    # ---------- 断线重连 ----------

    def _notify(self, event: str, server: str, detail: str = "") -> None:
        callback = self._on_event
        if callback is None:
            return
        try:
            callback(event, server, detail)
        except Exception:
            pass

    def _mark_failed(
        self, server: str, reason: str, notify: Optional[str] = None
    ) -> None:
        with self._lock:
            status = self._statuses.get(server)
            if status is None:
                return
            status.state = "reconnecting"
            status.last_error = reason
            status.retry_in = 0.0
        if notify:
            self._notify(notify, server, reason)
        self._notify("lost", server, reason)

    def _schedule_reconnect(self, server: str) -> None:
        """按退避序列为单个 Server 安排一次后台重连。"""
        with self._lock:
            if self._closed:
                return
            status = self._statuses.get(server)
            if status is None or status.state == "connected":
                return
            if server in self._reconnect_timers:
                return
            index = min(status.attempt, len(self.reconnect_delays) - 1)
            delay = max(0.1, self.reconnect_delays[index])
            status.state = "reconnecting"
            status.retry_in = delay
            status.next_retry_at = time.monotonic() + delay
            timer = threading.Timer(delay, self._reconnect_attempt, args=(server,))
            timer.daemon = True
            self._reconnect_timers[server] = timer
            timer.start()

    def _reconnect_attempt(self, server: str) -> None:
        """后台线程：尝试重新连接一个 Server。"""
        with self._lock:
            self._reconnect_timers.pop(server, None)
            if self._closed:
                return
            status = self._statuses.get(server)
            if status is None or status.state == "connected":
                return
            if self._servers.get(server) is not None:
                return
            status.attempt += 1
            status.state = "reconnecting"
            status.retry_in = 0.0
            attempt = status.attempt
            last_error = status.last_error
            config = status.config
        self._notify("reconnecting", server, last_error)

        runner = self._runner
        if runner is None:
            self._mark_failed(server, "MCP 事件循环不可用", "reconnect_failed")
            self._schedule_reconnect(server)
            return

        try:
            state = runner.submit(
                self._open_server(config), self.connect_timeout
            )
        except Exception as exc:
            reason = _reason(exc)
            self._mark_failed(server, reason, "reconnect_failed")
            self.add_warning(
                f"MCP Server '{server}' 第 {attempt} 次重连失败：{reason}"
            )
            self._schedule_reconnect(server)
            return

        old: Optional[_ServerState] = None
        with self._lock:
            old = self._servers.get(server)
            self._servers[server] = state
            status.state = "connected"
            status.attempt = 0
            status.last_error = ""
            status.retry_in = 0.0
        if old is not None:
            self._close_state(old, blocking=True)
        self._notify("connected", server, "")

    def _handle_connection_lost(self, server: str, reason: str) -> None:
        """调用阶段发现连接断开：移除失效会话并转入后台重连。"""
        old: Optional[_ServerState] = None
        with self._lock:
            old = self._servers.pop(server, None)
        if old is not None:
            # 调用线程不能等待关闭，交给后台线程处理，避免阻塞对话
            self._close_state(old, blocking=False)
        self._mark_failed(server, reason)
        self._schedule_reconnect(server)
        self.add_warning(
            f"MCP Server '{server}' 连接中断：{reason}，已转入后台自动重连"
        )

    def add_warning(self, message: str) -> None:
        """追加一条警告，并限制总量，避免持续重连失败时无限增长。"""
        with self._lock:
            self.warnings.append(message)
            if len(self.warnings) > 30:
                del self.warnings[:-30]

    def _close_state(self, state: _ServerState, blocking: bool = False) -> None:
        """关闭一个失效会话；blocking=False 时放到后台线程执行。"""
        runner = self._runner
        if runner is None or self._closed:
            return
        stack = state.stack

        def _close() -> None:
            try:
                runner.submit(self._close_stack(stack), 3.0)
            except Exception:
                pass

        if blocking:
            _close()
            return
        threading.Thread(
            target=_close, name=f"xagent-mcp-close-{id(state)}", daemon=True
        ).start()

    async def _open_server(self, cfg: MCPServerConfig) -> _ServerState:
        from mcp import ClientSession

        stack = AsyncExitStack()
        try:
            if cfg.transport == "stdio":
                read_stream, write_stream = await self._enter_stdio(stack, cfg)
            else:
                read_stream, write_stream = await self._enter_http(stack, cfg)
            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            # SDK 内部基于 anyio cancel scope，用 asyncio.wait_for 会吞掉真实错误
            with anyio.fail_after(self.connect_timeout):
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

    @staticmethod
    async def _enter_http(stack: AsyncExitStack, cfg: MCPServerConfig):
        """建立 Streamable HTTP 传输，返回 (read_stream, write_stream)。"""
        from mcp.client.streamable_http import (
            create_mcp_http_client,
            streamable_http_client,
        )

        http_client = create_mcp_http_client(headers=cfg.headers or None)
        await stack.enter_async_context(http_client)
        streams = await stack.enter_async_context(
            streamable_http_client(cfg.url, http_client=http_client)
        )
        return streams[0], streams[1]

    @staticmethod
    async def _enter_stdio(stack: AsyncExitStack, cfg: MCPServerConfig):
        """建立 stdio 传输：启动本地子进程并绑定 stdin/stdout。"""
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=cfg.command,
            args=list(cfg.args or []),
            env=cfg.env or None,
            cwd=cfg.cwd,
        )
        streams = await stack.enter_async_context(stdio_client(params))
        return streams[0], streams[1]

    # ---------- 查询 ----------

    def connected_servers(self) -> List[str]:
        with self._lock:
            return list(self._servers.keys())

    def server_config(self, server: str) -> Optional[MCPServerConfig]:
        with self._lock:
            state = self._servers.get(server)
            return state.config if state else None

    def server_tools(self, server: str) -> List[Any]:
        with self._lock:
            state = self._servers.get(server)
            return list(state.tools) if state else []

    def server_states(self) -> List[Dict[str, Any]]:
        """
        返回所有 Server 的状态快照，供 TUI 轮询展示。

        字段：name / url / state / attempt / last_error / retry_in / tools。
        """
        now = time.monotonic()
        snapshots: List[Dict[str, Any]] = []
        with self._lock:
            for name, status in self._statuses.items():
                if status.state == "connected":
                    state = self._servers.get(name)
                    tools = [
                        str(getattr(tool, "name", "")) for tool in state.tools
                    ] if state else []
                    retry_in = 0.0
                else:
                    tools = []
                    retry_in = max(0.0, status.next_retry_at - now) \
                        if status.next_retry_at else 0.0
                snapshots.append({
                    "name": name,
                    "url": status.config.target,
                    "transport": status.config.transport,
                    "state": status.state,
                    "attempt": status.attempt,
                    "last_error": status.last_error,
                    "retry_in": retry_in,
                    "tools": tools,
                })
        return snapshots

    def bind(self, exposed_name: str, server: str, tool_name: str) -> None:
        """记录 LLM 看到的工具名与 (Server, 原始工具名) 的映射。"""
        with self._lock:
            self._bindings[exposed_name] = (server, tool_name)

    def unbind(self, exposed_name: str) -> None:
        """移除工具名映射（Server 断开或工具重新注册前清理）。"""
        with self._lock:
            self._bindings.pop(exposed_name, None)

    def bindings(self) -> Dict[str, Tuple[str, str]]:
        with self._lock:
            return dict(self._bindings)

    # ---------- 调用 ----------

    def call_tool(self, exposed_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            binding = self._bindings.get(exposed_name)
        if binding is None:
            return {
                "success": False,
                "error": f"未知的 MCP 工具: {exposed_name}",
            }
        server, tool_name = binding

        with self._lock:
            state = self._servers.get(server)
        if state is None or self._runner is None:
            # 会话已失效（可能正在后台重连）：确保重连被安排并提示不可用
            if not self._closed:
                self._mark_failed(
                    server, f"MCP Server '{server}' 已断开，正在后台重连"
                )
                self._schedule_reconnect(server)
            return {
                "success": False,
                "error": (
                    f"MCP Server '{server}' 当前不可用（正在后台重连），"
                    "请稍后重试"
                ),
            }
        try:
            result = self._runner.submit(
                self._call_with_timeout(
                    state.session, tool_name, arguments or {}
                ),
                self.call_timeout + 2,
            )
        except Exception as exc:
            reason = _reason(exc)
            if is_connection_error(exc):
                # 连接类错误：判定会话失效，转入后台自动重连
                self._handle_connection_lost(server, reason)
                return {
                    "success": False,
                    "error": (
                        f"MCP Server '{server}' 连接中断，已转入后台自动重连："
                        f"{reason}"
                    ),
                }
            return {
                "success": False,
                "error": (
                    f"MCP 工具调用失败（{server}/{tool_name}）：{reason}"
                ),
            }
        return convert_call_result(server, tool_name, result)

    async def _call_with_timeout(
        self, session: Any, tool_name: str, arguments: Dict[str, Any]
    ) -> Any:
        with anyio.fail_after(self.call_timeout):
            return await session.call_tool(tool_name, arguments=arguments)

    # ---------- 关闭 ----------

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            timers = list(self._reconnect_timers.values())
            self._reconnect_timers.clear()
        for timer in timers:
            try:
                timer.cancel()
            except Exception:
                pass

        runner = self._runner
        if runner is None:
            return
        for state in list(self._servers.values()):
            try:
                runner.submit(self._close_stack(state.stack), 5.0)
            except Exception:
                pass
        with self._lock:
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
