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
    "fetch_news_rss": (
        "strategy_research.nodes.web_ds.fetch_news_rss",
        lambda q, **kw: m.mock_news_rss(q),
    ),
}

#: 共享资源（批内一次，与 token 数无关）
_SHARED_NAMES = [
    "fetch_ticker_24h_all",
    "fetch_listing_days",
    "fetch_premium_index_all",
    "fetch_fapi_prices_all",
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
        "error",
        "futures_error",
        "incomplete",
    }
    assert set(mkt["price"]) == {"value", "source", "timestamp", "confidence"}
    assert mkt["price"]["value"] == 70000.0
    assert mkt["price"]["source"] == "binance"
    assert mkt["basis"]["value"] == 0.0  # fapi 价 == 现货价
    assert mkt["funding_trend"]["value"] in ("rising", "falling", "flat")
    assert mkt["funding_pctile_90d"]["value"] == 20.0  # mock 费率 5 档周期，最新为最低档
    assert mkt["incomplete"] is False

    fund = res["fundamental_data"]["UNI"]
    assert fund["kind"] == "protocol" and fund["name"] == "uniswap"
    assert fund["resolved"] is True
    assert fund["tvl"]["value"] is not None
    assert fund["fees_24h"]["value"] is not None
    fund_chain = res["fundamental_data"]["BTC"]
    assert fund_chain["kind"] == "chain" and fund_chain["name"] == "bitcoin"
    assert fund_chain["stablecoin_supply"]["value"] is not None
    assert fund_chain["dex_volume_24h"]["value"] is not None

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
        "taker_bs_ratio",
        "board",
        "error",
        "incomplete",
    }
    assert ms["board"] is None  # PoC 阶段固定 None
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


def test_mock_shared_fetched_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """共享资源批内一次：与 token 数无关；per-token 请求恰为 token 数。"""
    counters: dict[str, dict] = {}
    for name in _SHARED_NAMES:
        path, fn = _PATCH_TARGETS[name]
        counters[name] = _count(monkeypatch, path, fn)
    for name in (
        "fetch_klines",
        "fetch_funding_rate_history",
        "fetch_open_interest",
        "fetch_open_interest_hist",
        "fetch_global_long_short_ratio",
        "fetch_top_long_short_account_ratio",
        "fetch_top_long_short_position_ratio",
        "fetch_taker_long_short_ratio",
        "fetch_news_rss",
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
        "fetch_klines",
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
    # 链类 4 个（BTC/ETH/SOL/DOGE）、协议类 2 个（UNI 静态 + XRP 兑底）
    assert counters["fetch_chain_tvl"]["n"] == 4
    assert counters["fetch_protocol_tvl"]["n"] == 2


def test_mock_single_token_exception_does_not_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单 token 注入异常：仅该 token 标 error，批不中断。"""
    orig = nodes.binance.fetch_klines

    def boom(symbol: str, **kwargs):
        if symbol == "XRPUSDT":
            raise RuntimeError("注入失败")
        return orig(symbol, **kwargs)

    monkeypatch.setattr("strategy_research.nodes.binance.fetch_klines", boom)
    res = nodes.collect_data({"tokens": list(MOCK_TOKENS)})

    assert set(res["market_data"]) == set(MOCK_TOKENS)  # 批不中断
    assert res["market_data"]["XRP"]["error"] == "装配异常: 注入失败"
    assert res["market_data"]["XRP"]["incomplete"] is True
    assert "XRP" in res["meta"]["incomplete_tokens"]
    assert res["market_data"]["BTC"]["price"]["value"] is not None  # 其余正常
    assert res["market_data"]["BTC"]["incomplete"] is False


def test_real_full_snapshot_with_patched_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实路径（全 fake）：字段映射 / source 白名单 / 聚合字段正确。"""
    monkeypatch.setenv("SR_MOCK", "0")
    _patch_fetches(monkeypatch)
    res = nodes.collect_data({"tokens": list(MOCK_TOKENS)})

    mkt = res["market_data"]["BTC"]
    assert mkt["price"]["source"] == "binance"
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
    assert ms["taker_bs_ratio"]["value"] == 1.0  # mock taker 恒定 1.0

    web = res["web_data"]["BTC"]
    assert web["items"] is not None


def test_real_failure_marks_unknown_not_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    """失败即失败：共享资源失败 → 数据点 UNKNOWN + error/incomplete，批不中断。"""
    monkeypatch.setenv("SR_MOCK", "0")
    _patch_fetches(
        monkeypatch,
        {
            "fetch_ticker_24h_all": None,
            "fetch_premium_index_all": None,
            "fetch_chain_tvl": None,
        },
    )
    res = nodes.collect_data({"tokens": list(MOCK_TOKENS)})

    assert set(res["market_data"]) == set(MOCK_TOKENS)  # 批不中断
    mkt = res["market_data"]["BTC"]
    assert mkt["error"] == "ticker 缺失"
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


def test_real_unknown_kind_fundamental(monkeypatch: pytest.MonkeyPatch) -> None:
    """静态映射未命中 + 惰性兜底失败 → kind=unknown，resolved=False。"""
    monkeypatch.setenv("SR_MOCK", "0")
    _patch_fetches(monkeypatch, {"fetch_protocols": None})
    res = nodes.collect_data({"tokens": ["ZZZ"]})

    fund = res["fundamental_data"]["ZZZ"]
    assert fund["kind"] == "unknown"
    assert fund["resolved"] is False
    assert fund["tvl"]["value"] is None
    assert fund["incomplete"] is True
    assert "ZZZ" in res["meta"]["incomplete_tokens"]


# ── 校准基线加载（13 票：① 注入 meta，③-⑥ 摘要消费） ──


def test_mock_mode_skips_calibration_context() -> None:
    """mock 模式不注入（mock 决策不进评估池，隔离保持一致）。"""
    res = nodes.collect_data({"tokens": ["BTC"]})
    assert "calibration_context" not in res["meta"]


def test_live_mode_loads_calibration_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """live 模式：历史记录渲染为校准基线注入 meta（确定性、零 LLM）。"""
    monkeypatch.setenv("SR_MOCK", "0")
    _patch_fetches(monkeypatch)
    monkeypatch.setattr(
        nodes.review_mod,
        "load_records",
        lambda *a, **k: [{"hit_7d": True, "decision": "TRADE", "confidence": 0.8}],
    )
    res = nodes.collect_data({"tokens": ["BTC"]})
    assert "命中率" in res["meta"]["calibration_context"]


def test_live_mode_calibration_failure_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """校准加载失败仅记 calibration_error，不中断批。"""
    monkeypatch.setenv("SR_MOCK", "0")
    _patch_fetches(monkeypatch)
    monkeypatch.setattr(
        nodes.review_mod, "load_records", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    res = nodes.collect_data({"tokens": ["BTC"]})
    assert "calibration_context" not in res["meta"]
    assert "calibration_error" in res["meta"]
