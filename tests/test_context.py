"""LLM 上下文装配契约测试（预测能力 01 票）。

契约：prompt 引用的关键字段在渲染出的摘要中必现——改 prompt 解读规则而渲染不同步
时先红后绿；schemas 层再导出与 context 本体保持一致。
"""

from __future__ import annotations

import pytest

from strategy_research import context, schemas


def _dp(value):
    """四元组包装（与 nodes._dp 同构）。"""
    return {"value": value, "source": "mock", "timestamp": "2026-08-20", "confidence": 1.0}


def _state_with(symbol="BTC"):
    """带关键字段的最小 state（字段名与 prompt 引用对齐）。"""
    return {
        "tokens": [symbol],
        "fundamental_data": {
            symbol: {
                "kind": "protocol",
                "name": "Mock",
                "tvl": _dp(1_000_000_000),
                "tvl_change_1d": _dp(1.5),
                "tvl_change_7d": _dp(12.5),
                "tvl_change_30d": _dp(30.0),
                "mcap": _dp(500_000_000),
                "fdv": _dp(2_000_000_000),
                "fees_24h": _dp(100_000),
                "fees_7d": _dp(700_000),
                "revenue_24h": _dp(50_000),
                "revenue_7d": _dp(350_000),
            }
        },
        "market_data": {
            symbol: {
                "price": _dp(100.0),
                "quote_volume_24h": _dp(2e8),
                "change_24h": _dp(3.2),
                "change_7d": _dp(8.0),
                "change_30d": _dp(21.0),
                "change_90d": _dp(45.0),
                "change_1y": _dp(120.0),
                "funding": _dp(0.0001),
                "funding_avg_7d": _dp(0.00008),
                "funding_trend": _dp("flat"),
                "funding_pctile_90d": _dp(85.0),
                "oi": _dp(3e8),
                "basis": _dp(0.15),
                "taker_buy_ratio_24h": _dp(1.02),
                "listing_days": _dp(60),
            }
        },
        "microstructure_data": {
            symbol: {
                "oi_change_24h": _dp(5.0),
                "oi_change_48h": _dp(9.0),
                "oi_value_change_24h": _dp(6.0),
                "ls_ratio_all": _dp(1.05),
                "ls_ratio_all_change_24h": _dp(2.0),
                "ls_ratio_top_acc": _dp(1.2),
                "ls_ratio_top_pos": _dp(1.1),
                "taker_bs_ratio": _dp(1.0),
                "oi_price_divergence": _dp({"label": "confirm_long", "note": "x"}),
            }
        },
        "signals": {
            symbol: {
                "valuation": {"value": {"mc_fees": 13.7, "fees_tvl": 0.036}},
                "momentum": {"value": 21.25},
                "divergence": {
                    "value": {"divergence_7d": 4.5, "divergence_30d": 9.0, "quadrant": "I"}
                },
                "sentiment": {
                    "components": {
                        "funding": 0.0001,
                        "funding_pctile_90d": 85.0,
                        "funding_trend": "flat",
                        "oi_price_divergence": {
                            "label": "confirm_long",
                            "note": "价涨 OI 增",
                        },
                    }
                },
            }
        },
        "web_data": {
            symbol: {
                "items": [
                    {"date": "2026-08-20", "title": "Mock news", "source": "bing"}
                ]
            }
        },
        "facts": {symbol: []},
        "decisions": {},
        "challenges": {},
    }


class TestPromptRenderingContract:
    """DECIDE_PROMPT 引用的关键字段，在渲染摘要中必现。"""

    @pytest.mark.parametrize(
        "field",
        [
            "tvl_change_7d",  # 基本面增速比较（rule 3）
            "tvl_change_30d",
            "momentum",  # 信号解读（rule 4）
            "quadrant",
            "funding",  # 多维度交叉验证（rule 5）
            "funding_trend",
            "funding_pctile_90d",
            "oi_price_divergence",
            "taker_buy_ratio_24h",
        ],
    )
    def test_decide_prompt_field_renders(self, field):
        summary = context.build_decide_summary("BTC", _state_with())
        assert field in summary, f"DECIDE_PROMPT 引用 {field}，但 decide 摘要未渲染"

    def test_decide_summary_has_facts_section(self):
        """rule 2 证据规则引用的"事实证据"节必现。"""
        assert "== 事实证据（research_facts 产出）==" in context.build_decide_summary(
            "BTC", _state_with()
        )

    def test_challenge_summary_sections(self):
        """对抗摘要：原决策 + 反方事实 + 信号三节（CHALLENGE_PROMPT 消费面）。"""
        state = _state_with()
        state["decisions"]["BTC"] = {
            "symbol": "BTC",
            "decision": "TRADE",
            "direction": "long",
            "confidence": 0.7,
        }
        summary = context.build_challenge_summary("BTC", state)
        assert "== 原决策 ==" in summary
        assert "== 反方事实（预筛：多头取 bear / 空头取 bull）==" in summary
        assert "== 信号（确定性计算）==" in summary

    def test_finalize_summary_has_challenges_section(self):
        """复审摘要：反方挑战节（FINALIZE_PROMPT 消费面）。"""
        state = _state_with()
        state["challenges"]["BTC"] = [
            {"severity": "high", "stance": "conservative", "claim": "x", "evidence": "y"}
        ]
        summary = context.build_finalize_summary("BTC", state)
        assert "== 反方挑战（≤3 条）==" in summary
        assert "[high/conservative] x" in summary


class TestNoteSingleSource:
    """解读规则注记单一来源：signals 与 mock 输出同一常量。"""

    def test_signals_note_is_context_constant(self):
        from strategy_research import signals
        from strategy_research.datasources import mock as mock_ds

        note = signals.sentiment_raw({"funding": _dp(0.0001)})["note"]
        assert note is context.SENTIMENT_NOTE
        # mock 侧 note 同源（mock_signals_data 输出，不再逐字复制）
        mock_out = mock_ds.mock_signals_data("BTC", "protocol")
        assert mock_out["sentiment"]["note"] == context.SENTIMENT_NOTE

    def test_sentiment_note_anchors_decide_prompt(self):
        """注记指向的解读规则锚点仍然成立（原 test_signals 断言语义保留）。"""
        assert "DECIDE_PROMPT" in context.SENTIMENT_NOTE


class TestSchemasReexport:
    """schemas 层兼容再导出与 context 本体一致。"""

    def test_reexports_match_context(self):
        for name in ("FACTS_PROMPT", "DECIDE_PROMPT", "CHALLENGE_PROMPT", "FINALIZE_PROMPT"):
            assert getattr(schemas, name) is getattr(context, name)

    def test_prompt_markers_preserved(self):
        """env mock 路由依赖的 prompt 特征标记未漂移。"""
        assert "事实收集员" in context.FACTS_PROMPT
        assert "策略研究员" in context.DECIDE_PROMPT
        assert "对抗官" in context.CHALLENGE_PROMPT
        assert "复审员" in context.FINALIZE_PROMPT


class TestCalibrationSection:
    """13 票：校准基线节注入（仅④⑥ 摘要携带，③ 不带）。"""

    def _with_cal(self):
        state = _state_with()
        state["meta"] = {"calibration_context": "累积方向判断 3 条（T+7d），命中率 0.667"}
        return state

    def test_decide_summary_carries_section(self):
        summary = context.build_decide_summary("BTC", self._with_cal())
        assert "== 校准基线（历史决策复盘，T+7d 方向命中）==" in summary
        assert "累积方向判断 3 条" in summary

    def test_finalize_summary_carries_section(self):
        summary = context.build_finalize_summary("BTC", self._with_cal())
        assert "== 校准基线（历史决策复盘，T+7d 方向命中）==" in summary

    def test_facts_summary_does_not_carry(self):
        """③ 采证只提取证据，不携带校准基线。"""
        summary = context.build_facts_summary("BTC", self._with_cal())
        assert "校准基线" not in summary

    def test_no_calibration_no_section(self):
        summary = context.build_decide_summary("BTC", _state_with())
        assert "校准基线" not in summary


class TestCalibrationSectionScope:
    """校准基线仅 ④⑥ 携带（03 票验收：③ 采证与 ⑤ 对抗不带）。"""

    def test_challenge_summary_does_not_carry(self):
        state = _state_with()
        state["meta"] = {"calibration_context": "累积方向判断 3 条（T+7d），命中率 0.667"}
        state["decisions"]["BTC"] = {
            "symbol": "BTC",
            "decision": "TRADE",
            "direction": "long",
            "confidence": 0.7,
        }
        summary = context.build_challenge_summary("BTC", state)
        assert "校准基线" not in summary
