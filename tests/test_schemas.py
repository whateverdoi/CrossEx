"""schemas 测试：4 schema 宽容 validator + _extract_json 容错解析（06 票）。"""

from __future__ import annotations

import pytest

from strategy_research import schemas as s

# ── TokenAnalysis ────────────────────────────────────────


def test_token_analysis_normal() -> None:
    """全字段正常构造。"""
    ta = s.TokenAnalysis.model_validate(
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
    assert ta.decision == "TRADE" and ta.direction == "long"
    assert ta.confidence == pytest.approx(0.8)
    assert ta.evidence[0].source == "defillama"


def test_token_analysis_no_max_loss_invalidation() -> None:
    """Q5 决策：无 max_loss / invalidation 字段（LLM 主观值不进确定性核验）。"""
    fields = set(s.TokenAnalysis.model_fields)
    assert "max_loss" not in fields
    assert "invalidation" not in fields
    assert "trade_structure" in fields


def test_token_analysis_direction_whitelist() -> None:
    """direction 白名单 long/short（大小写不敏感），非法置空。"""
    assert s.TokenAnalysis.model_validate({"direction": "long"}).direction == "long"
    assert s.TokenAnalysis.model_validate({"direction": "Short"}).direction == "short"
    assert s.TokenAnalysis.model_validate({"direction": "flat"}).direction == ""


def test_token_analysis_decision_whitelist() -> None:
    """decision 白名单 TRADE/WATCH/PASS（大小写不敏感），非法置 PASS（保守）。"""
    assert s.TokenAnalysis.model_validate({"decision": "trade"}).decision == "TRADE"
    assert s.TokenAnalysis.model_validate({"decision": "watch"}).decision == "WATCH"
    assert s.TokenAnalysis.model_validate({"decision": "HOLD"}).decision == "PASS"


def test_token_analysis_null_output_parses() -> None:
    """null 输出可解析不抛异常（整对象 null / 字段 null / "null" 字符串）。"""
    assert s.TokenAnalysis.model_validate(None).decision == "PASS"
    assert s.TokenAnalysis.model_validate("null").decision == "PASS"
    ta = s.TokenAnalysis.model_validate(
        {"decision": None, "confidence": None, "risks": None, "evidence": None}
    )
    assert ta.decision == "PASS" and ta.confidence == 0.0
    assert ta.risks == [] and ta.evidence == []


def test_token_analysis_confidence_clamped() -> None:
    """confidence 归一：数字/数字字符串，clamp 0-1，非法 → 0.0。"""
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


def test_token_analysis_evidence_tolerated() -> None:
    """evidence 条目：非 dict 丢弃，条目字段宽容清洗。"""
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


def test_token_analysis_garbage_input() -> None:
    """非 dict 输入 → 全默认（永不抛）。"""
    ta = s.TokenAnalysis.model_validate(42)
    assert ta.decision == "PASS" and ta.symbol == ""


# ── FactItem ─────────────────────────────────────────────


def test_fact_item_variants_normalized() -> None:
    """变体字段归一：dimension/topic 中英映射，非法置默认。"""
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


def test_fact_item_direction_whitelist() -> None:
    """direction 白名单 bull/bear/neutral，非法置 neutral。"""
    assert s.FactItem.model_validate({"direction": "Bull"}).direction == "bull"
    assert s.FactItem.model_validate({"direction": "up"}).direction == "neutral"


def test_fact_item_source_whitelist() -> None:
    """source 白名单：合法保留，非法清空（白名单过滤在装配层）。"""
    assert s.FactItem.model_validate({"source": "defillama"}).source == "defillama"
    assert s.FactItem.model_validate({"source": "web_search"}).source == ""


def test_fact_item_null_defaults() -> None:
    """null / 非 dict → 默认值，不抛异常。"""
    f = s.FactItem.model_validate(None)
    assert f.claim == "" and f.dimension == "fundamentals" and f.topic == "unknown"
    assert s.FactItem.model_validate([]).direction == "neutral"


# ── ChallengeItem / RebuttalItem ─────────────────────────


def test_challenge_item_stance_severity_whitelist() -> None:
    """stance 三视角 / severity 三档白名单，非法置保守默认。"""
    c = s.ChallengeItem.model_validate({"stance": "Aggressive", "severity": "HIGH"})
    assert c.stance == "aggressive" and c.severity == "high"
    c2 = s.ChallengeItem.model_validate({"stance": "wild", "severity": "bad"})
    assert c2.stance == "conservative" and c2.severity == "medium"
    assert s.ChallengeItem.model_validate(None).claim == ""


def test_rebuttal_item_outcome_whitelist() -> None:
    """outcome 白名单 rebutted/accepted，非法置 rebutted。"""
    r = s.RebuttalItem.model_validate({"outcome": "Accepted"})
    assert r.outcome == "accepted"
    assert s.RebuttalItem.model_validate({"outcome": "maybe"}).outcome == "rebutted"


# ── _extract_json ────────────────────────────────────────


def test_extract_json_fence() -> None:
    """代码块围栏（含 json 标注）剥离。"""
    text = '好的，结果如下：```json\n{"facts": [{"claim": "a"}]}\n``` 结束'
    obj = s._extract_json(text)
    assert obj == {"facts": [{"claim": "a"}]}


def test_extract_json_surrounding_text() -> None:
    """前后杂质文本剥离。"""
    obj = s._extract_json('前缀说明 {"facts": []} 后缀说明')
    assert obj == {"facts": []}


def test_extract_json_single_quote_keys() -> None:
    """单引号键容错。"""
    assert s._extract_json("{'facts': []}") == {"facts": []}


def test_extract_json_missing_close() -> None:
    """尾部缺闭合括号 → 补闭合修复（部分损坏容错）。"""
    obj = s._extract_json('{"facts": [{"claim": "a"}]')
    assert isinstance(obj, dict)
    assert obj["facts"] == [{"claim": "a"}]


def test_extract_json_truncated_recovers() -> None:
    """截断在完整键值对之间 → 截断修复取前缀。"""
    obj = s._extract_json('{"facts": [{"claim": "a"}, {"claim": "b"')
    assert isinstance(obj, dict)
    assert obj["facts"] == [{"claim": "a"}, {"claim": "b"}]


def test_extract_json_truncated_in_string_recovers() -> None:
    """截断在字符串中间：补闭引号 + 补全括号 → 完整恢复。"""
    obj = s._extract_json('{"facts": [{"claim": "ab')
    assert obj == {"facts": [{"claim": "ab"}]}


def test_extract_json_truncated_bad_value_recovers_skeleton() -> None:
    """截断在非法值中间（无引号）：丢键保留对象骨架，装配层坏条目丢弃。"""
    obj = s._extract_json('{"facts": [{"claim": ab')
    assert obj == {"facts": [{}]}


def test_extract_json_no_json() -> None:
    """无 JSON 结构 → None。"""
    assert s._extract_json("模型只输出了文本") is None
    assert s._extract_json("") is None
    assert s._extract_json(None) is None
    assert s._extract_json(42) is None


def test_extract_json_array() -> None:
    """顶层数组结构也可解析。"""
    assert s._extract_json("[1, 2, 3]") == [1, 2, 3]


def test_extract_json_no_inner_fallback() -> None:
    """外层对象不闭合时不回退内层数组（契约是对象，.get 不崩溃）。"""
    obj = s._extract_json('{"facts": [{"claim": "a"}]')
    assert isinstance(obj, dict)  # 补闭合成功，而非返回内层列表
