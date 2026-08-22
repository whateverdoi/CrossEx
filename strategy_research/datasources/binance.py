"""binance 现货数据源（官方 SDK 薄适配）。

- ``fetch_ticker_24h_all()``：/api/v3/ticker/24hr 全市场（权重 80），
  返回 ``[{symbol, price, price_change_pct, quote_volume}]``（float 化）；
  失败返回 ``None``（装配层标 UNKNOWN，规格 ⑨ 契约）。
- ``fetch_exchange_info()``：/api/v3/exchangeInfo 全量交易对（权重 10），
  供筛选器构建现货 USDT 白名单（与 BinanceApi 筛选同款：TRADING + 非稳定币标的）。
- ``fetch_klines()``：/api/v3/klines 日线窗口（权重随 limit），
  返回 ``[{open_time, close_price}]``。
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
        ``[{symbol, price, price_change_pct, quote_volume}]``；失败返回 ``None``
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
            rows.append(
                {
                    "symbol": t["symbol"],
                    "price": float(t["lastPrice"]),
                    "price_change_pct": float(t["priceChangePercent"]),
                    "quote_volume": float(t["quoteVolume"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue  # 单条字段异常跳过，该 symbol 数据点缺失
    return rows


def fetch_exchange_info() -> dict | None:
    """现货全量交易对信息（/api/v3/exchangeInfo，权重 10）。

    供筛选器构建候选白名单（symbol 格式 + 状态 + 标的资产判定）；
    失败返回 ``None``（筛选层抛 ``ScreeningError`` 批终止）。
    """
    if env.is_mock_mode():
        return mock.mock_spot_exchange_info()
    try:
        data = _binance_sdk.sync_call_with_rate_limit(
            _binance_sdk.get_spot_data_client().exchange_info,
            name="exchangeInfo(spot)",
            weight=10,
        )
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("symbols") is None:
        return None
    return data


def fetch_klines(
    symbol: str, interval: str = "1d", limit: int = 365
) -> list[dict] | None:
    """日线窗口：``[{open_time, close_price}]``（供 ret_7d/30d/90d/1y）。

    权重随 limit（≤100 时 1，≤500 时 2，更大时 5）；失败返回 ``None``。
    """
    if env.is_mock_mode():
        return mock.mock_klines(symbol, interval, limit)
    try:
        data = _binance_sdk.sync_call_with_rate_limit(
            _binance_sdk.get_spot_data_client().klines,
            symbol=symbol,
            interval=interval,
            limit=limit,
            name="klines",
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


# ── 日线收益（预测能力 02 票：单一口径，live 与评估各一、同处一个适配器）──


#: 计价后缀（裸 symbol 补全为交易所对时使用；现货/合约同口径）
QUOTES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI")


def pair_symbol(symbol: str) -> str:
    """裸名补 USDT 计价对；已带计价后缀原样返回。"""
    return symbol if symbol.endswith(QUOTES) else symbol + "USDT"


def trailing_return(klines: list[dict] | None, days: int) -> float | None:
    """实时尾窗收益：最新收盘 vs N 日前收盘（live 信号口径）。

    含未收盘 bar（最新一根即当前形成中的日线，价格=现价）——信号层要的就是
    "此刻 vs N 日前"；与 :func:`closed_daily_returns` 的评估口径（无前视）
    语义不同，勿混用。窗口不足 / 非法值 → None（UNKNOWN 纪律）。
    """
    if not klines or len(klines) < days + 2:
        return None
    last = klines[-1].get("close_price")
    prev = klines[-1 - days].get("close_price")
    if last is None or prev in (None, 0):
        return None
    return (last / prev - 1.0) * 100.0


_DAY_MS = 86_400_000


def closed_daily_returns(
    klines: list[dict] | None, run_ts_ms: int, days: tuple[int, ...] = (1, 7)
) -> dict:
    """无前视日线收益（评估口径）：基准价 + 各 N 日收益 %。

    base = 决策时最近已收盘日线的 close（open_time + 1d ≤ run_ts 的最后一根），
    无前视偏差；ret_N = base 后第 N 根 close 相对 base 的涨跌幅（按绝对日键
    定位，缺口 → None，不误用相邻日）。窗口不足或决策早于窗口 → {}（调用方
    按无收益处理）。与 :func:`trailing_return` 语义不同：评估用已收盘基准。
    供决策回看回路（review）使用；klines 按 open_time 升序/降序均可（内部排序）。
    """
    rows = sorted(
        (
            k
            for k in (klines or [])
            if k.get("open_time") and k.get("close_price")
        ),
        key=lambda k: k["open_time"],
    )
    if not rows:
        return {}
    base_idx = -1
    for i, k in enumerate(rows):
        if k["open_time"] + _DAY_MS <= run_ts_ms:
            base_idx = i
        else:
            break
    if base_idx < 0:
        return {}
    base_open = rows[base_idx]["open_time"]
    base = rows[base_idx]["close_price"]
    by_time = {k["open_time"]: k["close_price"] for k in rows}
    out: dict = {"base_price": base}
    for days_n in days:
        close = by_time.get(base_open + days_n * _DAY_MS)
        out[f"ret_{days_n}d"] = (
            round((close / base - 1.0) * 100.0, 2)
            if close is not None and base
            else None
        )
    return out
