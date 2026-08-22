"""schemas 测试：4 schema 宽容 validator + _extract_json 容错解析（06 票）。"""

from __future__ import annotations

import pytest

from strategy_research import schemas as s

# ── TokenAnalysis ────────────────────────────────────────


def test_token_analysis_no_max_loss_invalidation() -> None:
    """Q5 决策：无 max_loss / invalidation 字段（LLM 主观值不进确定性核验）。"""
    fields = set(s.TokenAnalysis.model_fields)
    assert "max_loss" not in fields
    assert "invalidation" not in fields
    assert "trade_structure" in fields


def test_token_analysis_whitelists() -> None:
    """direction/decision/horizon 白名单（大小写不敏感），非法置保守默认。"""
    assert s.TokenAnalysis.model_validate({"direction": "long"}).direction == "long"
    assert s.TokenAnalysis.model_validate({"direction": "Short"}).direction == "short"
    assert s.TokenAnalysis.model_validate({"direction": "flat"}).direction == ""
    assert s.TokenAnalysis.model_validate({"decision": "trade"}).decision == "TRADE"
    assert s.TokenAnalysis.model_validate({"decision": "watch"}).decision == "WATCH"
    assert s.TokenAnalysis.model_validate({"decision": "HOLD"}).decision == "PASS"
    assert s.TokenAnalysis.model_validate({"horizon": "short_term"}).horizon == "short_term"
    assert s.TokenAnalysis.model_validate({"horizon": "Trend"}).horizon == "trend"
    assert s.TokenAnalysis.model_validate({"horizon": "week"}).horizon == ""
    assert s.TokenAnalysis.model_validate({}).horizon == ""


def test_token_analysis_tolerant() -> None:
    """null / 非 dict / 全字段 null → 默认值（永不抛）；全字段正常构造可用。"""
    assert s.TokenAnalysis.model_validate(None).decision == "PASS"
    assert s.TokenAnalysis.model_validate("null").decision == "PASS"
    ta = s.TokenAnalysis.model_validate(
        {"decision": None, "confidence": None, "risks": None, "evidence": None}
    )
    assert ta.decision == "PASS" and ta.confidence == 0.0
    assert ta.risks == [] and ta.evidence == []
    assert s.TokenAnalysis.model_validate(42).decision == "PASS"  # 非 dict → 全默认
    # 正常构造（原 normal 断言并入）
    ta2 = s.TokenAnalysis.model_validate(
        {
            "symbol": "UNIUSDT",
            "decision": "TRADE",
            "direction": "long",
            "confidence": 0.8,
            "fundamental_thesis": "TVL 增长",
            "mispricing": "低估",
            "risks": ["解锁"],
            "evidence": [{"claim": "x", "source": "defillama", "timestamp": "t"}],
            "quadrant": "III",
            "trade_structure": "spot",
        }
    )
    assert ta2.decision == "TRADE" and ta2.direction == "long"
    assert ta2.confidence == pytest.approx(0.8)
    assert ta2.evidence[0].source == "defillama"


def test_token_analysis_cleansing() -> None:
    """confidence 归一 clamp 0-1 + evidence 条目宽容清洗（非 dict 丢弃）。"""
    assert s.TokenAnalysis.model_validate(
        {"confidence": "0.85"}
    ).confidence == pytest.approx(0.85)
    assert s.TokenAnalysis.model_validate(
        {"confidence": 1.5}
    ).confidence == pytest.approx(1.0)
    assert s.TokenAnalysis.model_validate(
        {"confidence": -0.2}
    ).confidence == pytest.approx(0.0)
    assert s.TokenAnalysis.model_validate(
        {"confidence": "abc"}
    ).confidence == pytest.approx(0.0)
    ta = s.TokenAnalysis.model_validate(
        {
            "evidence": [
                {"claim": "ok", "source": "bing"},
                "bad",
                {"claim": 123, "source": "not-whitelist"},
            ]
        }
    )
    assert len(ta.evidence) == 2
    assert ta.evidence[0].source == "bing"
    assert ta.evidence[1].claim == "123" and ta.evidence[1].source == ""


# ── FactItem ─────────────────────────────────────────────


def test_fact_item_whitelists() -> None:
    """变体字段归一 + direction/source 白名单，非法置保守默认。"""
    f = s.FactItem.model_validate(
        {"dimension": "基本面", "topic": "解锁", "direction": "neutral"}
    )
    assert f.dimension == "fundamentals" and f.topic == "unlock"
    f2 = s.FactItem.model_validate(
        {"dimension": "情绪", "topic": "社媒热度", "direction": "bull"}
    )
    assert f2.dimension == "sentiment" and f2.topic == "social"
    f3 = s.FactItem.model_validate({"dimension": "nonsense", "topic": "nonsense"})
    assert f3.dimension == "fundamentals" and f3.topic == "unknown"
    assert s.FactItem.model_validate({"direction": "Bull"}).direction == "bull"
    assert s.FactItem.model_validate({"direction": "up"}).direction == "neutral"
    assert s.FactItem.model_validate({"source": "defillama"}).source == "defillama"
    assert s.FactItem.model_validate({"source": "web_search"}).source == ""


def test_fact_item_tolerant() -> None:
    """null / 非 dict → 默认值，不抛异常。"""
    f = s.FactItem.model_validate(None)
    assert f.claim == "" and f.dimension == "fundamentals" and f.topic == "unknown"
    assert s.FactItem.model_validate([]).direction == "neutral"


# ── ChallengeItem / RebuttalItem ─────────────────────────


def test_challenge_rebuttal_whitelists() -> None:
    """stance/severity/outcome 白名单，非法置保守默认。"""
    c = s.ChallengeItem.model_validate({"stance": "Aggressive", "severity": "HIGH"})
    assert c.stance == "aggressive" and c.severity == "high"
    c2 = s.ChallengeItem.model_validate({"stance": "wild", "severity": "bad"})
    assert c2.stance == "conservative" and c2.severity == "medium"
    assert s.ChallengeItem.model_validate(None).claim == ""
    r = s.RebuttalItem.model_validate({"outcome": "Accepted"})
    assert r.outcome == "accepted"
    assert s.RebuttalItem.model_validate({"outcome": "maybe"}).outcome == "rebutted"


# ── _extract_json ────────────────────────────────────────


def test_extract_json_quirks() -> None:
    """杂质剥离：代码块围栏 / 前后文本；单引号键容错；尾部缺闭合 → 补闭合。"""
    text = '好的，结果如下：```json\n{"facts": [{"claim": "a"}]}\n``` 结束'
    assert s._extract_json(text) == {"facts": [{"claim": "a"}]}
    assert s._extract_json('前缀说明 {"facts": []} 后缀说明') == {"facts": []}
    assert s._extract_json("{'facts': []}") == {"facts": []}
    obj = s._extract_json('{"facts": [{"claim": "a"}]')
    assert isinstance(obj, dict)
    assert obj["facts"] == [{"claim": "a"}]


def test_extract_json_truncated() -> None:
    """截断三分支：键值对间取前缀 / 字符串中间补闭引号 / 非法值中间丢键保骨架。"""
    obj = s._extract_json('{"facts": [{"claim": "a"}, {"claim": "b"')
    assert isinstance(obj, dict)
    assert obj["facts"] == [{"claim": "a"}, {"claim": "b"}]
    assert s._extract_json('{"facts": [{"claim": "ab') == {"facts": [{"claim": "ab"}]}
    assert s._extract_json('{"facts": [{"claim": ab') == {"facts": [{}]}


def test_extract_json_no_json() -> None:
    """无 JSON 结构 → None；顶层数组可解析；外层对象不闭合时不回退内层数组。"""
    assert s._extract_json("模型只输出了文本") is None
    assert s._extract_json("") is None
    assert s._extract_json(None) is None
    assert s._extract_json(42) is None
    assert s._extract_json("[1, 2, 3]") == [1, 2, 3]  # 顶层数组也可解析
    obj = s._extract_json('{"facts": [{"claim": "a"}]')
    assert isinstance(obj, dict)  # 补闭合成功，而非返回内层列表
