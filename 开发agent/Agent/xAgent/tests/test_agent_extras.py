# -*- coding: utf-8 -*-
"""D6 / D7：AGENTS.md 注入截断与工具调用落盘日志。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xAgent.core import ToolService
from xAgent.core.call_log import _summarize, log_tool_call
from xAgent.core.config import read_agents_md


class TestAgentsMdInjection(unittest.TestCase):
    """D6：AGENTS.md 读取与可配置长度截断。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_file_returns_empty(self):
        self.assertEqual(read_agents_md(self.root), "")

    def test_short_content_not_truncated(self):
        (self.root / "AGENTS.md").write_text("简短规则\n", encoding="utf-8")
        self.assertEqual(read_agents_md(self.root), "简短规则")

    def test_long_content_truncated_with_notice(self):
        content = "A" * 9000
        (self.root / "AGENTS.md").write_text(content, encoding="utf-8")
        result = read_agents_md(self.root)
        self.assertTrue(result.startswith("A" * 8000))
        self.assertIn("已截断", result)
        self.assertIn("9000", result)
        self.assertLess(len(result), len(content) + 200)

    def test_limit_is_configurable(self):
        (self.root / "AGENTS.md").write_text("B" * 300, encoding="utf-8")
        (self.root / "config.ini").write_text(
            "[session]\nagents_md_max_chars = 50\n", encoding="utf-8"
        )
        result = read_agents_md(self.root)
        self.assertTrue(result.startswith("B" * 50))
        self.assertIn("已截断", result)

    def test_custom_filename(self):
        (self.root / "TEAM.md").write_text("团队规则", encoding="utf-8")
        self.assertEqual(read_agents_md(self.root, filename="TEAM.md"), "团队规则")

    def test_explicit_limit_overrides_config(self):
        (self.root / "AGENTS.md").write_text("C" * 500, encoding="utf-8")
        result = read_agents_md(self.root, max_chars=10)
        self.assertTrue(result.startswith("C" * 10))
        self.assertIn("已截断", result)


class TestToolCallLog(unittest.TestCase):
    """D7：工具调用日志落盘（工具名、入参/结果摘要、耗时、成功失败）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _env(self, **extra):
        values = {
            "XAGENT_LOG_DIR": str(self.log_dir),
            "XAGENT_LOG_ENABLED": "1",
        }
        values.update(extra)
        return mock.patch.dict(os.environ, values)

    def _records(self):
        files = sorted(self.log_dir.glob("tool_calls-*.jsonl"))
        self.assertTrue(files, "未找到日志文件")
        lines = files[-1].read_text(encoding="utf-8").strip().splitlines()
        return [json.loads(line) for line in lines]

    def test_successful_call_is_logged(self):
        with self._env():
            result = ToolService.call("is_leap_year", year=2024)
        self.assertTrue(result.success)
        record = self._records()[-1]
        self.assertEqual(record["tool"], "is_leap_year")
        self.assertTrue(record["success"])
        self.assertGreaterEqual(record["elapsed_ms"], 0)
        self.assertIn("2024", record["arguments"])
        self.assertIn("闰年", record["result"])
        self.assertIsInstance(record["time"], str)

    def test_failed_call_is_logged(self):
        with self._env():
            result = ToolService.call("is_leap_year", year="abc")
        self.assertFalse(result.success)
        record = self._records()[-1]
        self.assertFalse(record["success"])
        self.assertIn("abc", record["arguments"])
        self.assertIn("整数", record["result"])

    def test_logging_can_be_disabled(self):
        with self._env(XAGENT_LOG_ENABLED="0"):
            result = ToolService.call("is_leap_year", year=2024)
        self.assertTrue(result.success)
        self.assertEqual(list(self.log_dir.glob("tool_calls-*.jsonl")), [])

    def test_log_failure_does_not_break_tool_call(self):
        bad_dir = Path(self._tmp.name) / "not-a-dir"
        bad_dir.write_text("x", encoding="utf-8")
        with mock.patch.dict(os.environ, {
            "XAGENT_LOG_DIR": str(bad_dir),
            "XAGENT_LOG_ENABLED": "1",
        }):
            result = ToolService.call("is_leap_year", year=2024)
        self.assertTrue(result.success)

    def test_summary_is_truncated(self):
        text = _summarize({"content": "x" * 500}, 50)
        self.assertLessEqual(len(text), 51)
        self.assertTrue(text.endswith("…"))


if __name__ == "__main__":
    unittest.main()
