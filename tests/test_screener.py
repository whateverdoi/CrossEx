"""03 票 RED：screener 确定性筛选 + main 模式互斥。

验收映射：次新过滤 / 波动榜排序 / 稳定币排除 / 快照失败抛 ScreeningError /
rank 空注册表不抛异常 / mock 固定 6 候选 / 手动模式 meta.screening.mode。
"""

from __future__ import annotations

import pytest

from strategy_research.datasources import binance, binance_futures
from strategy_research.main import _resolve_tokens, main, parse_args
from strategy_research.screener import (
    DEFAULT_RULES,
    ScreeningError,
    ScreenRule,
    select_tokens,
)

UNKNOWN = "UNKNOWN"


def _row(symbol: str, change=None, vol=None, days=None) -> dict:
    row = {"symbol": symbol}
    if change is not None:
        row["price_change_pct"] = change
    if vol is not None:
        row["quote_volume"] = vol
    if days is not None:
        row["listing_days"] = days
    return row


# ── Filter 注册表 ──────────────────────────────────────────


def test_filter_listing_days_lt_keeps_recent_only():
    from strategy_research.screener import FILTERS

    rows = [
        _row("A", days=45),
        _row("B", days=100),
        _row("C", days=101),
        _row("D"),
    ]  # listing_days 缺失（UNKNOWN 保守排除）
    out = FILTERS["listing_days_lt"]({"max_days": 100}).apply(rows)
    assert [r["symbol"] for r in out] == ["A", "B"]


def test_filter_min_quote_volume_keeps_above_floor():
    from strategy_research.screener import FILTERS

    rows = [
        _row("A", vol=1e8),
        _row("B", vol=1e7),
        _row("C", vol=5e6),
        _row("D"),
    ]  # 缺失排除
    out = FILTERS["min_quote_volume"]({"min_quote_volume": 1e7}).apply(rows)
    assert [r["symbol"] for r in out] == ["A", "B"]


def test_filter_exclude_stablecoins():
    from strategy_research.screener import FILTERS

    rows = [
        _row("BTCUSDT"),
        _row("USDCUSDT"),
        _row("FDUSDUSDT"),
        _row("TUSDUSDT"),
        _row("BTCFDUSD"),
        _row("FRAXUSDT"),
        _row("USDDUSDT"),
    ]
    out = FILTERS["exclude_stablecoins"]({}).apply(rows)
    assert [r["symbol"] for r in out] == ["BTCUSDT", "BTCFDUSD"]


def test_stablecoin_pair_boundaries():
    """边界：稳定币 base + 非稳定币 quote 保留；单段 symbol 不崩。"""
    from strategy_research.screener import FILTERS

    rows = [_row("USDCBTC"), _row("USDT"), _row("BTC")]
    out = FILTERS["exclude_stablecoins"]({}).apply(rows)
    assert [r["symbol"] for r in out] == ["USDCBTC", "USDT", "BTC"]


# ── Rank 注册表 ────────────────────────────────────────────


def test_rank_volatility_24h_sorts_by_abs_change_desc():
    from strategy_research.screener import RANKERS

    rows = [_row("A", change=2.0), _row("B", change=-5.0), _row("C", change=0.5)]
    out = RANKERS["volatility_24h"]({}).apply(rows)
    assert [r["symbol"] for r in out] == ["B", "A", "C"]


def test_rank_gain_24h_sorts_by_change_desc():
    from strategy_research.screener import RANKERS

    rows = [_row("A", change=2.0), _row("B", change=-5.0), _row("C", change=0.5)]
    out = RANKERS["gain_24h"]({}).apply(rows)
    assert [r["symbol"] for r in out] == ["A", "C", "B"]


def test_rank_loss_24h_sorts_by_change_asc():
    from strategy_research.screener import RANKERS

    rows = [_row("A", change=2.0), _row("B", change=-5.0), _row("C", change=0.5)]
    out = RANKERS["loss_24h"]({}).apply(rows)
    assert [r["symbol"] for r in out] == ["B", "C", "A"]


def test_rank_quote_volume_sorts_by_volume_desc():
    from strategy_research.screener import RANKERS

    rows = [_row("A", vol=1e6), _row("B", vol=1e9), _row("C", vol=1e8)]
    out = RANKERS["quote_volume"]({}).apply(rows)
    assert [r["symbol"] for r in out] == ["B", "C", "A"]


def test_rank_mispricing_24h_prefers_weak_price_high_volume():
    """错价榜：价格弱 + 成交活跃优先（pct 升序排名 + vol 降序排名等权）。"""
    from strategy_research.screener import RANKERS

    rows = [
        _row("A", change=2.0, vol=1e9),
        _row("B", change=-5.0, vol=5e8),
        _row("C", change=-3.0, vol=1e8),
        _row("D", change=-1.0, vol=1e6),
    ]
    out = RANKERS["mispricing_24h"]({}).apply(rows)
    # score（越小越优先）: B=0+1=1 → A=3+0=3 / C=1+2=3 → D=2+3=5
    assert [r["symbol"] for r in out] == ["B", "A", "C", "D"]


def test_rank_mispricing_24h_missing_metric_last():
    """任一指标缺失（含 UNKNOWN 字符串）→ 排最后（保守纪律）。"""
    from strategy_research.screener import RANKERS

    rows = [
        _row("A", change=2.0, vol=5e8),
        _row("B", change=-5.0, vol=1e9),
        _row("C"),
        _row("D", change=-3.0),
        _row("E", vol=1e8),
    ]
    out = RANKERS["mispricing_24h"]({}).apply(rows)
    # score: B=0+0=0 → A=1+1=2；C/D/E 缺失排最后（保序）
    assert [r["symbol"] for r in out] == ["B", "A", "C", "D", "E"]


def test_rank_volatility_abs_false_keeps_sign():
    """abs=False 时按原始涨跌降序（负数在前）。"""
    from strategy_research.screener import RANKERS

    rows = [_row("A", change=2.0), _row("B", change=-5.0), _row("C", change=0.5)]
    out = RANKERS["volatility_24h"]({"abs": False}).apply(rows)
    assert [r["symbol"] for r in out] == ["A", "C", "B"]


def test_rank_missing_metric_goes_last():
    from strategy_research.screener import RANKERS

    rows = [_row("A"), _row("B", change=5.0), _row("C", change=-3.0)]
    out = RANKERS["volatility_24h"]({}).apply(rows)
    assert [r["symbol"] for r in out] == ["B", "C", "A"]


def test_rank_unknown_string_metric_goes_last():
    """UNKNOWN 字符串指标与 None 同权：保守排最后。"""
    from strategy_research.screener import RANKERS

    rows = [_row("A", change=UNKNOWN), _row("B", change=5.0)]
    out = RANKERS["volatility_24h"]({}).apply(rows)
    assert [r["symbol"] for r in out] == ["B", "A"]


# ── select_tokens 集成（真实模式，注入 fetch） ─────────────


def _fake_tickers() -> list[dict]:
    return [
        {"symbol": "BTCUSDT", "price_change_pct": 2.5, "quote_volume": 1.2e9},
        {"symbol": "SOLUSDT", "price_change_pct": 5.2, "quote_volume": 3.0e8},
        {"symbol": "NEWUSDT", "price_change_pct": -3.1, "quote_volume": 5e8},
        {"symbol": "OLDUSDT", "price_change_pct": 1.0, "quote_volume": 2.0e8},
        {"symbol": "USDCUSDT", "price_change_pct": 0.1, "quote_volume": 9.9e9},
        # 真实全市场 24hr ticker 的噪声对：交叉对/指数类/杠杆代币
        {"symbol": "ETHBTC", "price_change_pct": 1.0, "quote_volume": 3.0e8},
        {"symbol": "BTCUSD1", "price_change_pct": 2.4, "quote_volume": 3.4e8},
        {"symbol": "BTCU", "price_change_pct": 2.5, "quote_volume": 1.3e8},
        {"symbol": "ETHU", "price_change_pct": 1.7, "quote_volume": 2.8e7},
    ]


def _fake_exchange_info() -> dict:
    """现货 exchangeInfo：白名单 = TRADING + USDT 计价 + 标的非稳定币。"""
    return {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC"},
            {"symbol": "SOLUSDT", "status": "TRADING", "baseAsset": "SOL"},
            {"symbol": "NEWUSDT", "status": "TRADING", "baseAsset": "NEW"},
            {"symbol": "OLDUSDT", "status": "TRADING", "baseAsset": "OLD"},
            # 以下全部应被白名单排除
            {"symbol": "USDCUSDT", "status": "TRADING", "baseAsset": "USDC"},
            {"symbol": "ETHBTC", "status": "TRADING", "baseAsset": "ETH"},
            {"symbol": "BTCUSD1", "status": "TRADING", "baseAsset": "BTC"},
            {"symbol": "BTCU", "status": "TRADING", "baseAsset": "BTC"},
            {"symbol": "ETHU", "status": "TRADING", "baseAsset": "ETH"},
            {"symbol": "SUSPENDUSDT", "status": "BREAK", "baseAsset": "SUSPEND"},
        ]
    }


def _fake_listing() -> dict[str, int]:
    return {
        "BTCUSDT": 2000,
        "SOLUSDT": 90,
        "NEWUSDT": 45,
        "OLDUSDT": 101,
        "USDCUSDT": 500,
    }


def _patch_fetch(monkeypatch) -> None:
    monkeypatch.setenv("SR_MOCK", "0")
    monkeypatch.setattr(binance, "fetch_ticker_24h_all", _fake_tickers)
    monkeypatch.setattr(binance, "fetch_exchange_info", _fake_exchange_info)
    monkeypatch.setattr(binance_futures, "fetch_listing_days", _fake_listing)


def test_select_tokens_auto_filters_ranks_and_annotates(monkeypatch):
    _patch_fetch(monkeypatch)
    result = select_tokens(DEFAULT_RULES, top_n=3)

    assert result.mode == "auto"
    assert result.rules == [
        "listing_days_lt(max_days=100)",
        "min_quote_volume(min_quote_volume=10000000.0)",
        "exclude_stablecoins()",
        "mispricing_24h()",
    ]
    # 过滤掉 OLDUSDT(101 天)、USDCUSDT(稳定币) 与 BTCUSDT(2000 天次新外)；
    # 错价榜：NEW(价跌量高) score=0 → SOL(价涨量中) score=2（与波动榜 SOL 居首区分）
    assert [c["symbol"] for c in result.candidates] == [
        "NEWUSDT",
        "SOLUSDT",
    ]
    c = result.candidates[0]
    assert "mispricing_24h" in c["reason"]
    assert c["metrics"]["price_change_pct"] == -3.1
    assert c["metrics"]["listing_days"] == 45


def test_select_tokens_snapshot_failure_raises_screening_error(monkeypatch):
    monkeypatch.setenv("SR_MOCK", "0")
    monkeypatch.setattr(binance, "fetch_ticker_24h_all", lambda: None)
    monkeypatch.setattr(binance, "fetch_exchange_info", _fake_exchange_info)
    monkeypatch.setattr(binance_futures, "fetch_listing_days", _fake_listing)
    with pytest.raises(ScreeningError):
        select_tokens(DEFAULT_RULES)

    monkeypatch.setattr(binance, "fetch_ticker_24h_all", _fake_tickers)
    monkeypatch.setattr(binance, "fetch_exchange_info", lambda: None)
    monkeypatch.setattr(binance_futures, "fetch_listing_days", lambda: None)
    with pytest.raises(ScreeningError, match="批终止"):
        select_tokens(DEFAULT_RULES)

    # exchangeInfo 失败同样批终止（规格：ticker + exchangeInfo 各 1 次，任一失败终止）
    monkeypatch.setattr(binance, "fetch_ticker_24h_all", _fake_tickers)
    monkeypatch.setattr(binance, "fetch_exchange_info", lambda: None)
    monkeypatch.setattr(binance_futures, "fetch_listing_days", _fake_listing)
    with pytest.raises(ScreeningError, match="批终止"):
        select_tokens(DEFAULT_RULES)


def test_select_tokens_whitelist_filters_noise_pairs(monkeypatch):
    """现货 USDT 白名单：交叉对/指数类/杠杆代币/非 TRADING/稳定币标的全部排除。"""
    _patch_fetch(monkeypatch)
    result = select_tokens(
        [ScreenRule("rank", "quote_volume", {})], top_n=50
    )
    symbols = [c["symbol"] for c in result.candidates]
    assert "BTCUSDT" in symbols
    for noise in ("ETHBTC", "BTCUSD1", "BTCU", "ETHU", "USDCUSDT", "SUSPENDUSDT"):
        assert noise not in symbols, f"白名单应排除 {noise}"


def test_select_tokens_no_rank_rules_skips_sorting(monkeypatch):
    _patch_fetch(monkeypatch)
    rules = [ScreenRule("filter", "listing_days_lt", {"max_days": 100})]
    result = select_tokens(rules, top_n=2)
    # 无 rank：保持 ticker 原始顺序（不抛异常，防 StopIteration）
    assert [c["symbol"] for c in result.candidates] == ["SOLUSDT", "NEWUSDT"]


def test_select_tokens_unknown_fields_conservative(monkeypatch):
    monkeypatch.setenv("SR_MOCK", "0")
    monkeypatch.setattr(
        binance, "fetch_ticker_24h_all", lambda: [{"symbol": "NOINFOUSDT"}]
    )  # 全字段缺失
    monkeypatch.setattr(binance_futures, "fetch_listing_days", dict)
    result = select_tokens(DEFAULT_RULES, top_n=10)
    assert result.candidates == []  # UNKNOWN 保守排除，无候选但不抛


def test_select_tokens_unknown_filter_rule_raises(monkeypatch):
    _patch_fetch(monkeypatch)
    with pytest.raises(ScreeningError, match="未知筛选规则"):
        select_tokens([ScreenRule("filter", "no_such_filter")])


def test_select_tokens_unknown_rank_rule_raises(monkeypatch):
    _patch_fetch(monkeypatch)
    with pytest.raises(ScreeningError, match="未知排序规则"):
        select_tokens([ScreenRule("rank", "no_such_rank")])


def test_select_tokens_unknown_rule_kind_raises(monkeypatch):
    _patch_fetch(monkeypatch)
    with pytest.raises(ScreeningError, match="未知规则类别"):
        select_tokens([ScreenRule("fliter", "listing_days_lt")])


def test_select_tokens_multiple_rank_rules_raises(monkeypatch):
    _patch_fetch(monkeypatch)
    with pytest.raises(ScreeningError, match="单一 rank"):
        select_tokens(
            [
                ScreenRule("rank", "volatility_24h"),
                ScreenRule("rank", "gain_24h"),
            ]
        )


# ── mock 模式 ──────────────────────────────────────────────


def test_select_tokens_mock_mode_fixed_candidates_no_io(monkeypatch):
    monkeypatch.setenv("SR_MOCK", "1")

    def _fail(*args, **kwargs):
        raise AssertionError("mock 模式不应发起外部请求")

    monkeypatch.setattr(binance, "fetch_ticker_24h_all", _fail)
    monkeypatch.setattr(binance_futures, "fetch_listing_days", _fail)
    result = select_tokens(DEFAULT_RULES)
    assert result.mode == "mock"
    assert [c["symbol"] for c in result.candidates] == [
        "BTC",
        "ETH",
        "SOL",
        "UNI",
        "DOGE",
        "XRP",
    ]
    assert all(c["reason"] == "mock 固定候选" for c in result.candidates)


# ── main 模式互斥 ──────────────────────────────────────────


def test_main_manual_tokens_skip_screening():
    tokens, screening = _resolve_tokens(parse_args(["--tokens", "btc, ETH"]))
    assert tokens == ["BTC", "ETH"]
    assert screening == {"mode": "manual"}


def test_main_manual_tokens_via_env(monkeypatch):
    monkeypatch.setenv("SR_TOKENS", "SOL,XRP")
    tokens, screening = _resolve_tokens(parse_args([]))
    assert tokens == ["SOL", "XRP"]
    assert screening["mode"] == "manual"


def test_main_auto_mode_uses_screener(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SR_MOCK", "1")
    meta = main([])
    screening = meta["screening"]
    assert screening["mode"] == "mock"
    assert [c["symbol"] for c in screening["candidates"]] == [
        "BTC",
        "ETH",
        "SOL",
        "UNI",
        "DOGE",
        "XRP",
    ]
    assert screening["rules"]  # describe 落盘，报告可审计
