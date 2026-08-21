"""mock 数据源：SR_MOCK=1 显式离线模式，与真实路径字段同构。

每个 mock 函数对应一个真实 fetch 函数（见 binance.py / binance_futures.py
/ defillama.py / web.py），字段名、类型与真实响应解析后完全一致。
symbol 命名空间沿用交易所格式（如 ``BTCUSDT``），与真实 ticker 一致。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

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
    return [
        {
            "open_time": int(_now_ms() / 1000) - i * int(86_400 * step_days),
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


def mock_funding_rate_history(symbol: str, limit: int = 25) -> list[dict]:
    """资金费率历史（与 fetch_funding_rate_history 同构）。"""
    now = _now_ms()
    return [
        {
            "funding_time": now - i * 8 * 3_600_000,
            "funding_rate": 0.0001 * (1 + (i % 5) * 0.1),
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
        }
        for i in range(3)
    ]


#: sentiment 解读规则（与 signals.sentiment_raw 的 note 同构）
_SENTIMENT_NOTE = (
    "持仓指标原始直读；解读规则见 DECIDE_PROMPT（funding 高=拥挤反向，多空比高=偏多等）"
)


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
    """mock 日线 N 日收益率 %（与 nodes._ret 同口径）。"""
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
            },
            "note": _SENTIMENT_NOTE,
        },
        "error": None,
    }
