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
    return {
        "value": value,
        "source": "mock",
        "timestamp": "2026-08-20",
        "confidence": 1.0,
    }


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
                "category": "DEX",  # 板块标签（DeFiLlama 协议详情自带，09 票）
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
                    "value": {
                        "divergence_7d": 4.5,
                        "divergence_30d": 9.0,
                        "quadrant": "I",
                    }
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

    def test_social_section_renders_post_detail(self):
        """社交节 = 整体指标 + 样本档 + 单条推文明细（最近 10 条，下标沿用原序列）。

        旧契约「只渲染整体指标」已作废：prompt 第 4 条一直要求引用
        ``posts[i].likes``，而渲染器从不输出该字段——一条永远不可能被满足的死
        规则，同时核验按 state 全量解引用，编造的下标照样能对上数值。
        """
        state = _state_with()
        posts = [
            {
                "likes": str(10 * (i + 1)),
                "reposts": "3",
                "comments": "1",
                "views": "5200",
                "time": f"{12 - i}h",
            }
            for i in range(12)
        ]
        state["social_data"] = {
            "BTC": {
                "follower_count": _dp("16.6万"),
                "posts": posts,
                "post_frequency": _dp(0.5),
                "social_heat_trend": _dp(217.0),
                "social_heat_window": _dp("recent5_vs_prior_median"),
                "social_price_divergence": _dp(
                    {"label": "confirm_long", "note": "价涨社区热度升：趋势确认"}
                ),
            }
        }
        summary = context.build_branch_summary("BTC", state)
        assert "== 社交（social_data）==" in summary
        assert "follower_count.value: 16.6万" in summary
        assert "post_frequency.value: 0.5 天/条" in summary
        assert "social_heat_trend.value: 217.0" in summary
        assert "social_heat_window.value: recent5_vs_prior_median" in summary
        assert "social_price_divergence.value.label: confirm_long" in summary
        assert "social_price_divergence.value.note: 价涨社区热度升：趋势确认" in summary
        # 明细送达：12 条只渲染最近 10 条，且下标 = 原序列下标（2..11）
        assert "posts[2].likes: 30" in summary
        assert "posts[11].likes: 120" in summary
        assert "posts[1].likes" not in summary  # 超出最近 10 条上限的下标不渲染
        assert "posts[11].time: 1h" in summary
        assert "下为最近 10 条" in summary  # 明示截断口径，LLM 才知道自己看到的不是全部

    def test_social_section_missing_posts(self):
        """抓取失败（posts 空 + 派生值 None）→ 整体指标与明细一律 UNKNOWN。"""
        state = _state_with()
        state["social_data"] = {
            "BTC": {
                "follower_count": _dp(None),
                "posts": [],
                "post_frequency": _dp(None),
                "social_heat_trend": _dp(None),
                "social_heat_window": _dp(None),
                "social_price_divergence": _dp(None),
            }
        }
        summary = context.build_branch_summary("BTC", state)
        assert "social_heat_trend.value: UNKNOWN" in summary
        assert "social_heat_window.value: UNKNOWN" in summary
        assert "post_frequency: UNKNOWN" in summary
        assert "posts: UNKNOWN" in summary

    def test_fundamental_trend_features_render(self):
        """趋势特征送达：三键早已进快照 _TREND_KEYS 却从不进摘要（抓了不喂）。"""
        summary = context.build_branch_summary("BTC", _state_with())
        assert "tvl_trend_30d: UNKNOWN" in summary  # 本 fixture 未给值
        state = _state_with()
        state["fundamental_data"]["BTC"]["tvl_trend_30d"] = _dp("rising")
        state["fundamental_data"]["BTC"]["fees_trend_30d"] = _dp("falling")
        proto = context.build_branch_summary("BTC", state)
        assert "tvl_trend_30d: rising fees_trend_30d: falling" in proto
        # chain 类：结构性缺 tvl/fees 趋势，改渲 stablecoin 变化
        chain = _state_with()
        chain["fundamental_data"]["BTC"]["kind"] = "chain"
        chain["fundamental_data"]["BTC"]["stablecoin_change_30d"] = _dp(4.2)
        assert "stablecoin_change_30d: 4.20" in context.build_branch_summary(
            "BTC", chain
        )

    def test_fundamental_category_renders_for_audience(self):
        """板块进摘要：LLM 与人工都能看出「这几条证据同属一个板块」；缺失则不留空标签。"""
        summary = context.build_branch_summary("BTC", _state_with())
        assert "kind: protocol (Mock) category: DEX" in summary
        state = _state_with()
        del state["fundamental_data"]["BTC"]["category"]
        basic = context.build_branch_summary("BTC", state).split("== 市场（market_data）==")[0]
        assert "kind: protocol (Mock)\n" in basic  # 行为仍渲染
        assert "category" not in basic  # 无板块 ≠ 空板块，不伪造标签

    def test_branch_summary_is_deterministic_snapshot(self):
        """分支摘要 = 纯确定性快照：信号节 + 指令行，无任何决策链产物。"""
        summary = context.build_branch_summary("BTC", _state_with())
        assert "== 信号（signals）==" in summary
        assert "只提取证据，禁止结论。" in summary
        assert "原决策" not in summary
        assert "校准基线" not in summary

    def test_scanner_snapshot_section_uses_bare_symbol_key(self):
        """扫描器快照 key = 裸符号（AKEUSDT → AKE）：tokens 全符号也能渲染快照节。

        契约：data 源边界归一化为裸符号（与 scanner_snapshot._strip_quote 一致），
        摘要渲染路径 market.BTC.price 与核验解引用（evidence._resolve）对齐。
        """
        state = _state_with()
        state["scanner_snapshot"] = {
            "date": "2026-08-16",
            "market": {
                "BTC": {
                    "price": 70000.0,
                    "ret_24h": 2.5,
                    "boards": ["top_gainers"],
                }
            },
            "microstructure": {"BTC": {"ls_ratio_all": 1.2}},
        }
        summary = context.build_branch_summary("BTC", state)
        assert "== 扫描器快照（scanner_snapshot，截至 2026-08-16" in summary
        assert "market.BTC.price: 70000.000000" in summary
        assert "market.BTC.boards: top_gainers" in summary
        assert "microstructure.BTC.ls_ratio_all: 1.20" in summary
        # 快照缺失 → 节整体跳过（不渲染空节占位）
        empty = context.build_branch_summary("BTC", _state_with())
        assert "== 扫描器快照" not in empty


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
