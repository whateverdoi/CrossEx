"""binance_futures 合约数据源测试（微观结构端点 + mock 同构 + 失败即失败）。"""

from __future__ import annotations

import pytest

from strategy_research.datasources import binance_futures as futures
from strategy_research.datasources.mock import MOCK_TOKENS

# 合约 symbol 后缀
SYM = "BTCUSDT"


def test_mock_point_and_all_endpoints() -> None:
    """mock 单点/全量端点：mark_price、premium_index_all、fapi_prices_all、open_interest。"""
    row = futures.fetch_mark_price(SYM)
    assert row is not None
    assert set(row) == {
        "symbol",
        "mark_price",
        "index_price",
        "last_funding_rate",
        "next_funding_time",
    }
    assert isinstance(row["mark_price"], float)
    assert isinstance(row["next_funding_time"], int)

    rows = futures.fetch_premium_index_all()
    assert isinstance(rows, list)
    # 覆盖全部固定候选，且费率离散（横截面分位需要真实分布，不能只有候选币）
    assert {f"{s}USDT" for s in MOCK_TOKENS} <= {r["symbol"] for r in rows}
    assert len({r["last_funding_rate"] for r in rows}) > 1
    assert set(rows[0]) == {
        "symbol",
        "mark_price",
        "index_price",
        "last_funding_rate",
        "next_funding_time",
    }
    assert isinstance(rows[0]["mark_price"], float)
    assert isinstance(rows[0]["next_funding_time"], int)

    prices = futures.fetch_fapi_prices_all()
    assert isinstance(prices, dict)
    assert set(prices) == {s + "USDT" for s in MOCK_TOKENS}
    assert all(isinstance(v, float) for v in prices.values())

    assert futures.fetch_open_interest(SYM) == {"symbol": SYM, "open_interest": 12345.6}


def test_mock_order_book_shape() -> None:
    """盘口 mock 同构：{symbol, bids, asks} 档位为 [价格, 数量] 且买卖排序正确。

    ``signals.book_structure`` 依赖 bids[0] 为最优买、asks[0] 为最优卖。
    """
    book = futures.fetch_order_book(SYM)
    assert book is not None
    assert set(book) == {"symbol", "bids", "asks"}
    assert book["symbol"] == SYM
    assert all(len(lv) == 2 for lv in book["bids"] + book["asks"])
    bids = [lv[0] for lv in book["bids"]]
    asks = [lv[0] for lv in book["asks"]]
    assert bids == sorted(bids, reverse=True)
    assert asks == sorted(asks)
    assert bids[0] < asks[0]


def test_mock_fapi_ticker_24h_all() -> None:
    """全量合约 24hr ticker（市场主源）：symbol 带后缀，price/pct/quote_volume 数值化。"""
    rows = futures.fetch_fapi_ticker_24h_all()
    assert isinstance(rows, dict)
    assert set(rows) == {s + "USDT" for s in MOCK_TOKENS}
    for row in rows.values():
        assert set(row) == {"price", "price_change_pct", "quote_volume"}
        assert all(isinstance(v, (int, float)) for v in row.values())


def test_mock_fapi_klines() -> None:
    """合约日线窗口：与现货 mock 同构（open_time 毫秒 + close_price）。"""
    rows = futures.fetch_fapi_klines(SYM, "1d", 400)
    assert isinstance(rows, list) and len(rows) == 400
    assert set(rows[0]) == {"open_time", "close_price"}
    assert isinstance(rows[0]["open_time"], int)
    assert isinstance(rows[0]["close_price"], float)


def test_mock_series_shapes() -> None:
    """mock 序列形状：OI 历史/多空比/taker/资金费率时间序列字段齐备。"""
    rows = futures.fetch_open_interest_hist(SYM)
    assert isinstance(rows, list) and len(rows) > 0
    assert set(rows[0]) == {
        "symbol",
        "sum_open_interest",
        "sum_open_interest_value",
        "timestamp",
    }

    rows = futures.fetch_global_long_short_ratio(SYM)
    assert isinstance(rows, list) and len(rows) > 0
    assert set(rows[0]) == {
        "symbol",
        "long_short_ratio",
        "long_account",
        "short_account",
        "timestamp",
    }

    for fn in (
        futures.fetch_top_long_short_account_ratio,
        futures.fetch_top_long_short_position_ratio,
    ):
        rows = fn(SYM)
        assert isinstance(rows, list) and len(rows) > 0
        assert {"long_short_ratio", "timestamp"} <= set(rows[0])

    rows = futures.fetch_taker_long_short_ratio(SYM)
    assert isinstance(rows, list) and len(rows) > 0
    assert set(rows[0]) == {
        "symbol",
        "buy_sell_ratio",
        "buy_vol",
        "sell_vol",
        "timestamp",
    }

    rows = futures.fetch_funding_rate_history(SYM)
    assert isinstance(rows, list) and len(rows) == 25
    assert set(rows[0]) == {"funding_time", "funding_rate"}
    assert isinstance(rows[0]["funding_time"], int)
    assert isinstance(rows[0]["funding_rate"], float)
    # 时间升序（最新在末尾，与真实 API 同向）；间隔 8 小时
    stamps = [r["funding_time"] for r in rows]
    assert stamps == sorted(stamps)
    assert stamps[-1] - stamps[-2] == 8 * 3_600_000


def test_fetch_exchange_info_mock() -> None:
    data = futures.fetch_exchange_info()
    assert isinstance(data, dict)
    assert "symbols" in data and isinstance(data["symbols"], list)
    assert all(s["symbol"] for s in data["symbols"])


def test_fetch_listing_days_mock() -> None:
    """onboardDate 口径：dict[symbol, int 上市天数]，与 exchange_info 键一致。"""
    days = futures.fetch_listing_days()
    assert isinstance(days, dict)
    assert set(days) == {s + "USDT" for s in MOCK_TOKENS}
    assert all(isinstance(v, int) and v >= 0 for v in days.values())
    # 与 mock_exchange_info 派生一致（真实路径同构：从 exchangeInfo 计算）
    assert days["BTCUSDT"] >= 1800


def test_mock_zero_external_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """SR_MOCK=1 时零外部请求：SDK 单例被调用即爆炸也不影响。"""
    monkeypatch.setattr(
        "strategy_research.datasources._binance_sdk.get_futures_data_client",
        lambda: pytest.fail("mock 模式不应触碰 SDK"),
    )
    assert futures.fetch_mark_price(SYM) is not None
    assert futures.fetch_listing_days() is not None


def test_fetch_mark_price_real_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实响应 → 字段 float 化/命名转换。"""
    monkeypatch.setenv("SR_MOCK", "0")

    def fake_call(fn, **kwargs):
        return {
            "symbol": SYM,
            "markPrice": "70000.5",
            "indexPrice": "69999.0",
            "lastFundingRate": "0.0001",
            "nextFundingTime": 1780000000000,
        }

    monkeypatch.setattr(
        "strategy_research.datasources._binance_sdk.sync_call_with_rate_limit",
        fake_call,
    )
    row = futures.fetch_mark_price(SYM)
    assert row == {
        "symbol": SYM,
        "mark_price": 70000.5,
        "index_price": 69999.0,
        "last_funding_rate": 0.0001,
        "next_funding_time": 1780000000000,
    }


def test_fetch_listing_days_real_computes_days(monkeypatch: pytest.MonkeyPatch) -> None:
    """exchangeInfo → 按 onboardDate 计算上市天数（毫秒口径）。"""
    monkeypatch.setenv("SR_MOCK", "0")
    import time as _t

    now_ms = int(_t.time() * 1000)
    listed_10d = now_ms - 10 * 86_400_000

    def fake_call(fn, **kwargs):
        return {
            "symbols": [
                {"symbol": "BTCUSDT", "onboardDate": listed_10d},
                {"symbol": "ETHUSDT", "onboardDate": now_ms},
                {"symbol": "NEWUSDT", "onboardDate": 0},  # 0 = 未知，应跳过
            ]
        }

    monkeypatch.setattr(
        "strategy_research.datasources._binance_sdk.sync_call_with_rate_limit",
        fake_call,
    )
    days = futures.fetch_listing_days()
    assert set(days) == {"BTCUSDT", "ETHUSDT"}
    assert 9 <= days["BTCUSDT"] <= 11
    assert days["ETHUSDT"] == 0


def test_fetch_series_real_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实响应 camelCase+字符串 → snake_case+数值（与 mock 同构）；缺 symbol 补上。"""
    monkeypatch.setenv("SR_MOCK", "0")

    def fake_call(fn, **kwargs):
        return [
            {
                "symbol": SYM,
                "sumOpenInterest": "107316.966",
                "sumOpenInterestValue": "7985555445.47",
                "CMCCirculatingSupply": "20071518",
                "timestamp": 1787281200000,
            },
        ]

    monkeypatch.setattr(
        "strategy_research.datasources._binance_sdk.sync_call_with_rate_limit",
        fake_call,
    )
    rows = futures.fetch_open_interest_hist(SYM, limit=1)
    assert rows[0] == {
        "symbol": SYM,
        "sum_open_interest": 107316.966,
        "sum_open_interest_value": 7985555445.47,
        "CMCCirculatingSupply": "20071518",  # 未知字段原样保留
        "timestamp": 1787281200000,
    }

    def fake_call(fn, **kwargs):
        return [
            {
                "buySellRatio": "0.9032",
                "buyVol": "7512.33",
                "sellVol": "8317.09",
                "timestamp": 1787277600000,
            }
        ]

    monkeypatch.setattr(
        "strategy_research.datasources._binance_sdk.sync_call_with_rate_limit",
        fake_call,
    )
    rows = futures.fetch_taker_long_short_ratio(SYM, limit=1)
    assert rows[0] == {
        "symbol": SYM,
        "buy_sell_ratio": 0.9032,
        "buy_vol": 7512.33,
        "sell_vol": 8317.09,
        "timestamp": 1787277600000,
    }

    def fake_call(fn, **kwargs):
        assert kwargs.get("symbol") == SYM
        return [
            {"symbol": SYM, "fundingTime": 1780000000000, "fundingRate": "0.0001"},
            {
                "symbol": SYM,
                "fundingTime": 1780000000000,
                "fundingRate": "bad",
            },  # 解析失败跳过
        ]

    monkeypatch.setattr(
        "strategy_research.datasources._binance_sdk.sync_call_with_rate_limit",
        fake_call,
    )
    rows = futures.fetch_funding_rate_history(SYM, limit=2)
    assert rows == [{"funding_time": 1780000000000, "funding_rate": 0.0001}]

    def fake_call(fn, **kwargs):
        return {"symbol": SYM, "openInterest": "12345.6"}

    monkeypatch.setattr(
        "strategy_research.datasources._binance_sdk.sync_call_with_rate_limit",
        fake_call,
    )
    assert futures.fetch_open_interest(SYM) == {"symbol": SYM, "open_interest": 12345.6}


def test_fetch_all_endpoints_real_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实全量端点：premiumIndex 不带 symbol 参数；fapi 价格 None 跳过。"""
    monkeypatch.setenv("SR_MOCK", "0")

    def fake_call(fn, **kwargs):
        assert kwargs.get("symbol") is None  # 全量端点不带 symbol
        return [
            {
                "symbol": "BTCUSDT",
                "markPrice": "70000.5",
                "indexPrice": "69999.0",
                "lastFundingRate": "0.0001",
                "nextFundingTime": 1780000000000,
            },
            {
                "symbol": "ETHUSDT",
                "markPrice": "3500.0",
                "indexPrice": "3499.0",
                "lastFundingRate": "-0.0002",
                "nextFundingTime": 1780000000000,
            },
        ]

    monkeypatch.setattr(
        "strategy_research.datasources._binance_sdk.sync_call_with_rate_limit",
        fake_call,
    )
    rows = futures.fetch_premium_index_all()
    assert rows[0] == {
        "symbol": "BTCUSDT",
        "mark_price": 70000.5,
        "index_price": 69999.0,
        "last_funding_rate": 0.0001,
        "next_funding_time": 1780000000000,
    }
    assert len(rows) == 2

    def fake_call(fn, **kwargs):
        return [
            {"symbol": "BTCUSDT", "price": "70000.0"},
            {"symbol": "ETHUSDT", "price": "3500.0"},
            {"symbol": "BADUSDT", "price": None},
        ]  # None 应跳过

    monkeypatch.setattr(
        "strategy_research.datasources._binance_sdk.sync_call_with_rate_limit",
        fake_call,
    )
    prices = futures.fetch_fapi_prices_all()
    assert prices == {"BTCUSDT": 70000.0, "ETHUSDT": 3500.0}


def test_fetch_mark_price_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """失败即失败：异常 → None（装配层标 UNKNOWN），不回退 mock。"""
    monkeypatch.setenv("SR_MOCK", "0")

    def boom(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(
        "strategy_research.datasources._binance_sdk.sync_call_with_rate_limit", boom
    )
    assert futures.fetch_mark_price(SYM) is None
    assert futures.fetch_listing_days() is None
    assert futures.fetch_open_interest_hist(SYM) is None
    assert futures.fetch_premium_index_all() is None
    assert futures.fetch_fapi_prices_all() is None
    assert futures.fetch_funding_rate_history(SYM) is None
    assert futures.fetch_open_interest(SYM) is None
