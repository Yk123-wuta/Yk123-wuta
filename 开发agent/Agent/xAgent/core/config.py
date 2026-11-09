# -*- coding: utf-8 -*-
"""
xAgent.core.config —— 全局配置（LLM / 上下文 / MCP / 工具 / 界面）。

配置来源（优先级从高到低）：
    1. 环境变量
    2. 当前工作目录下的 config.ini
    3. 代码内置默认值

首次运行时如果当前目录没有 config.ini，会自动生成一份完整的 Mock 配置，
用户修改后重新启动 xAgent 即可切换到真实模型。

config.ini 结构（所有可选值均有默认值）：
    [llm]       provider / api_key / base_url / model / search_model / max_model_calls
    [context]   压缩阈值、摘要长度、归档目录、检索条数
    [session]   会话目录、AGENTS.md 文件名
    [mcp]       MCP 配置文件、连接与调用超时
    [tools]     搜索 / 抓取 / 命令行超时与截断长度
    [ui]        侧栏宽度

⚠️ 安全提示：API Key 只放在 config.ini 或环境变量中，不要写进源码。
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

CONFIG_FILE_NAME = "config.ini"

# 旧版配置位置（仅作迁移提示，不再作为主配置来源）
LEGACY_CONFIG_INI: Path = Path(__file__).resolve().parent / "config.ini"

# 首次运行时写出的完整 Mock 配置
DEFAULT_CONFIG_TEMPLATE = """\
; ============================================================
; xAgent 配置文件
; 首次运行自动生成。修改后请重新启动 xAgent 生效。
; ⚠️ 本文件包含密钥，提交到远程仓库前请加入 .gitignore
; ============================================================

[llm]
; provider: mock（本地演示，不联网） / qianfan（OpenAI 兼容接口）
provider = mock
api_key = mock-key-please-replace
base_url = https://qianfan.baidubce.com/v2
model = mock-model
search_model = mock-model
search_url = https://qianfan.baidubce.com/v2/ai_search/chat/completions
max_model_calls = 8
; 如需沿用旧版配置，可参考 Agent/xAgent/core/config.ini

[context]
; 0 = 自动推断；无法识别模型时使用默认 128k
window = 0
warn_ratio = 0.70
summary_target_tokens = 5000
archive_dir = .agents/context
recall_max_matches = 8
recall_snippet_chars = 240
recall_total_chars = 6000
tool_result_limit = 2000

[session]
dir = .agents/sessions
agents_md = AGENTS.md

[mcp]
config_file = .mcp.json
connect_timeout = 15
call_timeout = 120
; 断线重连退避（秒），列表用完后重复最后一个值
reconnect_delays = 1,2,5,10,30

[tools]
search_top_k = 10
search_timeout = 60
fetch_timeout = 30
fetch_connect_timeout = 10
fetch_max_chars = 20000
cli_timeout = 30
cli_max_timeout = 120
cli_max_output_chars = 8000

[ui]
sidebar_width = 40
"""


def config_path(root: str | Path | None = None) -> Path:
    """当前项目的 config.ini 路径。"""
    return Path(root or os.getcwd()).resolve() / CONFIG_FILE_NAME


def ensure_config_file(root: str | Path | None = None) -> tuple[Path, bool]:
    """
    确保当前目录存在 config.ini。

    返回 (路径, 是否本次新建)。新建时写入完整的 Mock 配置，
    调用方应提示用户修改后重新启动。
    """
    path = config_path(root)
    if path.exists():
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    return path, True


# ============================================================
# 配置对象
# ============================================================

@dataclass(frozen=True)
class Settings:
    """一次读取得到的完整配置（所有字段都有默认值）。"""

    config_path: Path = field(default_factory=config_path)

    # LLM
    provider: str = "mock"
    api_key: str = ""
    base_url: str = "https://qianfan.baidubce.com/v2"
    model: str = "mock-model"
    search_model: str = "mock-model"
    search_url: str = (
        "https://qianfan.baidubce.com/v2/ai_search/chat/completions"
    )
    max_model_calls: int = 8

    # 上下文
    context_window: int = 0          # 0 = 自动推断
    context_warn_ratio: float = 0.70
    summary_target_tokens: int = 5000
    archive_dir: str = ".agents/context"
    recall_max_matches: int = 8
    recall_snippet_chars: int = 240
    recall_total_chars: int = 6000
    tool_result_limit: int = 2000

    # 会话
    session_dir: str = ".agents/sessions"
    agents_md_file: str = "AGENTS.md"

    # MCP
    mcp_config_file: str = ".mcp.json"
    mcp_connect_timeout: float = 15.0
    mcp_call_timeout: float = 120.0
    mcp_reconnect_delays: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0, 30.0)

    # 工具
    search_top_k: int = 10
    search_timeout: float = 60.0
    fetch_timeout: float = 30.0
    fetch_connect_timeout: float = 10.0
    fetch_max_chars: int = 20000
    cli_timeout: int = 30
    cli_max_timeout: int = 120
    cli_max_output_chars: int = 8000

    # 界面
    sidebar_width: int = 40

    @property
    def use_mock(self) -> bool:
        """provider=mock 或没有 api_key 时使用本地 Mock。"""
        return self.provider.lower() == "mock" or not self.api_key


def _read_parser(path: Path) -> tuple[configparser.ConfigParser, str]:
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError) as exc:
        return parser, f"config.ini 解析失败：{exc}"
    return parser, ""


def _get(parser, section: str, option: str, default: str) -> str:
    try:
        return parser.get(section, option, fallback=default).strip()
    except (configparser.Error, ValueError):
        return default


def _get_int(parser, section: str, option: str, default: int) -> int:
    try:
        return int(_get(parser, section, option, str(default)))
    except (TypeError, ValueError):
        return default


def _get_float(parser, section: str, option: str, default: float) -> float:
    try:
        return float(_get(parser, section, option, str(default)))
    except (TypeError, ValueError):
        return default


def _get_float_list(
    parser, section: str, option: str, default: tuple[float, ...]
) -> tuple[float, ...]:
    """解析 "1,2,5,10" 形式的退避列表。"""
    raw = _get(parser, section, option, "")
    if not raw:
        return default
    try:
        values = tuple(float(part) for part in raw.split(",") if part.strip())
    except ValueError:
        return default
    return values or default


def _env(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def load_settings(root: str | Path | None = None) -> Settings:
    """
    读取当前目录的 config.ini 并叠加环境变量。

    文件不存在或解析失败时全部使用内置默认值，不会抛异常。
    """
    path = config_path(root)
    parser, error = _read_parser(path)
    if error:
        parser = configparser.ConfigParser()

    # 旧版 [qianfan] 段兼容
    legacy_key = _get(parser, "qianfan", "baidu_qianfan_api_key", "")
    legacy_model = _get(parser, "qianfan", "model", "")
    legacy_search_model = _get(parser, "qianfan", "search_model", "")

    api_key = _env(
        "QIANFAN_API_KEY",
        _get(parser, "llm", "api_key", legacy_key) or legacy_key,
    )
    # 旧版 [qianfan] 配置有 Key 时默认按真实接口处理
    default_provider = "qianfan" if legacy_key else "mock"

    return Settings(
        config_path=path,
        provider=_env(
            "XAGENT_PROVIDER",
            _get(parser, "llm", "provider", default_provider),
        ),
        api_key=api_key,
        base_url=_env(
            "QIANFAN_BASE_URL",
            _get(parser, "llm", "base_url", "https://qianfan.baidubce.com/v2"),
        ),
        model=_env(
            "QIANFAN_MODEL",
            _get(parser, "llm", "model", legacy_model or "mock-model"),
        ),
        search_model=_env(
            "QIANFAN_SEARCH_MODEL",
            _get(
                parser, "llm", "search_model",
                legacy_search_model or "mock-model",
            ),
        ),
        search_url=_env(
            "QIANFAN_SEARCH_URL",
            _get(
                parser, "llm", "search_url",
                "https://qianfan.baidubce.com/v2/ai_search/chat/completions",
            ),
        ),
        max_model_calls=_get_int(parser, "llm", "max_model_calls", 8),
        context_window=_get_int(parser, "context", "window", 0),
        context_warn_ratio=_get_float(parser, "context", "warn_ratio", 0.70),
        summary_target_tokens=_get_int(
            parser, "context", "summary_target_tokens", 5000
        ),
        archive_dir=_get(parser, "context", "archive_dir", ".agents/context"),
        recall_max_matches=_get_int(parser, "context", "recall_max_matches", 8),
        recall_snippet_chars=_get_int(
            parser, "context", "recall_snippet_chars", 240
        ),
        recall_total_chars=_get_int(parser, "context", "recall_total_chars", 6000),
        tool_result_limit=_get_int(parser, "context", "tool_result_limit", 2000),
        session_dir=_get(parser, "session", "dir", ".agents/sessions"),
        agents_md_file=_get(parser, "session", "agents_md", "AGENTS.md"),
        mcp_config_file=_env(
            "XAGENT_MCP_CONFIG",
            _get(parser, "mcp", "config_file", ".mcp.json"),
        ),
        mcp_connect_timeout=_get_float(parser, "mcp", "connect_timeout", 15.0),
        mcp_call_timeout=_get_float(parser, "mcp", "call_timeout", 120.0),
        mcp_reconnect_delays=_get_float_list(
            parser, "mcp", "reconnect_delays", (1.0, 2.0, 5.0, 10.0, 30.0)
        ),
        search_top_k=_get_int(parser, "tools", "search_top_k", 10),
        search_timeout=_get_float(parser, "tools", "search_timeout", 60.0),
        fetch_timeout=_get_float(parser, "tools", "fetch_timeout", 30.0),
        fetch_connect_timeout=_get_float(
            parser, "tools", "fetch_connect_timeout", 10.0
        ),
        fetch_max_chars=_get_int(parser, "tools", "fetch_max_chars", 20000),
        cli_timeout=_get_int(parser, "tools", "cli_timeout", 30),
        cli_max_timeout=_get_int(parser, "tools", "cli_max_timeout", 120),
        cli_max_output_chars=_get_int(
            parser, "tools", "cli_max_output_chars", 8000
        ),
        sidebar_width=_get_int(parser, "ui", "sidebar_width", 40),
    )
