# -*- coding: utf-8 -*-
"""config.ini 读取 / 自动生成 与 会话持久化 的单元测试。"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xAgent.core.config import (
    DEFAULT_CONFIG_TEMPLATE, Settings, ensure_config_file, load_settings,
)
from xAgent.core.llm import LLMMessage, ToolCall
from xAgent.core.sessions import SessionStore


class TestConfigFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_when_missing(self):
        path, created = ensure_config_file(self.tmp)
        self.assertTrue(created)
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("[llm]", text)
        self.assertIn("provider = mock", text)
        self.assertIn("[context]", text)

    def test_second_call_does_not_overwrite(self):
        path, created = ensure_config_file(self.tmp)
        path.write_text("[llm]\nmodel = custom\n", encoding="utf-8")
        same_path, created_again = ensure_config_file(self.tmp)
        self.assertFalse(created_again)
        self.assertEqual(same_path, path)
        self.assertIn("custom", same_path.read_text(encoding="utf-8"))

    def test_load_defaults_without_file(self):
        settings = load_settings(self.tmp)
        self.assertIsInstance(settings, Settings)
        self.assertTrue(settings.use_mock)
        self.assertEqual(settings.context_warn_ratio, 0.70)
        self.assertEqual(settings.sidebar_width, 40)

    def test_load_values(self):
        path, _ = ensure_config_file(self.tmp)
        path.write_text(
            "[llm]\nprovider = qianfan\napi_key = sk-test\nmodel = m1\n"
            "[context]\nwindow = 32000\nwarn_ratio = 0.5\n"
            "[tools]\nsearch_top_k = 3\n"
            "[ui]\nsidebar_width = 30\n",
            encoding="utf-8",
        )
        settings = load_settings(self.tmp)
        self.assertFalse(settings.use_mock)
        self.assertEqual(settings.api_key, "sk-test")
        self.assertEqual(settings.model, "m1")
        self.assertEqual(settings.context_window, 32000)
        self.assertEqual(settings.context_warn_ratio, 0.5)
        self.assertEqual(settings.search_top_k, 3)
        self.assertEqual(settings.sidebar_width, 30)

    def test_legacy_qianfan_section(self):
        Path(self.tmp, "config.ini").write_text(
            "[qianfan]\nbaidu_qianfan_api_key = legacy-key\nmodel = legacy\n",
            encoding="utf-8",
        )
        settings = load_settings(self.tmp)
        self.assertEqual(settings.api_key, "legacy-key")
        self.assertEqual(settings.model, "legacy")

    def test_env_overrides_file(self):
        path, _ = ensure_config_file(self.tmp)
        path.write_text("[llm]\nprovider = mock\nmodel = file-model\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"QIANFAN_MODEL": "env-model"}):
            settings = load_settings(self.tmp)
        self.assertEqual(settings.model, "env-model")

    def test_template_is_complete(self):
        for section in ("[llm]", "[context]", "[session]", "[mcp]", "[tools]", "[ui]"):
            self.assertIn(section, DEFAULT_CONFIG_TEMPLATE)


class TestSessionStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        Path(self.tmp, "config.ini").write_text(
            "[session]\ndir = .agents/sessions\n", encoding="utf-8"
        )
        self.store = SessionStore(self.tmp)

    def test_save_and_load_roundtrip(self):
        messages = [
            LLMMessage(role="system", content="[项目规范] AGENTS.md\n规则"),
            LLMMessage(role="user", content="帮我建个文件"),
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c1", name="create_file", arguments={"a": 1})],
            ),
            LLMMessage(role="tool", content='{"success": true}', tool_call_id="c1"),
        ]
        meta = self.store.save(messages, created_at="2026-11-05 12:00:00")
        loaded_meta, loaded = self.store.load(meta.session_id)

        self.assertEqual(loaded_meta.session_id, meta.session_id)
        self.assertEqual(loaded_meta.created_at, "2026-11-05 12:00:00")
        self.assertEqual(loaded_meta.title, "帮我建个文件")
        self.assertEqual(len(loaded), 4)
        self.assertEqual(loaded[2].tool_calls[0].name, "create_file")
        self.assertEqual(loaded[3].tool_call_id, "c1")

    def test_list_sorted_and_preview(self):
        m1 = self.store.save(
            [LLMMessage(role="user", content="会话一")],
            created_at="2026-11-01 10:00:00",
        )
        m2 = self.store.save(
            [LLMMessage(role="user", content="会话二")],
            created_at="2026-11-02 10:00:00",
        )
        # 直接改写 meta 的 updated_at 以验证排序
        self._patch_updated(m1.session_id, "2099-01-01 10:00:00")
        ids = [m.session_id for m in self.store.list()]
        self.assertEqual(ids, [m1.session_id, m2.session_id])

    def _patch_updated(self, session_id, stamp):
        path = self.store.path_for(session_id)
        lines = path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[0])
        record["updated_at"] = stamp
        lines[0] = json.dumps(record, ensure_ascii=False)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_load_missing(self):
        with self.assertRaises(FileNotFoundError):
            self.store.load("20260101-000000-abcdef")

    def test_invalid_id_rejected(self):
        with self.assertRaises(ValueError):
            self.store.path_for("../../etc/passwd")

    def test_empty_store_list(self):
        self.assertEqual(self.store.list(), [])


if __name__ == "__main__":
    unittest.main()
