# -*- coding: utf-8 -*-
"""xAgent 测试包。"""

import os
import tempfile

# 测试期间的工具调用日志写到临时目录，避免污染项目工作目录
os.environ.setdefault(
    "XAGENT_LOG_DIR",
    os.path.join(tempfile.gettempdir(), "xagent-test-logs"),
)
