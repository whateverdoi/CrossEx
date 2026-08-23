"""02 票：证据 schema 契约 + evidence_verify 确定性核验纯函数测试。

schema 契约：EvidenceItem = claim + basis(domain/field/value 三元组) + source，
无 confidence 字段；宽容解析（坏输入归一不抛异常）。
核验契约：basis 逐级解引用存在且值一致 → 通过；否则剔除并留痕（claim + reason）。
"""

from __future__ import annotations

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from strategy_research import evidence as ev
from strategy_research import nodes
from strategy_research.datasources.mock import MOCK_TOKENS
from strategy_research.evidence import BranchOutput, EvidenceItem


def _ev(
    claim: str = "c",
    domain: str = "signals",
    field: str = "momentum.value",
    value: str = "6.25",
    source: str = "signals",
) -> dict:
    """构造一条证据（默认引用 mock 恒定动量分，核验必过）。"""
    return {
        "claim": claim,
        "basis": {"domain": domain, "field": field, "value": value},
        "source": source,
    }


def _state() -> dict:
    """最小快照：覆盖全部数据域 + 嵌套路径 + None 值 + 扫描器快照。"""
    return {
        "signals": {
            "BTC": {
                "momentum": {"value": 6.25},
                "divergence": {"value": {"quadrant": "III"}},
            }
        },
        "fundamental_data": {
            "BTC": {"tvl": {"value": 1007.0}, "tvl_trend_30d": {"value": "rising"}}
        },
        "market_data": {"BTC": {"price": {"value": 70000.0}}},
        "microstructure_data": {"BTC": {"oi_change_24h": {"value": 0.0}}},
        "web_data": {"BTC": {"items": None}},
        "scanner_snapshot": {"market": {"BTC": {"price": 70000.0}}},
    }


# ── schema 契约 ───────────────────────────────────────────


def test_evidence_item_schema_contract() -> None:
    """契约：claim + basis 三元组 + source；无 confidence 字段（D3/D4 决策）。"""
    item = EvidenceItem.model_validate(
        {
            "claim": "TVL 30d 趋势上升",
            "basis": {
                "domain": "fundamental_data",
                "field": "tvl_trend_30d",
                "value": "rising",
            },
            "source": "fundamental_data",
        }
    )
    assert item.claim == "TVL 30d 趋势上升"
    assert item.basis.domain == "fundamental_data"
    assert item.basis.field == "tvl_trend_30d"
    assert item.basis.value == "rising"
    assert item.source == "fundamental_data"
    assert not hasattr(item, "confidence")  # 置信度已退役
    out = BranchOutput.model_validate({"evidence": [item.model_dump()]})
    assert len(out.evidence) == 1 and out.evidence[0] == item


def test_evidence_item_tolerant_parse() -> None:
    """宽容解析：坏输入归一不抛异常（字段漂移/非 dict basis → 默认值）。"""
    item = EvidenceItem.model_validate({"claim": "x", "basis": "oops", "source": 123})
    assert item.claim == "x"
    assert item.basis.domain == "" and item.basis.field == "" and item.basis.value == ""
    assert item.source == "123"
    assert EvidenceItem.model_validate(None).basis.domain == ""
    assert BranchOutput.model_validate({"evidence": None}).evidence == []
    assert BranchOutput.model_validate("oops").evidence == []
    item2 = EvidenceItem.model_validate(
        {"claim": None, "basis": {"domain": "d", "field": "f", "value": None}}
    )
    assert item2.claim == ""
    assert item2.basis.value == ""


# ── verify_evidence 纯函数 ────────────────────────────────


def test_verify_passes_valid_basis() -> None:
    """合法 basis：解引用存在且值一致 → 全部通过，无剔除。"""
    bull = [
        _ev(claim="动量分为正"),
        _ev(claim="象限 III", field="divergence.value.quadrant", value="III"),
    ]
    bear = [
        _ev(
            claim="OI 无增量",
            domain="microstructure_data",
            field="oi_change_24h.value",
            value="0.0",
        )
    ]
    verified, rejected = ev.verify_evidence({"BTC": bull}, {"BTC": bear}, _state())
    assert verified["BTC"]["bull_case"] == bull
    assert verified["BTC"]["bear_case"] == bear
    assert rejected == {}


def test_verify_rejects_unknown_domain() -> None:
    """数据域未知（如 onchain 未接入）：剔除留痕，不中断。"""
    bull = [_ev(domain="onchain", field="tvl", value="1.0")]
    verified, rejected = ev.verify_evidence({"BTC": bull}, {}, _state())
    assert verified["BTC"]["bull_case"] == []
    assert rejected["BTC"] == [{"claim": "c", "reason": "basis 数据域未知: onchain"}]


def test_verify_rejects_missing_field() -> None:
    """字段路径不存在：剔除留痕。"""
    bull = [_ev(field="nonexistent.value")]
    verified, rejected = ev.verify_evidence({"BTC": bull}, {}, _state())
    assert verified["BTC"]["bull_case"] == []
    assert rejected["BTC"][0]["reason"] == "字段不存在: nonexistent.value"


def test_verify_rejects_value_mismatch() -> None:
    """值不一致（引用 6.35 vs 快照 6.25，差 0.10 超出 0.05 容差）：剔除留痕含双方值。"""
    bull = [_ev(value="6.35")]
    verified, rejected = ev.verify_evidence({"BTC": bull}, {}, _state())
    assert verified["BTC"]["bull_case"] == []
    assert rejected["BTC"][0]["reason"] == "值不一致: 引用 6.35 vs 快照 6.25"


def test_verify_value_tolerance() -> None:
    """数值宽容：浮点精度差与整数等价写法通过（1007.0 vs "1007"）。"""
    bull = [_ev(domain="fundamental_data", field="tvl.value", value="1007")]
    verified, _ = ev.verify_evidence({"BTC": bull}, {}, _state())
    assert len(verified["BTC"]["bull_case"]) == 1
    bad = [
        _ev(domain="fundamental_data", field="tvl.value", value="1008")
    ]  # 绝对差 1.0，超 0.05 容差
    verified, rejected = ev.verify_evidence({"BTC": bad}, {}, _state())
    assert verified["BTC"]["bull_case"] == []
    assert rejected["BTC"][0]["reason"].startswith("值不一致")


def test_verify_numeric_render_tolerance() -> None:
    """渲染精度容差（06 票）：LLM 引用摘要渲染值（2 位小数等）允许小误差，防误剔。"""
    bull = [_ev(domain="fundamental_data", field="tvl.value", value="1007.001")]
    verified, rejected = ev.verify_evidence({"BTC": bull}, {}, _state())
    assert len(verified["BTC"]["bull_case"]) == 1
    assert rejected == {}


def test_verify_percent_suffix_tolerated() -> None:
    """% 后缀宽容（06 票）：摘要把百分比渲染成 6.62%，LLM 逐字引用带 %，比较前剥离。"""
    bull = [_ev(value="6.25%")]
    verified, rejected = ev.verify_evidence({"BTC": bull}, {}, _state())
    assert len(verified["BTC"]["bull_case"]) == 1
    assert rejected == {}
    bad = [_ev(value="6.40%")]  # 剥 % 后差 0.15，超 0.05 容差 → 剔除
    verified, rejected = ev.verify_evidence({"BTC": bad}, {}, _state())
    assert verified["BTC"]["bull_case"] == []
    assert rejected["BTC"][0]["reason"].startswith("值不一致")


def test_verify_legacy_field_prefix_stripped() -> None:
    """旧域前缀宽容（06 票）：sentiment.momentum.value → 剥首段重试后通过。"""
    bull = [_ev(field="sentiment.momentum.value")]
    verified, rejected = ev.verify_evidence({"BTC": bull}, {}, _state())
    assert len(verified["BTC"]["bull_case"]) == 1
    assert rejected == {}


def test_verify_legacy_prefix_still_missing_rejected() -> None:
    """旧前缀剥离后仍不存在的路径：照剔（divergence.divergence_7d 是 LLM 幻觉字段）。"""
    bull = [_ev(field="divergence.divergence_7d")]
    verified, rejected = ev.verify_evidence({"BTC": bull}, {}, _state())
    assert verified["BTC"]["bull_case"] == []
    assert rejected["BTC"][0]["reason"].startswith("字段不存在")


def test_verify_list_index_path() -> None:
    """列表索引段（06 票）：items[0].title 解引用 web_data 新闻标题。"""
    st = _state()
    st["web_data"] = {
        "BTC": {"items": [{"title": "x", "date": "2026-08-01", "source": "s"}]}
    }
    bull = [_ev(domain="web_data", field="items[0].title", value="x")]
    verified, rejected = ev.verify_evidence({"BTC": bull}, {}, st)
    assert len(verified["BTC"]["bull_case"]) == 1
    assert rejected == {}
    bad = [_ev(domain="web_data", field="items[5].title", value="x")]  # 越界
    verified, rejected = ev.verify_evidence({"BTC": bad}, {}, st)
    assert verified["BTC"]["bull_case"] == []
    assert rejected["BTC"][0]["reason"].startswith("字段不存在")


def test_verify_wrapped_value_drilldown() -> None:
    """数据点包装自动下钻（06 票）：field 漏 .value 后缀（momentum）仍可核验通过。"""
    bull = [_ev(field="momentum", value="6.25")]
    verified, rejected = ev.verify_evidence({"BTC": bull}, {}, _state())
    assert len(verified["BTC"]["bull_case"]) == 1
    assert rejected == {}


def test_verify_none_value_rejected() -> None:
    """快照值为 None（UNKNOWN）：不可作为证据引用，剔除。"""
    bull = [_ev(domain="web_data", field="items", value="UNKNOWN")]
    verified, rejected = ev.verify_evidence({"BTC": bull}, {}, _state())
    assert verified["BTC"]["bull_case"] == []
    assert rejected["BTC"][0]["reason"].startswith("值不一致")


def test_verify_scanner_snapshot_domain() -> None:
    """scanner_snapshot 域：field 自带完整路径（market.BTC.price），不做 symbol 注入。"""
    bull = [_ev(domain="scanner_snapshot", field="market.BTC.price", value="70000.0")]
    verified, rejected = ev.verify_evidence({"BTC": bull}, {}, _state())
    assert len(verified["BTC"]["bull_case"]) == 1
    assert rejected == {}


def test_verify_claim_invented_number_rejected() -> None:
    """claim 含输入中不存在的数值（编造，如 99.99）：剔除留痕（07 票弱检查）。"""
    bull = [_ev(claim="7日涨幅达 99.99%，强势")]
    verified, rejected = ev.verify_evidence({"BTC": bull}, {}, _state())
    assert verified["BTC"]["bull_case"] == []
    assert rejected["BTC"][0]["reason"] == "claim 含输入中不存在的数值: 99.99"


def test_verify_claim_visible_number_passed() -> None:
    """claim 数值来自输入（渲染精度截断/整数等价）：通过不剔除。"""
    bull = [
        _ev(claim="动量分 6.25 处于增长区"),
        _ev(
            claim="价格 70000.0 美元",
            domain="market_data",
            field="price.value",
            value="70000.0",
        ),  # 摘要渲染 70000.00 → 数值近似
        _ev(
            claim="过去24小时 OI 无增量",
            domain="microstructure_data",
            field="oi_change_24h.value",
            value="0.0",
        ),  # 24 命中路径数字
    ]
    verified, rejected = ev.verify_evidence({"BTC": bull}, {}, _state())
    assert len(verified["BTC"]["bull_case"]) == 3
    assert rejected == {}


def test_verify_claim_negative_number_passed() -> None:
    """claim 负数值（含 % 后缀）来自输入：通过（-11.95 vs 摘要 -11.95）。"""
    st = _state()
    st["signals"]["BTC"]["divergence"]["value"]["divergence_7d"] = -11.95
    bull = [
        _ev(
            claim="7日背离 -11.95%，动能减弱",
            field="divergence.value.divergence_7d",
            value="-11.95",
        )
    ]
    verified, rejected = ev.verify_evidence({"BTC": bull}, {}, st)
    assert len(verified["BTC"]["bull_case"]) == 1
    assert rejected == {}
    bad = [
        _ev(
            claim="7日背离 -88.88%，动能减弱",
            field="divergence.value.divergence_7d",
            value="-11.95",
        )
    ]
    verified, rejected = ev.verify_evidence({"BTC": bad}, {}, st)
    assert verified["BTC"]["bull_case"] == []
    assert rejected["BTC"][0]["reason"].startswith("claim 含输入中不存在的数值")


def test_verify_claim_formatted_number_passed() -> None:
    """claim 数值格式宽容（07 票）：千分位逗号 / 负值绝对值表述 / 单位换算。"""
    st = _state()
    st["market_data"]["BTC"]["change_24h"] = {"value": -15.59}
    bull = [
        _ev(
            claim="TVL 达 1,007.00 美元，资金充裕",
            domain="fundamental_data",
            field="tvl.value",
            value="1007.0",
        ),  # 千分位：1,007.00 ↔ 1007.0
        _ev(
            claim="24小时下跌 15.59%",
            domain="market_data",
            field="change_24h.value",
            value="-15.59",
        ),  # 负值绝对值表述：15.59 ↔ -15.59
        _ev(
            claim="市值约 7,000.0 万",
            domain="market_data",
            field="price.value",
            value="70000.0",
        ),  # 单位换算：70000 × 10⁻¹
    ]
    verified, rejected = ev.verify_evidence({"BTC": bull}, {}, st)
    assert len(verified["BTC"]["bull_case"]) == 3
    assert rejected == {}


def test_verify_duplicate_claim_rejected() -> None:
    """同一事实拆条凑数（claim 相同）：第二条剔除留痕（07 票）。"""
    st = _state()
    st["signals"]["BTC"]["divergence"]["value"]["divergence_7d"] = 34.57
    claim = "30日背离 34.57 处于正向象限，估值偏低"
    bull = [
        _ev(claim=claim, field="divergence.value.divergence_7d", value="34.57"),
        _ev(
            claim=claim, field="divergence.value.divergence_7d", value="34.57"
        ),  # 同 claim 重复
    ]
    verified, rejected = ev.verify_evidence({"BTC": bull}, {}, st)
    assert len(verified["BTC"]["bull_case"]) == 1
    assert rejected["BTC"] == [
        {"claim": claim, "reason": "重复 claim（同一事实拆条凑数）"}
    ]


def test_verify_duplicate_claim_whitespace_insensitive() -> None:
    """claim 仅空白/标点差异视为同一事实：去重；不同事实保留。"""
    bull = [
        _ev(claim="动量分 6.25 处于增长区"),
        _ev(claim="动量分6.25处于增长区"),  # 去空白后与前一条相同
        _ev(claim="TVL 稳步增长"),
    ]
    verified, rejected = ev.verify_evidence({"BTC": bull}, {}, _state())
    assert len(verified["BTC"]["bull_case"]) == 2
    assert len(rejected["BTC"]) == 1
    assert rejected["BTC"][0]["reason"].startswith("重复 claim")


def test_verify_empty_and_missing() -> None:
    """空输入 / 缺失 token：不抛异常，产出空结构。"""
    verified, rejected = ev.verify_evidence({}, {}, _state())
    assert verified == {} and rejected == {}
    verified, rejected = ev.verify_evidence(None, {"BTC": []}, _state())
    assert verified["BTC"] == {"bull_case": [], "bear_case": []}
    assert rejected == {}


def test_verify_symbol_union() -> None:
    """token 并集：仅 bull 有产出的 token，bear_case 为空清单。"""
    verified, rejected = ev.verify_evidence({"BTC": [_ev()]}, {}, _state())
    assert verified["BTC"]["bull_case"] and verified["BTC"]["bear_case"] == []
    assert rejected == {}


# ── 分支节点（02 票：不接线，函数级单测） ────────────────


def _branch_state() -> dict:
    """mock 模式完整快照（与全链同构：collect_data → compute_signals，
    分支消费全量数据域含 signals）。"""
    state = nodes.collect_data({"tokens": list(MOCK_TOKENS)})
    state["tokens"] = list(MOCK_TOKENS)
    state.update(nodes.compute_signals(state))
    return state


def test_branch_nodes_mock_structured_evidence() -> None:
    """mock 模式：两分支产出结构化证据（claim/basis 三元组/source，无 confidence），
    数量不设上限；mock 引用恒定快照值 → 核验全部通过（无剔除）。
    分支只写独占字段（不写 meta——并行写共享键冲突，node_order 归 evidence_verify）。"""
    state = _branch_state()
    bull_out = nodes.bull_research(state)
    bear_out = nodes.bear_research(state)
    assert "meta" not in bull_out and "meta" not in bear_out
    assert "bull_errors" not in bull_out and "bear_errors" not in bear_out
    for s in MOCK_TOKENS:
        for items in (bull_out["bull_evidence"][s], bear_out["bear_evidence"][s]):
            assert items, f"{s} 分支产出为空（验收：mock 非空）"
            for item in items:
                assert item["claim"] and item["source"]
                assert set(item["basis"]) == {"domain", "field", "value"}
                assert "confidence" not in item
    verified, rejected = ev.verify_evidence(
        bull_out["bull_evidence"], bear_out["bear_evidence"], state
    )
    for s in MOCK_TOKENS:
        assert verified[s]["bull_case"] and verified[s]["bear_case"]
    assert rejected == {}


def test_branch_bad_items_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """坏条目丢弃：claim/source 缺失条目不进产出（交核验前已滤）；数量不设上限全保留。"""
    raw = {
        "evidence": [
            {
                "claim": "好条目",
                "basis": {
                    "domain": "signals",
                    "field": "momentum.value",
                    "value": "6.25",
                },
                "source": "signals",
            },
            {
                "claim": "",
                "basis": {
                    "domain": "signals",
                    "field": "momentum.value",
                    "value": "6.25",
                },
                "source": "signals",
            },
            {
                "claim": "无 source",
                "basis": {
                    "domain": "signals",
                    "field": "momentum.value",
                    "value": "6.25",
                },
                "source": "",
            },
        ]
        + [
            {
                "claim": f"第 {i} 条",
                "basis": {
                    "domain": "signals",
                    "field": "momentum.value",
                    "value": "6.25",
                },
                "source": "signals",
            }
            for i in range(10)
        ]
    }
    fake = FakeMessagesListChatModel(responses=[AIMessage(content=json.dumps(raw))])
    monkeypatch.setattr(nodes.env, "get_llm", lambda *a, **k: fake)
    out = nodes.bull_research({"tokens": ["BTC"]})
    items = out["bull_evidence"]["BTC"]
    assert len(items) == 11  # 好条目 1 + 溢出 10 → 全保留（数量不限）
    assert all(item["claim"] and item["source"] for item in items)
    assert items[0]["claim"] == "好条目"


def test_branch_single_token_error_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """分支异常：该 token 产出空清单 + 错误留痕（独占字段，不中断批）。"""

    def boom(*a, **k):
        raise RuntimeError("注入失败")

    monkeypatch.setattr(nodes.env, "get_llm", boom)
    out = nodes.bull_research({"tokens": ["BTC", "ETH"]})
    assert out["bull_evidence"]["BTC"] == []
    assert out["bull_evidence"]["ETH"] == []
    assert out["bull_errors"]["BTC"].startswith("分支异常")
    assert out["bull_errors"]["ETH"].startswith("分支异常")
