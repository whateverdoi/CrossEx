"""pytest 共享配置：默认 mock 模式，避免测试误发外部请求。"""

from __future__ import annotations

import os

os.environ.setdefault("SR_MOCK", "1")
