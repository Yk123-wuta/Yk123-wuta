# -*- coding: utf-8 -*-
"""
xAgent.core.registry —— 工具注册表 + 分发器 + 门面类。

设计要点：
  - ToolService 是给 UI 层用的**唯一入口**，UI 不应直接 import tools.py 的函数
  - TOOLS dict 和 call_tool 保留以兼容旧代码
  - 类型转换 / 参数校验 / 异常捕获都在此层完成，tools.py 保持纯净
"""

from __future__ import annotations

import copy
import json
import os
import re
import shlex
import threading
import time
from typing import Any, Callable, Dict, List

from .types import ToolResult, ToolInfo
from .mcp_client import get_mcp_manager
from .context import recall_context
from .call_log import log_tool_call
from .tools import (
    get_current_working_directory,
    get_current_datetime,
    is_leap_year,
    list_directory,
    get_file_type,
    read_file,
    create_file,
    write_file,
    create_directory,
    delete_directory,
    delete_file,
    rename_item,
    search_web,
    fetch_web,
    run_cli,
)
from .skills import load_skill, create_skill


# ---------- 上下文归档检索包装 ----------

def _recall_context(id: str = "", text: str = "") -> dict:
    """recallContext 工具：以当前项目目录为根检索历史归档。"""
    return recall_context(text=text, archive_id=id or None, project_root=os.getcwd())


# ---------- 工具注册表（兼容旧版 dict-of-dict 结构） ----------

TOOL_META: Dict[str, ToolInfo] = {
    "get_current_working_directory": ToolInfo(
        name="get_current_working_directory",
        description="获取当前工作目录",
        parameters={},
    ),
    "get_current_datetime": ToolInfo(
        name="get_current_datetime",
        description=(
            "获取当前日期时间：返回本地时间、UTC 时间、时区与时间戳"
            "（读取真实系统时钟，跨平台）"
        ),
        parameters={},
    ),
    "is_leap_year": ToolInfo(
        name="is_leap_year",
        description=(
            "判断指定年份是否为闰年"
            "（规则：能被 4 整除且不能被 100 整除，或能被 400 整除）"
        ),
        parameters={"year": "int: 待判断的年份，1-9999（必需）"},
    ),
    "list_directory": ToolInfo(
        name="list_directory",
        description="列出指定目录下所有文件和子目录",
        parameters={"path": "str: 目录路径（可选，默认为当前目录）"},
    ),
    "get_file_type": ToolInfo(
        name="get_file_type",
        description="获取指定文件的文件类型",
        parameters={"file_path": "str: 文件路径（必需）"},
    ),
    "read_file": ToolInfo(
        name="read_file",
        description="获取指定文件的内容（仅支持纯文本格式文件）",
        parameters={"file_path": "str: 文件路径（必需）"},
    ),
    "create_file": ToolInfo(
        name="create_file",
        description="创建指定的目录和文件名创建新文件",
        parameters={
            "file_path": "str: 文件路径（必需）",
            "content": "str: 文件初始内容（可选，默认为空）",
        },
    ),
    "write_file": ToolInfo(
        name="write_file",
        description="编辑指定文件的内容（仅支持纯文本格式文件）",
        parameters={
            "file_path": "str: 文件路径（必需）",
            "content": "str: 要写入的新内容（必需）",
            "mode": "str: 写入模式 'overwrite' 或 'append'（可选，默认 overwrite）",
        },
    ),
    "create_directory": ToolInfo(
        name="create_directory",
        description="创建新目录",
        parameters={"dir_path": "str: 目录路径（必需）"},
    ),
    "delete_directory": ToolInfo(
        name="delete_directory",
        description="删除目录（非空）",
        parameters={
            "dir_path": "str: 目录路径（必需）",
            "force": "bool: 是否强制删除非空目录（可选，默认 True）",
        },
    ),
    "delete_file": ToolInfo(
        name="delete_file",
        description="删除文件",
        parameters={"file_path": "str: 文件路径（必需）"},
    ),
    "rename_item": ToolInfo(
        name="rename_item",
        description="重命名文件或目录",
        parameters={
            "source_path": "str: 源文件或目录路径（必需）",
            "new_name": "str: 新名称，只提供名称不含路径（必需）",
        },
    ),
    "searchWeb": ToolInfo(
        name="searchWeb",
        description="联网搜索，获取互联网最新信息（返回摘要与参考来源）",
        parameters={
            "query": "str: 搜索关键词或问题（必需）",
            "top_k": "int: 返回网页条数，1-10（可选，默认 10）",
        },
    ),
    "fetchWeb": ToolInfo(
        name="fetchWeb",
        description="获取指定网页的正文内容（提取正文并转为 Markdown，返回正文与原始 URL）",
        parameters={
            "url": "str: 网页地址，必须以 http:// 或 https:// 开头（必需）",
        },
    ),
    "runCli": ToolInfo(
        name="runCli",
        description="执行本地命令行程序（返回标准输出、标准错误与退出码）",
        parameters={
            "command": "str: 程序名称或路径，如 python、git、curl（必需）",
            "args": 'list[str]: 参数列表，如 ["-c", "print(1)"]（可选，默认空）',
            "timeout": "int: 超时秒数，1-120（可选，默认 30）",
        },
    ),
    "loadSkill": ToolInfo(
        name="loadSkill",
        description=(
            "加载 Skill 的完整说明：仅提供 name 时返回该 Skill 的 SKILL.md 全文；"
            "提供 filename 时读取该 Skill 目录内的附加文件（不重复加载 SKILL.md）"
        ),
        parameters={
            "name": "str: Skill 名称（必需）",
            "filename": "str: 需要加载的 Skill 文件路径或名称（可选）",
        },
    ),
    "createSkill": ToolInfo(
        name="createSkill",
        description=(
            "在当前项目的 .agents/skills 目录下创建新 Skill：生成英文名称子目录，"
            "并写入符合 Agent Skills 规范的 SKILL.md（含 name / description frontmatter 与正文）。"
            "目标 Skill 已存在时不会覆盖，返回失败提示"
        ),
        parameters={
            "name": "str: Skill 名称，仅小写字母、数字和连字符，如 my-skill（必需）",
            "skillContext": "str: 写入 SKILL.md 的完整内容，可包含 frontmatter 与正文（必需）",
        },
    ),
    "recallContext": ToolInfo(
        name="recallContext",
        description=(
            "检索上下文压缩时保存的历史归档（.agents/context/*.jsonl）："
            "按关键词返回原始对话片段、归档编号与消息位置。"
            "当压缩摘要缺少某些历史细节时使用"
        ),
        parameters={
            "text": "str: 要查找的关键词或文本内容（必需）",
            "id": "str: 归档编号，如 20261022-153000（可选，不填则检索全部归档）",
        },
    ),
}

# 工具函数绑定
TOOL_FUNCS: Dict[str, Callable[..., dict]] = {
    "get_current_working_directory": get_current_working_directory,
    "get_current_datetime": get_current_datetime,
    "is_leap_year": is_leap_year,
    "list_directory": list_directory,
    "get_file_type": get_file_type,
    "read_file": read_file,
    "create_file": create_file,
    "write_file": write_file,
    "create_directory": create_directory,
    "delete_directory": delete_directory,
    "delete_file": delete_file,
    "rename_item": rename_item,
    "searchWeb": search_web,
    "fetchWeb": fetch_web,
    "runCli": run_cli,
    "loadSkill": load_skill,
    "createSkill": create_skill,
    "recallContext": _recall_context,
}

# 兼容旧版 TOOLS dict 格式
TOOLS: Dict[str, Dict[str, Any]] = {
    name: {
        "func": TOOL_FUNCS[name],
        "description": meta.description,
        "parameters": dict(meta.parameters),
    }
    for name, meta in TOOL_META.items()
}


# ---------- 动态工具注册（MCP 等外部工具） ----------

_MCP_LOCK = threading.Lock()
_MCP_READY = False
_MCP_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")
_MAX_TOOL_NAME = 64

# 注册表读写锁：后台 MCP 重连线程与 TUI 轮询会并发访问
_TOOL_LOCK = threading.RLock()
# 记录「已注册的 MCP 工具名 -> 所属 Server」，用于按 Server 注销
_MCP_TOOL_SERVERS: Dict[str, str] = {}


def _sanitize_tool_name(value: str, fallback: str = "mcp_tool") -> str:
    """Function Calling 工具名只允许字母、数字、下划线和连字符。"""
    cleaned = _MCP_NAME_RE.sub("_", str(value or "")).strip("_")
    return cleaned[:_MAX_TOOL_NAME] or fallback


def _mcp_parameters(input_schema: Dict[str, Any]) -> Dict[str, str]:
    """把 JSON Schema 转成 UI 用的 "type: description（必需/可选）"。"""
    if not isinstance(input_schema, dict):
        return {}
    props = input_schema.get("properties")
    if not isinstance(props, dict):
        return {}
    required = set(input_schema.get("required") or [])
    result: Dict[str, str] = {}
    for pname, prop in props.items():
        prop = prop if isinstance(prop, dict) else {}
        ptype = prop.get("type") or ("array" if "items" in prop else "any")
        desc = prop.get("description") or ""
        flag = "必需" if pname in required else "可选"
        result[str(pname)] = f"{ptype}: {desc}（{flag}）"
    return result


def register_tool(name: str, info: ToolInfo, func: Callable[..., dict]) -> bool:
    """动态注册一个工具；名称已存在时返回 False（不覆盖）。"""
    with _TOOL_LOCK:
        if not name or name in TOOL_FUNCS:
            return False
        TOOL_META[name] = info
        TOOL_FUNCS[name] = func
        TOOLS[name] = {
            "func": func,
            "description": info.description,
            "parameters": dict(info.parameters),
        }
        return True


def unregister_tool(name: str) -> bool:
    """注销动态工具（测试或断开 MCP Server 时使用）。"""
    with _TOOL_LOCK:
        if name not in TOOL_META:
            return False
        TOOL_META.pop(name, None)
        TOOL_FUNCS.pop(name, None)
        TOOLS.pop(name, None)
        _MCP_TOOL_SERVERS.pop(name, None)
        return True


def _unregister_server_tools(server: str, manager=None) -> List[str]:
    """注销某个 MCP Server 当前已注册的全部工具。"""
    manager = manager if manager is not None else _mcp_manager_safe()
    removed: List[str] = []
    with _TOOL_LOCK:
        for exposed, owner in list(_MCP_TOOL_SERVERS.items()):
            if owner != server:
                continue
            TOOL_META.pop(exposed, None)
            TOOL_FUNCS.pop(exposed, None)
            TOOLS.pop(exposed, None)
            _MCP_TOOL_SERVERS.pop(exposed, None)
            removed.append(exposed)
            if manager is not None:
                manager.unbind(exposed)
    return removed


def _register_server_tools(manager, server: str) -> List[str]:
    """
    为单个 MCP Server 注册工具：先清理该 Server 的旧工具，再按最新列表注册。

    这样重连后不会出现重复工具或残留的失效工具。
    """
    with _TOOL_LOCK:
        _unregister_server_tools(server, manager)
        planned: set[str] = set()
        registered: List[str] = []
        for tool in manager.server_tools(server):
            base = _sanitize_tool_name(getattr(tool, "name", ""))
            exposed = base
            if exposed in TOOL_FUNCS or exposed in planned:
                exposed = _sanitize_tool_name(f"{server}__{base}")
            suffix = 2
            while exposed in TOOL_FUNCS or exposed in planned:
                expose_suffix = f"_{suffix}"
                exposed = exposed[: _MAX_TOOL_NAME - len(expose_suffix)] + expose_suffix
                suffix += 1

            schema = getattr(tool, "input_schema", None) or {}
            if hasattr(schema, "model_dump"):
                schema = schema.model_dump()
            schema = copy.deepcopy(dict(schema)) if isinstance(schema, dict) else {}

            info = ToolInfo(
                name=exposed,
                description=(
                    getattr(tool, "description", "") or
                    f"MCP 工具 {server}/{base}"
                ),
                parameters=_mcp_parameters(schema),
                input_schema=schema,
            )
            if not register_tool(exposed, info, _make_mcp_caller(manager, exposed)):
                continue
            _MCP_TOOL_SERVERS[exposed] = server
            planned.add(exposed)
            manager.bind(exposed, server, str(getattr(tool, "name", base)))
            registered.append(exposed)
        return registered


def _on_mcp_event(event: str, server: str, detail: str = "") -> None:
    """MCP 连接状态回调：断开时注销工具，恢复后重新注册。"""
    if not _MCP_READY:
        return
    manager = _mcp_manager_safe()
    if manager is None:
        return
    if event == "connected":
        _register_server_tools(manager, server)
    elif event in ("lost", "reconnect_failed"):
        _unregister_server_tools(server)


def ensure_mcp_tools() -> None:
    """
    连接 MCP Server 并注册其工具（幂等、线程安全）。

    TUI 会在后台线程调用它，避免启动时阻塞；CLI 可以直接同步调用。
    连接失败的 Server 由 manager 在后台按退避策略自动重连，
    恢复后通过事件回调重新注册其工具。
    """
    global _MCP_READY
    if _MCP_READY:
        return
    with _MCP_LOCK:
        if _MCP_READY:
            return
        _MCP_READY = True

        manager = None
        try:
            manager = get_mcp_manager()
            manager.set_event_callback(_on_mcp_event)
            manager.initialize()
        except Exception as exc:
            if manager is not None:
                manager.add_warning(f"MCP 客户端初始化失败：{exc}")
            return

        for server in manager.connected_servers():
            _register_server_tools(manager, server)


def _mcp_manager_safe():
    try:
        return get_mcp_manager()
    except Exception:
        return None


def _make_mcp_caller(manager, exposed_name: str) -> Callable[..., dict]:
    def _call(**kwargs: Any) -> dict:
        return manager.call_tool(exposed_name, kwargs)

    return _call


def get_mcp_warnings() -> List[str]:
    """返回 MCP 配置/连接阶段的警告（不触发初始化）。"""
    manager = _mcp_manager_safe()
    return list(manager.warnings) if manager is not None else []


def mcp_status() -> List[tuple[str, List[str]]]:
    """返回 [(服务器名, [工具名...])]，供 TUI 边栏展示。"""
    manager = _mcp_manager_safe()
    if manager is None:
        return []
    return [
        (
            server,
            [str(getattr(tool, "name", "")) for tool in manager.server_tools(server)],
        )
        for server in manager.connected_servers()
    ]


def mcp_server_states() -> List[Dict[str, Any]]:
    """
    返回所有 MCP Server 的连接状态快照（含重连进度），供 TUI 轮询展示。

    每个元素包含：name / url / state / attempt / last_error / retry_in / tools。
    """
    manager = _mcp_manager_safe()
    if manager is None:
        return []
    return manager.server_states()


# ---------- 类型转换 ----------

def _convert_kwargs(tool_name: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """根据 ToolInfo.parameters 声明的类型字符串做轻量转换。"""
    with _TOOL_LOCK:
        meta = TOOL_META[tool_name]
    # 外部工具（MCP）的参数已由 LLM 按 JSON Schema 传入，无需再做字符串转换
    if meta.input_schema is not None:
        return dict(kwargs)
    converted: Dict[str, Any] = {}
    for key, value in kwargs.items():
        param_type = meta.parameters.get(key, "")
        if "list" in param_type.lower():
            converted[key] = _to_list(value)
        elif "bool" in param_type.lower():
            if isinstance(value, str):
                converted[key] = value.lower() in ('true', '1', 'yes')
            else:
                converted[key] = bool(value)
        elif "int" in param_type.lower():
            try:
                converted[key] = int(value)
            except (ValueError, TypeError):
                converted[key] = value
        else:
            converted[key] = value
    return converted


def _to_list(value: Any) -> list:
    """把 LLM 传来的 list 参数归一化：支持 list / JSON 数组字符串 / 空格分隔字符串。"""
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except json.JSONDecodeError:
                pass
        try:
            return shlex.split(text)
        except ValueError:
            return text.split()
    return value


# ---------- 旧版 call_tool 入口（dict -> dict） ----------

def call_tool(tool_name: str, **kwargs: Any) -> dict:
    """调用工具，返回 dict（与旧版接口完全兼容）。"""
    result = call_tool_result(tool_name, **kwargs)
    return result.to_dict()


def call_tool_result(tool_name: str, **kwargs: Any) -> ToolResult:
    """调用工具，返回 ToolResult（UI 层推荐用这个）。"""
    with _TOOL_LOCK:
        func = TOOL_FUNCS.get(tool_name)
    if func is None:
        ensure_mcp_tools()
    with _TOOL_LOCK:
        func = TOOL_FUNCS.get(tool_name)
    if func is None:
        return ToolResult.fail(f"未知工具: {tool_name}")

    started = time.time()
    converted: Dict[str, Any] = {}
    try:
        converted = _convert_kwargs(tool_name, kwargs)
        raw = func(**converted)
        # tools.py 函数已经返回 dict，直接转 ToolResult
        result = ToolResult.from_dict(raw)
    except TypeError as e:
        result = ToolResult.fail(f"参数错误: {str(e)}")
    except Exception as e:
        result = ToolResult.fail(str(e))

    # 调用日志落盘（与 TUI 展示分离，失败不影响调用结果）
    log_tool_call(
        tool_name,
        converted,
        result.to_dict(),
        int((time.time() - started) * 1000),
        success=result.success,
    )
    return result


# ---------- ToolService 门面（UI 层唯一入口） ----------

class ToolService:
    """
    面向 UI 层的工具服务门面。

    未来 web / desktop / mobile 都只 import 这个类，
    不依赖 TOOLS / call_tool / 任何 tools.py 内部函数。
    """

    @staticmethod
    def list_tools() -> List[ToolInfo]:
        """获取当前已注册的工具（不会触发 MCP 连接，便于界面先行渲染）。"""
        with _TOOL_LOCK:
            return list(TOOL_META.values())

    @staticmethod
    def get_tool_info(name: str) -> ToolInfo | None:
        """按名称查工具元数据。"""
        with _TOOL_LOCK:
            return TOOL_META.get(name)

    @staticmethod
    def ensure_mcp() -> None:
        """显式连接 MCP Server 并注册工具（CLI / 后台线程调用）。"""
        ensure_mcp_tools()

    @staticmethod
    def call(tool_name: str, **kwargs: Any) -> ToolResult:
        """调用工具，返回 ToolResult。"""
        return call_tool_result(tool_name, **kwargs)

    @staticmethod
    def call_dict(tool_name: str, **kwargs: Any) -> dict:
        """调用工具，返回 dict（序列化到网络时用）。"""
        return call_tool(tool_name, **kwargs)
