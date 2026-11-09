# -*- coding: utf-8 -*-
"""
xAgent.core.context —— 上下文管理（Token 监控 / 归档 / 压缩 / 历史检索）。

设计原则：只用标准库与本地文件，不引入向量数据库或 Embedding。
  - 归档：压缩前把完整消息按 JSONL 写入项目 .agents/context/
  - 压缩：调用一次 LLM（不带工具）生成约 5000 tokens 的 Markdown 摘要
  - 检索：recallContext 工具对归档做本地全文关键词匹配
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import load_settings
from .llm import LLMMessage, ToolCall


# ============================================================
# 默认值（实际运行值从 config.ini 读取）
# ============================================================

DEFAULT_CONTEXT_WINDOW = 128_000

_ARCHIVE_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}(-[0-9]+)?$")

# 已知模型的上下文窗口（单位：token）；模型名带 128k/32k 等标记时也会自动推断
_KNOWN_WINDOWS: Dict[str, int] = {
    "ernie-4.5-turbo-128k": 128_000,
    "ernie-4.5-turbo-32k": 32_000,
    "ernie-4.5-turbo-8k": 8_000,
    "ernie-4.5-turbo-4k": 4_000,
}


# ============================================================
# Token 估算与窗口解析
# ============================================================

def estimate_tokens(text: str) -> int:
    """
    粗略估算文本 token 数。

    中文按约 1 字 ≈ 1 token，其它字符按约 3.5 字符 ≈ 1 token。
    仅用于拿不到 API usage 时的兜底估算。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return int(cjk + other / 3.5) + 1


def estimate_message_tokens(message: LLMMessage) -> int:
    """估算单条消息（含工具调用参数）的 token 数。"""
    total = estimate_tokens(message.content or "")
    if message.tool_calls:
        payload = [
            {"name": tc.name, "arguments": tc.arguments}
            for tc in message.tool_calls
        ]
        total += estimate_tokens(json.dumps(payload, ensure_ascii=False))
    total += 4  # role / 分隔符等固定开销
    return total


def estimate_messages_tokens(messages: Iterable[LLMMessage]) -> int:
    return sum(estimate_message_tokens(m) for m in messages)


def resolve_context_window(model: str) -> Tuple[int, bool]:
    """
    返回 (上下文窗口 token 数, 是否为默认值)。

    优先查已知模型表，其次从模型名中的 ``128k`` / ``32k`` 等标记推断；
    都取不到时按需求使用 128k 默认值。
    """
    name = (model or "").strip().lower()
    if name in _KNOWN_WINDOWS:
        return _KNOWN_WINDOWS[name], False
    match = re.search(r"(\d+)\s*k", name)
    if match:
        return int(match.group(1)) * 1_000, False
    return DEFAULT_CONTEXT_WINDOW, True


# ============================================================
# 上下文用量
# ============================================================

@dataclass
class ContextUsage:
    """一次上下文用量快照。"""

    tokens: int
    window: int
    percent: float
    is_default_window: bool
    over_threshold: bool

    def display(self) -> str:
        text = (
            f"{self.tokens / 1000:.1f}k / {self.window / 1000:.0f}k"
            f"（{self.percent:.1f}%）"
        )
        if self.is_default_window:
            text += " · 默认"
        return text


class ContextManager:
    """
    跟踪会话 token 用量并判断是否达到压缩阈值。

    优先使用模型 API 返回的 prompt_tokens；API 之后新增的消息（工具结果等）
    用本地估算补足。
    """

    def __init__(
        self,
        project_root: Optional[str] = None,
        model: str = "",
        window: Optional[int] = None,
    ) -> None:
        self.project_root = Path(project_root or os.getcwd()).resolve()
        self.model = model
        settings = load_settings(str(self.project_root))
        if window is not None:
            self.window = int(window)
            self.is_default_window = False
        elif settings.context_window > 0:
            self.window = settings.context_window
            self.is_default_window = False
        else:
            self.window, self.is_default_window = resolve_context_window(model)
        self._api_prompt_tokens: Optional[int] = None
        self._api_message_index = 0

    # ---------- 归档目录 ----------

    @property
    def archive_dir(self) -> Path:
        return self.project_root / load_settings(str(self.project_root)).archive_dir

    # ---------- API usage 记录 ----------

    def record_api_usage(self, prompt_tokens: int, message_index: int) -> None:
        """记录一次模型调用：prompt_tokens 覆盖 messages[:message_index]。"""
        if prompt_tokens and prompt_tokens > 0:
            self._api_prompt_tokens = int(prompt_tokens)
            self._api_message_index = max(0, int(message_index))

    def reset_usage(self) -> None:
        self._api_prompt_tokens = None
        self._api_message_index = 0

    def usage(self, messages: List[LLMMessage]) -> ContextUsage:
        """计算当前上下文用量。"""
        if self._api_prompt_tokens is None:
            tokens = estimate_messages_tokens(messages)
        else:
            tail = messages[self._api_message_index:]
            tokens = self._api_prompt_tokens + estimate_messages_tokens(tail)
        percent = (tokens / self.window) * 100 if self.window else 0.0
        warn_ratio = load_settings(str(self.project_root)).context_warn_ratio
        threshold = int(self.window * warn_ratio)
        return ContextUsage(
            tokens=tokens,
            window=self.window,
            percent=percent,
            is_default_window=self.is_default_window,
            over_threshold=tokens >= threshold,
        )


# ============================================================
# 原始上下文归档
# ============================================================

def message_to_dict(message: LLMMessage) -> Dict[str, Any]:
    """把一条消息转成可持久化的 dict（角色/内容/工具调用/工具返回）。"""
    return {
        "role": message.role,
        "content": message.content or "",
        "tool_calls": [
            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
            for tc in (message.tool_calls or [])
        ] or None,
        "tool_call_id": message.tool_call_id,
    }


def message_from_dict(record: Dict[str, Any]) -> LLMMessage:
    """从持久化 dict 还原 LLMMessage。"""
    calls = record.get("tool_calls") or []
    return LLMMessage(
        role=str(record.get("role") or "user"),
        content=str(record.get("content") or ""),
        tool_calls=[
            ToolCall(
                id=str(tc.get("id") or ""),
                name=str(tc.get("name") or ""),
                arguments=tc.get("arguments") or {},
            )
            for tc in calls
            if isinstance(tc, dict)
        ] or None,
        tool_call_id=record.get("tool_call_id"),
    )


def _message_to_record(index: int, message: LLMMessage) -> Dict[str, Any]:
    """保留角色、内容、工具调用参数、工具返回与顺序。"""
    record = message_to_dict(message)
    record["index"] = index
    return record


def _unique_archive_id(archive_dir: Path, now: datetime) -> str:
    base = now.strftime("%Y%m%d-%H%M%S")
    candidate = base
    suffix = 1
    while (archive_dir / f"{candidate}.jsonl").exists():
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


def archive_messages(
    messages: List[LLMMessage],
    project_root: Optional[str] = None,
    now: Optional[datetime] = None,
) -> str:
    """
    压缩前保存完整上下文，返回归档编号。

    采用临时文件 + 原子替换，任何写入失败都会抛异常，
    调用方据此中止压缩，保证“先保存成功再压缩”。
    """
    root = Path(project_root or os.getcwd()).resolve()
    archive_dir = root / load_settings(str(root)).archive_dir
    archive_dir.mkdir(parents=True, exist_ok=True)

    when = now or datetime.now()
    archive_id = _unique_archive_id(archive_dir, when)
    target = archive_dir / f"{archive_id}.jsonl"
    tmp = archive_dir / f".{archive_id}.jsonl.tmp"

    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            for index, message in enumerate(messages):
                line = json.dumps(
                    _message_to_record(index, message), ensure_ascii=False
                )
                fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    return archive_id


# ============================================================
# 历史检索（recallContext）
# ============================================================

def list_archives(project_root: Optional[str] = None) -> List[Path]:
    """按编号倒序列出所有归档文件（新归档在前）。"""
    root = Path(project_root or os.getcwd()).resolve()
    archive_dir = root / load_settings(str(root)).archive_dir
    if not archive_dir.is_dir():
        return []
    return sorted(archive_dir.glob("*.jsonl"), reverse=True)


def _record_haystack(record: Dict[str, Any]) -> str:
    parts = [str(record.get("content") or "")]
    if record.get("tool_calls"):
        parts.append(
            "工具调用: " + json.dumps(record["tool_calls"], ensure_ascii=False)
        )
    if record.get("tool_call_id"):
        parts.append(f"tool_call_id: {record['tool_call_id']}")
    return "\n".join(p for p in parts if p)


def _snippet(text: str, position: int, keyword_len: int) -> str:
    max_chars = load_settings().recall_snippet_chars
    half = max(40, (max_chars - keyword_len) // 2)
    start = max(0, position - half)
    end = min(len(text), position + keyword_len + half)
    piece = re.sub(r"\s+", " ", text[start:end]).strip()
    if start > 0:
        piece = "..." + piece
    if end < len(text):
        piece = piece + "..."
    return piece


def search_archive(
    archive_path: Path,
    text: str,
    remaining: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """在单个归档中检索关键词，返回最多 remaining 条片段。"""
    if remaining is None:
        remaining = load_settings().recall_max_matches
    matches: List[Dict[str, Any]] = []
    if remaining <= 0:
        return matches

    keyword = text.lower()
    with open(archive_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            haystack = _record_haystack(record)
            position = haystack.lower().find(keyword)
            if position < 0:
                continue
            matches.append(
                {
                    "archive": archive_path.stem,
                    "index": record.get("index"),
                    "role": record.get("role"),
                    "snippet": _snippet(haystack, position, len(text)),
                }
            )
            if len(matches) >= remaining:
                break
    return matches


def recall_context(
    text: str,
    archive_id: Optional[str] = None,
    project_root: Optional[str] = None,
) -> Dict[str, Any]:
    """
    recallContext 工具实现：在指定或全部归档中做本地全文检索。

    返回字段：success / message / matches / archives_searched / total_chars。
    """
    keyword = (text or "").strip()
    if not keyword:
        return {
            "success": False,
            "error": "参数 text 不能为空：请提供要查找的关键词或文本内容",
        }

    root = Path(project_root or os.getcwd()).resolve()
    settings = load_settings(str(root))
    archive_dir = root / settings.archive_dir

    if archive_id:
        if not _ARCHIVE_ID_RE.match(archive_id):
            return {
                "success": False,
                "error": f"归档编号格式无效：{archive_id}（示例：20261022-153000）",
            }
        path = archive_dir / f"{archive_id}.jsonl"
        if not path.is_file():
            return {
                "success": False,
                "error": f"未找到归档：{archive_id}",
            }
        archives = [path]
    else:
        archives = list_archives(str(root))

    if not archives:
        return {
            "success": False,
            "error": (
                f"在 {archive_dir} 下没有找到任何上下文归档；"
                "请先执行 /compact 生成归档"
            ),
        }

    matches: List[Dict[str, Any]] = []
    total_chars = 0
    for path in archives:
        remaining = settings.recall_max_matches - len(matches)
        if remaining <= 0:
            break
        for item in search_archive(path, keyword, remaining):
            size = len(item["snippet"])
            if total_chars + size > settings.recall_total_chars:
                break
            matches.append(item)
            total_chars += size
        if total_chars >= settings.recall_total_chars:
            break

    if not matches:
        return {
            "success": True,
            "message": f"未找到与「{keyword}」匹配的历史内容",
            "matches": [],
            "archives_searched": len(archives),
            "total_chars": 0,
        }

    return {
        "success": True,
        "message": (
            f"在 {len(archives)} 个归档中找到 {len(matches)} 条匹配片段"
            f"（关键词：{keyword}）"
        ),
        "matches": matches,
        "archives_searched": len(archives),
        "total_chars": total_chars,
    }


# ============================================================
# 压缩
# ============================================================

COMPACTION_PROMPT = """你是一名 AI Agent 的上下文管理助手。当前会话即将达到上下文窗口限制，需要将历史对话压缩为一份高质量的工作摘要，以便 Agent 在新的上下文中继续执行任务。

请认真阅读提供的历史上下文，提取对后续工作仍然有价值的信息，并生成一份约 {target} Tokens 的 Markdown 摘要。

摘要应重点保留以下内容：

1. **用户目标与要求**：用户希望完成什么任务？有哪些必须遵守的约束、偏好和验收条件？
2. **已经完成的工作**：哪些任务已经完成？实现了哪些功能？获得了哪些已经验证的结果？
3. **当前工作状态**：Agent 正在处理什么任务？当前进展到哪一步？是否存在尚未解决的问题？
4. **关键技术信息**：保留重要的文件路径、函数名称、接口定义、配置项、执行命令、错误信息和必要的代码片段。
5. **关键决策与原因**：记录用户和 Agent 已经确定的技术方案，以及重要的设计取舍。
6. **下一步任务**：明确接下来应该执行的操作，以及需要继续验证的事项。

请遵守以下要求：

- 优先保留对后续任务有直接影响的信息，删除寒暄、重复讨论、过时的尝试和无关内容。
- 对已经验证的事实与尚未验证的推测作出明确区分。
- 不要将计划完成的任务描述为已经完成。
- 重要的文件路径、函数名称、配置名称和命令应保留准确原文。
- 对失败的尝试，只保留有助于避免重复错误的信息。
- 如果信息存在冲突，应优先保留用户最新确认的要求，并简要说明仍然存在的冲突。
- 如果某些历史细节无法在摘要中完整保留，可简要说明它们已保存到原始上下文归档中，后续可以通过 recallContext 工具重新检索。
- 使用清晰的 Markdown 结构输出摘要，不要附加解释或无关内容。

最终目标：使一个从未阅读过原始对话的 Agent，仅凭这份摘要，就能够理解当前任务、继承关键决策，并继续开展工作。"""

def format_messages_for_summary(messages: List[LLMMessage]) -> str:
    """把历史消息序列化为压缩 Prompt 的输入文本（系统提示无需重复压缩）。"""
    tool_result_limit = load_settings().tool_result_limit
    blocks: List[str] = []
    for index, message in enumerate(messages):
        if message.role == "system":
            continue
        content = message.content or ""
        if message.role == "tool" and len(content) > tool_result_limit:
            content = content[:tool_result_limit] + "\n...(工具结果已截断)"
        lines = [f"## 消息 {index} · {message.role}"]
        if content:
            lines.append(content)
        if message.tool_calls:
            payload = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in message.tool_calls
            ]
            lines.append(
                "工具调用: " + json.dumps(payload, ensure_ascii=False)
            )
        if message.tool_call_id:
            lines.append(f"tool_call_id: {message.tool_call_id}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_compaction_prompt(messages: List[LLMMessage]) -> str:
    target = load_settings().summary_target_tokens
    prompt = COMPACTION_PROMPT.replace("{target}", f"{target:,}")
    history = format_messages_for_summary(messages)
    return f"{prompt}\n\n# 需要压缩的历史上下文\n\n{history}"


def archive_note(archive_id: str, when: datetime) -> str:
    return (
        f"此前上下文已于 {when:%Y-%m-%d %H:%M:%S} 完成压缩，"
        f"原始上下文保存在编号为 {archive_id} 的归档文件中。"
        "如需查找历史信息，请使用 recallContext 工具。"
    )


class CompactionError(RuntimeError):
    """压缩失败（归档可能已成功保存）。"""

    def __init__(self, message: str, archive_id: Optional[str] = None) -> None:
        super().__init__(message)
        self.archive_id = archive_id


@dataclass
class CompactionResult:
    messages: List[LLMMessage]
    archive_id: str
    summary: str
    note: str


def compact_messages(
    messages: List[LLMMessage],
    provider: Any,
    project_root: Optional[str] = None,
    now: Optional[datetime] = None,
) -> CompactionResult:
    """
    执行一次完整压缩：归档 → LLM 摘要 → 替换上下文。

    失败时抛 CompactionError，调用方应保留原 messages。
    """
    if not any(m.role != "system" and (m.content or m.tool_calls) for m in messages):
        raise CompactionError("当前没有可压缩的对话内容")

    # 1) 必须先成功保存原始上下文
    try:
        archive_id = archive_messages(messages, project_root, now)
    except Exception as exc:
        raise CompactionError(f"原始上下文归档失败，已取消压缩：{exc}") from exc

    # 2) 只允许一次不带工具的 LLM 调用生成摘要
    prompt = build_compaction_prompt(messages)
    try:
        response = provider.chat(
            [LLMMessage(role="user", content=prompt)], []
        )
    except Exception as exc:
        raise CompactionError(
            f"LLM 摘要生成失败：{exc}", archive_id
        ) from exc

    summary = (response.content or "").strip()
    if not summary:
        raise CompactionError("LLM 返回的摘要为空", archive_id)

    # 3) 保留 System Prompt 与工具定义，用摘要替换历史
    when = now or datetime.now()
    note = archive_note(archive_id, when)
    system_messages = [m for m in messages if m.role == "system"]
    summary_message = LLMMessage(
        role="user",
        content=f"# 此前上下文压缩摘要\n\n{summary}\n\n> {note}",
    )
    return CompactionResult(
        messages=system_messages + [summary_message],
        archive_id=archive_id,
        summary=summary,
        note=note,
    )
