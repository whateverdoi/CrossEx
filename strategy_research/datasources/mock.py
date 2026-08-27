"""mock 数据源：SR_MOCK=1 显式离线模式，与真实路径字段同构。

每个 mock 函数对应一个真实 fetch 函数（见 binance.py / binance_futures.py
/ defillama.py / web.py），字段名、类型与真实响应解析后完全一致。
symbol 命名空间沿用交易所格式（如 ``BTCUSDT``），与真实 ticker 一致。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from strategy_research.context import SENTIMENT_NOTE

#: mock 固定候选（规格 ⑨ 伪代码：6 个，裸 symbol 名）
MOCK_TOKENS = ["BTC", "ETH", "SOL", "UNI", "DOGE", "XRP"]

#: 各币种基准价格（USD）与 24h 涨跌幅（%）/ 24h 成交额（USDT）
_MOCK_MARKET = {
    "BTC": (70000.0, 2.5, 1.2e9),
    "ETH": (3500.0, 1.8, 8.0e8),
    "SOL": (150.0, 5.2, 3.0e8),
    "UNI": (10.0, -3.1, 1.5e8),
    "DOGE": (0.15, -1.2, 2.0e8),
    "XRP": (0.60, 0.6, 4.0e8),
}


#: 各币种上市天数（合约 onboardDate 口径，mock_exchange_info 数据源）
_MOCK_LISTING_DAYS = {
    "BTC": 1800,
    "ETH": 1500,
    "SOL": 800,
    "UNI": 700,
    "DOGE": 1100,
    "XRP": 1300,
}


def _price(symbol: str) -> float:
    return _MOCK_MARKET.get(symbol, (100.0, 0.0, 1e8))[0]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _day_open_ms() -> int:
    """当日 00:00 UTC 毫秒（mock 日线 open_time 基准）。

    同日多次调用同值（不用 _now_ms）：跨序列按 open_time 对齐的场景
    （signals.beta_alpha 等）要求两次 mock_klines 调用的时间戳严格一致，
    毫秒级错开会把交集清空。
    """
    now = datetime.now(timezone.utc)
    return int(
        datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp() * 1000
    )


def _iso_days_ago(days: int) -> str:
    """ISO 8601 时间戳（N 天前），与 web 真实条目 date 字段同构。"""
    ts = datetime.now(timezone.utc).timestamp() - days * 86_400
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def mock_screening_candidates() -> list[dict]:
    """mock 筛选结果：固定候选，每条带 reason 与空 metrics。"""
    return [
        {"symbol": s, "reason": "mock 固定候选", "metrics": {}} for s in MOCK_TOKENS
    ]


# ── binance（现货） ────────────────────────────────────────


def mock_ticker_24h_all() -> list[dict]:
    """全市场现货 24hr ticker（与 fetch_ticker_24h_all 同构，symbol 带计价后缀）。"""
    return [
        {
            "symbol": f"{s}USDT",
            "price": price,
            "price_change_pct": pct,
            "quote_volume": vol,
        }
        for s, (price, pct, vol) in _MOCK_MARKET.items()
    ]


def mock_klines(symbol: str, interval: str = "1d", limit: int = 365) -> list[dict]:
    """日线窗口（与 fetch_klines 同构）：价格绕基准小幅波动。"""
    price = _price(symbol)
    step_days = {"1d": 1, "4h": 1 / 6, "1h": 1 / 24}.get(interval, 1)
    # open_time 毫秒级（与真实 fetch_klines 的 int(k[0]) 同构；日界基准保证
    # 同日多次调用时间戳一致，供 beta_alpha 等跨序列对齐）
    base = _day_open_ms()
    return [
        {
            "open_time": base - i * int(86_400_000 * step_days),
            "close_price": round(price * (1 + 0.001 * (i % 7)), 6),
        }
        for i in range(max(1, limit))
    ]


# ── binance_futures（合约） ────────────────────────────────


def mock_mark_price(symbol: str) -> dict:
    """Mark Price（与 fetch_mark_price 同构）。"""
    price = _price(symbol)
    return {
        "symbol": symbol,
        "mark_price": price,
        "index_price": price * 0.999,
        "last_funding_rate": 0.0001,
        "next_funding_time": _now_ms() + 8 * 3_600_000,
    }


def mock_premium_index_all() -> list[dict]:
    """全量 premiumIndex（与 fetch_premium_index_all 同构）。"""
    now = _now_ms()
    return [
        {
            "symbol": f"{s}USDT",
            "mark_price": _price(s),
            "index_price": _price(s) * 0.999,
            "last_funding_rate": 0.0001,
            "next_funding_time": now + 8 * 3_600_000,
        }
        for s in MOCK_TOKENS
    ]


def mock_fapi_prices_all() -> dict[str, float]:
    """全量合约价格（与 fetch_fapi_prices_all 同构）。"""
    return {f"{s}USDT": _price(s) for s in MOCK_TOKENS}


def mock_fapi_ticker_24h_all() -> dict[str, dict]:
    """全量合约 24hr ticker（与 fetch_fapi_ticker_24h_all 同构）：
    价格与现货 mock 同价（同构纪律），symbol 带计价后缀。"""
    return {
        f"{s}USDT": {"price": price, "price_change_pct": pct, "quote_volume": vol}
        for s, (price, pct, vol) in _MOCK_MARKET.items()
    }


def mock_fapi_klines(symbol: str, interval: str = "1d", limit: int = 365) -> list[dict]:
    """合约日线窗口（与 fetch_fapi_klines 同构）：与现货 mock_klines 同价同形。"""
    return mock_klines(symbol, interval, limit)


def mock_funding_rate_history(symbol: str, limit: int = 25) -> list[dict]:
    """资金费率历史（与 fetch_funding_rate_history 同构）：时间升序，最新在末尾。

    费率图案随时间序反转（最新费率 = 0.0001，随序号周期性上升）——
    signals 的 funding_trend 按首尾比较，序列升序后图案不同步反转会翻转趋势。
    """
    now = _now_ms()
    return [
        {
            "funding_time": now - (limit - 1 - i) * 8 * 3_600_000,
            "funding_rate": 0.0001 * (1 + ((limit - 1 - i) % 5) * 0.1),
        }
        for i in range(max(1, limit))
    ]


def mock_open_interest(symbol: str) -> dict:
    """当前合约持仓量（与 fetch_open_interest 同构）。"""
    return {"symbol": symbol, "open_interest": 12345.6}


def mock_series(
    endpoint: str, symbol: str, period: str = "1h", limit: int = 48
) -> list[dict]:
    """历史序列端点（与各 _fetch_series 同构）。

    endpoint 决定记录字段形状，与真实端点解析后一致：
    open_interest_hist / global_long_short / top_ls_accounts /
    top_ls_positions / taker_bs
    """
    now = _now_ms()
    step_ms = {"5m": 300_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}.get(
        period, 3_600_000
    )
    base = {
        "open_interest_hist": {
            "sum_open_interest": 12345.6,
            "sum_open_interest_value": 8.6e8,
        },
        "global_long_short": {
            "long_short_ratio": 1.05,
            "long_account": 51.2,
            "short_account": 48.8,
        },
        "top_ls_accounts": {
            "long_short_ratio": 1.2,
            "long_account": 54.0,
            "short_account": 46.0,
        },
        "top_ls_positions": {
            "long_short_ratio": 1.1,
            "long_account": 52.0,
            "short_account": 48.0,
        },
        "taker_bs": {"buy_sell_ratio": 1.0, "buy_vol": 1.2e6, "sell_vol": 1.2e6},
    }[endpoint]
    return [
        {"symbol": symbol, **base, "timestamp": now - i * step_ms}
        for i in range(max(1, limit))
    ]


def mock_liquidation(
    symbol: str, interval: str = "4h", limit: int = 14
) -> list[dict]:
    """OKX 爆仓聚合序列 mock（与真实解析后字段同构）。

    固定常量序列（时间升序，最新在末尾）；装配层走同一聚合/信号路径，
    mock 与真实模式逐值相等（同构纪律，不写死信号层常量）。
    """
    step_ms = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}.get(
        interval, 14_400_000
    )
    now = _now_ms()
    # 常量：多头爆仓额高于空头（失衡比 1.6 → long_heavy）
    return [
        {
            "time": now - (limit - 1 - i) * step_ms,
            "long_liq_usd": 800_000.0,
            "short_liq_usd": 500_000.0,
        }
        for i in range(max(1, limit))
    ]


def mock_spot_exchange_info() -> dict:
    """现货全量交易对信息（与 fetch_exchange_info 同构；白名单=全部 mock 现货对）。"""
    return {
        "symbols": [
            {"symbol": f"{s}USDT", "status": "TRADING", "baseAsset": s}
            for s in _MOCK_MARKET
        ]
    }


def mock_exchange_info() -> dict:
    """全量合约交易对信息（与 fetch_exchange_info 同构）。"""
    return {
        "symbols": [
            {
                "symbol": f"{s}USDT",
                "contractType": "PERPETUAL",
                "onboardDate": _now_ms() - _MOCK_LISTING_DAYS[s] * 86_400_000,
            }
            for s in MOCK_TOKENS
        ]
    }


def mock_listing_days() -> dict[str, int]:
    """上市天数：从 mock_exchange_info 派生（与真实 fetch_listing_days 同构）。"""
    now_ms = _now_ms()
    days: dict[str, int] = {}
    for s in mock_exchange_info()["symbols"]:
        onboard = s.get("onboardDate")
        if onboard:
            days[s["symbol"]] = max(0, (now_ms - int(onboard)) // 86_400_000)
    return days


# ── defillama ─────────────────────────────────────────────


def mock_protocol_tvl(protocol: str) -> dict:
    return {
        "tvl": 1000.0 + len(protocol),
        "tvl_change_1d": 0.5,
        "tvl_change_7d": 2.5,
        "tvl_change_30d": 10.0,
        "mcap": 500.0,
        "fdv": 800.0,
    }


def mock_protocol_fees(protocol: str) -> dict:
    return {"fees_24h": 10.0, "fees_7d": 70.0, "revenue_24h": 5.0, "revenue_7d": 35.0}


def mock_protocol_tvl_history(protocol: str, days: int = 90) -> list[dict]:
    """协议 TVL 历史序列 mock（每日）：``[{date, tvl}]``。

    与 mock_protocol_tvl 同构：最新值 = 当前 tvl，且 7d/30d 复合变化率
    精确等于 tvl_change_7d/30d（分段指数构造，工具层降采样后可回算）；
    时间升序（最新在末尾，与真实 API 同向）。
    """
    latest = 1000.0 + len(protocol)
    v7 = latest / 1.025  # 7 天前（+2.5% 复合）
    v30 = latest / 1.10  # 30 天前（+10% 复合）
    rate2 = (v7 / v30) ** (1.0 / 23.0)  # 第 8..30 天衔接速率
    out = []
    for x in range(days - 1, -1, -1):  # x=0 为最新一天（末尾）
        if x <= 7:
            v = latest / (1.025 ** (x / 7.0))
        elif x <= 30:
            v = v7 / (rate2 ** (x - 7))
        else:
            v = v30 / (rate2 ** (x - 30))
        out.append({"date": _iso_days_ago(x), "tvl": round(v, 2)})
    return out


def mock_protocol_fees_history(protocol: str, days: int = 90) -> list[dict]:
    """协议费用历史序列 mock（每日）：``[{date, fees, revenue}]``。

    与 mock_protocol_fees 同构：最新值 = 当前 fees_24h/revenue_24h；
    恒定序列（mock 费用无变化率字段，趋势 flat）；时间升序（最新在末尾）。
    """
    return [
        {"date": _iso_days_ago(x), "fees": 10.0, "revenue": 5.0}
        for x in range(days - 1, -1, -1)
    ]


#: 已知链名（与 defillama.TOKEN_SLUG_MAP ``chain:`` 值一致；未知链返回
#: None 与真实路径同构——不模拟不存在的数据）
_MOCK_KNOWN_CHAINS = {"bitcoin", "ethereum", "solana", "doge", "avalanche", "cardano"}


def mock_stablecoin_history(chain: str, days: int = 90) -> list[dict] | None:
    """链稳定币总量历史序列 mock（每日）：``[{date, supply}]``。

    与 mock_stablecoin_supply 同构：最新值 = 当前供应量；时间升序（最新在末尾）；
    未知链返回 None（与真实路径一致，不模拟不存在的数据）。
    """
    if chain not in _MOCK_KNOWN_CHAINS:
        return None
    return [
        {"date": _iso_days_ago(x), "supply": 1.5e9} for x in range(days - 1, -1, -1)
    ]


def mock_stablecoin_supply(chain: str) -> dict:
    return {"stablecoin_supply": 1.5e9, "incomplete": False}


def mock_dex_volume_24h(chain: str | None = None) -> dict:
    return {"dex_volume_24h": 3.0e9}


#: mock token → DeFiLlama 链名（与 TOKEN_SLUG_MAP 的 ``chain:`` 值一致，
#: 保证装配层 get(chain) 命中）
_MOCK_CHAIN_NAMES = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "DOGE": "doge",
}


def mock_chains() -> dict[str, dict]:
    """全量链列表（与 fetch_chains 同构）：{链名小写: {tvl, token_symbol}}。"""
    return {
        name: {"tvl": _price(s), "token_symbol": s}
        for s, name in _MOCK_CHAIN_NAMES.items()
    }


def mock_protocols() -> dict[str, str]:
    """全量协议索引（与 fetch_protocols 同构）：{symbol: slug}。

    覆盖全部 mock token（含无静态映射的 XRP，兜底路径在 mock 下也齐全）。"""
    return {s: f"{s.lower()}-mock" for s in MOCK_TOKENS}


def mock_stablecoins() -> dict[str, float]:
    """全量稳定币聚合表（与 fetch_stablecoins 同构）：{链名小写: 供应量}。"""
    return {name: 1.5e9 for name in _MOCK_CHAIN_NAMES.values()}


def mock_dexs() -> dict[str, float]:
    """全量 DEX 聚合表（与 fetch_dexs 同构）：{链名小写: 24h 交易量}。"""
    return {name: 3.0e9 for name in _MOCK_CHAIN_NAMES.values()}


def mock_fees(slugs: list[str]) -> dict[str, dict]:
    """协议费用聚合表（与 fetch_fees 同构）。"""
    return {slug: mock_protocol_fees(slug) for slug in slugs}


def mock_chain_tvl(chain: str) -> dict:
    """链 TVL（与 fetch_chain_tvl 同构，字段同 mock_protocol_tvl）。"""
    return {
        "tvl": 1000.0 + len(chain),
        "tvl_change_1d": 0.5,
        "tvl_change_7d": 2.5,
        "tvl_change_30d": 10.0,
    }


# ── x_social（X 账号社交数据） ──────────────────────────

#: mock 最近 5 条 / 其余推文互动（固定图案：最近显著高于其余 → 热度上升）
_MOCK_X_RECENT = {"likes": "120", "reposts": "30", "comments": "18", "views": "5200"}
_MOCK_X_OLDER = {"likes": "40", "reposts": "8", "comments": "5", "views": "2100"}


def mock_x_stats(symbol: str) -> dict:
    """X 账号社交数据 mock（与 fetch_x_stats 返回同构）。

    固定 30 条互动序列（时间升序，最新在末尾）；最近 5 条互动显著高于其余
    （热度上升图案）——信号层经同一纯函数推导出确定方向（同构纪律，不写死
    信号层常量）；发帖间隔恒定 1 天/条（跨度 29 天）。
    """
    posts = []
    for i in range(30):  # i=0 最旧（30d 前），i=29 最新（1d 前）
        row = _MOCK_X_RECENT if i >= 25 else _MOCK_X_OLDER
        posts.append({**row, "time": f"{30 - i}d"})
    return {
        "account_name": symbol,
        "handle": symbol.lower(),
        "follower_count": "16.3万",
        "posts": posts,
    }


# ── web ───────────────────────────────────────────────────


def mock_news_rss(query: str) -> list[dict]:
    """Bing News RSS mock（与真实条目字段同构）。"""
    return [
        {
            "title": f"{query} 相关新闻 {i}",
            "date": _iso_days_ago(i),
            "source": "Mock News",
            "link": f"https://mock.example/news/{query}/{i}",
        }
        for i in range(3)
    ]


def mock_web_rss(query: str) -> list[dict]:
    """Bing Web RSS mock（与真实条目字段同构）。"""
    return [
        {
            "title": f"{query} 相关网页 {i}",
            "date": _iso_days_ago(i),
            "source": "Mock Web",
            "link": f"https://mock.example/web/{query}/{i}",
            "description": f"{query} 网页 {i} 的摘要内容",
        }
        for i in range(3)
    ]


#: mock 持仓指标原始值中与序列无关的固定部分（序列派生值见 _mock_funding_trend）
_MOCK_SENTIMENT_FIXED = {
    "funding": 0.0001,
    "ls_ratio_all": 1.05,
    "ls_ratio_top_acc": 1.2,
    "ls_ratio_top_pos": 1.1,
    "taker_bs_ratio": 1.0,
    "oi_change_24h": 0.0,
}

#: 估值常量的 mock 固定输入（与 mock_protocol_tvl/mock_protocol_fees 一致）
_MOCK_MCAP, _MOCK_FDV = 500.0, 800.0


#: 全 None 估值（chain 无 mcap/fdv/fees / unknown 全缺，与真实结构性缺失同构）
_VALUATION_NONE = {
    "value": {
        "mc_fees": None,
        "fdv_revenue": None,
        "mc_tvl": None,
        "fees_tvl": None,
    }
}


def _slug_for(symbol: str) -> str:
    """mock 模式协议 slug：静态映射优先，否则 mock_protocols 兜底（同 ① 解析）。"""
    from .defillama import (
        TOKEN_SLUG_MAP,  # 延迟导入避免循环（defillama 模块级 import mock）
    )

    return TOKEN_SLUG_MAP.get(symbol) or f"{symbol.lower()}-mock"


def _price_ret(symbol: str, days: int) -> float | None:
    """mock 日线 N 日收益率 %（与 binance.trailing_return 同口径）。"""
    klines = mock_klines(symbol)
    if not klines or len(klines) < days + 2:
        return None
    last = klines[-1].get("close_price")
    prev = klines[-1 - days].get("close_price")
    if last is None or prev in (None, 0):
        return None
    return (last / prev - 1.0) * 100.0


def _funding_trend(symbol: str) -> str | None:
    """mock 资金费率趋势（与 nodes._funding_stats 同口径：最新 vs 均值 ±10%）。"""
    pts = sorted(
        (
            r
            for r in mock_funding_rate_history(symbol)
            if isinstance(r.get("funding_rate"), (int, float))
        ),
        key=lambda r: r.get("funding_time") or 0,
    )
    if not pts:
        return None
    last = pts[-1]["funding_rate"]
    avg = sum(r["funding_rate"] for r in pts) / len(pts)
    ratio = last / avg if avg else None
    if ratio is None:
        return None
    if ratio > 1.1:
        return "rising"
    if ratio < 0.9:
        return "falling"
    return "flat"


def _mock_valuation(symbol: str, kind: str | None) -> dict:
    """mock 估值比率：protocol 从 mock 协议数据年化推导，其余全 None。"""
    if kind != "protocol":
        return dict(_VALUATION_NONE)
    slug = _slug_for(symbol)
    tvl = mock_protocol_tvl(slug)["tvl"]
    fees = mock_protocol_fees(slug)["fees_24h"]
    revenue = mock_protocol_fees(slug)["revenue_24h"]
    return {
        "value": {
            "mc_fees": _MOCK_MCAP / (fees * 365.0),
            "fdv_revenue": _MOCK_FDV / (revenue * 365.0),
            "mc_tvl": _MOCK_MCAP / tvl,
            "fees_tvl": (fees * 365.0) / tvl,
        }
    }


def _price_change_24h(symbol: str) -> float | None:
    """mock 24h 价格变化 %（与真实路径 mkt.change_24h 同源：mock ticker）。"""
    for row in mock_ticker_24h_all():
        if row["symbol"] == f"{symbol}USDT":
            return row["price_change_pct"]
    return None


def _funding_pctile_90d(symbol: str) -> float | None:
    """mock 费率极值分位（与真实路径同一纯函数：mock 270 根费率序列）。"""
    from ..signals import funding_percentile  # 延迟导入避免循环

    return funding_percentile(mock_funding_rate_history(symbol, 270))


def _oi_change_24h(symbol: str) -> float:
    """mock OI 24h 变化率（%）：mock 序列恒定 → 0.0，从序列首尾推导（同真实 _series_pct_change）。"""
    hist = mock_series("open_interest_hist", symbol, limit=25)
    if not hist:
        return 0.0
    first = hist[0]["sum_open_interest"]
    if not first:
        return 0.0
    return (hist[-1]["sum_open_interest"] - first) / first * 100.0


def _oi_price_divergence(symbol: str) -> dict:
    """mock OI/价格背离（同真实路径：ticker 价格变化 + OI 序列 24h 变化）。"""
    from ..signals import oi_price_divergence  # 延迟导入避免循环

    return oi_price_divergence(_price_change_24h(symbol), _oi_change_24h(symbol))


def _funding_z(symbol: str) -> float | None:
    """mock 费率横截面 Z（同真实路径：mock premium 全 0.0001 → 参照系无离散 → 0.0）。"""
    from ..signals import funding_cross_sectional_z  # 延迟导入避免循环

    rates = {
        r["symbol"]: r["last_funding_rate"] for r in mock_premium_index_all()
    }
    return funding_cross_sectional_z(rates, f"{symbol}USDT")


def _social_heat_trend(symbol: str) -> float | None:
    """mock 社交热度趋势（与真实路径同一纯函数：mock 30 条互动序列）。"""
    from ..signals import social_heat_trend  # 延迟导入避免循环

    return social_heat_trend(mock_x_stats(symbol)["posts"])


def _social_price_divergence(symbol: str) -> dict | None:
    """mock 社交/价格背离（同真实路径：mock ticker 价格变化 + mock 热度趋势）。"""
    from ..signals import social_price_divergence  # 延迟导入避免循环

    return social_price_divergence(_price_change_24h(symbol), _social_heat_trend(symbol))


def _mock_market_metrics(symbol: str, kind: str | None) -> dict:
    """mock 市场派生指标：从 mock klines / ticker 推导，复用同一纯函数（同构纪律）。"""
    from ..signals import beta_alpha, market_metrics, volatility_metrics  # 延迟导入

    klines = mock_klines(symbol, "1d", 400)
    vol = volatility_metrics(klines)["value"]
    ba = beta_alpha(klines, mock_klines("BTCUSDT", "1d", 400))["value"]
    ticker = mock_fapi_ticker_24h_all().get(f"{symbol}USDT") or {}
    return market_metrics(
        {"mcap": {"value": _MOCK_MCAP if kind == "protocol" else None}},
        {
            "quote_volume_24h": {"value": ticker.get("quote_volume")},
            **{k: {"value": v} for k, v in {**vol, **ba}.items()},
        },
    )


def mock_signals_data(symbol: str, kind: str | None = None) -> dict:
    """② 信号层 mock：值从 mock 数据源推导，与同一快照上的纯函数输出一致。

    protocol → 估值全字段（年化口径）；chain → 估值全 None（结构性缺失同构）；
    unknown → 全 None（UNKNOWN 纪律）。
    """
    if kind is None:
        momentum: float | None = None
        divergence: dict = {
            "divergence_7d": None,
            "divergence_30d": None,
            "quadrant": None,
        }
    else:
        # tvl_change_7d/30d 恒 2.5/10.0（mock_chain_tvl/mock_protocol_tvl 固定）
        momentum = (2.5 + 10.0) / 2.0
        change_7d = _price_ret(symbol, 7)
        change_30d = _price_ret(symbol, 30)
        div7 = 2.5 - change_7d if change_7d is not None else None
        div30 = 10.0 - change_30d if change_30d is not None else None
        quad: str | None = None
        if change_7d is not None:
            quad = "III" if change_7d <= 0 else "I"
        divergence = {"divergence_7d": div7, "divergence_30d": div30, "quadrant": quad}
    return {
        "symbol": symbol,
        "valuation": _mock_valuation(symbol, kind),
        "momentum": {"value": momentum},
        "divergence": {"value": divergence},
        "sentiment": {
            "components": {
                **_MOCK_SENTIMENT_FIXED,
                "funding_trend": _funding_trend(symbol),
                "funding_pctile_90d": _funding_pctile_90d(symbol),
                "oi_price_divergence": _oi_price_divergence(symbol),
                "funding_z": _funding_z(symbol),
                "social_heat_trend": _social_heat_trend(symbol),
                "social_price_divergence": _social_price_divergence(symbol),
            },
            "note": SENTIMENT_NOTE,
        },
        "market_metrics": _mock_market_metrics(symbol, kind),
        "error": None,
    }
