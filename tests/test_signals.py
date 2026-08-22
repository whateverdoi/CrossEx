"""② compute_signals 装配 + 4 个纯函数测试（正常 / 缺失 / 异常 → None）。

纯函数输入为四元组包装快照（与 ① 输出同构）；节点测试覆盖
mock 模式（mock_signals_data 同构）与真实模式（纯函数装配）。
"""

from __future__ import annotations

import pytest

from strategy_research import nodes
from strategy_research import signals as sig
from strategy_research.datasources import mock as m
from strategy_research.datasources.mock import MOCK_TOKENS

#: sentiment components 字段集（规格 ② 6 字段 + 票 05 的 ls_ratio_top_acc
#: + 票 14 的 funding_pctile_90d / oi_price_divergence）
_COMPONENT_KEYS = {
    "funding",
    "funding_pctile_90d",
    "funding_trend",
    "ls_ratio_all",
    "ls_ratio_top_acc",
    "ls_ratio_top_pos",
    "taker_bs_ratio",
    "oi_change_24h",
    "oi_price_divergence",
}


def _dp(value) -> dict:
    """四元组包装（与 nodes._dp 同构，测试专用）。"""
    return {
        "value": value,
        "source": "test",
        "timestamp": 0,
        "confidence": 1.0 if value is not None else 0.0,
    }


def _fund(kind: str = "protocol", **over: object) -> dict:
    """构造 fundamental 快照（mock 协议数据同构，值可预期）。"""
    base = {
        "kind": kind,
        "name": "uniswap",
        "category": "DEX",
        "resolved": True,
        "tvl": _dp(1005.0),
        "tvl_change_7d": _dp(2.5),
        "tvl_change_30d": _dp(10.0),
        "tvl_change_1d": _dp(0.5),
        "mcap": _dp(500.0),
        "fdv": _dp(800.0),
        "fees_24h": _dp(10.0),
        "fees_7d": _dp(70.0),
        "revenue_24h": _dp(5.0),
        "revenue_7d": _dp(35.0),
        "stablecoin_supply": _dp(None),
        "dex_volume_24h": _dp(None),
        "error": None,
        "incomplete": False,
    }
    base.update(over)
    return base


def _mkt(**over: object) -> dict:
    """构造 market 快照。"""
    base = {
        "price": _dp(70000.0),
        "change_24h": _dp(1.0),
        "quote_volume_24h": _dp(1e9),
        "change_7d": _dp(0.0),
        "change_30d": _dp(5.0),
        "change_90d": _dp(10.0),
        "change_1y": _dp(20.0),
        "funding": _dp(0.0001),
        "funding_avg_7d": _dp(0.0001),
        "funding_trend": _dp("rising"),
        "funding_pctile_90d": _dp(50.0),
        "oi": _dp(1e9),
        "basis": _dp(0.0),
        "taker_buy_ratio_24h": _dp(0.5),
        "listing_days": _dp(1800),
        "error": None,
        "futures_error": None,
        "incomplete": False,
    }
    base.update(over)
    return base


def _ms(**over: object) -> dict:
    """构造 microstructure 快照。"""
    base = {
        "oi_change_24h": _dp(2.0),
        "oi_price_divergence": _dp(
            {"label": "confirm_long", "note": "价涨 OI 增：新多进场，趋势确认"}
        ),
        "oi_change_48h": _dp(3.0),
        "oi_value_change_24h": _dp(4.0),
        "ls_ratio_all": _dp(1.05),
        "ls_ratio_all_change_24h": _dp(0.1),
        "ls_ratio_top_acc": _dp(1.2),
        "ls_ratio_top_pos": _dp(1.1),
        "taker_bs_ratio": _dp(1.0),
        "board": None,
        "error": None,
        "incomplete": False,
    }
    base.update(over)
    return base


# ── valuation_ratios ───────────────────────────────────────


def test_valuation_ratios() -> None:
    """正常 / 缺失 / 异常三分支：protocol 四比率（年化口径）；缺失绝不猜测；除零→None。"""
    v = sig.valuation_ratios(_fund(), _mkt())["value"]
    assert v["mc_fees"] == pytest.approx(500.0 / (10.0 * 365.0))
    assert v["fdv_revenue"] == pytest.approx(800.0 / (5.0 * 365.0))
    assert v["mc_tvl"] == pytest.approx(500.0 / 1005.0)
    assert v["fees_tvl"] == pytest.approx((10.0 * 365.0) / 1005.0)
    # 缺失：输入缺失 / chain 类无 mcap/fdv/fees → 全 None
    assert sig.valuation_ratios(None, None)["value"] == {
        "mc_fees": None,
        "fdv_revenue": None,
        "mc_tvl": None,
        "fees_tvl": None,
    }
    chain = _fund(
        kind="chain",
        mcap=_dp(None),
        fdv=_dp(None),
        fees_24h=_dp(None),
        revenue_24h=_dp(None),
    )
    assert all(x is None for x in sig.valuation_ratios(chain, _mkt())["value"].values())
    # 异常：除零 / 非数值字段 → 对应比率 None（UNKNOWN 纪律）
    v2 = sig.valuation_ratios(_fund(fees_24h=_dp(0.0)), _mkt())["value"]
    assert v2["mc_fees"] is None  # 除零
    assert v2["fees_tvl"] == 0.0  # 零费用协议 → 0.0 而非 None
    v3 = sig.valuation_ratios(_fund(mcap=_dp("n/a")), _mkt())["value"]
    assert v3["mc_fees"] is None and v3["mc_tvl"] is None


# ── momentum_score ─────────────────────────────────────────


def test_momentum() -> None:
    """正常 / 缺失 / 异常：tvl_change 各 0.5 加权；任一窗口缺失或非数值 → None。"""
    assert sig.momentum_score(_fund())["value"] == pytest.approx(6.25)
    assert sig.momentum_score(None)["value"] is None
    assert sig.momentum_score(_fund(tvl_change_30d=_dp(None)))["value"] is None
    assert sig.momentum_score(_fund(tvl_change_7d=_dp("x")))["value"] is None


# ── divergence ─────────────────────────────────────────────


def test_divergence() -> None:
    """四象限 + 缺失 + 异常：7d 窗口 0 为界 I/II/III/IV；任一缺失或非数值 → None。"""
    d = sig.divergence(_fund(tvl_change_7d=_dp(5.0)), _mkt(change_7d=_dp(3.0)))["value"]
    assert d["divergence_7d"] == pytest.approx(2.0)
    assert d["divergence_30d"] == pytest.approx(10.0 - 5.0)
    assert d["quadrant"] == "I"
    assert (
        sig.divergence(_fund(tvl_change_7d=_dp(-2.0)), _mkt(change_7d=_dp(3.0)))[
            "value"
        ]["quadrant"]
        == "II"
    )
    assert (
        sig.divergence(_fund(tvl_change_7d=_dp(5.0)), _mkt(change_7d=_dp(-3.0)))[
            "value"
        ]["quadrant"]
        == "III"
    )
    assert (
        sig.divergence(_fund(tvl_change_7d=_dp(-5.0)), _mkt(change_7d=_dp(-3.0)))[
            "value"
        ]["quadrant"]
        == "IV"
    )
    # 缺失
    assert sig.divergence(None, None)["value"] == {
        "divergence_7d": None,
        "divergence_30d": None,
        "quadrant": None,
    }
    d2 = sig.divergence(_fund(), _mkt(change_7d=_dp(None)))["value"]
    assert d2["divergence_7d"] is None and d2["quadrant"] is None
    assert d2["divergence_30d"] is not None
    # 异常
    d3 = sig.divergence(_fund(), _mkt(change_7d=_dp("x")))["value"]
    assert d3["divergence_7d"] is None and d3["quadrant"] is None


# ── funding_percentile ────────────────────────────────────


def test_funding_percentile() -> None:
    """最新 |funding| 在窗口分布中的分位（0-100），时间升序最新在末尾。"""
    # 三档周期序列（0.0001/0.00015/0.0002 各 4 个），最新为最大档 → 100
    hist = [
        {"funding_time": i, "funding_rate": 0.0001 * (1 + (i % 3) * 0.5)}
        for i in range(12)
    ]
    assert sig.funding_percentile(hist) == 100.0
    # 最新改为最小档（0.0001 出现 5 次/12）→ 41.7
    hist[-1]["funding_rate"] = 0.0001
    assert sig.funding_percentile(hist) == pytest.approx(41.7, abs=0.1)
    # 中位：等差 11 个，最新为中位值 → 50 附近
    vals = [0.0001 + i * 0.00001 for i in range(11)]
    hist_mid = [{"funding_time": i, "funding_rate": v} for i, v in enumerate(vals)]
    hist_mid[-1]["funding_rate"] = 0.00015
    assert sig.funding_percentile(hist_mid) == pytest.approx(54.5, abs=0.1)


def test_funding_percentile_degenerate_and_abnormal() -> None:
    """样本不足 / 空 / 常数序列（无分位信息）→ None；非法值过滤后计算。"""
    hist = [
        {"funding_time": i, "funding_rate": 0.0001 * (1 + i * 0.1)}
        for i in range(9)
    ]
    assert sig.funding_percentile(hist) is None  # 样本 <10
    assert sig.funding_percentile(None) is None
    assert sig.funding_percentile([]) is None
    const = [{"funding_time": i, "funding_rate": 0.0001} for i in range(20)]
    assert sig.funding_percentile(const) is None  # 分布退化
    # 异常：样本充足（15）时，最新值非法 → None；序列含非法但最新合法 → 过滤后计算
    hist15 = [
        {"funding_time": i, "funding_rate": 0.0001 * (1 + i * 0.1)}
        for i in range(15)
    ]
    bad = [*hist15, {"funding_time": 99, "funding_rate": "oops"}]
    assert sig.funding_percentile(bad) is None  # 最新值非法
    hist15[3]["funding_rate"] = "bad"
    assert sig.funding_percentile(hist15) is not None  # 序列含非法但最新合法


# ── oi_price_divergence ────────────────────────────────────


def test_oi_price_divergence() -> None:
    """四象限：价 OI 同向 = 新仓确认，背离 = 存量换手弱势；缺失/零值 → none。"""
    assert sig.oi_price_divergence(2.0, 5.0)["label"] == "confirm_long"
    assert sig.oi_price_divergence(2.0, -5.0)["label"] == "weak_long"
    assert sig.oi_price_divergence(-2.0, 5.0)["label"] == "confirm_short"
    assert sig.oi_price_divergence(-2.0, -5.0)["label"] == "weak_short"
    assert sig.oi_price_divergence(None, 5.0) is None
    assert sig.oi_price_divergence(2.0, None) is None
    for price, oi in ((0.0, 5.0), (2.0, 0.0)):
        got = sig.oi_price_divergence(price, oi)
        assert got["label"] == "none"
        assert got["note"]


# ── sentiment_raw ──────────────────────────────────────────


def test_sentiment_raw() -> None:
    """正常 / 缺失 / 异常：持仓指标直读（不打分）；输入缺失 → 全 None。"""
    s = sig.sentiment_raw(_mkt(), _ms())
    assert s["components"] == {
        "funding": 0.0001,
        "funding_pctile_90d": 50.0,
        "funding_trend": "rising",
        "ls_ratio_all": 1.05,
        "ls_ratio_top_acc": 1.2,
        "ls_ratio_top_pos": 1.1,
        "taker_bs_ratio": 1.0,
        "oi_change_24h": 2.0,
        "oi_price_divergence": {
            "label": "confirm_long",
            "note": "价涨 OI 增：新多进场，趋势确认",
        },
    }
    assert "DECIDE_PROMPT" in s["note"]
    # 缺失
    s2 = sig.sentiment_raw(None, None)
    assert all(v is None for v in s2["components"].values())
    assert s2["note"]
    # 异常：ms 缺失 / 字段非 dict → 对应 None；mkt 正常字段不受影响
    s3 = sig.sentiment_raw(_mkt(), None)
    assert s3["components"]["funding"] == 0.0001
    assert s3["components"]["ls_ratio_all"] is None
    s4 = sig.sentiment_raw(_mkt(), {"ls_ratio_all": "oops"})
    assert s4["components"]["ls_ratio_all"] is None


# ── 趋势特征（01 票：历史序列确定性提炼） ────────────────


def test_series_change() -> None:
    """最新 vs N 天前变化 %：ISO 与 unix 秒日期兼容；数据不足/非法 → None。"""
    rows = [
        {"date": "2026-08-01T00:00:00+00:00", "supply": 100.0},
        {"date": "2026-08-15T00:00:00+00:00", "supply": 110.0},
        {"date": "2026-08-31T00:00:00+00:00", "supply": 121.0},
    ]
    # 30 天窗口：cutoff=8-01 含首点 → (121-100)/100
    assert sig.series_change(rows, "supply", 30) == pytest.approx(21.0)
    # 15 天窗口：cutoff=8-16 排除首点 → 取 8-15 → (121-110)/110
    assert sig.series_change(rows, "supply", 15) == pytest.approx(10.0)
    # unix 秒日期兼容（升序，最新在末尾）
    ts = [{"date": 1700000000 + i * 86400, "supply": 100.0 + i} for i in range(11)]
    assert sig.series_change(ts, "supply", 5) == pytest.approx((110.0 - 105.0) / 105.0 * 100.0)
    # 缺失 / 非法：空、单点、窗口天数 ≤0、prev 值 ≤0
    assert sig.series_change(None, "supply", 30) is None
    assert sig.series_change([], "supply", 30) is None
    assert sig.series_change([{"date": 1, "supply": 5.0}], "supply", 30) is None
    assert sig.series_change(rows, "supply", 0) is None
    bad = [{"date": 1, "supply": 0.0}, {"date": 86401, "supply": 100.0}]
    assert sig.series_change(bad, "supply", 1) is None
    # 非法日期/数值点跳过，合法点照常计算
    mixed = [
        {"date": "oops", "supply": 100.0},
        {"date": 1700000000, "supply": "n/a"},
        {"date": 1697580800, "supply": 110.0},
        {"date": 1700172800, "supply": 121.0},
    ]
    assert sig.series_change(mixed, "supply", 30) == pytest.approx(10.0)


def test_series_trend() -> None:
    """前后半段均值比分档：≥+3% rising / ≤-3% falling / 其余 flat；窗口不足 → None。"""
    up = [{"date": i, "tvl": 100.0 + 3 * i} for i in range(8)]
    assert sig.series_trend(up, "tvl", 30) == "rising"
    down = [{"date": i, "tvl": 130.0 - 3 * i} for i in range(8)]
    assert sig.series_trend(down, "tvl", 30) == "falling"
    flat = [{"date": i, "tvl": 100.0} for i in range(8)]
    assert sig.series_trend(flat, "tvl", 30) == "flat"
    wobble = [{"date": i, "tvl": 100.0 + (i % 2)} for i in range(8)]
    assert sig.series_trend(wobble, "tvl", 30) == "flat"
    # 窗口过滤：仅最近 days 天内的点参与
    old = [{"date": i - 100, "tvl": 10.0} for i in range(5)]
    recent = [{"date": i, "tvl": 100.0 + 3 * i} for i in range(8)]
    assert sig.series_trend([*old, *recent], "tvl", 30) == "rising"
    # 缺失 / 数据不足
    assert sig.series_trend(None, "tvl", 30) is None
    assert sig.series_trend([], "tvl", 30) is None
    assert sig.series_trend([{"date": 1, "tvl": 1.0}], "tvl", 30) is None
    assert sig.series_trend([{"date": i, "tvl": 1.0} for i in range(3)], "tvl", 30) is None


# ── compute_signals 节点 ───────────────────────────────────


def _mock_state() -> dict:
    """mock 模式先 collect_data（与 test_collect 同构），再补 tokens 喂 compute_signals。"""
    state = nodes.collect_data({"tokens": list(MOCK_TOKENS)})
    state["tokens"] = list(MOCK_TOKENS)
    return state


def test_compute_signals_mock_full() -> None:
    """mock 模式：全 6 token 产出，结构同构，kind 分支与真实一致。"""
    res = nodes.compute_signals(_mock_state())
    sigs = res["signals"]
    assert set(sigs) == set(MOCK_TOKENS)
    for symbol in MOCK_TOKENS:
        assert set(sigs[symbol]) == {
            "symbol",
            "valuation",
            "momentum",
            "divergence",
            "sentiment",
            "error",
        }
        assert sigs[symbol]["error"] is None
        assert set(sigs[symbol]["sentiment"]["components"]) == _COMPONENT_KEYS
        assert "note" in sigs[symbol]["sentiment"]
    # protocol（UNI）：估值全有值；chain（BTC）：mcap/fdv/fees 结构性缺失 → None
    uni = sigs["UNI"]
    tvl_uni = m.mock_protocol_tvl("uniswap")["tvl"]  # 1007（len("uniswap")=7）
    assert uni["valuation"]["value"]["mc_fees"] == pytest.approx(500.0 / (10.0 * 365.0))
    assert uni["valuation"]["value"]["mc_tvl"] == pytest.approx(500.0 / tvl_uni)
    assert uni["momentum"]["value"] == pytest.approx(6.25)
    assert uni["divergence"]["value"]["quadrant"] == "III"  # 基本强 + 价平
    assert uni["sentiment"]["components"]["funding_trend"] == "falling"
    btc = sigs["BTC"]
    assert all(v is None for v in btc["valuation"]["value"].values())
    assert btc["momentum"]["value"] is not None  # 链类有 tvl_change
    assert res["meta"]["node_order"][-1] == "compute_signals"


def _assert_approx(a: object, b: object) -> None:
    """递归近似断言：dict 逐键、数值 approx、其余相等（None/str/bool）。"""
    if isinstance(a, dict) and isinstance(b, dict):
        assert set(a) == set(b)
        for k in a:
            _assert_approx(a[k], b[k])
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        assert a == pytest.approx(b)
    else:
        assert a == b


def test_mock_signals_consistent_with_pure_functions() -> None:
    """mock 同构强契约：mock_signals_data 与同一快照上的纯函数输出逐值一致。"""
    state = _mock_state()
    for symbol in MOCK_TOKENS:
        fund = state["fundamental_data"][symbol]
        mkt = state["market_data"][symbol]
        ms = state["microstructure_data"][symbol]
        want = {
            "valuation": sig.valuation_ratios(fund, mkt),
            "momentum": sig.momentum_score(fund),
            "divergence": sig.divergence(fund, mkt),
            "sentiment": sig.sentiment_raw(mkt, ms),
        }
        got = m.mock_signals_data(symbol, fund["kind"])
        for key, value in want.items():
            _assert_approx(got[key], value)
        assert got["error"] is None and got["symbol"] == symbol


def test_compute_signals_real_assembly(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实模式（手写快照）：纯函数装配值可预期；缺失输入 → None。"""
    monkeypatch.setenv("SR_MOCK", "0")
    state = {
        "tokens": ["UNI", "BTC", "ZZZ"],
        "fundamental_data": {
            "UNI": _fund(),
            "BTC": _fund(
                kind="chain",
                mcap=_dp(None),
                fdv=_dp(None),
                fees_24h=_dp(None),
                revenue_24h=_dp(None),
            ),
            "ZZZ": _fund(
                kind="unknown",
                tvl=_dp(None),
                mcap=_dp(None),
                tvl_change_7d=_dp(None),
                tvl_change_30d=_dp(None),
            ),
        },
        "market_data": {"UNI": _mkt(), "BTC": _mkt(), "ZZZ": _mkt()},
        "microstructure_data": {"UNI": _ms(), "BTC": _ms(), "ZZZ": _ms()},
        "meta": {"node_order": ["collect_data"]},
    }
    res = nodes.compute_signals(state)
    sigs = res["signals"]

    uni = sigs["UNI"]
    assert uni["error"] is None
    assert uni["valuation"]["value"]["mc_fees"] == pytest.approx(500.0 / (10.0 * 365.0))
    assert uni["momentum"]["value"] == pytest.approx(6.25)
    assert uni["divergence"]["value"]["quadrant"] == "III"
    assert uni["sentiment"]["components"]["funding"] == 0.0001
    assert uni["sentiment"]["components"]["ls_ratio_top_acc"] == 1.2

    btc = sigs["BTC"]
    assert all(v is None for v in btc["valuation"]["value"].values())
    assert btc["divergence"]["value"]["quadrant"] == "III"

    zzz = sigs["ZZZ"]
    assert zzz["momentum"]["value"] is None  # tvl_change 缺失 → UNKNOWN
    assert zzz["divergence"]["value"]["quadrant"] is None
    assert res["meta"]["node_order"] == ["collect_data", "compute_signals"]


def test_compute_signals_single_error_not_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实模式单 token 纯函数异常：仅该 token 置 {symbol, error}，批不中断。"""
    monkeypatch.setenv("SR_MOCK", "0")
    orig = sig.valuation_ratios

    def boom(fund, mkt):
        if (fund or {}).get("kind") == "protocol":
            raise RuntimeError("注入失败")
        return orig(fund, mkt)

    monkeypatch.setattr(sig, "valuation_ratios", boom)
    state = {
        "tokens": ["UNI", "BTC"],
        "fundamental_data": {"UNI": _fund(), "BTC": _fund(kind="chain")},
        "market_data": {"UNI": _mkt(), "BTC": _mkt()},
        "microstructure_data": {"UNI": _ms(), "BTC": _ms()},
    }
    res = nodes.compute_signals(state)
    assert res["signals"]["UNI"] == {
        "symbol": "UNI",
        "error": "信号计算异常: 注入失败",
    }
    assert res["signals"]["BTC"]["error"] is None
    assert res["signals"]["BTC"]["momentum"]["value"] is not None


def test_compute_signals_mock_error_not_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mock 模式单 token mock_signals_data 异常：同 {symbol, error} 不阻断。"""
    orig = m.mock_signals_data

    def boom(symbol: str, kind: str | None = None):
        if symbol == "XRP":
            raise RuntimeError("注入失败")
        return orig(symbol, kind)

    monkeypatch.setattr(m, "mock_signals_data", boom)
    res = nodes.compute_signals(_mock_state())
    assert res["signals"]["XRP"]["error"].startswith("信号计算异常")
    assert res["signals"]["BTC"]["error"] is None
