"""① collect_data 装配测试：mock 全量 / 共享批内一次 / 单 token 异常不中断 / 失败即失败。

数据点四元组（{value, source, timestamp, confidence}）与 mock 同构断言；
真实路径用 _patch_fetches 全量替换为 mock 版本（零外部请求）。
"""

from __future__ import annotations

import pytest

from strategy_research import nodes
from strategy_research.datasources import mock as m
from strategy_research.datasources.mock import MOCK_TOKENS

#: collect_data 涉及的全部 fetch → (monkeypatch 路径, mock 替代实现)
_PATCH_TARGETS: dict[str, tuple[str, object]] = {
    "fetch_ticker_24h_all": (
        "strategy_research.nodes.binance.fetch_ticker_24h_all",
        m.mock_ticker_24h_all,
    ),
    "fetch_klines": ("strategy_research.nodes.binance.fetch_klines", m.mock_klines),
    "fetch_listing_days": (
        "strategy_research.nodes.binance_futures.fetch_listing_days",
        m.mock_listing_days,
    ),
    "fetch_premium_index_all": (
        "strategy_research.nodes.binance_futures.fetch_premium_index_all",
        m.mock_premium_index_all,
    ),
    "fetch_fapi_prices_all": (
        "strategy_research.nodes.binance_futures.fetch_fapi_prices_all",
        m.mock_fapi_prices_all,
    ),
    "fetch_fapi_ticker_24h_all": (
        "strategy_research.nodes.binance_futures.fetch_fapi_ticker_24h_all",
        m.mock_fapi_ticker_24h_all,
    ),
    "fetch_fapi_klines": (
        "strategy_research.nodes.binance_futures.fetch_fapi_klines",
        m.mock_fapi_klines,
    ),
    "fetch_funding_rate_history": (
        "strategy_research.nodes.binance_futures.fetch_funding_rate_history",
        m.mock_funding_rate_history,
    ),
    "fetch_open_interest": (
        "strategy_research.nodes.binance_futures.fetch_open_interest",
        m.mock_open_interest,
    ),
    "fetch_open_interest_hist": (
        "strategy_research.nodes.binance_futures.fetch_open_interest_hist",
        lambda sym, *a, **kw: m.mock_series("open_interest_hist", sym, *a, **kw),
    ),
    "fetch_global_long_short_ratio": (
        "strategy_research.nodes.binance_futures.fetch_global_long_short_ratio",
        lambda sym, *a, **kw: m.mock_series("global_long_short", sym, *a, **kw),
    ),
    "fetch_top_long_short_account_ratio": (
        ("strategy_research.nodes.binance_futures.fetch_top_long_short_account_ratio"),
        lambda sym, *a, **kw: m.mock_series("top_ls_accounts", sym, *a, **kw),
    ),
    "fetch_top_long_short_position_ratio": (
        ("strategy_research.nodes.binance_futures.fetch_top_long_short_position_ratio"),
        lambda sym, *a, **kw: m.mock_series("top_ls_positions", sym, *a, **kw),
    ),
    "fetch_taker_long_short_ratio": (
        "strategy_research.nodes.binance_futures.fetch_taker_long_short_ratio",
        lambda sym, *a, **kw: m.mock_series("taker_bs", sym, *a, **kw),
    ),
    "fetch_chains": ("strategy_research.nodes.defillama.fetch_chains", m.mock_chains),
    "fetch_protocols": (
        "strategy_research.nodes.defillama.fetch_protocols",
        m.mock_protocols,
    ),
    "fetch_fees": ("strategy_research.nodes.defillama.fetch_fees", m.mock_fees),
    "fetch_stablecoins": (
        "strategy_research.nodes.defillama.fetch_stablecoins",
        m.mock_stablecoins,
    ),
    "fetch_dexs": ("strategy_research.nodes.defillama.fetch_dexs", m.mock_dexs),
    "fetch_protocol_tvl": (
        "strategy_research.nodes.defillama.fetch_protocol_tvl",
        m.mock_protocol_tvl,
    ),
    "fetch_chain_tvl": (
        "strategy_research.nodes.defillama.fetch_chain_tvl",
        m.mock_chain_tvl,
    ),
    "fetch_protocol_tvl_history": (
        "strategy_research.nodes.defillama.fetch_protocol_tvl_history",
        m.mock_protocol_tvl_history,
    ),
    "fetch_protocol_fees_history": (
        "strategy_research.nodes.defillama.fetch_protocol_fees_history",
        m.mock_protocol_fees_history,
    ),
    "fetch_stablecoin_history": (
        "strategy_research.nodes.defillama.fetch_stablecoin_history",
        m.mock_stablecoin_history,
    ),
    "fetch_news_rss": (
        "strategy_research.nodes.web_ds.fetch_news_rss",
        lambda q, **kw: m.mock_news_rss(q),
    ),
    "fetch_x_stats": (
        "strategy_research.nodes.x_social.fetch_x_stats",
        lambda sym, *a, **kw: m.mock_x_stats(sym),
    ),
}

#: 共享资源（批内一次，与 token 数无关）
_SHARED_NAMES = [
    "fetch_ticker_24h_all",
    "fetch_listing_days",
    "fetch_premium_index_all",
    "fetch_fapi_prices_all",
    "fetch_fapi_ticker_24h_all",
    "fetch_chains",
    "fetch_protocols",
    "fetch_fees",
    "fetch_stablecoins",
    "fetch_dexs",
]


def _patch_fetches(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object] | None = None
) -> None:
    """真实路径测试：全部 fetch 替换为 mock 版本；overrides 键为 None → 模拟失败。"""
    overrides = overrides or {}
    for name, (path, fn) in _PATCH_TARGETS.items():
        if name in overrides:
            val = overrides[name]
            monkeypatch.setattr(path, (lambda *a, **k: None) if val is None else val)
        else:
            monkeypatch.setattr(path, fn)


def _count(monkeypatch: pytest.MonkeyPatch, path: str, fn) -> dict:
    """包装 fetch 计数调用次数。"""
    counter = {"n": 0}

    def wrapper(*args, **kwargs):
        counter["n"] += 1
        return fn(*args, **kwargs)

    monkeypatch.setattr(path, wrapper)
    return counter


def test_mock_collect_full_snapshot() -> None:
    """mock 模式：全 6 token 四快照齐全；数据点四元组结构正确。"""
    res = nodes.collect_data({"tokens": list(MOCK_TOKENS)})
    assert set(res["market_data"]) == set(MOCK_TOKENS)
    assert set(res["fundamental_data"]) == set(MOCK_TOKENS)
    assert set(res["microstructure_data"]) == set(MOCK_TOKENS)
    assert set(res["web_data"]) == set(MOCK_TOKENS)

    mkt = res["market_data"]["BTC"]
    assert set(mkt) == {
        "price",
        "change_24h",
        "quote_volume_24h",
        "change_7d",
        "change_30d",
        "change_90d",
        "change_1y",
        "funding",
        "funding_avg_7d",
        "funding_trend",
        "funding_pctile_90d",
        "oi",
        "basis",
        "taker_buy_ratio_24h",
        "listing_days",
        "rv_7d",
        "rv_30d",
        "drawdown_1y",
        "vol_adj_ret_7d",
        "vol_adj_ret_30d",
        "beta_30d",
        "alpha_30d",
        "funding_x_pctile",
        "funding_interval_hours",
        "funding_carry_7d_pct",
        "funding_carry_30d_pct",
        "spread_pct",
        "bid_depth_usd_2pct",
        "ask_depth_usd_2pct",
        "depth_band_state",
        "error",
        "futures_error",
        "incomplete",
    }
    assert set(mkt["price"]) == {"value", "source", "timestamp", "confidence"}
    assert mkt["price"]["value"] == 70000.0
    assert mkt["price"]["source"] == "binance_futures"
    assert mkt["basis"]["value"] == 0.0  # fapi 价 == 现货价
    assert mkt["funding_trend"]["value"] in ("rising", "falling", "flat")
    assert (
        mkt["funding_pctile_90d"]["value"] == 20.0
    )  # mock 费率 5 档周期，最新为最低档
    # 交易结构：mock 费率序列 8h 一步 → 间隔 8h；0.0001×3×7×100 = 0.21
    assert mkt["funding_interval_hours"]["value"] == 8.0
    assert mkt["funding_carry_7d_pct"]["value"] == pytest.approx(0.21)
    assert mkt["funding_carry_30d_pct"]["value"] == pytest.approx(0.9)
    # mock 盘口 100 档间距 0.03% → 完整覆盖 2% 带 → 深度为实测而非下限
    assert mkt["depth_band_state"]["value"] == "band_complete"
    assert mkt["spread_pct"]["value"] == pytest.approx(0.03, abs=1e-4)
    assert mkt["bid_depth_usd_2pct"]["value"] > 0
    assert mkt["funding_x_pctile"]["value"] is not None  # 全市场分布过样本门槛
    assert mkt["incomplete"] is False

    fund = res["fundamental_data"]["UNI"]
    assert fund["kind"] == "protocol" and fund["name"] == "uniswap"
    assert fund["resolved"] is True
    assert fund["tvl"]["value"] is not None
    assert fund["fees_24h"]["value"] is not None
    # 趋势特征（01 票）：mock TVL 序列 30 天 +10% → rising；费用恒定 → flat
    assert fund["tvl_trend_30d"]["value"] == "rising"
    assert fund["fees_trend_30d"]["value"] == "flat"
    assert fund["stablecoin_change_30d"]["value"] is None  # 协议类结构性缺失
    fund_chain = res["fundamental_data"]["BTC"]
    assert fund_chain["kind"] == "chain" and fund_chain["name"] == "bitcoin"
    assert fund_chain["stablecoin_supply"]["value"] is not None
    assert fund_chain["dex_volume_24h"]["value"] is not None
    # 趋势特征：链类取稳定币变化（mock 恒定 → 0%）；无协议 TVL/费用序列 → None
    assert fund_chain["stablecoin_change_30d"]["value"] == 0.0
    assert fund_chain["tvl_trend_30d"]["value"] is None
    assert fund_chain["fees_trend_30d"]["value"] is None

    ms = res["microstructure_data"]["BTC"]
    assert set(ms) == {
        "oi_change_24h",
        "oi_price_divergence",
        "oi_change_48h",
        "oi_value_change_24h",
        "ls_ratio_all",
        "ls_ratio_all_change_24h",
        "ls_ratio_top_acc",
        "ls_ratio_top_pos",
        "taker_bs_ratio_1h",
        "liq_long_24h",
        "liq_short_24h",
        "liq_total_24h",
        "liq_total_oi_ratio",
        "liq_imbalance",
        "error",
        "incomplete",
    }
    assert ms["oi_change_24h"]["value"] == 0.0  # mock 序列恒定 → 0%
    assert ms["oi_change_48h"]["value"] == 0.0  # 96 点跨 95h，48h 窗口可算
    # mock oi 变化恒 0 → 背离无方向；价格变化 2.5%（BTC）→ 不缺失
    assert ms["oi_price_divergence"]["value"]["label"] == "none"
    assert ms["incomplete"] is False

    web = res["web_data"]["BTC"]
    assert set(web) == {"symbol", "items", "web_error", "incomplete"}
    assert web["items"] and set(web["items"][0]) == {"date", "title", "source"}

    # mock 全 6 token 数据齐全（链类 mcap/fdv 为结构性缺失，不计 incomplete）
    assert res["meta"]["incomplete_tokens"] == []
    assert res["meta"]["incomplete_detail"] == {}


def test_mock_shared_fetched_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """共享资源批内一次：与 token 数无关；per-token 请求恰为 token 数。"""
    counters: dict[str, dict] = {}
    for name in _SHARED_NAMES:
        path, fn = _PATCH_TARGETS[name]
        counters[name] = _count(monkeypatch, path, fn)
    for name in (
        "fetch_fapi_klines",
        "fetch_funding_rate_history",
        "fetch_open_interest",
        "fetch_open_interest_hist",
        "fetch_global_long_short_ratio",
        "fetch_top_long_short_account_ratio",
        "fetch_top_long_short_position_ratio",
        "fetch_taker_long_short_ratio",
        "fetch_news_rss",
        "fetch_protocol_tvl_history",
        "fetch_protocol_fees_history",
        "fetch_stablecoin_history",
    ):
        path, fn = _PATCH_TARGETS[name]
        counters[name] = _count(monkeypatch, path, fn)
    path, fn = _PATCH_TARGETS["fetch_chain_tvl"]
    counters["fetch_chain_tvl"] = _count(monkeypatch, path, fn)
    path, fn = _PATCH_TARGETS["fetch_protocol_tvl"]
    counters["fetch_protocol_tvl"] = _count(monkeypatch, path, fn)

    nodes.collect_data({"tokens": list(MOCK_TOKENS)})

    for name in _SHARED_NAMES:
        assert counters[name]["n"] == 1, (
            f"{name} 应批内一次，实际 {counters[name]['n']}"
        )
    for name in (
        "fetch_funding_rate_history",
        "fetch_open_interest",
        "fetch_open_interest_hist",
        "fetch_global_long_short_ratio",
        "fetch_top_long_short_account_ratio",
        "fetch_top_long_short_position_ratio",
        "fetch_taker_long_short_ratio",
        "fetch_news_rss",
    ):
        assert counters[name]["n"] == len(MOCK_TOKENS), (
            f"{name} 应 per-token 6 次，实际 {counters[name]['n']}"
        )
    # fetch_fapi_klines：per-token 6 次 + BTC 基准共享 1 次（08 票 β/宽度参照）
    assert counters["fetch_fapi_klines"]["n"] == len(MOCK_TOKENS) + 1
    # 链类 4 个（BTC/ETH/SOL/DOGE）、协议类 2 个（UNI 静态 + XRP 兑底）
    assert counters["fetch_chain_tvl"]["n"] == 4
    assert counters["fetch_protocol_tvl"]["n"] == 2
    # 趋势特征历史序列：per-token——协议 2 类各 2 次，链类稳定币 4 次
    assert counters["fetch_protocol_tvl_history"]["n"] == 2
    assert counters["fetch_protocol_fees_history"]["n"] == 2
    assert counters["fetch_stablecoin_history"]["n"] == 4


def test_failure_marks_unknown_not_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    """失败语义：单 token 异常仅标 error 批不中断；共享失败 → 数据点 UNKNOWN，批不中断。"""
    orig = nodes.binance_futures.fetch_fapi_klines

    def boom(symbol: str, **kwargs):
        if symbol == "XRPUSDT":
            raise RuntimeError("注入失败")
        return orig(symbol, **kwargs)

    monkeypatch.setattr(
        "strategy_research.nodes.binance_futures.fetch_fapi_klines", boom
    )
    res = nodes.collect_data({"tokens": list(MOCK_TOKENS)})

    assert set(res["market_data"]) == set(MOCK_TOKENS)  # 批不中断
    assert res["market_data"]["XRP"]["error"] == "装配异常: 注入失败"
    assert res["market_data"]["XRP"]["incomplete"] is True
    assert "XRP" in res["meta"]["incomplete_tokens"]
    assert "mkt" in res["meta"]["incomplete_detail"]["XRP"]  # 缺失域名细粒度
    assert res["market_data"]["BTC"]["price"]["value"] is not None  # 其余正常
    assert res["market_data"]["BTC"]["incomplete"] is False

    # real 路径：共享资源失败 → 数据点 UNKNOWN + error/incomplete
    monkeypatch.setenv("SR_MOCK", "0")
    _patch_fetches(
        monkeypatch,
        {
            "fetch_fapi_ticker_24h_all": None,
            "fetch_premium_index_all": None,
            "fetch_chain_tvl": None,
        },
    )
    res = nodes.collect_data({"tokens": list(MOCK_TOKENS)})
    assert set(res["market_data"]) == set(MOCK_TOKENS)  # 批不中断
    mkt = res["market_data"]["BTC"]
    assert mkt["error"] == "fapi ticker 缺失"
    assert mkt["price"]["value"] is None
    assert mkt["futures_error"] == "premium 缺失"
    assert mkt["incomplete"] is True
    fund = res["fundamental_data"]["BTC"]
    assert fund["error"] == "chain TVL 拉取失败"
    assert fund["tvl"]["value"] is None
    assert "BTC" in res["meta"]["incomplete_tokens"]
    # 未失败 token 数据正常（XRP 走协议路径，tvl 不受 chain TVL 失败影响）
    assert res["fundamental_data"]["XRP"]["resolved"] is True
    assert res["fundamental_data"]["XRP"]["tvl"]["value"] is not None


def test_real_path_maps_fields_and_unknown_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实路径（全 fake）：字段映射 / source 白名单 / 聚合字段正确；
    静态映射未命中 + 兜底失败 → kind=unknown，resolved=False。"""
    monkeypatch.setenv("SR_MOCK", "0")
    _patch_fetches(monkeypatch)
    res = nodes.collect_data({"tokens": list(MOCK_TOKENS)})

    mkt = res["market_data"]["BTC"]
    assert mkt["price"]["source"] == "binance_futures"
    assert mkt["price"]["value"] == 70000.0
    assert mkt["change_7d"]["value"] == 0.0  # mock klines 周期 7 天同相
    assert mkt["funding"]["source"] == "binance_futures"
    assert mkt["basis"]["value"] == 0.0
    assert mkt["listing_days"]["value"] == 1800

    fund = res["fundamental_data"]["UNI"]
    assert fund["kind"] == "protocol" and fund["resolved"] is True
    assert fund["tvl"]["source"] == "defillama"
    assert fund["tvl"]["value"] is not None

    ms = res["microstructure_data"]["BTC"]
    assert ms["oi_change_24h"]["source"] == "binance_futures"
    assert ms["taker_bs_ratio_1h"]["value"] == 1.0  # mock taker 恒定 1.0

    web = res["web_data"]["BTC"]
    assert web["items"] is not None

    # 趋势特征（01 票）：真实路径与 mock 同源序列 → 逐值一致
    fund = res["fundamental_data"]["UNI"]
    assert fund["tvl_trend_30d"]["value"] == "rising"
    assert fund["fees_trend_30d"]["value"] == "flat"
    assert res["fundamental_data"]["BTC"]["stablecoin_change_30d"]["value"] == 0.0

    # 静态映射未命中 + 惰性兜底失败 → kind=unknown
    _patch_fetches(monkeypatch, {"fetch_protocols": None})
    res_zzz = nodes.collect_data({"tokens": ["ZZZ"]})
    fund = res_zzz["fundamental_data"]["ZZZ"]
    assert fund["kind"] == "unknown"
    assert fund["resolved"] is False
    assert fund["tvl"]["value"] is None
    assert fund["incomplete"] is True
    assert "ZZZ" in res_zzz["meta"]["incomplete_tokens"]


def test_trend_features_failure_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """历史序列拉取失败：趋势特征 None（UNKNOWN 纪律），主数据与批不受影响。"""
    monkeypatch.setattr(
        "strategy_research.nodes.defillama.fetch_protocol_tvl_history",
        lambda *a, **k: None,
    )
    res = nodes.collect_data({"tokens": ["UNI", "BTC"]})
    fund = res["fundamental_data"]["UNI"]
    assert fund["tvl_trend_30d"]["value"] is None
    assert fund["tvl"]["value"] is not None  # 主数据不受影响
    assert fund["incomplete"] is False  # 趋势特征缺失不置 incomplete
    assert res["fundamental_data"]["BTC"]["stablecoin_change_30d"]["value"] == 0.0
