"""全局状态：16 字段 TypedDict（规格二节数据字典）。

后写覆盖语义：每字段每 symbol 恰好写一次，无 reducer；
字段来源 = 数据流（① 写五快照 + scanner_snapshot / ② 写 signals / 分支写证据 / ④ 写工件）。
"""

from __future__ import annotations

from typing import Any, TypedDict


class State(TypedDict, total=False):
    # 输入（⑨ 筛选器产出；SR_TOKENS 手动覆盖时跳过筛选）
    tokens: list[str]

    # 确定性层五快照（① collect_data 写入）
    market_data: dict[str, dict]
    fundamental_data: dict[str, dict]
    microstructure_data: dict[str, dict]
    web_data: dict[str, dict]
    social_data: dict[str, dict]

    # 扫描器快照（① collect_data 读外部 BinanceApi CSV，分支摘要参考 + ④ 报告渲染）
    scanner_snapshot: dict

    # 确定性信号（② compute_signals 写入）
    signals: dict[str, dict]

    # 分支证据（02 票：bull/bear 各写各的字段，并行安全；evidence_verify 合并）
    bull_evidence: dict[str, list[dict]]
    bear_evidence: dict[str, list[dict]]
    # 分支异常留痕（03 票：同样 side 独占，避免并行写共享键冲突）
    bull_errors: dict[str, str]
    bear_errors: dict[str, str]
    evidence: dict[str, dict]
    rejected_evidence: dict[str, list[dict]]

    # 工件层（④ 写入）
    research_artifacts: dict[str, dict]

    # 元数据（各节点追加，④ 消费）
    meta: dict[str, Any]
