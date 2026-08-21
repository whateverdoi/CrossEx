"""mock 数据源：SR_MOCK=1 显式离线模式，与真实路径字段同构。

01 票骨架：仅固定候选列表 + 占位快照；02 票扩展为完整同构字段。
"""

from __future__ import annotations

#: mock 固定候选（规格 ⑨ 伪代码：6 个）
MOCK_TOKENS = ["BTC", "ETH", "SOL", "UNI", "DOGE", "XRP"]


def mock_screening_candidates() -> list[dict]:
    """mock 筛选结果：固定候选，每条带 reason 与空 metrics。"""
    return [
        {"symbol": s, "reason": "mock 固定候选", "metrics": {}}
        for s in MOCK_TOKENS
    ]
