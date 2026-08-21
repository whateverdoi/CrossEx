"""binance 现货数据源（官方 SDK 薄适配）。

- ``fetch_ticker_24h_all()``：/api/v3/ticker/24hr 全市场（权重 80），
  返回 ``[{symbol, price_change_pct, quote_volume}]``（float 化）；
  失败返回 ``None``（装配层标 UNKNOWN，规格 ⑨ 契约）。
- ``SR_MOCK=1`` 时走 :mod:`mock` 同构数据，零外部请求。
"""

from __future__ import annotations

from .. import env
from . import _binance_sdk, mock


def fetch_ticker_24h_all() -> list[dict] | None:
    """全市场现货 24hr ticker。

    Returns
    -------
    list[dict] | None
        ``[{symbol, price_change_pct, quote_volume}]``；失败返回 ``None``
        （失败即失败，绝不回退 mock）。
    """
    if env.is_mock_mode():
        return mock.mock_ticker_24h_all()
    try:
        data = _binance_sdk.sync_call_with_rate_limit(
            _binance_sdk.get_spot_data_client().ticker24hr,
            name="ticker/24hr(spot)",
            weight=80,
        )
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    rows: list[dict] = []
    for t in data:
        try:
            rows.append({
                "symbol": t["symbol"],
                "price_change_pct": float(t["priceChangePercent"]),
                "quote_volume": float(t["quoteVolume"]),
            })
        except (KeyError, TypeError, ValueError):
            continue  # 单条字段异常跳过，该 symbol 数据点缺失
    return rows
