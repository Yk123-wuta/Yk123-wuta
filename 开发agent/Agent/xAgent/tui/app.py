# -*- coding: utf-8 -*-
"""
xAgent.tui.app —— 对话式 LLM Agent（老师样式对齐版）。

老师界面结构：
  ┌─────────────────────────────────────────────────────────────────────────┐
  │ xAgent · 12 个工具 · deepseek-chat · /Users/...                        │ ← 自定义 Header
  │   Enter 发送 /help 命令 Esc 中断 Ctrl+B 侧栏 Ctrl+N 新会话             │
  ├──────────────────────────────────────────┬────────────────────────────┤
  │                                          │ 会话  deepseek-chat          │
  │ 对话流 VerticalScroll                     │ 模型  mock                   │
  │  [dim]你[/dim]                           │ 接口  https://api.deepseek   │
  │    xxx                                    │ 目录  /Users/...             │
  │  ┌─ ▸ isLeapYear(year=2026) · 2 ms ──┐  │ 会话  e9e7b179               │
  │  │ ▼ 参数 { "year": 2026 }            │  │ 轮次  1                      │
  │  │   结果 2026 年不是闰年...          │  │ 上限  8 次模型调用/轮        │
  │  └───────────────────────────────────┘  │ 状态  就绪                   │
  │  [dim]xAgent[/dim]                       │ ──────────────────────────── │
  │    不是...                                │ 工具                         │
  │                                          │ · getDate  获取当前...       │
  ├──────────────────────────────────────────┴────────────────────────────┤
  │ 输入问题后 Enter 发送 · /help Esc 中断生成                             │
  ├─────────────────────────────────────────────────────────────────────────┤
  │ 就绪 · 1 轮 · mock · 工具 2 次 · 2.9s · Esc 中断 · Ctrl+H · Ctrl+Q   │ ← StatusBar
  ├─────────────────────────────────────────────────────────────────────────┤
  │ q 退出 · ~ 清屏 · n 新会话 · b 侧栏 · f1 帮助 · p palette              │ ← Footer
  └─────────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import json
import os
import time
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical, Horizontal, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Static, Input, Button, Label, ListView, ListItem,
)

from xAgent.core import ToolService, ToolResult
from xAgent.core.registry import (
    ensure_mcp_tools, get_mcp_warnings, mcp_status, mcp_server_states,
)
from xAgent.core.context import (
    ContextManager, CompactionError, compact_messages, estimate_tokens,
)
from xAgent.core.skills import (
    build_skill_prompt, discover_skills, get_skill_registry,
)
from xAgent.core.config import ensure_config_file, load_settings
from xAgent.core.llm import (
    LLMMessage, ToolCall, MockProvider, OpenAICompatibleProvider,
)
from xAgent.core.sessions import SessionMeta, SessionStore


# ============================================================
# 斜杠命令 / AGENTS.md 标记
# ============================================================

# (命令, 说明) —— 同时用于 / 补全面板与 /help
SLASH_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/help", "显示帮助"),
    ("/new", "新建会话（自动保存当前会话）"),
    ("/resume", "恢复历史会话（↑/↓ + Enter）"),
    ("/save", "保存当前会话"),
    ("/compact", "压缩当前上下文"),
    ("/clear", "清空对话显示"),
    ("/tools", "列出可用工具"),
    ("/cwd", "显示当前目录"),
    ("/model", "显示当前模型与接口"),
    ("/mcp", "显示 MCP 服务器状态"),
    ("/quit", "退出程序"),
)

AGENTS_MARKER = "[项目规范] AGENTS.md"


# ============================================================
# 自定义 Header（老师样式：两行 Static）
# ============================================================

class CustomHeader(Vertical):
    """老师样式的顶部 Header —— 两行 Static。"""

    DEFAULT_CSS = """
    CustomHeader {
        width: 100%;
        height: auto;
        padding: 0 1;
        margin: 0;
        border: none;
        background: $surface;
    }
    CustomHeader .line1 {
        height: 1;
        color: $accent;
        text-style: bold;
    }
    CustomHeader .line2 {
        height: 1;
        color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        tool_count = len(ToolService.list_tools())
        cwd = os.getcwd()
        # 老师显示完整路径，太长就截断末尾
        if len(cwd) > 45:
            cwd = "..." + cwd[-42:]
        self._model_label = "mock"
        self._line1 = Static(
            f"xAgent · {tool_count} 个工具 · mock · {cwd}",
            classes="line1",
        )
        self._line2 = Static(
            "  Enter 发送  输入 / 查看命令  Esc 中断  Ctrl+B 侧栏  Ctrl+N 新会话",
            classes="line2",
        )

    def compose(self) -> ComposeResult:
        yield self._line1
        yield self._line2

    def set_model(self, model: str) -> None:
        self._model_label = model
        self._refresh_line1()

    def set_tool_count(self) -> None:
        self._refresh_line1()

    def _refresh_line1(self) -> None:
        tool_count = len(ToolService.list_tools())
        cwd = os.getcwd()
        if len(cwd) > 45:
            cwd = "..." + cwd[-42:]
        model = getattr(self, "_model_label", "mock")
        self._line1.update(f"xAgent · {tool_count} 个工具 · {model} · {cwd}")


# ============================================================
# 紧凑可折叠工具卡片（老师样式）
# ============================================================

class ToolCallCard(Vertical):
    """
    工具调用卡片。老师样式：紧凑 border，▸/▼ 折叠。
      ▸ isLeapYear(year=2026) · 2 ms     ← 折叠态
      ▼ isLeapYear(year=2026) · 2 ms     ← 展开态
          参数  { "year": 2026 }
          结果  2026 年不是闰年...
    """

    DEFAULT_CSS = """
    ToolCallCard {
        border: $primary $surface-darken-1;
        width: 100%;
        margin: 0 0 0 2;
        padding: 0;
    }
    ToolCallCard .summary {
        width: 100%;
        height: 1;
        color: $accent;
        background: transparent;
        border: none;
        padding: 0 1;
        text-align: left;
    }
    ToolCallCard .body {
        width: 100%;
        margin-top: 0;
        padding: 0 2;
    }
    ToolCallCard .body.hidden {
        height: 0;
        min-height: 0;
        overflow: hidden;
    }
    """

    def __init__(
        self,
        call_id: str,
        tool_name: str,
        args: Dict[str, Any],
        result: ToolResult,
        elapsed_ms: int,
    ) -> None:
        super().__init__()
        self._call_id = call_id
        self._tool_name = tool_name
        self._args = args
        self._result = result
        self._elapsed_ms = elapsed_ms
        self._expanded = False

        args_str = ", ".join(
            f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items()
        ) or "(无)"
        self._summary_collapsed = f"  ▸ {tool_name}({args_str}) · {elapsed_ms} ms"
        self._summary_expanded = self._summary_collapsed.replace("▸", "▼", 1)
        self._body_text = self._build_body(result)

    def _build_body(self, result: ToolResult) -> str:
        """构造展开后的参数 + 结果文本。"""
        args_json = json.dumps(self._args, ensure_ascii=False)
        flat = result.to_dict()
        # 结果内容做截断，老师截图里结果是单行/双行
        result_content: str
        if result.success:
            if "content" in flat and isinstance(flat["content"], str):
                result_content = flat["content"]
                if len(result_content) > 120:
                    result_content = result_content[:117] + "..."
            else:
                result_content = json.dumps(flat, ensure_ascii=False)
                if len(result_content) > 120:
                    result_content = result_content[:117] + "..."
        else:
            result_content = flat.get("error", str(flat))
            if len(result_content) > 120:
                result_content = result_content[:117] + "..."

        return f"[dim]参数[/dim]  {args_json}\n[dim]结果[/dim]  {result_content}"

    def compose(self) -> ComposeResult:
        self._summary_btn = Button(self._summary_collapsed, classes="summary")
        self._body = Static(self._body_text, classes="body hidden")
        yield self._summary_btn
        yield self._body

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button is self._summary_btn:
            self._toggle()

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        if self._expanded:
            self._summary_btn.label = self._summary_expanded
            self._body.remove_class("hidden")
        else:
            self._summary_btn.label = self._summary_collapsed
            self._body.add_class("hidden")


# ============================================================
# 对话流容器
# ============================================================

class ConversationFlow(VerticalScroll):
    """左侧主对话区。"""

    DEFAULT_CSS = """
    ConversationFlow {
        width: 1fr;
        border: solid $surface-darken-2;
        padding: 0 1;
    }
    ConversationFlow .role-badge {
        color: $text-muted;
        height: 1;
        margin-top: 1;
    }
    ConversationFlow .msg-body {
        width: 100%;
        color: $text;
    }
    """

    def add_user_message(self, text: str) -> None:
        badge = Static("  你", classes="role-badge")
        body = Static(text, classes="msg-body")
        self.mount_all([badge, body])
        self.scroll_end(animate=False)

    def add_tool_card(self, card: ToolCallCard) -> None:
        self.mount(card)
        self.scroll_end(animate=False)

    def add_assistant_message(self, text: str) -> None:
        badge = Static("  xAgent", classes="role-badge")
        body = Static(text, classes="msg-body")
        self.mount_all([badge, body])
        self.scroll_end(animate=False)

    def clear_all(self) -> None:
        self.remove_children()


# ============================================================
# 右侧侧栏（老师样式：kv + 工具列表带灰色描述）
# ============================================================

# 侧栏 width 40 - 边框 2 - 内边距 2 = 36 列；文本按实际可用宽度自动换行
_SIDEBAR_CONTENT = 36


class Sidebar(VerticalScroll):
    """右侧会话信息 + 工具列表（内容超高时可滚动）。"""

    DEFAULT_CSS = """
    Sidebar {
        width: 40;
        border: solid $primary;
        padding: 0 1;
        overflow-x: auto;
        overflow-y: auto;
        scrollbar-size-horizontal: 1;
        scrollbar-size-vertical: 2;
    }
    Sidebar .kv {
        color: $text-muted;
        width: 1fr;
        height: auto;
        text-wrap: wrap;
    }
    Sidebar .divider {
        height: 1;
        color: $text-muted;
    }
    Sidebar .section-label {
        color: $accent;
        text-style: bold;
        height: 1;
    }
    Sidebar .tool-row {
        color: $text;
    }
    Sidebar .tool-row .tname {
        color: $text;
    }
    Sidebar .tool-row .tdesc {
        color: $text-muted;
    }
    Sidebar #side-mini {
        color: $accent;
        height: 1;
    }
    Sidebar .tdesc-line {
        color: $text-muted;
        width: 1fr;
        height: auto;
        text-wrap: wrap;
    }
    Sidebar #tool-list {
        height: auto;
        width: 1fr;
    }
    Sidebar #side-mcp {
        color: $text-muted;
        width: 1fr;
        height: auto;
        text-wrap: wrap;
    }
    Sidebar.-collapsed {
        width: 5;
    }
    """

    _collapsed: bool = False

    # (控件 id, 前缀, 初始值)
    _INFO_ROWS = (
        ("side-session", "会话  ", "mock"),
        ("side-model", "模型  ", "mock"),
        ("side-context", "上下文  ", "-"),
        ("side-api", "接口  ", "-"),
        ("side-cwd", "目录  ", ""),
        ("side-skills", "技能  ", "-"),
        ("side-id", "会话  ", "-"),
        ("side-round", "轮次  ", "1"),
        ("side-limit", "上限  ", "8 次模型调用/轮"),
        ("side-status", "状态  ", "就绪"),
    )

    def compose(self) -> ComposeResult:
        yield Label("▾", id="side-mini")

        # 内容宽度依赖运行时容器宽度（滚动条会占列），统一在 _refresh_width 里渲染
        self._loaded_skills: set[str] = set()
        self._skill_total = len(get_skill_registry().skills)
        self._info: Dict[str, List[str]] = {}
        for wid, prefix, value in self._INFO_ROWS:
            if wid == "side-cwd":
                value = os.getcwd()
            elif wid == "side-skills":
                value = self._skill_display()
            self._info[wid] = [prefix, value]
            yield Label("", classes="kv", id=wid)

        yield Label("工具", classes="section-label")

        self._tool_rows: List[tuple] = []
        with Vertical(id="tool-list"):
            for index, t in enumerate(ToolService.list_tools(), start=1):
                name_label = Label("", classes="kv")
                desc_label = (
                    Label("", classes="kv tdesc-line") if t.description else None
                )
                self._tool_rows.append(
                    (name_label, desc_label, index, t.name, t.description or "")
                )
                yield name_label
                if desc_label is not None:
                    yield desc_label

        yield Label("MCP", classes="section-label")
        yield Static("加载中…", id="side-mcp")

    def on_mount(self) -> None:
        self.call_after_refresh(self._refresh_width)

    def on_resize(self, event) -> None:
        self.call_after_refresh(self._refresh_width)

    def _avail_width(self) -> int:
        """可用内容列数（扣除占位的垂直滚动条，避免文字被滚动条压住）。"""
        width = self.size.width - self.scrollbar_size_vertical
        return width if width > 0 else _SIDEBAR_CONTENT

    def _refresh_width(self) -> None:
        """按真实可用宽度重排所有文本，避免被容器硬切。"""
        if not self.is_mounted or self._collapsed:
            return
        avail = self._avail_width()
        if avail <= 0:
            return

        # 文本不再截断，超出宽度由底部横向滚动条查看
        for wid, (prefix, value) in self._info.items():
            raw = value
            if wid == "side-api" and raw not in ("", "-"):
                raw = raw.replace("https://", "").replace("http://", "").rstrip("/")
            self.query_one(f"#{wid}", Label).update(f"{prefix}{raw}")

        # 工具名独占一行（带序号），描述另起一行；两者都按宽度自动换行，不做截断
        for name_label, desc_label, index, name, desc in self._tool_rows:
            name_label.update(f"{index:>2d}. {name}")
            if desc_label is not None:
                desc_label.display = True
                desc_label.update(f"    {desc}" if desc else "")

    def _set_value(self, wid: str, value: str) -> None:
        self._info[wid] = [self._info[wid][0], value]
        self._refresh_width()

    def set_status(self, text: str) -> None:
        self._set_value("side-status", text)

    def set_round(self, n: int) -> None:
        self._set_value("side-round", str(n))

    def set_model(self, text: str) -> None:
        self._set_value("side-model", text)

    def set_api(self, text: str) -> None:
        self._set_value("side-api", text)

    def set_context(self, text: str) -> None:
        self._set_value("side-context", text)

    def set_model_limit(self, count: int) -> None:
        self._set_value("side-limit", f"{count} 次模型调用/轮")

    # ---------- 工具 / MCP 区域 ----------

    def refresh_tools(self) -> None:
        """重新渲染工具列表（MCP 工具异步注册后调用）。"""
        if not self.is_mounted:
            return
        container = self.query_one("#tool-list", Vertical)
        container.remove_children()
        self._tool_rows = []
        for index, t in enumerate(ToolService.list_tools(), start=1):
            name_label = Label("", classes="kv")
            desc_label = (
                Label("", classes="kv tdesc-line") if t.description else None
            )
            self._tool_rows.append(
                (name_label, desc_label, index, t.name, t.description or "")
            )
            container.mount(name_label)
            if desc_label is not None:
                container.mount(desc_label)
        self.call_after_refresh(self._refresh_width)

    def set_mcp_status(
        self,
        states: List[Dict[str, Any]],
        warnings: Optional[List[str]] = None,
        loading: bool = False,
    ) -> None:
        """更新 MCP 区域：已连接服务器 + 可用工具 + 重连进度。"""
        if not self.is_mounted:
            return
        if loading:
            text = "加载中…"
        elif not states:
            text = "未连接"
            if warnings:
                text += f"（{warnings[0]}）"
        else:
            lines: List[str] = []
            for st in states:
                name = str(st.get("name", ""))
                state = str(st.get("state", ""))
                if state == "connected":
                    tools = st.get("tools") or []
                    lines.append(f"▸ {name}（{len(tools)}）")
                    for tool in tools:
                        lines.append(f"    · {tool}")
                    continue
                if state == "connecting":
                    lines.append(f"… {name} 连接中")
                else:
                    attempt = int(st.get("attempt") or 0)
                    retry_in = float(st.get("retry_in") or 0.0)
                    wait = f"{retry_in:.0f}s 后重试" if retry_in > 0 else "正在重试"
                    lines.append(f"⟳ {name} 重连中（第 {attempt} 次，{wait}）")
                error = str(st.get("last_error") or "").strip()
                if error:
                    lines.append(f"    ! {error[:80]}")
            text = "\n".join(lines)
        self.query_one("#side-mcp", Static).update(text)

    # ---------- Skill 加载角标 ----------

    def _skill_display(self) -> str:
        """技能角标：已加载数/总数，以及已加载的技能名。"""
        if self._skill_total <= 0:
            return "未发现"
        if not self._loaded_skills:
            return f"0/{self._skill_total}"
        names = "、".join(sorted(self._loaded_skills))
        return f"{len(self._loaded_skills)}/{self._skill_total} {names}"

    def set_skill_total(self, total: int) -> None:
        self._skill_total = total
        self._set_value("side-skills", self._skill_display())

    def mark_skill_loaded(self, name: str) -> None:
        """记录一次成功的 loadSkill，并在侧栏显示加载角标。"""
        if not name or name in self._loaded_skills:
            return
        self._loaded_skills.add(name)
        self._set_value("side-skills", self._skill_display())

    def reset_skill_loaded(self) -> None:
        self._loaded_skills.clear()
        self._set_value("side-skills", self._skill_display())

    # ---------- 最小化 / 展开 ----------

    def toggle_collapsed(self) -> None:
        """在完整侧栏与最小化竖条之间切换。"""
        self._collapsed = not self._collapsed
        self.set_class(self._collapsed, "-collapsed")
        for child in self.children:
            if child.id != "side-mini":
                child.display = not self._collapsed
        self.query_one("#side-mini", Label).update("▸" if self._collapsed else "▾")
        if not self._collapsed:
            # 等布局按新宽度重算后再重排文本
            self.call_after_refresh(self._refresh_width)

    def on_click(self) -> None:
        """最小化状态下点击竖条任意位置恢复侧栏。"""
        if self._collapsed:
            self.toggle_collapsed()


# ============================================================
# 输入框：斜杠补全面板可见时接管方向键 / Enter / Esc / Tab
# ============================================================

class PromptInput(Input):
    """命令补全激活时，↑/↓/Tab/Enter/Esc 交给 App 处理。"""

    async def _on_key(self, event) -> None:
        app = self.app
        if getattr(app, "completion_visible", False) and event.key in (
            "up", "down", "tab", "enter", "escape",
        ):
            event.stop()
            event.prevent_default()
            if event.key == "up":
                app.completion_move(-1)
            elif event.key == "down":
                app.completion_move(1)
            elif event.key == "tab":
                app.completion_accept(execute=False)
            elif event.key == "enter":
                app.completion_accept(execute=True)
            else:
                app.hide_completion()
            return
        await super()._on_key(event)


# ============================================================
# 启动提示对话框（首次生成 config.ini 时弹出）
# ============================================================

class NotifyDialog(ModalScreen[None]):
    """居中的提示对话框，Esc / Enter / 按钮均可关闭。"""

    BINDINGS = [
        Binding("escape", "close_dialog", show=False),
        Binding("enter", "close_dialog", show=False),
    ]

    DEFAULT_CSS = """
    NotifyDialog {
        align: center middle;
    }
    NotifyDialog #notify-box {
        width: 76;
        height: auto;
        padding: 1 2;
        border: thick $warning;
        background: $surface;
    }
    NotifyDialog #notify-title {
        width: 1fr;
        height: auto;
        color: $warning;
        text-style: bold;
        padding-bottom: 1;
    }
    NotifyDialog #notify-message {
        width: 1fr;
        height: auto;
        color: $text;
        text-wrap: wrap;
    }
    NotifyDialog #notify-ok {
        margin-top: 1;
        width: 16;
    }
    """

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self._title = title
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="notify-box"):
            yield Static(self._title, id="notify-title")
            yield Static(self._message, id="notify-message")
            yield Button("知道了", id="notify-ok")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "notify-ok":
            self.dismiss(None)

    def action_close_dialog(self) -> None:
        self.dismiss(None)


# ============================================================
# /resume 历史会话列表（↑/↓ 选择，Enter 恢复，Esc 取消）
# ============================================================

class SessionItem(ListItem):
    """列表项携带会话编号。"""

    def __init__(self, meta: SessionMeta) -> None:
        stamp = meta.updated_at or meta.created_at or "-"
        preview = meta.title or "未命名会话"
        super().__init__(Label(f"{stamp}  {preview}  ({meta.message_count} 条)"))
        self.session_id = meta.session_id


class SessionListScreen(ModalScreen[Optional[str]]):
    """历史会话列表面板。"""

    BINDINGS = [Binding("escape", "cancel_dialog", show=False)]

    DEFAULT_CSS = """
    SessionListScreen {
        align: center middle;
    }
    SessionListScreen #session-box {
        width: 76;
        height: 22;
        padding: 1 1;
        border: thick $primary;
        background: $surface;
    }
    SessionListScreen #session-title {
        width: 1fr;
        height: 1;
        color: $accent;
        text-style: bold;
        padding: 0 1;
    }
    SessionListScreen #session-list {
        height: 1fr;
        width: 1fr;
        margin-top: 1;
    }
    """

    def __init__(self, sessions: List[SessionMeta]) -> None:
        super().__init__()
        self._sessions = sessions

    def compose(self) -> ComposeResult:
        with Vertical(id="session-box"):
            yield Static(
                "历史会话 · ↑/↓ 选择 · Enter 恢复 · Esc 取消",
                id="session-title",
            )
            yield ListView(
                *[SessionItem(meta) for meta in self._sessions],
                id="session-list",
            )

    def on_mount(self) -> None:
        view = self.query_one("#session-list", ListView)
        view.focus()
        if len(view.children):
            view.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        session_id = getattr(event.item, "session_id", None)
        self.dismiss(session_id)

    def action_cancel_dialog(self) -> None:
        self.dismiss(None)


# ============================================================
# 底部 StatusBar（老师样式：就绪 · N轮 · model · 工具 N次 · Ns · 快捷键）
# ============================================================

class StatusBar(Static):
    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        width: 100%;
        color: $text-muted;
        background: $surface;
        padding: 0 1;
    }
    """

    def update_status(
        self,
        ready: bool = True,
        round_no: int = 1,
        model: str = "mock",
        tool_calls: int = 0,
        elapsed_ms: int = 0,
    ) -> None:
        parts = []
        parts.append("就绪" if ready else "生成中")
        parts.append(f"{round_no} 轮")
        parts.append(model)
        parts.append(f"工具 {tool_calls} 次")
        if elapsed_ms:
            # 老师用 Ns 格式
            parts.append(f"{elapsed_ms / 1000:.1f}s")
        parts.append("Esc 中断")
        parts.append("Ctrl+H 帮助")
        parts.append("Ctrl+Q 退出")
        self.update(" · ".join(parts))


# ============================================================
# 自定义 Footer（老师样式：快捷键提示）
# ============================================================

class CustomFooter(Static):
    """底部快捷键栏。"""

    DEFAULT_CSS = """
    CustomFooter {
        height: 1;
        width: 100%;
        color: $text-muted;
        background: $surface-darken-1;
        padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        # 老师截图的快捷键
        yield Static(
            "q 退出 · ~ 清屏 · n 新会话 · ctrl+b 侧栏 · f1 帮助 · p palette",
            markup=False,
        )


# ============================================================
# 主 App
# ============================================================

def _param_schema(pdesc: str) -> Dict[str, Any]:
    """按参数描述前缀（str / int / bool / list）生成对应的 JSON Schema。"""
    low = pdesc.lower()
    if low.startswith("list"):
        return {"type": "array", "items": {"type": "string"}, "description": pdesc}
    if low.startswith("int"):
        return {"type": "integer", "description": pdesc}
    if low.startswith("bool"):
        return {"type": "boolean", "description": pdesc}
    return {"type": "string", "description": pdesc}


class XAgentApp(App):
    """对话式 LLM Agent（老师样式对齐版）。"""

    # Textual App 元信息（不用默认 Header/Footer）
    TITLE = "xAgent"

    # Ctrl+B / Ctrl+N：输入框有焦点时也能触发
    BINDINGS = [
        Binding("ctrl+b", "toggle_sidebar", "侧栏"),
        Binding("ctrl+n", "new_session", "新会话"),
    ]

    CSS = """
    Screen { layout: vertical; }
    #main-row { layout: horizontal; height: 1fr; }
    #completion-panel {
        height: auto;
        padding: 0 1;
        display: none;
    }
    #completion-panel.visible { display: block; }
    #completion-list {
        width: 100%;
        height: auto;
        padding: 0 1;
        color: $text-muted;
        background: $surface-darken-1;
    }
    #prompt-row { layout: horizontal; height: auto; padding: 0 1; }
    #prompt Input { width: 100%; }
    """

    round_no = reactive(1)
    tool_calls_total = reactive(0)

    _messages: List[LLMMessage] = []
    _provider: Any
    _model_label: str = "mock"
    _skill_prompt: str = ""
    _context_mgr: Optional[ContextManager] = None
    _compacting: bool = False
    _context_blocked: bool = False
    _context_warned: bool = False

    # 斜杠补全
    _completion_matches: List[tuple[str, str]] = []
    _completion_index: int = 0
    _completion_visible: bool = False

    # 会话
    _session_id: Optional[str] = None
    _session_created_at: str = ""
    _agents_injected: bool = False
    _mcp_warnings_shown: set[str] = set()
    _mcp_loaded: bool = False
    _mcp_states_seen: Dict[str, str] = {}
    _mcp_tool_signature: tuple = ()

    def __init__(
        self,
        config_created: Optional[bool] = None,
        config_path: Optional[str] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        if config_created is None:
            path, created = ensure_config_file()
            config_created, config_path = created, str(path)
        self._config_created = bool(config_created)
        self._config_path = config_path or "config.ini"
        self._mcp_warnings_shown = set()
        self._mcp_loaded = False
        self._mcp_states_seen = {}
        self._mcp_tool_signature = ()

    def compose(self) -> ComposeResult:
        # 自定义 Header（老师样式）
        yield CustomHeader()

        # 主区
        with Container(id="main-row"):
            yield ConversationFlow(id="conversation")
            yield Sidebar(id="sidebar")

        # 斜杠命令补全面板（默认隐藏）
        with Container(id="completion-panel"):
            yield Static("", id="completion-list", markup=False)

        # 底部输入框
        with Container(id="prompt-row"):
            yield PromptInput(
                placeholder="输入问题后 Enter 发送 · 输入 / 查看命令",
                id="prompt",
                restrict=None,
            )

        # 状态栏
        yield StatusBar("就绪 · 1 轮 · mock · 工具 0 次")

        # 自定义 Footer
        yield CustomFooter()

    def on_mount(self) -> None:
        self._conversation = self.query_one("#conversation", ConversationFlow)
        self._sidebar = self.query_one("#sidebar", Sidebar)
        self._prompt = self.query_one("#prompt", PromptInput)
        self._status = self.query_one(StatusBar)
        self._header = self.query_one(CustomHeader)
        self._completion_panel = self.query_one("#completion-panel", Container)
        self._completion_list = self.query_one("#completion-list", Static)

        # 配置 / 会话存储
        self._settings = load_settings()
        self._store = SessionStore()
        self._session_id = self._store.new_id()
        self._session_created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._sidebar.styles.width = self._settings.sidebar_width
        self._sidebar.set_model_limit(self._settings.max_model_calls)

        # 按 config 初始化 LLM
        self._init_provider()

        # 上下文管理：跟踪 Token 用量、归档与压缩
        self._messages = []
        self._context_mgr = ContextManager(
            project_root=os.getcwd(), model=self._model_label
        )
        self._update_context_display()

        # Skill 发现：以启动时的工作目录为项目根目录，只加载 name / description
        registry = discover_skills()
        self._skill_prompt = build_skill_prompt(registry)
        self._sidebar.set_skill_total(len(registry.skills))

        # 会话首次加载：注入 Skill 清单 + AGENTS.md（一次注入，后续复用）
        self._ensure_skill_prompt()
        self._inject_agents_md()

        # 欢迎消息
        self._conversation.add_assistant_message(
            "你好，我是 xAgent。我可以帮你操作文件系统。\n\n"
            "试试：'列出当前目录'、'读取 README.md 的内容'、"
            "'创建文件 demo.txt'，或输入 /help 查看命令。"
        )
        if self._config_created:
            self._conversation.add_assistant_message(
                f"已生成新的配置文件：{self._config_path}\n"
                "当前使用 Mock 模型，请修改配置后重新启动 xAgent。"
            )
            self.call_after_refresh(self._show_config_notice)

        # MCP 异步加载：先进入界面，加载完成后刷新边栏
        self._sidebar.set_mcp_status([], [], loading=True)
        self.run_worker(
            self._load_mcp_worker, thread=True, name="mcp-loader"
        )
        # 周期轮询 MCP 状态：后台重连进度与恢复情况实时反映到界面
        self.set_interval(1.0, self._poll_mcp_status)

        self._prompt.focus()

    # ---------- 启动提示 ----------

    def _show_config_notice(self) -> None:
        self.push_screen(
            NotifyDialog(
                "已生成 config.ini",
                f"首次运行已在当前目录创建配置文件：\n{self._config_path}\n\n"
                "当前使用 Mock 信息，修改文件（provider / api_key / model）"
                "后重新启动 xAgent 才能正常工作。",
            )
        )

    # ---------- MCP 异步加载 ----------

    def _load_mcp_worker(self) -> None:
        try:
            ensure_mcp_tools()
        except Exception:
            pass
        try:
            self.call_from_thread(self._refresh_mcp_ui)
        except Exception:
            pass

    def _refresh_mcp_ui(self) -> None:
        states = mcp_server_states()
        warnings = get_mcp_warnings()
        self._sidebar.set_mcp_status(states, warnings)
        self._sidebar.refresh_tools()
        self._header.set_tool_count()
        self._mcp_loaded = True
        self._mcp_states_seen = {
            str(st.get("name", "")): str(st.get("state", "")) for st in states
        }
        self._mcp_tool_signature = self._tool_signature(states)
        for warning in warnings:
            if warning not in self._mcp_warnings_shown:
                self._mcp_warnings_shown.add(warning)
                self._conversation.add_assistant_message(
                    f"MCP 连接异常：{warning}"
                )

    @staticmethod
    def _tool_signature(states: List[Dict[str, Any]]) -> tuple:
        """已连接 Server 与工具数量的签名，用于判断是否需要重绘工具列表。"""
        return tuple(sorted(
            (str(st.get("name", "")), len(st.get("tools") or []))
            for st in states
            if str(st.get("state")) == "connected"
        ))

    def _poll_mcp_status(self) -> None:
        """主线程周期轮询：展示重连进度，并在状态变化时提示用户。"""
        if not self._mcp_loaded:
            return
        states = mcp_server_states()
        prev = self._mcp_states_seen
        current = {
            str(st.get("name", "")): str(st.get("state", "")) for st in states
        }

        for st in states:
            name = str(st.get("name", ""))
            state = str(st.get("state", ""))
            old = prev.get(name)
            if old == state:
                continue
            if state == "reconnecting" and old in (None, "connected"):
                attempt = int(st.get("attempt") or 0)
                detail = str(st.get("last_error") or "").strip()
                suffix = f"（{detail}）" if detail else ""
                self._conversation.add_assistant_message(
                    f"MCP Server '{name}' 连接中断{suffix}，"
                    f"正在后台自动重连（第 {max(attempt, 1)} 次）"
                )
            elif state == "connected" and old == "reconnecting":
                count = len(st.get("tools") or [])
                self._conversation.add_assistant_message(
                    f"MCP Server '{name}' 已恢复连接，"
                    f"已重新注册 {count} 个工具"
                )

        self._mcp_states_seen = current

        signature = self._tool_signature(states)
        tools_changed = signature != self._mcp_tool_signature
        if tools_changed:
            self._mcp_tool_signature = signature
            self._sidebar.refresh_tools()
            self._header.set_tool_count()

        # 有 Server 处于重连中时每秒重绘边栏，让「剩余秒数」实时更新
        if tools_changed or any(v != "connected" for v in current.values()):
            self._sidebar.set_mcp_status(states, get_mcp_warnings())

    # ---------- LLM 初始化 ----------

    def _init_provider(self) -> None:
        """provider=qianfan 且有 Key 时使用真实接口，否则回落本地 Mock。"""
        settings = load_settings()
        if not settings.use_mock:
            self._provider = OpenAICompatibleProvider(
                api_key=settings.api_key,
                base_url=settings.base_url,
                model=settings.model,
            )
            self._model_label = settings.model
            api_label = settings.base_url
        else:
            self._provider = MockProvider()
            self._model_label = "mock"
            api_label = "-"

        self._header.set_model(self._model_label)
        self._sidebar.set_model(self._model_label)
        self._sidebar.set_api(api_label)

    # ---------- 输入处理 ----------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        self.hide_completion()
        if not text:
            return
        if text.startswith("/"):
            self._handle_command(text)
            return
        if self._compacting:
            self._conversation.add_assistant_message(
                "正在压缩上下文，请等待压缩完成后再输入。"
            )
            return
        if self._context_blocked:
            usage = self._context_mgr.usage(self._messages) if self._context_mgr else None
            detail = f"（当前 {usage.display()}）" if usage else ""
            ratio = int(load_settings().context_warn_ratio * 100)
            self._conversation.add_assistant_message(
                f"上下文已达到 {ratio}% 警告阈值{detail}，"
                "请先执行 /compact 压缩上下文后再继续。"
            )
            return
        self._run_user_turn(text)

    def _handle_command(self, cmd: str) -> None:
        parts = cmd.split(maxsplit=1)
        head = parts[0].lower()

        if head in ("/help", "/?"):
            lines = [
                "xAgent 帮助",
                "",
                "使用方式：直接输入自然语言，我会自动选择工具。",
                "",
                "命令：",
            ]
            for name, desc in SLASH_COMMANDS:
                lines.append(f"  {name:<10} {desc}")
            lines += ["", "提示：输入 / 会弹出补全面板，↑/↓ 选择，Enter 执行，Esc 关闭。"]
            self._conversation.add_assistant_message("\n".join(lines))
        elif head == "/compact":
            self._compact_context()
        elif head == "/clear":
            self._conversation.clear_all()
            self._conversation.add_assistant_message(
                "对话显示已清空（历史仍保留在上下文中）。"
            )
        elif head == "/new":
            self._new_session()
        elif head == "/save":
            self._save_session(verbose=True)
        elif head == "/resume":
            self._show_session_list()
        elif head == "/tools":
            lines = []
            for t in ToolService.list_tools():
                lines.append(f"  · {t.name:<20} {t.description}")
            self._conversation.add_assistant_message("可用工具：\n" + "\n".join(lines))
        elif head == "/cwd":
            self._conversation.add_assistant_message(f"当前目录：{os.getcwd()}")
        elif head == "/model":
            settings = load_settings()
            api = settings.base_url if not settings.use_mock else "-"
            self._conversation.add_assistant_message(
                f"模型：{self._model_label}\n"
                f"接口：{api}\n"
                f"配置：{settings.config_path}"
            )
        elif head == "/mcp":
            states = mcp_server_states()
            if states:
                lines = ["MCP 服务器："]
                for st in states:
                    name = str(st.get("name", ""))
                    if str(st.get("state")) == "connected":
                        tools = st.get("tools") or []
                        lines.append(f"  ▸ {name}（{len(tools)} 个工具）")
                        lines.extend(f"      · {tool}" for tool in tools)
                    else:
                        attempt = int(st.get("attempt") or 0)
                        retry_in = float(st.get("retry_in") or 0.0)
                        lines.append(
                            f"  ⟳ {name} 重连中（第 {attempt} 次，"
                            f"{retry_in:.0f}s 后重试）"
                        )
                        error = str(st.get("last_error") or "").strip()
                        if error:
                            lines.append(f"      ! {error}")
                self._conversation.add_assistant_message("\n".join(lines))
            else:
                self._conversation.add_assistant_message(
                    "当前没有配置 MCP 服务器。请检查 .mcp.json。"
                )
        elif head in ("/quit", "/exit", "/q"):
            self._save_session(quiet=True)
            self.exit()
        else:
            self._conversation.add_assistant_message(
                f"未知命令：{cmd}。输入 /help 查看可用命令。"
            )

    # ---------- 会话管理 ----------

    def _reset_conversation_state(self) -> None:
        """清空内存中的对话状态（不动会话文件）。"""
        self._conversation.clear_all()
        self._messages = []
        self._agents_injected = False
        self.round_no = 1
        self.tool_calls_total = 0
        self._sidebar.set_round(1)
        self._sidebar.reset_skill_loaded()
        if self._context_mgr:
            self._context_mgr.reset_usage()
        self._context_blocked = False
        self._context_warned = False

    def _new_session(self) -> None:
        """保存当前会话并新建一个空会话。"""
        saved = self._save_session(quiet=True)
        self._reset_conversation_state()
        self._session_id = self._store.new_id()
        self._session_created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._ensure_skill_prompt()
        self._inject_agents_md()
        self._update_context_display()
        message = "新会话已创建。"
        if saved is not None:
            message += f"\n上一个会话已保存：{saved.session_id}"
        self._conversation.add_assistant_message(message)

    def _save_session(self, quiet: bool = False, verbose: bool = False):
        """保存当前会话到 .agents/sessions，返回 SessionMeta。"""
        if not any(m.role != "system" for m in self._messages):
            if verbose:
                self._conversation.add_assistant_message("当前没有可保存的对话内容。")
            return None
        try:
            meta = self._store.save(
                self._messages,
                session_id=self._session_id,
                created_at=self._session_created_at,
            )
        except Exception as exc:
            if not quiet:
                self._conversation.add_assistant_message(f"保存会话失败：{exc}")
            return None

        self._session_id = meta.session_id
        if verbose and not quiet:
            self._conversation.add_assistant_message(
                f"会话已保存：{meta.session_id}\n"
                f"位置：{self._store.path_for(meta.session_id)}"
            )
        return meta

    def _show_session_list(self) -> None:
        sessions = self._store.list()
        if not sessions:
            self._conversation.add_assistant_message("还没有保存过的会话。")
            return
        self.push_screen(
            SessionListScreen(sessions), callback=self._on_session_picked
        )

    def _on_session_picked(self, session_id: Optional[str]) -> None:
        if not session_id:
            return
        # 切换前先保存当前会话，避免丢失
        self._save_session(quiet=True)
        try:
            meta, loaded = self._store.load(session_id)
        except Exception as exc:
            self._conversation.add_assistant_message(f"恢复会话失败：{exc}")
            return

        self._reset_conversation_state()
        self._session_id = session_id
        self._session_created_at = meta.created_at or datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # 还原非 System 消息
        self._messages = [m for m in loaded if m.role != "system"]
        agents_message = next(
            (
                m for m in loaded
                if m.role == "system" and m.content.startswith(AGENTS_MARKER)
            ),
            None,
        )
        self._ensure_skill_prompt()
        if agents_message is not None:
            self._messages.insert(1, agents_message)
            self._agents_injected = True
        else:
            self._inject_agents_md()

        self._render_history(self._messages)
        user_rounds = sum(1 for m in self._messages if m.role == "user")
        self.round_no = max(1, user_rounds)
        self._sidebar.set_round(self.round_no)
        self._update_context_display()
        self._conversation.add_assistant_message(
            f"已恢复会话：{meta.title}（{meta.session_id}，"
            f"{meta.message_count} 条消息）"
        )
        self._prompt.focus()

    def _render_history(self, messages: List[LLMMessage]) -> None:
        """把历史消息回显到对话区（工具结果只显示摘要）。"""
        for message in messages:
            if message.role == "user":
                self._conversation.add_user_message(message.content or "")
            elif message.role == "assistant":
                if (message.content or "").strip():
                    self._conversation.add_assistant_message(message.content)
                if message.tool_calls:
                    names = "、".join(tc.name for tc in message.tool_calls)
                    self._conversation.add_assistant_message(f"[调用工具] {names}")
            elif message.role == "tool":
                text = (message.content or "").replace("\n", " ")
                if len(text) > 160:
                    text = text[:157] + "..."
                self._conversation.add_assistant_message(f"[工具结果] {text}")

    def _inject_agents_md(self) -> None:
        """会话首次加载时把 AGENTS.md 作为一条 System 消息注入，后续复用。"""
        if self._agents_injected:
            return
        self._agents_injected = True
        path = self._store.root / load_settings().agents_md_file
        if not path.is_file():
            return
        try:
            content = path.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            return
        if not content:
            return
        message = LLMMessage(
            role="system",
            content=f"{AGENTS_MARKER}\n\n{content}",
        )
        index = 1 if self._messages and self._messages[0].role == "system" else 0
        self._messages.insert(index, message)
        self._update_context_display()

    # ---------- 斜杠命令补全 ----------

    @property
    def completion_visible(self) -> bool:
        return self._completion_visible

    def _refresh_completion(self, value: str) -> None:
        if not value.startswith("/") or " " in value:
            self.hide_completion()
            return

        query = value[1:].lower()
        matches = [
            (name, desc)
            for name, desc in SLASH_COMMANDS
            if name[1:].lower().startswith(query)
        ]
        if not matches:
            self.hide_completion()
            return

        self._completion_matches = matches
        self._completion_index = 0
        self._completion_visible = True
        self._completion_panel.add_class("visible")
        self._render_completion()

    def hide_completion(self) -> None:
        self._completion_visible = False
        self._completion_matches = []
        self._completion_index = 0
        self._completion_panel.remove_class("visible")

    def _render_completion(self) -> None:
        lines = ["命令补全  ↑/↓ 选择 · Enter 执行 · Esc 关闭", ""]
        for index, (name, desc) in enumerate(self._completion_matches):
            marker = "▸" if index == self._completion_index else " "
            lines.append(f" {marker} {name:<10} {desc}")
        self._completion_list.update("\n".join(lines))

    def completion_move(self, delta: int) -> None:
        if not self._completion_visible or not self._completion_matches:
            return
        total = len(self._completion_matches)
        self._completion_index = (self._completion_index + delta) % total
        self._render_completion()

    def completion_accept(self, execute: bool) -> None:
        if not self._completion_matches:
            return
        command = self._completion_matches[self._completion_index][0]
        self._prompt.value = command
        self._prompt.cursor_position = len(command)
        self.hide_completion()
        if execute:
            self._prompt.value = ""
            self._handle_command(command)
        self._prompt.focus()

    # ---------- 核心：一轮对话 ----------

    @property
    def _max_model_calls(self) -> int:
        """单轮最多模型调用次数（来自 config.ini，防止工具死循环）。"""
        return max(1, load_settings().max_model_calls)

    def _ensure_skill_prompt(self) -> None:
        """把 Skill 清单注入 System Prompt；/new 清空历史后重新注入。"""
        if not self._skill_prompt:
            return
        if self._messages and self._messages[0].role == "system":
            return
        self._messages.insert(
            0, LLMMessage(role="system", content=self._skill_prompt)
        )
        self._update_context_display()

    # ---------- 上下文用量 / 压缩 ----------

    def _update_context_display(self) -> None:
        """刷新侧栏上下文用量；达到配置阈值时警告并暂停普通输入。"""
        if not self._context_mgr:
            return
        ratio = int(load_settings().context_warn_ratio * 100)
        usage = self._context_mgr.usage(self._messages)
        text = usage.display()
        if usage.over_threshold:
            text += f" · 已达 {ratio}%，请 /compact"
        self._sidebar.set_context(text)

        if usage.over_threshold and not self._context_warned:
            self._context_warned = True
            self._context_blocked = True
            self._sidebar.set_status("需压缩")
            self._conversation.add_assistant_message(
                f"上下文已使用 {usage.display()}，达到 {ratio}% 警告阈值。\n"
                "请执行 /compact 压缩上下文；在压缩成功前将暂停接受新的普通提示词。"
            )

    def _track_response_usage(self, response: LLMMessage, sent_index: int) -> None:
        """优先采用模型 API 的 prompt_tokens，随后估算新增消息。"""
        usage = getattr(response, "usage", None) or {}
        prompt_tokens = usage.get("prompt_tokens") or usage.get("total_tokens")
        if self._context_mgr and prompt_tokens:
            self._context_mgr.record_api_usage(int(prompt_tokens), sent_index)
        self._update_context_display()

    def _compact_context(self) -> None:
        """执行 /compact：归档原始上下文 → LLM 摘要 → 替换历史。"""
        if self._compacting:
            self._conversation.add_assistant_message("上下文正在压缩，请稍候。")
            return
        if not self._messages:
            self._conversation.add_assistant_message("当前没有可压缩的对话内容。")
            return
        if not self._context_mgr:
            self._conversation.add_assistant_message("上下文管理器未初始化。")
            return

        self._compacting = True
        self._prompt.disabled = True
        self._sidebar.set_status("压缩中")
        self._status.update_status(
            ready=False, round_no=self.round_no, model=self._model_label,
            tool_calls=self.tool_calls_total,
        )

        try:
            result = compact_messages(
                self._messages, self._provider, project_root=os.getcwd()
            )
        except CompactionError as exc:
            self._restore_after_compaction_failure(str(exc), exc.archive_id)
            return
        except Exception as exc:
            self._restore_after_compaction_failure(f"未知错误：{exc}")
            return

        # 成功：替换历史（保留 System Prompt），并解除输入限制
        self._messages = result.messages
        self._context_mgr.reset_usage()
        self._context_blocked = False
        self._context_warned = False
        self._compacting = False
        self._prompt.disabled = False
        self._update_context_display()
        self._sidebar.set_status("就绪")
        self._status.update_status(
            ready=True, round_no=self.round_no, model=self._model_label,
            tool_calls=self.tool_calls_total,
        )
        target_k = load_settings().summary_target_tokens / 1000
        summary_k = estimate_tokens(result.summary) / 1000
        self._conversation.add_assistant_message(
            f"上下文压缩完成。\n"
            f"归档编号：{result.archive_id}\n"
            f"摘要约 {summary_k:.1f}k tokens（目标约 {target_k:g}k）\n\n"
            f"{result.note}"
        )
        self._prompt.focus()

    def _restore_after_compaction_failure(
        self, reason: str, archive_id: Optional[str] = None
    ) -> None:
        """压缩失败：保留原有上下文，允许重新执行 /compact。"""
        self._compacting = False
        self._prompt.disabled = False
        self._sidebar.set_status("就绪")
        self._status.update_status(
            ready=True, round_no=self.round_no, model=self._model_label,
            tool_calls=self.tool_calls_total,
        )
        detail = f"\n归档编号：{archive_id}" if archive_id else ""
        self._conversation.add_assistant_message(
            f"上下文压缩失败：{reason}{detail}\n"
            "原有上下文已保留，可修正问题后重新执行 /compact。"
        )
        self._prompt.focus()

    def _run_user_turn(self, user_text: str) -> None:
        self._ensure_skill_prompt()
        self._conversation.add_user_message(user_text)
        self._messages.append(LLMMessage(role="user", content=user_text))
        self._update_context_display()

        model_label = self._model_label
        self._status.update_status(
            ready=False, round_no=self.round_no, model=model_label
        )
        self._sidebar.set_status("生成中")

        tools_schema = self._build_tools_schema()

        t0 = time.time()
        try:
            self._run_model_loop(tools_schema)
        except Exception as exc:
            self._conversation.add_assistant_message(f"LLM 调用失败：{exc}")
            self._status.update_status(
                ready=True, round_no=self.round_no, model=model_label
            )
            self._sidebar.set_status("就绪")
            return

        total_ms = int((time.time() - t0) * 1000)
        self._status.update_status(
            ready=True, round_no=self.round_no, model=model_label,
            tool_calls=self.tool_calls_total, elapsed_ms=total_ms,
        )

        self.round_no += 1
        self._sidebar.set_round(self.round_no)
        self._sidebar.set_status("就绪")

    def _run_model_loop(self, tools_schema: List[Dict[str, Any]]) -> None:
        """
        反复调用模型直到不再请求工具。

        关键：assistant 的每个 tool_call 都必须有对应的 tool 消息回传，
        否则下一轮请求会被千帆判为消息格式错误
        （Message format error, index[i] should be [tool] but is [user]）。
        """
        for _ in range(self._max_model_calls):
            sent_index = len(self._messages)
            response = self._provider.chat(self._messages, tools_schema)
            self._messages.append(response)
            self._track_response_usage(response, sent_index)

            if not response.tool_calls:
                if response.content:
                    self._conversation.add_assistant_message(response.content.strip())
                return

            for tc in response.tool_calls:
                card, tool_msg = self._execute_tool(tc)
                if card:
                    self._conversation.add_tool_card(card)
                self._messages.append(tool_msg)
                self.tool_calls_total += 1
                self._update_context_display()

        self._conversation.add_assistant_message(
            f"（已达到单轮 {self._max_model_calls} 次模型调用上限，停止继续调用工具）"
        )

    def _execute_tool(self, tc: ToolCall) -> tuple[Optional[ToolCallCard], LLMMessage]:
        t0 = time.time()
        result = ToolService.call(tc.name, **tc.arguments)
        elapsed_ms = int((time.time() - t0) * 1000)

        # loadSkill 成功后在侧栏点亮技能加载角标
        if tc.name == "loadSkill" and result.success:
            loaded_name = result.data.get("name")
            if loaded_name:
                self._sidebar.mark_skill_loaded(str(loaded_name))

        card = ToolCallCard(
            call_id=tc.id,
            tool_name=tc.name,
            args=tc.arguments,
            result=result,
            elapsed_ms=elapsed_ms,
        )
        tool_msg = LLMMessage(
            role="tool",
            content=json.dumps(result.to_dict(), ensure_ascii=False),
            tool_call_id=tc.id,
        )
        return card, tool_msg

    def _build_tools_schema(self) -> List[Dict[str, Any]]:
        schemas: List[Dict[str, Any]] = []
        for info in ToolService.list_tools():
            # MCP 等外部工具直接使用 Server 提供的完整 JSON Schema
            if info.input_schema:
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": info.name,
                        "description": info.description,
                        "parameters": info.input_schema,
                    },
                })
                continue
            props: Dict[str, Any] = {}
            required: List[str] = []
            for pname, pdesc in info.parameters.items():
                props[pname] = _param_schema(pdesc)
                if "必需" in pdesc:
                    required.append(pname)
            schemas.append({
                "type": "function",
                "function": {
                    "name": info.name,
                    "description": info.description,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required,
                    },
                },
            })
        return schemas

    # ---------- 快捷键 ----------

    def action_quit(self) -> None:
        self._save_session(quiet=True)
        self.exit()

    def action_toggle_sidebar(self) -> None:
        self._sidebar.toggle_collapsed()

    def action_help(self) -> None:
        self._handle_command("/help")
        self._prompt.focus()

    def action_clear(self) -> None:
        self._handle_command("/clear")

    def action_new_session(self) -> None:
        self._handle_command("/new")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "prompt":
            self._refresh_completion(event.value)

    def on_key(self, event) -> None:
        if event.key == "escape" and self._completion_visible:
            self.hide_completion()
            event.stop()
            return
        if event.key == "escape" and self._prompt.has_focus:
            self._prompt.value = ""
            event.stop()
