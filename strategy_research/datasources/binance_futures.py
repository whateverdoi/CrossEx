"""binance_futures 合约数据源（官方 SDK 薄适配）。

微观结构端点（全部 /fapi/v1 公开数据，单 symbol 权重 1/请求）：

- ``fetch_mark_price``：mark_price（premiumIndex，含 mark / index / funding）
- ``fetch_open_interest_hist``：openInterestHist
- ``fetch_global_long_short_ratio``：globalLongShortAccountRatio
- ``fetch_top_long_short_account_ratio`` / ``fetch_top_long_short_position_ratio``
- ``fetch_taker_long_short_ratio``：takerlongshortRatio
- ``fetch_exchange_info``：全量交易对信息
- ``fetch_listing_days``：``dict[symbol, 上市天数]``（onboardDate 毫秒口径，
  与 ``market_data.listing_days`` 一致；onboardDate=0 视为未知跳过）
- ``fetch_premium_index_all``：全量 premiumIndex（批内一次，含 funding）
- ``fetch_fapi_prices_all``：全量合约价格（批内一次）
- ``fetch_funding_rate_history``：资金费率历史（funding_avg_7d / trend）
- ``fetch_open_interest``：当前持仓量（单 symbol）

所有 fetch 失败返回 ``None``（装配层标 UNKNOWN，失败即失败不回退 mock）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .. import env
from . import _binance_sdk, mock

#: 历史序列端点（symbol + period + limit）
_SERIES_ENDPOINTS: dict[str, Callable] = {
    "open_interest_hist": "open_interest_statistics",
    "global_long_short": "long_short_ratio",
    "top_ls_accounts": "top_trader_long_short_ratio_accounts",
    "top_ls_positions": "top_trader_long_short_ratio_positions",
    "taker_bs": "taker_buy_sell_volume",
}

#: 端点 → (camelCase→snake_case 字段映射, 需 float 化的字段)
#: 使真实输出与 mock 同构（snake_case + 数值类型）
_SERIES_FIELD_MAPS: dict[str, tuple[dict[str, str], set[str]]] = {
    "open_interest_hist": (
        {
            "sumOpenInterest": "sum_open_interest",
            "sumOpenInterestValue": "sum_open_interest_value",
        },
        {"sum_open_interest", "sum_open_interest_value"},
    ),
    "global_long_short": (
        {
            "longShortRatio": "long_short_ratio",
            "longAccount": "long_account",
            "shortAccount": "short_account",
        },
        {"long_short_ratio", "long_account", "short_account"},
    ),
    "top_ls_accounts": (
        {
            "longShortRatio": "long_short_ratio",
            "longAccount": "long_account",
            "shortAccount": "short_account",
        },
        {"long_short_ratio", "long_account", "short_account"},
    ),
    "top_ls_positions": (
        {
            "longShortRatio": "long_short_ratio",
            "longAccount": "long_account",
            "shortAccount": "short_account",
        },
        {"long_short_ratio", "long_account", "short_account"},
    ),
    "taker_bs": (
        {"buySellRatio": "buy_sell_ratio", "buyVol": "buy_vol", "sellVol": "sell_vol"},
        {"buy_sell_ratio", "buy_vol", "sell_vol"},
    ),
}


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_mark_price(symbol: str) -> dict | None:
    """Mark Price / Premium Index（含资金费率），单 symbol。"""
    if env.is_mock_mode():
        return mock.mock_mark_price(symbol)
    client = _binance_sdk.get_futures_data_client()
    try:
        data = _binance_sdk.sync_call_with_rate_limit(
            client.mark_price, symbol=symbol, name="markPrice", weight=1
        )
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return {
        "symbol": data.get("symbol", symbol),
        "mark_price": _float_or_none(data.get("markPrice")),
        "index_price": _float_or_none(data.get("indexPrice")),
        "last_funding_rate": _float_or_none(data.get("lastFundingRate")),
        "next_funding_time": _int_or_none(data.get("nextFundingTime")),
    }


def _map_series_row(endpoint: str, row: dict, symbol: str) -> dict:
    """序列记录字段转换：camelCase→snake_case + 数值化 + 补 symbol。

    与 mock 输出同构（规格纪律 6）；未知字段原样保留。
    """
    field_map, numeric = _SERIES_FIELD_MAPS[endpoint]
    out: dict = {}
    for key, value in row.items():
        mapped = field_map.get(key, key)
        if mapped in numeric:
            out[mapped] = _float_or_none(value)
        elif mapped == "timestamp":
            out[mapped] = _int_or_none(value)
        else:
            out[mapped] = value
    out.setdefault("symbol", symbol)
    return out


def _fetch_series(
    endpoint: str, symbol: str, period: str, limit: int
) -> list[dict] | None:
    """历史序列端点公共路径（权重 1/请求）。"""
    if env.is_mock_mode():
        return mock.mock_series(endpoint, symbol, period, limit)
    client = _binance_sdk.get_futures_data_client()
    fn = getattr(client, _SERIES_ENDPOINTS[endpoint])
    try:
        data = _binance_sdk.sync_call_with_rate_limit(
            fn,
            symbol=symbol,
            period=period,
            limit=limit,
            name=endpoint,
            weight=1,
        )
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    return [_map_series_row(endpoint, row, symbol) for row in data]


def fetch_open_interest_hist(
    symbol: str, period: str = "1h", limit: int = 48
) -> list[dict] | None:
    """Open Interest 历史（openInterestHist）。"""
    return _fetch_series("open_interest_hist", symbol, period, limit)


def fetch_global_long_short_ratio(
    symbol: str, period: str = "1h", limit: int = 48
) -> list[dict] | None:
    """全市场多空账户比（globalLongShortAccountRatio）。"""
    return _fetch_series("global_long_short", symbol, period, limit)


def fetch_top_long_short_account_ratio(
    symbol: str, period: str = "1h", limit: int = 48
) -> list[dict] | None:
    """大户多空账户比（topLongShortAccountRatio）。"""
    return _fetch_series("top_ls_accounts", symbol, period, limit)


def fetch_top_long_short_position_ratio(
    symbol: str, period: str = "1h", limit: int = 48
) -> list[dict] | None:
    """大户多空持仓比（topLongShortPositionRatio）。"""
    return _fetch_series("top_ls_positions", symbol, period, limit)


def fetch_taker_long_short_ratio(
    symbol: str, period: str = "1h", limit: int = 48
) -> list[dict] | None:
    """官方 taker 买卖比（takerlongshortRatio）。"""
    return _fetch_series("taker_bs", symbol, period, limit)


def fetch_exchange_info() -> dict | None:
    """全量合约交易对信息（exchangeInfo，权重 1）。"""
    if env.is_mock_mode():
        return mock.mock_exchange_info()
    client = _binance_sdk.get_futures_data_client()
    try:
        data = _binance_sdk.sync_call_with_rate_limit(
            client.exchange_information, name="exchangeInfo", weight=1
        )
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("symbols") is None:
        return None
    return data


def fetch_listing_days() -> dict[str, int] | None:
    """全量上市天数：``{symbol: 上市天数}``（onboardDate 毫秒口径）。

    ``onboardDate=0`` 的 symbol 视为未知跳过（UNKNOWN 纪律）。
    失败返回 ``None``。
    """
    if env.is_mock_mode():
        return mock.mock_listing_days()
    data = fetch_exchange_info()
    if data is None:
        return None
    now_ms = int(time.time() * 1000)
    days: dict[str, int] = {}
    for s in data.get("symbols", []):
        onboard = s.get("onboardDate")
        symbol = s.get("symbol")
        if not onboard or not symbol:
            continue  # 缺失字段按 UNKNOWN 跳过
        try:
            days[symbol] = max(0, (now_ms - int(onboard)) // 86_400_000)
        except (TypeError, ValueError):
            continue
    return days


def fetch_premium_index_all() -> list[dict] | None:
    """全量 premiumIndex（fapi/v1/premiumIndex 无 symbol，权重 1，批内一次）。

    返回 ``[{symbol, mark_price, index_price, last_funding_rate,
    next_funding_time}]``（数值化）；失败返回 ``None``。
    """
    if env.is_mock_mode():
        return mock.mock_premium_index_all()
    client = _binance_sdk.get_futures_data_client()
    try:
        data = _binance_sdk.sync_call_with_rate_limit(
            client.mark_price, name="premiumIndex(all)", weight=1
        )
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    rows: list[dict] = []
    for p in data:
        try:
            rows.append(
                {
                    "symbol": p["symbol"],
                    "mark_price": _float_or_none(p.get("markPrice")),
                    "index_price": _float_or_none(p.get("indexPrice")),
                    "last_funding_rate": _float_or_none(p.get("lastFundingRate")),
                    "next_funding_time": _int_or_none(p.get("nextFundingTime")),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def fetch_fapi_prices_all() -> dict[str, float] | None:
    """全量合约价格：``{symbol: price}``（fapi/v1/ticker/price，权重 2）。"""
    if env.is_mock_mode():
        return mock.mock_fapi_prices_all()
    client = _binance_sdk.get_futures_data_client()
    try:
        data = _binance_sdk.sync_call_with_rate_limit(
            client.symbol_price_ticker, name="ticker/price(fapi)", weight=2
        )
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    prices: dict[str, float] = {}
    for p in data:
        price = _float_or_none(p.get("price"))
        symbol = p.get("symbol")
        if symbol and price is not None:
            prices[symbol] = price
    return prices


def fetch_fapi_ticker_24h_all() -> dict[str, dict] | None:
    """全量合约 24hr ticker（fapi/v1/ticker/24hr，权重 40，批内一次）。

    返回 ``{symbol: {price, price_change_pct, quote_volume}}``（数值化）；
    失败返回 ``None``。市场数据主源（06 票：原生合约数据，非现货兜底）。
    """
    if env.is_mock_mode():
        return mock.mock_fapi_ticker_24h_all()
    client = _binance_sdk.get_futures_data_client()
    try:
        data = _binance_sdk.sync_call_with_rate_limit(
            client.ticker24hr_price_change_statistics,
            name="ticker/24hr(fapi)",
            weight=40,
        )
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    rows: dict[str, dict] = {}
    for t in data:
        symbol = t.get("symbol")
        if not symbol:
            continue
        rows[symbol] = {
            "price": _float_or_none(t.get("lastPrice")),
            "price_change_pct": _float_or_none(t.get("priceChangePercent")),
            "quote_volume": _float_or_none(t.get("quoteVolume")),
        }
    return rows


def fetch_fapi_klines(
    symbol: str, interval: str = "1d", limit: int = 365
) -> list[dict] | None:
    """合约日线窗口（fapi/v1/klines，与现货 fetch_klines 同构）：
    ``[{open_time, close_price}]``（供 ret_7d/30d/90d/1y）。

    权重随 limit（≤100 时 1，≤500 时 2，更大时 5）；失败返回 ``None``。
    """
    if env.is_mock_mode():
        return mock.mock_fapi_klines(symbol, interval, limit)
    client = _binance_sdk.get_futures_data_client()
    try:
        data = _binance_sdk.sync_call_with_rate_limit(
            client.kline_candlestick_data,
            symbol=symbol,
            interval=interval,
            limit=limit,
            name="klines(fapi)",
            weight=1 if limit <= 100 else 2 if limit <= 500 else 5,
        )
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    rows: list[dict] = []
    for k in data:
        try:
            rows.append(
                {
                    "open_time": int(k[0]),
                    "close_price": float(k[4]),
                }
            )
        except (IndexError, KeyError, TypeError, ValueError):
            continue
    return rows


def fetch_funding_rate_history(symbol: str, limit: int = 25) -> list[dict] | None:
    """资金费率历史（fapi/v1/fundingRate，权重 1）：
    ``[{funding_time, funding_rate}]`` 时间升序。"""
    if env.is_mock_mode():
        return mock.mock_funding_rate_history(symbol, limit)
    client = _binance_sdk.get_futures_data_client()
    try:
        data = _binance_sdk.sync_call_with_rate_limit(
            client.get_funding_rate_history,
            symbol=symbol,
            limit=limit,
            name="fundingRate",
            weight=1,
        )
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    rows: list[dict] = []
    for f in data:
        rate = _float_or_none(f.get("fundingRate"))
        f_time = _int_or_none(f.get("fundingTime"))
        if rate is None or f_time is None:
            continue
        rows.append({"funding_time": f_time, "funding_rate": rate})
    return rows


def fetch_open_interest(symbol: str) -> dict | None:
    """当前合约持仓量（fapi/v1/openInterest，权重 1）：
    ``{symbol, open_interest}``。"""
    if env.is_mock_mode():
        return mock.mock_open_interest(symbol)
    client = _binance_sdk.get_futures_data_client()
    try:
        data = _binance_sdk.sync_call_with_rate_limit(
            client.open_interest, symbol=symbol, name="openInterest", weight=1
        )
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return {
        "symbol": data.get("symbol", symbol),
        "open_interest": _float_or_none(data.get("openInterest")),
    }
