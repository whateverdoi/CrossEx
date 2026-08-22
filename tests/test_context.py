"""LLM 上下文装配契约测试（预测能力 01 票）。

契约：prompt 引用的关键字段在渲染出的摘要中必现——改 prompt 解读规则而渲染
不同步时先红后绿；分支摘要是纯确定性快照，不含任何决策链产物（05 票：旧
decide/challenge/finalize 摘要契约退役）。
"""

from __future__ import annotations

import pytest

from strategy_research import context


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
    }


class TestBranchSummaryContract:
    """分支 prompt（BULL/BEAR）引用的关键字段，在渲染摘要中必现。"""

    @pytest.mark.parametrize(
        "field",
        [
            "tvl_change_7d",  # 基本面增速（basis 引用域 fundamentals）
            "tvl_change_30d",
            "momentum",  # 信号解读（basis 引用域 signals）
            "quadrant",
            "funding",  # 多维度交叉验证
            "funding_trend",
            "funding_pctile_90d",
            "oi_price_divergence",
            "taker_buy_ratio_24h",
        ],
    )
    def test_branch_prompt_field_renders(self, field):
        summary = context.build_branch_summary("BTC", _state_with())
        assert field in summary, f"分支 prompt 引用 {field}，但摘要未渲染"

    def test_branch_summary_is_deterministic_snapshot(self):
        """分支摘要 = 纯确定性快照：信号节 + 指令行，无任何决策链产物。"""
        summary = context.build_branch_summary("BTC", _state_with())
        assert "== 信号（确定性计算）==" in summary
        assert "只提取证据，禁止结论。" in summary
        assert "原决策" not in summary
        assert "校准基线" not in summary


class TestNoteSingleSource:
    """解读规则注记单一来源：signals 与 mock 输出同一常量。"""

    def test_sentiment_note_single_source(self):
        from strategy_research import signals
        from strategy_research.datasources import mock as mock_ds

        note = signals.sentiment_raw({"funding": _dp(0.0001)})["note"]
        assert note is context.SENTIMENT_NOTE
        # mock 侧 note 同源（mock_signals_data 输出，不再逐字复制）
        mock_out = mock_ds.mock_signals_data("BTC", "protocol")
        assert mock_out["sentiment"]["note"] == context.SENTIMENT_NOTE
        # 注记内嵌解读规则锚点（05 票：不再指向已退役 prompt）
        assert "funding 高=拥挤反向" in context.SENTIMENT_NOTE
        assert "funding_pctile_90d" in context.SENTIMENT_NOTE
