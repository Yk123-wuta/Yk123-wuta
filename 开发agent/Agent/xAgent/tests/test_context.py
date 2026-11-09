# -*- coding: utf-8 -*-
"""上下文压缩 / 归档 / recallContext 单元测试。"""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from xAgent.core.context import (
    CompactionError,
    ContextManager,
    archive_messages,
    compact_messages,
    estimate_tokens,
    recall_context,
    resolve_context_window,
)
from xAgent.core.llm import LLMMessage, ToolCall


class _FakeProvider:
    def __init__(self, summary="# 摘要\n\n- 已完成：MCP 接入", error=None):
        self.summary = summary
        self.error = error
        self.calls = []

    def chat(self, messages, tools_schema):
        self.calls.append((messages, tools_schema))
        if self.error:
            raise self.error
        return LLMMessage(role="assistant", content=self.summary)


class TestEstimateAndWindow(unittest.TestCase):
    def test_estimate_tokens(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertGreater(estimate_tokens("你好，世界"), 0)
        self.assertGreater(estimate_tokens("hello world"), 0)

    def test_resolve_context_window_known(self):
        window, is_default = resolve_context_window("ernie-4.5-turbo-128k")
        self.assertEqual(window, 128000)
        self.assertFalse(is_default)

    def test_resolve_context_window_from_name(self):
        window, is_default = resolve_context_window("some-model-32k")
        self.assertEqual(window, 32000)
        self.assertFalse(is_default)

    def test_resolve_context_window_default(self):
        window, is_default = resolve_context_window("unknown-model")
        self.assertEqual(window, 128000)
        self.assertTrue(is_default)


class TestContextUsage(unittest.TestCase):
    def test_estimate_all_messages(self):
        mgr = ContextManager(project_root=".", model="unknown-model")
        usage = mgr.usage(
            [
                LLMMessage(role="system", content="system prompt"),
                LLMMessage(role="user", content="hello"),
            ]
        )
        self.assertGreater(usage.tokens, 0)
        self.assertTrue(usage.is_default_window)
        self.assertIn("默认", usage.display())

    def test_prefer_api_usage(self):
        mgr = ContextManager(project_root=".", model="unknown-model")
        messages = [
            LLMMessage(role="user", content="a" * 1000),
            LLMMessage(role="assistant", content="b" * 1000),
        ]
        mgr.record_api_usage(1234, 1)
        usage = mgr.usage(messages)
        self.assertGreaterEqual(usage.tokens, 1234)
        self.assertLess(usage.tokens, 1234 + estimate_tokens("b" * 1000) + 10)

    def test_threshold(self):
        mgr = ContextManager(project_root=".", model="x", window=1000)
        messages = [LLMMessage(role="user", content="字" * 900)]
        self.assertTrue(mgr.usage(messages).over_threshold)


class TestArchiveAndRecall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _messages(self):
        return [
            LLMMessage(role="system", content="系统提示"),
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(id="c1", name="list_pets", arguments={"input": {}})
                ],
            ),
            LLMMessage(role="tool", content='{"pets": ["泡芙"]}', tool_call_id="c1"),
            LLMMessage(role="user", content="请记住我的猫叫泡芙"),
        ]

    def test_archive_preserves_order_and_tool_calls(self):
        when = datetime(2026, 10, 22, 15, 30, 0)
        archive_id = archive_messages(self._messages(), self.root, now=when)
        self.assertEqual(archive_id, "20261022-153000")
        path = self.root / ".agents" / "context" / f"{archive_id}.jsonl"
        self.assertTrue(path.is_file())

        records = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
        self.assertEqual([r["index"] for r in records], [0, 1, 2, 3])
        self.assertEqual(records[1]["tool_calls"][0]["name"], "list_pets")
        self.assertEqual(records[2]["tool_call_id"], "c1")
        self.assertEqual(records[3]["role"], "user")

    def test_archive_unique_id(self):
        when = datetime(2026, 10, 22, 15, 30, 0)
        first = archive_messages(self._messages(), self.root, now=when)
        second = archive_messages(self._messages(), self.root, now=when)
        self.assertNotEqual(first, second)

    def test_recall_by_keyword_and_id(self):
        when = datetime(2026, 10, 22, 15, 30, 0)
        archive_id = archive_messages(self._messages(), self.root, now=when)
        result = recall_context("泡芙", project_root=str(self.root))
        self.assertTrue(result["success"])
        self.assertGreaterEqual(len(result["matches"]), 1)
        self.assertIn("archive", result["matches"][0])

        one = recall_context("泡芙", archive_id=archive_id, project_root=str(self.root))
        self.assertTrue(one["success"])
        self.assertEqual(one["matches"][0]["archive"], archive_id)

    def test_recall_no_match(self):
        archive_messages(self._messages(), self.root)
        result = recall_context("不存在的内容", project_root=str(self.root))
        self.assertTrue(result["success"])
        self.assertEqual(result["matches"], [])

    def test_recall_errors(self):
        self.assertFalse(
            recall_context("", project_root=str(self.root))["success"]
        )
        self.assertFalse(
            recall_context("x", archive_id="../etc", project_root=str(self.root))["success"]
        )
        self.assertFalse(
            recall_context("x", archive_id="20990101-000000", project_root=str(self.root))["success"]
        )

    def test_recall_without_archives(self):
        result = recall_context("anything", project_root=str(self.root))
        self.assertFalse(result["success"])


class TestCompaction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)
        self.messages = [
            LLMMessage(role="system", content="系统提示：你是 xAgent"),
            LLMMessage(role="user", content="帮我接入 MCP"),
            LLMMessage(role="assistant", content="已完成 MCPClient"),
        ]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_compact_success_keeps_system_and_archive_note(self):
        provider = _FakeProvider()
        when = datetime(2026, 10, 22, 15, 30, 0)
        result = compact_messages(
            self.messages, provider, project_root=self.root, now=when
        )
        self.assertEqual(result.archive_id, "20261022-153000")
        # 只允许一次不带工具的摘要调用
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0][1], [])
        # 保留 System Prompt
        self.assertEqual(result.messages[0].role, "system")
        self.assertIn("系统提示", result.messages[0].content)
        # 摘要 + 归档说明 + recallContext 提示
        summary_message = result.messages[-1]
        self.assertIn(result.summary, summary_message.content)
        self.assertIn(result.archive_id, summary_message.content)
        self.assertIn("recallContext", summary_message.content)
        # 归档文件存在
        self.assertTrue(
            (self.root / ".agents/context" / f"{result.archive_id}.jsonl").is_file()
        )

    def test_compact_llm_failure_keeps_original(self):
        provider = _FakeProvider(error=RuntimeError("API down"))
        original = list(self.messages)
        with self.assertRaises(CompactionError) as ctx:
            compact_messages(self.messages, provider, project_root=self.root)
        self.assertIn("API down", str(ctx.exception))
        self.assertIsNotNone(ctx.exception.archive_id)
        self.assertEqual(self.messages, original)

    def test_compact_empty_summary_failure(self):
        provider = _FakeProvider(summary="   ")
        with self.assertRaises(CompactionError):
            compact_messages(self.messages, provider, project_root=self.root)

    def test_compact_without_content(self):
        with self.assertRaises(CompactionError):
            compact_messages(
                [LLMMessage(role="system", content="sys")],
                _FakeProvider(),
                project_root=self.root,
            )


if __name__ == "__main__":
    unittest.main()
