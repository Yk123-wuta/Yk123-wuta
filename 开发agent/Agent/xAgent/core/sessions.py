# -*- coding: utf-8 -*-
"""
xAgent.core.sessions —— 会话历史持久化（/save /resume /new）。

存储位置：<项目根>/<config [session] dir>/<session_id>.jsonl

文件格式（每行一个 JSON 事件）：
    {"type": "meta", "id": "...", "title": "...", "created_at": "...",
     "updated_at": "...", "message_count": 3}
    {"type": "message", "role": "user", "content": "...", ...}

只用标准库，写入采用临时文件 + 原子替换，避免中断后留下半个文件。
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .config import load_settings
from .context import message_from_dict, message_to_dict
from .llm import LLMMessage

META_TYPE = "meta"
MESSAGE_TYPE = "message"
_SESSION_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")


@dataclass
class SessionMeta:
    """会话摘要（用于 /resume 列表）。"""

    session_id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0
    preview: str = ""


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _title_from(messages: List[LLMMessage]) -> str:
    for message in messages:
        if message.role == "user" and (message.content or "").strip():
            first_line = message.content.strip().splitlines()[0]
            return first_line[:40]
    return "未命名会话"


class SessionStore:
    """按项目目录读写会话 JSONL 文件。"""

    def __init__(self, project_root: str | Path | None = None) -> None:
        self.root = Path(project_root or os.getcwd()).resolve()
        self.dir = self.root / load_settings(str(self.root)).session_dir

    # ---------- 基础 ----------

    def new_id(self) -> str:
        return (
            datetime.now().strftime("%Y%m%d-%H%M%S")
            + "-"
            + uuid.uuid4().hex[:6]
        )

    def path_for(self, session_id: str) -> Path:
        if not _SESSION_ID_RE.match(session_id or ""):
            raise ValueError(f"会话编号格式无效：{session_id}")
        return self.dir / f"{session_id}.jsonl"

    # ---------- 保存 ----------

    def save(
        self,
        messages: List[LLMMessage],
        session_id: Optional[str] = None,
        created_at: Optional[str] = None,
        title: Optional[str] = None,
    ) -> SessionMeta:
        """保存整个会话（覆盖写），返回会话摘要。"""
        session_id = session_id or self.new_id()
        path = self.path_for(session_id)
        self.dir.mkdir(parents=True, exist_ok=True)

        now = _now_text()
        created_at = created_at or now
        title = title or _title_from(messages)
        preview = ""
        for message in reversed(messages):
            if message.role == "assistant" and (message.content or "").strip():
                preview = message.content.strip().splitlines()[0][:60]
                break

        meta = SessionMeta(
            session_id=session_id,
            title=title,
            created_at=created_at,
            updated_at=now,
            message_count=len(messages),
            preview=preview,
        )

        tmp = self.dir / f".{session_id}.jsonl.tmp"
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                meta_record = {"type": META_TYPE, **meta.__dict__}
                fh.write(json.dumps(meta_record, ensure_ascii=False) + "\n")
                for message in messages:
                    record = {"type": MESSAGE_TYPE, **message_to_dict(message)}
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return meta

    # ---------- 列表 / 读取 ----------

    def list(self) -> List[SessionMeta]:
        """列出所有会话，按更新时间倒序。"""
        if not self.dir.is_dir():
            return []
        metas: List[SessionMeta] = []
        for path in self.dir.glob("*.jsonl"):
            meta = self._read_meta(path)
            if meta is not None:
                metas.append(meta)
        metas.sort(key=lambda m: m.updated_at, reverse=True)
        return metas

    def _read_meta(self, path: Path) -> Optional[SessionMeta]:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                first = json.loads(fh.readline())
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(first, dict) or first.get("type") != META_TYPE:
            return None
        return SessionMeta(
            session_id=str(first.get("session_id") or path.stem),
            title=str(first.get("title") or "未命名会话"),
            created_at=str(first.get("created_at") or ""),
            updated_at=str(first.get("updated_at") or ""),
            message_count=int(first.get("message_count") or 0),
            preview=str(first.get("preview") or ""),
        )

    def load(self, session_id: str) -> tuple[SessionMeta, List[LLMMessage]]:
        """读取会话，返回 (摘要, 消息列表)。"""
        path = self.path_for(session_id)
        if not path.is_file():
            raise FileNotFoundError(f"会话不存在：{session_id}")

        meta: Optional[SessionMeta] = None
        messages: List[LLMMessage] = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                if record.get("type") == META_TYPE:
                    meta = SessionMeta(
                        session_id=str(record.get("session_id") or session_id),
                        title=str(record.get("title") or "未命名会话"),
                        created_at=str(record.get("created_at") or ""),
                        updated_at=str(record.get("updated_at") or ""),
                        message_count=int(record.get("message_count") or 0),
                        preview=str(record.get("preview") or ""),
                    )
                elif record.get("type") == MESSAGE_TYPE:
                    messages.append(message_from_dict(record))

        if meta is None:
            meta = SessionMeta(
                session_id=session_id,
                title=_title_from(messages),
                created_at="",
                updated_at="",
                message_count=len(messages),
            )
        return meta, messages
