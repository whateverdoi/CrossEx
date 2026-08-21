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
_MOCK_LISTING_DAYS = {"BTC": 1800, "ETH": 1500, "SOL": 800,
                      "UNI": 700, "DOGE": 1100, "XRP": 1300}


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
        {"symbol": s, "reason": "mock 固定候选", "metrics": {}}
        for s in MOCK_TOKENS
    ]


# ── binance（现货） ────────────────────────────────────────


def mock_ticker_24h_all() -> list[dict]:
    """全市场现货 24hr ticker（与 fetch_ticker_24h_all 同构）。"""
    return [
        {"symbol": s, "price_change_pct": pct, "quote_volume": vol}
        for s, (_, pct, vol) in _MOCK_MARKET.items()
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


def mock_series(endpoint: str, symbol: str, period: str = "1h",
                limit: int = 48) -> list[dict]:
    """历史序列端点（与各 _fetch_series 同构）。

    endpoint 决定记录字段形状，与真实端点解析后一致：
    open_interest_hist / global_long_short / top_ls_accounts /
    top_ls_positions / taker_bs
    """
    now = _now_ms()
    step_ms = {"5m": 300_000, "1h": 3_600_000, "4h": 14_400_000,
               "1d": 86_400_000}.get(period, 3_600_000)
    base = {
        "open_interest_hist": {
            "sum_open_interest": 12345.6,
            "sum_open_interest_value": 8.6e8,
        },
        "global_long_short": {
            "long_short_ratio": 1.05, "long_account": 51.2,
            "short_account": 48.8,
        },
        "top_ls_accounts": {"long_short_ratio": 1.2,
                            "long_account": 54.0, "short_account": 46.0},
        "top_ls_positions": {"long_short_ratio": 1.1,
                             "long_account": 52.0, "short_account": 48.0},
        "taker_bs": {"buy_sell_ratio": 1.0, "buy_vol": 1.2e6,
                     "sell_vol": 1.2e6},
    }[endpoint]
    return [
        {"symbol": symbol, **base, "timestamp": now - i * step_ms}
        for i in range(max(1, limit))
    ]


def mock_exchange_info() -> dict:
    """全量合约交易对信息（与 fetch_exchange_info 同构）。"""
    return {"symbols": [
        {"symbol": f"{s}USDT", "contractType": "PERPETUAL",
         "onboardDate": _now_ms() - _MOCK_LISTING_DAYS[s] * 86_400_000}
        for s in MOCK_TOKENS
    ]}


def mock_listing_days() -> dict[str, int]:
    """上市天数：从 mock_exchange_info 派生（与真实 fetch_listing_days 同构）。"""
    now_ms = _now_ms()
    days: dict[str, int] = {}
    for s in mock_exchange_info()["symbols"]:
        onboard = s.get("onboardDate")
        if onboard:
            days[s["symbol"]] = max(
                0, (now_ms - int(onboard)) // 86_400_000)
    return days


# ── defillama ─────────────────────────────────────────────


def mock_protocol_tvl(protocol: str) -> dict:
    return {"tvl": 1000.0 + len(protocol), "change_7d": 2.5,
            "mcap": 500.0, "fdv": 800.0}


def mock_protocol_fees(protocol: str) -> dict:
    return {"fees_24h": 10.0, "fees_7d": 70.0,
            "revenue_24h": 5.0, "revenue_7d": 35.0}


def mock_stablecoin_supply(chain: str) -> dict:
    return {"stablecoin_supply": 1.5e9, "incomplete": False}


def mock_dex_volume_24h(chain: str | None = None) -> dict:
    return {"dex_volume_24h": 3.0e9}


# ── web ───────────────────────────────────────────────────


def mock_news_rss(query: str) -> list[dict]:
    """Bing News RSS mock（与真实条目字段同构）。"""
    return [
        {"title": f"{query} 相关新闻 {i}", "date": _iso_days_ago(i),
         "source": "Mock News", "link": f"https://mock.example/news/{query}/{i}"}
        for i in range(3)
    ]


def mock_web_rss(query: str) -> list[dict]:
    """Bing Web RSS mock（与真实条目字段同构）。"""
    return [
        {"title": f"{query} 相关网页 {i}", "date": _iso_days_ago(i),
         "source": "Mock Web", "link": f"https://mock.example/web/{query}/{i}"}
        for i in range(3)
    ]
