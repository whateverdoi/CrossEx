"""binance_futures 合约数据源测试（微观结构端点 + mock 同构 + 失败即失败）。"""

from __future__ import annotations

import pytest

from strategy_research.datasources import _binance_sdk as sdk
from strategy_research.datasources import binance_futures as futures
from strategy_research.datasources.mock import MOCK_TOKENS

# 合约 symbol 后缀
SYM = "BTCUSDT"


def test_fetch_mark_price_mock() -> None:
    """mark_price（premium/funding）mock 同构。"""
    row = futures.fetch_mark_price(SYM)
    assert row is not None
    assert set(row) == {
        "symbol", "mark_price", "index_price", "last_funding_rate",
        "next_funding_time"}
    assert isinstance(row["mark_price"], float)
    assert isinstance(row["next_funding_time"], int)


def test_fetch_open_interest_hist_mock() -> None:
    rows = futures.fetch_open_interest_hist(SYM)
    assert isinstance(rows, list) and len(rows) > 0
    assert set(rows[0]) == {"symbol", "sum_open_interest",
                            "sum_open_interest_value", "timestamp"}


def test_fetch_global_long_short_ratio_mock() -> None:
    rows = futures.fetch_global_long_short_ratio(SYM)
    assert isinstance(rows, list) and len(rows) > 0
    assert set(rows[0]) == {"symbol", "long_short_ratio", "long_account",
                            "short_account", "timestamp"}


def test_fetch_top_long_short_ratios_mock() -> None:
    """topLongShortAccountRatio 与 topLongShortPositionRatio 均可用。"""
    for fn in (futures.fetch_top_long_short_account_ratio,
               futures.fetch_top_long_short_position_ratio):
        rows = fn(SYM)
        assert isinstance(rows, list) and len(rows) > 0
        assert {"long_short_ratio", "timestamp"} <= set(rows[0])


def test_fetch_taker_long_short_ratio_mock() -> None:
    rows = futures.fetch_taker_long_short_ratio(SYM)
    assert isinstance(rows, list) and len(rows) > 0
    assert set(rows[0]) == {"symbol", "buy_sell_ratio", "buy_vol",
                            "sell_vol", "timestamp"}


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
        lambda: pytest.fail("mock 模式不应触碰 SDK"))
    assert futures.fetch_mark_price(SYM) is not None
    assert futures.fetch_listing_days() is not None


def test_fetch_mark_price_real_maps_fields(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """真实响应 → 字段 float 化/命名转换。"""
    monkeypatch.setenv("SR_MOCK", "0")

    def fake_call(fn, **kwargs):
        return {"symbol": SYM, "markPrice": "70000.5",
                "indexPrice": "69999.0", "lastFundingRate": "0.0001",
                "nextFundingTime": 1780000000000}

    monkeypatch.setattr(
        "strategy_research.datasources._binance_sdk.sync_call_with_rate_limit",
        fake_call)
    row = futures.fetch_mark_price(SYM)
    assert row == {"symbol": SYM, "mark_price": 70000.5,
                   "index_price": 69999.0, "last_funding_rate": 0.0001,
                   "next_funding_time": 1780000000000}


def test_fetch_listing_days_real_computes_days(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """exchangeInfo → 按 onboardDate 计算上市天数（毫秒口径）。"""
    monkeypatch.setenv("SR_MOCK", "0")
    import time as _t

    now_ms = int(_t.time() * 1000)
    listed_10d = now_ms - 10 * 86_400_000

    def fake_call(fn, **kwargs):
        return {"symbols": [
            {"symbol": "BTCUSDT", "onboardDate": listed_10d},
            {"symbol": "ETHUSDT", "onboardDate": now_ms},
            {"symbol": "NEWUSDT", "onboardDate": 0},  # 0 = 未知，应跳过
        ]}

    monkeypatch.setattr(
        "strategy_research.datasources._binance_sdk.sync_call_with_rate_limit",
        fake_call)
    days = futures.fetch_listing_days()
    assert set(days) == {"BTCUSDT", "ETHUSDT"}
    assert 9 <= days["BTCUSDT"] <= 11
    assert days["ETHUSDT"] == 0


def test_fetch_open_interest_hist_real_maps_fields(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """真实响应 camelCase+字符串 → snake_case+数值（与 mock 同构）。"""
    monkeypatch.setenv("SR_MOCK", "0")

    def fake_call(fn, **kwargs):
        return [
            {"symbol": SYM, "sumOpenInterest": "107316.966",
             "sumOpenInterestValue": "7985555445.47",
             "CMCCirculatingSupply": "20071518",
             "timestamp": 1787281200000},
        ]

    monkeypatch.setattr(
        "strategy_research.datasources._binance_sdk.sync_call_with_rate_limit",
        fake_call)
    rows = futures.fetch_open_interest_hist(SYM, limit=1)
    assert rows[0] == {
        "symbol": SYM, "sum_open_interest": 107316.966,
        "sum_open_interest_value": 7985555445.47,
        "CMCCirculatingSupply": "20071518",  # 未知字段原样保留
        "timestamp": 1787281200000,
    }


def test_fetch_taker_ratio_real_adds_symbol(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """taker 真实响应无 symbol 字段 → 补 symbol（与 mock 同构）。"""
    monkeypatch.setenv("SR_MOCK", "0")

    def fake_call(fn, **kwargs):
        return [{"buySellRatio": "0.9032", "buyVol": "7512.33",
                 "sellVol": "8317.09", "timestamp": 1787277600000}]

    monkeypatch.setattr(
        "strategy_research.datasources._binance_sdk.sync_call_with_rate_limit",
        fake_call)
    rows = futures.fetch_taker_long_short_ratio(SYM, limit=1)
    assert rows[0] == {"symbol": SYM, "buy_sell_ratio": 0.9032,
                       "buy_vol": 7512.33, "sell_vol": 8317.09,
                       "timestamp": 1787277600000}


def test_fetch_mark_price_failure_returns_none(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """失败即失败：异常 → None（装配层标 UNKNOWN），不回退 mock。"""
    monkeypatch.setenv("SR_MOCK", "0")

    def boom(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(
        "strategy_research.datasources._binance_sdk.sync_call_with_rate_limit",
        boom)
    assert futures.fetch_mark_price(SYM) is None
    assert futures.fetch_listing_days() is None
    assert futures.fetch_open_interest_hist(SYM) is None
