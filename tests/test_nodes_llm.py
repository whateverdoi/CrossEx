"""02/03 票验收：分支节点 LLM 链（mock 假模型全链 + 分支摘要 + 扫描器快照）。

mock 模式 get_llm 返回确定性假模型（env._MockChatModel），LLM 调用计数走
env._MOCK_CALL_COUNTS（03 票：键为 bull/bear），全链两分支各 6 次可验证。
05 票：旧决策链节点测试随节点退役，本文件仅存分支证据链测试。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from strategy_research import context, env
from strategy_research.graph import build_graph

MOCK_TOKENS = ["BTC", "ETH", "SOL", "UNI", "DOGE", "XRP"]


def _reset_counts() -> None:
    for k in env._MOCK_CALL_COUNTS:
        env._MOCK_CALL_COUNTS[k] = 0


@pytest.fixture(scope="module")
def full_result(tmp_path_factory):
    """mock 全链一次（6 token）+ 调用计数快照，供多个验收断言复用。

    返回计数快照而非实时 dict：断言不依赖测试执行顺序（后续测试若再触发
    LLM 调用也不会污染本批计数断言）。落盘隔离在临时目录，不写项目 reports/。
    """
    _reset_counts()
    cwd = Path.cwd()
    os.chdir(tmp_path_factory.mktemp("full_chain"))
    try:
        result = build_graph().invoke({"tokens": MOCK_TOKENS, "meta": {}})
    finally:
        os.chdir(cwd)
    return result, dict(env._MOCK_CALL_COUNTS)


def test_mock_evidence_shape(full_result):
    """mock 产物形状：两分支证据非空（claim/basis 三元组/source 与 domain 一致），
    核验合并后 bull_case/bear_case 各归其位、全部通过（无剔除）。"""
    result, _ = full_result
    for s in MOCK_TOKENS:
        for side, items in (
            ("bull", result["bull_evidence"][s]),
            ("bear", result["bear_evidence"][s]),
        ):
            assert items, f"{s} {side} 证据为空（验收：mock 非空）"
            assert len(items) <= 8
            for it in items:
                assert it["claim"]
                assert set(it["basis"]) == {"domain", "field", "value"}
                assert it["source"] == it["basis"]["domain"]
                assert "confidence" not in it
        ev = result["evidence"][s]
        assert ev["bull_case"] and ev["bear_case"]
    assert result["rejected_evidence"] == {}


def test_mock_branch_llm_calls(full_result):
    """llm_calls 反映 bull/bear：两分支各 6 次（每 token 各 1），total=12。"""
    result, counts = full_result
    assert counts == {"bull": 6, "bear": 6}
    assert result["meta"]["llm_calls"]["total"] == 12
    assert result["meta"]["llm_calls"]["bull"] == 6
    assert result["meta"]["llm_calls"]["bear"] == 6


def test_mock_routing_keys_linked_to_prompts():
    """mock 路由关键词与分支 prompt 措辞绑定：断链即红（防措辞漂移致路由失效）。"""
    assert "多头证据研究员" in context.BULL_PROMPT  # → bull
    assert "空头证据研究员" in context.BEAR_PROMPT  # → bear


# ── 分支摘要与扫描器快照（03 票保留：分支消费同一份快照） ──────


_SNAP_STATE = {
    "date": "2026-08-16",
    "market": {
        "AKEUSDT": {
            "price": 0.009465,
            "ret_1h": 3.9425,
            "ret_4h": 0.0952,
            "ret_24h": -10.3184,
            "ret_7d": 132.2585,
            "price_change_pct_24h": -8.668,
            "quote_volume_24h": 202807691.0,
            "funding_rate": 5e-05,
            "taker_buy_ratio_24h": 0.4983,
            "open_interest_value": 39484773.0,
            "futures_premium_pct": 0.1501,
            "listing_days": 324.0,
            "onboard_date": "2025-09-26",
            "boards": ["gain_1h", "loss_24h", "gain_7d"],
        }
    },
    "microstructure": {
        "AKEUSDT": {
            "oi_change_24h": -0.5416,
            "oi_change_48h": -27.8883,
            "oi_value_change_24h": -10.0886,
            "ls_ratio_all": 0.5172,
            "ls_ratio_all_change_24h": -3.2186,
            "ls_ratio_top_acc": 0.4286,
            "ls_ratio_top_pos": 0.6445,
            "taker_bs_ratio": 1.0105,
            "funding_avg": 7.9e-05,
            "funding_trend": "flat",
        }
    },
}


@pytest.fixture(scope="module")
def scanned_full_result(tmp_path_factory):
    """mock 全链一次（1 token + 快照注入）：collect_data 后 state 带 scanner_snapshot。
    落盘隔离在临时目录，不写项目 reports/。"""
    _reset_counts()
    cwd = Path.cwd()
    os.chdir(tmp_path_factory.mktemp("scanned_chain"))
    try:
        result = build_graph().invoke(
            {"tokens": ["AKEUSDT"], "scanner_snapshot": _SNAP_STATE, "meta": {}}
        )
    finally:
        os.chdir(cwd)
    return result


def test_facts_summary_scanner_section():
    """验收：摘要含扫描器快照节（完整路径 market.{SYMBOL}.xxx 与口径标注）；
    快照缺失 → 无该节。"""
    state = {"tokens": ["AKEUSDT"], "signals": {}, "scanner_snapshot": _SNAP_STATE}
    summary = "\n".join(context._facts_summary_lines("AKEUSDT", state))
    assert (
        "== 扫描器快照（scanner_snapshot，截至 2026-08-16，field 直接抄写下方完整路径）=="
        in summary
    )
    assert "market.AKEUSDT.price: 0.009465" in summary
    assert "market.AKEUSDT.ret_1h: 3.94%" in summary
    assert "market.AKEUSDT.ret_24h: -10.32%" in summary
    assert "market.AKEUSDT.price_change_pct_24h: -8.67%" in summary
    assert "market.AKEUSDT.futures_premium_pct: 0.15%" in summary
    assert "market.AKEUSDT.onboard_date: 2025-09-26" in summary
    assert "market.AKEUSDT.boards: gain_1h、loss_24h、gain_7d" in summary
    assert "microstructure.AKEUSDT.ls_ratio_all: 0.52" in summary
    assert "microstructure.AKEUSDT.funding_avg: 0.000079" in summary
    assert "microstructure.AKEUSDT.funding_trend: flat" in summary

    no_snap = {"tokens": ["BTC"], "signals": {}}
    no_text = "\n".join(context._facts_summary_lines("BTC", no_snap))
    assert "扫描器快照" not in no_text


def test_mock_full_chain_keeps_scanner_snapshot(scanned_full_result):
    """验收：全链 state 透传 scanner_snapshot（分支摘要同源参考，⑧ 报告消费）。"""
    result = scanned_full_result
    snap = result["scanner_snapshot"]
    assert snap["date"] == "2026-08-16"
    assert snap["market"]["AKEUSDT"]["price"] == 0.009465
    assert snap["microstructure"]["AKEUSDT"]["funding_trend"] == "flat"
