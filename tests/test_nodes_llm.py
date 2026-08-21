"""07 票验收：③-⑥ 四节点 LLM 链（mock 假模型全链 + 摘要预筛 + 异常注入）。

mock 模式 get_llm 返回确定性假模型（env._MockChatModel），LLM 调用计数走
env._MOCK_CALL_COUNTS（按 prompt 特征分类），PASS 透传零调用可验证。
"""

from __future__ import annotations

import pytest

from strategy_research import env, nodes
from strategy_research.graph import build_graph
from strategy_research.schemas import TokenAnalysis

MOCK_TOKENS = ["BTC", "ETH", "SOL", "UNI", "DOGE", "XRP"]
_SOURCE_WHITELIST = ("binance", "binance_futures", "defillama", "bing", "mock")
_DIMENSIONS = ("fundamentals", "market", "sentiment", "news")
_TOPICS = (
    "project",
    "team",
    "social",
    "adoption",
    "unlock",
    "catalyst",
    "news",
    "unknown",
)
_STANCES = ("aggressive", "conservative", "neutral")
_SEVERITIES = ("high", "medium", "low")


def _reset_counts() -> None:
    for k in env._MOCK_CALL_COUNTS:
        env._MOCK_CALL_COUNTS[k] = 0


@pytest.fixture(scope="module")
def full_result():
    """mock 全链一次（6 token），供多个验收断言复用。"""
    _reset_counts()
    return build_graph().invoke({"tokens": MOCK_TOKENS, "meta": {}})


def test_mock_facts_nonempty_with_dimension_topic(full_result):
    """mock 模式产出非空 facts；每条含 dimension/topic 且 source 在白名单。"""
    facts = full_result["facts"]
    for s in MOCK_TOKENS:
        assert facts[s], f"{s} facts 为空（验收：mock 非空）"
        for f in facts[s]:
            assert f["claim"]
            assert f["source"] in _SOURCE_WHITELIST
            assert f["direction"] in ("bull", "bear", "neutral")
            assert f["dimension"] in _DIMENSIONS
            assert f["topic"] in _TOPICS


def test_mock_decide_fields_match_tokenanalysis(full_result):
    """decide 字段与 TokenAnalysis 一致；TRADE 必含 direction（验收）。"""
    for s in MOCK_TOKENS:
        d = full_result["decisions"][s]
        assert TokenAnalysis.model_validate(d).model_dump() == d
        if d["decision"] == "TRADE":
            assert d["direction"] in ("long", "short")
            assert d["trade_structure"]


def test_mock_pass_path_zero_llm_calls(full_result):
    """PASS 路径 ③+④ 共 2 次；⑤⑥ 只对非 PASS（BTC/ETH/SOL）调用（验收）。"""
    c = env._MOCK_CALL_COUNTS
    assert c["facts"] == 6
    assert c["decide"] == 6
    assert c["challenge"] == 3  # BTC/ETH/SOL 非 PASS
    assert c["rebuttals"] == 3
    # 透传产物：PASS token 无挑战、无复审回应
    for s in ("UNI", "DOGE", "XRP"):
        assert full_result["challenges"][s] == []
        assert full_result["final_decisions"][s]["rebuttals"] == []


def test_mock_challenge_max3_with_stance(full_result):
    """非 PASS 挑战 ≤3 条，含 claim/evidence/severity/stance。"""
    for s in ("BTC", "ETH", "SOL"):
        chs = full_result["challenges"][s]
        assert 0 < len(chs) <= 3
        for c in chs:
            assert c["claim"] and c["evidence"]
            assert c["severity"] in _SEVERITIES
            assert c["stance"] in _STANCES


_MIXED_FACTS = [
    {
        "claim": "bull 事实 A",
        "direction": "bull",
        "dimension": "market",
        "topic": "unknown",
        "source": "binance",
        "timestamp": "t",
    },
    {
        "claim": "bear 事实 B",
        "direction": "bear",
        "dimension": "news",
        "topic": "unlock",
        "source": "bing",
        "timestamp": "t",
    },
]


def test_challenge_prescreen_short_takes_bull_facts():
    """⑤ 反方预筛：空头决策只取 bull facts（验收：空头用例）。"""
    state = {
        "tokens": ["ETH"],
        "decisions": {
            "ETH": {"symbol": "ETH", "decision": "TRADE", "direction": "short"}
        },
        "facts": {"ETH": _MIXED_FACTS},
        "signals": {},
    }
    summary = nodes._build_challenge_summary("ETH", state)
    assert "bull 事实 A" in summary
    assert "bear 事实 B" not in summary


def test_challenge_prescreen_long_takes_bear_facts():
    """⑤ 反方预筛：多头决策只取 bear facts（对称用例）。"""
    state = {
        "tokens": ["BTC"],
        "decisions": {
            "BTC": {"symbol": "BTC", "decision": "TRADE", "direction": "long"}
        },
        "facts": {"BTC": _MIXED_FACTS},
        "signals": {},
    }
    summary = nodes._build_challenge_summary("BTC", state)
    assert "bear 事实 B" in summary
    assert "bull 事实 A" not in summary


def test_finalize_accepted_only_downgrade():
    """⑥ accepted 只降不升：TRADE→WATCH、置信度 -0.1、挑战并入 risks。"""
    state = {
        "tokens": ["BTC"],
        "decisions": {
            "BTC": {
                "symbol": "BTC",
                "decision": "TRADE",
                "direction": "long",
                "confidence": 0.7,
                "risks": [],
            }
        },
        "challenges": {
            "BTC": [
                {
                    "claim": "c1",
                    "evidence": "e1",
                    "severity": "high",
                    "refutes": "",
                    "stance": "conservative",
                }
            ]
        },
        "signals": {},
    }
    out = nodes.finalize(state)
    item = out["final_decisions"]["BTC"]
    # mock rebuttals 固定 1 accepted + 1 rebutted
    assert {r["outcome"] for r in item["rebuttals"]} == {"accepted", "rebutted"}
    assert item["analysis"]["decision"] == "WATCH"
    assert item["analysis"]["confidence"] == 0.6
    assert item["analysis"]["risks"] == ["承认解锁抛压风险，纳入风险清单并下调置信度"]


def test_finalize_accepted_keeps_watch_not_upgrade():
    """accepted 只降不升边界：WATCH 不反向升级，置信度仍减。"""
    state = {
        "tokens": ["SOL"],
        "decisions": {
            "SOL": {
                "symbol": "SOL",
                "decision": "WATCH",
                "direction": "long",
                "confidence": 0.5,
                "risks": [],
            }
        },
        "challenges": {
            "SOL": [
                {
                    "claim": "c1",
                    "evidence": "e1",
                    "severity": "low",
                    "refutes": "",
                    "stance": "neutral",
                }
            ]
        },
        "signals": {},
    }
    out = nodes.finalize(state)
    a = out["final_decisions"]["SOL"]["analysis"]
    assert a["decision"] == "WATCH"
    assert a["confidence"] == 0.4


def _boom(*args, **kwargs):
    raise RuntimeError("模拟断网：模型不可用")


def test_challenge_exception_does_not_break_batch(monkeypatch):
    """异常注入（断网跑 challenge）：非 PASS 挑战置空，批不中断（验收）。"""
    monkeypatch.setattr(env, "get_llm", _boom)
    state = {
        "tokens": ["BTC", "UNI"],
        "decisions": {
            "BTC": {
                "symbol": "BTC",
                "decision": "TRADE",
                "direction": "long",
                "confidence": 0.7,
            },
            "UNI": {"symbol": "UNI", "decision": "PASS", "confidence": 0.0},
        },
        "facts": {},
        "signals": {},
    }
    out = nodes.challenge(state)
    assert out["challenges"] == {"BTC": [], "UNI": []}


def test_decide_exception_fallback_pass(monkeypatch):
    """④ 异常注入：json_mode 失败 → PASS 兜底 + fallback 记录。"""
    monkeypatch.setattr(env, "get_llm", _boom)
    state = {"tokens": ["BTC"], "facts": {}, "signals": {}}
    out = nodes.decide(state)
    d = out["decisions"]["BTC"]
    assert d["decision"] == "PASS"
    assert d["confidence"] == 0.0
    assert d["fallback"] == "json_mode"
    assert "LLM 分析失败" in d["error"]
