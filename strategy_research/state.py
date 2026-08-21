"""全局状态：14 字段 TypedDict（规格二节数据字典）。

后写覆盖语义：每字段每 symbol 恰好写一次，无 reducer；
字段来源 = 数据流（① 写三快照 / ② 写 signals / ③-⑥ 写对抗产物 / ⑦ 写 results）。
"""

from __future__ import annotations

from typing import Any, TypedDict


class State(TypedDict, total=False):
    # 输入（⑨ 筛选器产出；SR_TOKENS 手动覆盖时跳过筛选）
    tokens: list[str]

    # 确定性层三快照（① collect_data 写入）
    market_data: dict[str, dict]
    fundamental_data: dict[str, dict]
    microstructure_data: dict[str, dict]
    web_data: dict[str, dict]

    # 确定性信号（② compute_signals 写入）
    signals: dict[str, dict]

    # LLM/对抗产物（③-⑥ 写入，后写覆盖）
    facts: dict[str, list[dict]]
    decisions: dict[str, dict]
    challenges: dict[str, list[dict]]
    final_decisions: dict[str, dict]

    # 风控终审（⑦ 写入）
    risk_flags: dict[str, list[str]]
    results: list[dict]

    # 工件层（⑧ 写入）
    research_artifacts: dict[str, dict]

    # 元数据（各节点追加，⑧ 消费）
    meta: dict[str, Any]
