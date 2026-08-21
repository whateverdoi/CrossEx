"""运行模式与环境变量（SR_MOCK 全离线模式）。"""

from __future__ import annotations

import os

MOCK_ENV = "SR_MOCK"


def is_mock_mode() -> bool:
    """SR_MOCK=1 显式离线模式：全链路走 mock，零外部请求。"""
    return os.environ.get(MOCK_ENV) == "1"


def is_manual_tokens() -> bool:
    """SR_TOKENS 手动覆盖：指定币种跳过筛选。"""
    return bool(os.environ.get("SR_TOKENS", "").strip())
