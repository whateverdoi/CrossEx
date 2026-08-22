"""08 票验收：⑦ risk_check 确定性风控（EV 边界 + 组合集中度，只降不升）。

纯函数无 IO：全部用例直接构造 state 调用 nodes.risk_check；
mock 全链集成用例验证真实装配下 risk_check 与 final_decisions 一致。
"""

from __future__ import annotations

import pytest

from strategy_research import nodes
from strategy_research.graph import build_graph

MOCK_TOKENS = ["BTC", "ETH", "SOL", "UNI", "DOGE", "XRP"]


def _sig(momentum=None, quadrant=None) -> dict:
    """信号子集（仅 EV 核验读取的字段）。"""
    return {
        "momentum": {"value": momentum},
        "divergence": {"value": {"quadrant": quadrant}},
    }


def _trade(symbol: str, direction: str = "long", confidence: float = 0.7) -> dict:
    return {
        "symbol": symbol,
        "decision": "TRADE",
        "direction": direction,
        "confidence": confidence,
    }


def _mk_state(decisions: dict, signals: dict | None = None) -> dict:
    """构造 risk_check 入参：tokens 按 decisions 顺序，rebuttals 空。"""
    return {
        "tokens": list(decisions),
        "final_decisions": {
            s: {"analysis": d, "rebuttals": []} for s, d in decisions.items()
        },
        "signals": signals or {},
    }


def test_non_trade_skipped_no_flags():
    """验收：非 TRADE 跳过核验——PASS/WATCH 不产生 flags，决策原样透传。"""
    state = _mk_state(
        {
            "UNI": {"symbol": "UNI", "decision": "PASS", "confidence": 0.0},
            "SOL": {"symbol": "SOL", "decision": "WATCH", "direction": "long"},
        }
    )
    out = nodes.risk_check(state)
    assert out["risk_flags"] == {"UNI": [], "SOL": []}
    assert out["results"][0]["decision"] == "PASS"
    assert out["results"][1]["decision"] == "WATCH"


def test_long_ev_flag_when_momentum_negative():
    """验收：多头 EV 矛盾——momentum<0 → flag 并降级。"""
    state = _mk_state({"BTC": _trade("BTC", "long")}, {"BTC": _sig(momentum=-5.0)})
    out = nodes.risk_check(state)
    assert out["risk_flags"]["BTC"] == ["EV 不足: 动量/背离信号与多头决策矛盾"]
    a = out["results"][0]
    assert a["decision"] == "WATCH"
    assert a["downgraded"] == out["risk_flags"]["BTC"]


def test_long_ev_pass_momentum_positive():
    """对照组：多头 momentum>=0 → 无 flag，保持 TRADE。"""
    state = _mk_state({"BTC": _trade("BTC", "long")}, {"BTC": _sig(momentum=5.0)})
    out = nodes.risk_check(state)
    assert out["risk_flags"]["BTC"] == []
    assert out["results"][0]["decision"] == "TRADE"


def test_long_ev_pass_quadrant_iii():
    """对照组：多头 momentum 缺失但 quadrant=III → 背离兜底通过。"""
    state = _mk_state({"BTC": _trade("BTC", "long")}, {"BTC": _sig(quadrant="III")})
    assert nodes.risk_check(state)["risk_flags"]["BTC"] == []


def test_short_ev_flag_when_momentum_positive():
    """验收：空头 EV 矛盾——momentum>=0 → flag 并降级。"""
    state = _mk_state({"ETH": _trade("ETH", "short")}, {"ETH": _sig(momentum=5.0)})
    out = nodes.risk_check(state)
    assert out["risk_flags"]["ETH"] == ["EV 不足: 动量/背离信号与空头决策矛盾"]
    assert out["results"][0]["decision"] == "WATCH"


def test_short_ev_pass_quadrant_ii():
    """对照组：空头 quadrant=II → 通过。"""
    state = _mk_state({"ETH": _trade("ETH", "short")}, {"ETH": _sig(quadrant="II")})
    assert nodes.risk_check(state)["risk_flags"]["ETH"] == []


def test_short_quadrant_iv_not_allowed():
    """验收：IV 双弱不做空——空头 + quadrant=IV → flag。"""
    state = _mk_state({"ETH": _trade("ETH", "short")}, {"ETH": _sig(quadrant="IV")})
    out = nodes.risk_check(state)
    assert out["risk_flags"]["ETH"] == ["EV 不足: 动量/背离信号与空头决策矛盾"]
    assert out["results"][0]["decision"] == "WATCH"


def test_direction_missing_flags():
    """验收：TRADE 未声明方向 → flag（未声明方向）。"""
    d = _trade("BTC", direction="")
    state = _mk_state({"BTC": d}, {"BTC": _sig(momentum=5.0)})
    out = nodes.risk_check(state)
    assert out["risk_flags"]["BTC"] == ["EV 不足: 动量/背离信号与未声明方向决策矛盾"]


def test_concentration_gt2_flags_all_trades():
    """验收：集中度>2——3 个 TRADE 全部 flag 并降级（两遍扫描先统计后标记）。"""
    state = _mk_state(
        {
            "BTC": _trade("BTC", "long"),
            "ETH": _trade("ETH", "short"),
            "SOL": _trade("SOL", "long"),
        },
        {
            "BTC": _sig(momentum=5.0),
            "ETH": _sig(quadrant="II"),
            "SOL": _sig(momentum=3.0),
        },
    )
    out = nodes.risk_check(state)
    expected = ["组合集中度超限: 批内 TRADE 数 = 3"]
    for s in ("BTC", "ETH", "SOL"):
        assert out["risk_flags"][s] == expected
        assert out["results"][list(state["tokens"]).index(s)]["decision"] == "WATCH"
        assert out["results"][list(state["tokens"]).index(s)]["downgraded"] == expected


def test_concentration_boundary_2_no_flag():
    """边界：恰好 2 个 TRADE → 无集中度 flag。"""
    state = _mk_state(
        {"BTC": _trade("BTC", "long"), "ETH": _trade("ETH", "short")},
        {"BTC": _sig(momentum=5.0), "ETH": _sig(quadrant="II")},
    )
    out = nodes.risk_check(state)
    assert out["risk_flags"] == {"BTC": [], "ETH": []}
    assert all(r["decision"] == "TRADE" for r in out["results"])


def test_signals_missing_skips_ev():
    """规格失败矩阵：signals 缺失 → EV 核验跳过（缺数据不等于矛盾，不误伤）。"""
    state = _mk_state({"BTC": _trade("BTC", "long")}, signals={})
    out = nodes.risk_check(state)
    assert out["risk_flags"]["BTC"] == []
    assert out["results"][0]["decision"] == "TRADE"


def test_signals_error_entry_skips_ev():
    """失败矩阵边界：signals 为错误条目（信号层失败）→ EV 跳过，不误判矛盾。"""
    state = _mk_state(
        {"BTC": _trade("BTC", "long")},
        {"BTC": {"symbol": "BTC", "error": "信号计算失败"}},
    )
    out = nodes.risk_check(state)
    assert out["risk_flags"]["BTC"] == []
    assert out["results"][0]["decision"] == "TRADE"


def test_concentration_applies_without_signals():
    """集中度核验不依赖 signals：3 TRADE 无 signals → 仅集中度降级。"""
    state = _mk_state(
        {"BTC": _trade("BTC"), "ETH": _trade("ETH"), "SOL": _trade("SOL")},
        signals={},
    )
    out = nodes.risk_check(state)
    for s in ("BTC", "ETH", "SOL"):
        assert out["risk_flags"][s] == ["组合集中度超限: 批内 TRADE 数 = 3"]


def test_downgrade_only_lowers_keeps_confidence():
    """只降不升：降级仅 decision→WATCH + downgraded 标记，其余字段不动。"""
    d = _trade("BTC", "long", confidence=0.8)
    d["trade_structure"] = "分批建仓"
    state = _mk_state({"BTC": d}, {"BTC": _sig(momentum=-5.0)})
    a = nodes.risk_check(state)["results"][0]
    assert a["decision"] == "WATCH"
    assert a["confidence"] == 0.8  # 置信度不被风控改写
    assert a["trade_structure"] == "分批建仓"
    assert "downgraded" in a


def test_results_derivation_structure():
    """验收：results 派生 = 分析对象 + rebuttals + risk_flags（level 属 ⑧/09 票）。"""
    state = _mk_state(
        {
            "BTC": _trade("BTC", "long"),
            "UNI": {"symbol": "UNI", "decision": "PASS", "confidence": 0.0},
        },
        {"BTC": _sig(momentum=-5.0)},
    )
    out = nodes.risk_check(state)
    btc = out["results"][0]
    # 降级后的分析对象：decision 改写 + downgraded 标记 + rebuttals/risk_flags 拼接
    assert btc["symbol"] == "BTC"
    assert btc["decision"] == "WATCH"
    assert btc["downgraded"] == ["EV 不足: 动量/背离信号与多头决策矛盾"]
    assert btc["rebuttals"] == []
    assert btc["risk_flags"] == btc["downgraded"]
    uni = out["results"][1]
    assert uni == {
        "symbol": "UNI",
        "decision": "PASS",
        "confidence": 0.0,
        "rebuttals": [],
        "risk_flags": [],
        "signal_state": {
            "momentum": None,
            "quadrant": None,
            "funding_pctile_90d": None,
            "oi_price_divergence": None,
        },
    }
    # 确定性信号状态落盘（04 票）：BTC 有动量，quadrant 缺 → None（UNKNOWN 纪律）
    assert btc["signal_state"]["momentum"] == -5.0
    assert btc["signal_state"]["quadrant"] is None


def test_batch_never_breaks_missing_final():
    """验收：批处理永不中断——final_decisions 缺 token 也不中断，全 tokens 有结果。"""
    state = _mk_state({"BTC": _trade("BTC", "long")}, {"BTC": _sig(momentum=5.0)})
    state["tokens"] = ["BTC", "ZZZ"]
    out = nodes.risk_check(state)
    assert out["risk_flags"] == {"BTC": [], "ZZZ": []}
    assert len(out["results"]) == 2
    assert out["results"][1] == {
        "rebuttals": [],
        "risk_flags": [],
        "signal_state": {
            "momentum": None,
            "quadrant": None,
            "funding_pctile_90d": None,
            "oi_price_divergence": None,
        },
    }


def test_mock_full_chain_risk_check_consistent():
    """集成（SR_MOCK=1）：finalize 后无 TRADE → risk_check 空转，results 与终审一致。"""
    result = build_graph().invoke({"tokens": MOCK_TOKENS, "meta": {}})
    assert all(v == [] for v in result["risk_flags"].values())
    for s, item in result["final_decisions"].items():
        row = result["results"][result["tokens"].index(s)]
        assert row["symbol"] == s
        assert row["decision"] == item["analysis"]["decision"]
        assert row["rebuttals"] == item["rebuttals"]
        assert row["risk_flags"] == []


if __name__ == "__main__":
    pytest.main([__file__, "-q"])


def test_signal_state_full_values():
    """04 票：信号状态全字段落盘（momentum/quadrant/funding 分位/OI 背离标签）。"""
    state = _mk_state(
        {"BTC": _trade("BTC", "long")},
        {
            "BTC": {
                "momentum": {"value": 3.5},
                "divergence": {"value": {"quadrant": "III"}},
            }
        },
    )
    state["market_data"] = {"BTC": {"funding_pctile_90d": {"value": 85.0}}}
    state["microstructure_data"] = {
        "BTC": {
            "oi_price_divergence": {
                "value": {"label": "confirm_long", "note": "价涨 OI 增"}
            }
        }
    }
    out = nodes.risk_check(state)
    assert out["results"][0]["signal_state"] == {
        "momentum": 3.5,
        "quadrant": "III",
        "funding_pctile_90d": 85.0,
        "oi_price_divergence": "confirm_long",
    }


def test_signal_state_error_entry_all_none():
    """信号层失败（error 条目）→ 信号状态全 None（缺数据不等于矛盾）。"""
    state = _mk_state(
        {"BTC": _trade("BTC", "long")},
        {"BTC": {"symbol": "BTC", "error": "信号计算异常"}},
    )
    out = nodes.risk_check(state)
    assert all(v is None for v in out["results"][0]["signal_state"].values())
