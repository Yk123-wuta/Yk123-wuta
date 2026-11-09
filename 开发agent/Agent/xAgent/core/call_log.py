# -*- coding: utf-8 -*-
"""
xAgent.core.call_log —— 工具调用落盘日志（与 TUI 展示分离）。

每次工具调用向 <log_dir>/tool_calls-YYYYMMDD.jsonl 追加一行 JSON，记录：
  - 工具名、耗时、成功/失败
  - 入参摘要、结果摘要（按配置长度截断，避免日志膨胀）

日志写入失败绝不影响工具调用本身。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .config import load_settings

_WRITE_LOCK = threading.Lock()


def _summarize(value: Any, limit: int) -> str:
    """把任意值压缩成单行摘要，并按字符上限截断。"""
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    text = " ".join(text.split())
    if limit > 0 and len(text) > limit:
        text = text[:limit] + "…"
    return text


def log_tool_call(
    tool_name: str,
    arguments: Any,
    result: Any,
    elapsed_ms: int,
    success: Optional[bool] = None,
) -> Optional[Path]:
    """
    记录一次工具调用，返回写入的文件路径（被禁用或失败时返回 None）。

    result 可以是 ToolResult.to_dict() 之后的扁平字典。
    """
    try:
        settings = load_settings()
        if not settings.log_enabled:
            return None

        directory = Path(settings.log_dir)
        if not directory.is_absolute():
            directory = Path(os.getcwd()) / directory
        directory.mkdir(parents=True, exist_ok=True)

        now = datetime.now()
        if success is None:
            success = bool(result.get("success", True)) if isinstance(result, dict) else True

        record = {
            "time": now.isoformat(timespec="seconds"),
            "tool": tool_name,
            "success": bool(success),
            "elapsed_ms": int(elapsed_ms),
            "arguments": _summarize(arguments, settings.log_arg_chars),
            "result": _summarize(result, settings.log_result_chars),
        }
        path = directory / f"tool_calls-{now:%Y%m%d}.jsonl"
        line = json.dumps(record, ensure_ascii=False)
        with _WRITE_LOCK:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return path
    except Exception:
        # 日志属于可观测性附属能力，任何异常都不应影响工具调用
        return None
