"""binance 现货数据源测试（官方 SDK 薄适配 + mock 同构 + 失败即失败）。"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import ClassVar

import pytest
from binance_common.errors import RateLimitBanError, TooManyRequestsError
from pydantic import BaseModel

from strategy_research.datasources import _binance_sdk as sdk
from strategy_research.datasources import binance as binance_ds
from strategy_research.datasources.mock import MOCK_TOKENS

# ── to_plain 解包 ───────────────────────────────────────────


class Nested(BaseModel):
    value: int


class Response(BaseModel):
    symbol: str
    nested: Nested


class OneOf(BaseModel):
    actual_instance: BaseModel | None = None


def test_to_plain_unpacks_basemodel_with_to_dict() -> None:
    plain = sdk.to_plain(Response(symbol="BTCUSDT", nested=Nested(value=7)))
    assert plain == {"symbol": "BTCUSDT", "nested": {"value": 7}}


def test_to_plain_unwraps_actual_instance() -> None:
    plain = sdk.to_plain(
        OneOf(actual_instance=Response(symbol="ETHUSDT", nested=Nested(value=1)))
    )
    assert plain == {"symbol": "ETHUSDT", "nested": {"value": 1}}


def test_to_plain_drops_empty_additional_properties() -> None:
    plain = sdk.to_plain({"symbol": "X", "additional_properties": {}})
    assert "additional_properties" not in plain


def test_to_plain_keeps_nonempty_additional_properties() -> None:
    plain = sdk.to_plain({"symbol": "X", "additional_properties": {"a": 1}})
    assert plain["additional_properties"] == {"a": 1}


def test_to_plain_recurses_nested_containers() -> None:
    plain = sdk.to_plain([{"list": [Response(symbol="A", nested=Nested(value=2))]}])
    assert plain == [{"list": [{"symbol": "A", "nested": {"value": 2}}]}]


# ── 429/418 桥接与退避 ──────────────────────────────────────


def _rate_limited_fn(status: int):
    def fn(*args, **kwargs):
        if status == 429:
            raise TooManyRequestsError(status_code=429)
        raise RateLimitBanError(status_code=418)

    return fn


def test_sdk_call_bridges_429() -> None:
    with pytest.raises(sdk.RateLimitedError) as exc:
        sdk.sdk_call(_rate_limited_fn(429))
    assert exc.value.status == 429


def test_sdk_call_bridges_418() -> None:
    with pytest.raises(sdk.RateLimitedError) as exc:
        sdk.sdk_call(_rate_limited_fn(418))
    assert exc.value.status == 418


def test_sdk_call_returns_plain_dict() -> None:
    class FakeResp:
        def data(self) -> BaseModel:
            return Response(symbol="BTCUSDT", nested=Nested(value=3))

    result = sdk.sdk_call(lambda: FakeResp())
    assert result == {"symbol": "BTCUSDT", "nested": {"value": 3}}


def test_retry_after_from_headers() -> None:
    class FakeExc:
        headers: ClassVar[dict[str, str]] = {"Retry-After": "30"}

    assert sdk._retry_after_from(FakeExc()) == 30.0
    assert sdk._retry_after_from(Exception()) is None


def test_backoff_seconds_prefers_retry_after() -> None:
    exc = sdk.RateLimitedError(429, retry_after=5.0)
    assert sdk._backoff_seconds(exc, 0) == 15.0  # +10s 缓冲


def test_backoff_seconds_fallbacks() -> None:
    assert sdk._backoff_seconds(sdk.RateLimitedError(429, None), 0) == 60
    assert sdk._backoff_seconds(sdk.RateLimitedError(418, None), 0) == 120
    assert sdk._backoff_seconds(sdk.RateLimitedError(418, None), 3) == 600


# ── WeightBudget 权重记账 ────────────────────────────────────


def test_weight_budget_tracks_usage() -> None:
    budget = sdk.WeightBudget(limit=100)
    budget.acquire_sync(5)
    budget.acquire_sync(10)
    assert budget.used() == 15


def test_weight_budget_expires_old_events() -> None:
    budget = sdk.WeightBudget(limit=100)
    budget._events.append((time.monotonic() - 61.0, 80))
    assert budget.used() == 0


def test_sync_call_with_rate_limit_succeeds() -> None:
    budget = sdk.WeightBudget(limit=100)
    result = sdk.sync_call_with_rate_limit(
        lambda: {"ok": 1}, name="test", weight=1, budget=budget
    )
    assert result == {"ok": 1}
    assert budget.used() == 1


def test_sync_call_with_rate_limit_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """重试耗尽仍失败 → RuntimeError，不吞异常。"""

    def boom(*args, **kwargs):
        raise TooManyRequestsError(status_code=429)

    monkeypatch.setattr(sdk, "_backoff_seconds", lambda exc, n: 0.001)
    with pytest.raises(RuntimeError, match="重试"):
        sdk.sync_call_with_rate_limit(
            boom, name="test", attempts=2, weight=1, budget=sdk.WeightBudget(limit=100)
        )


def test_sync_call_with_rate_limit_aborts_on_long_ban(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """418 且退避超上限（默认 600s）→ 立即中止，不傻等。"""

    def fn(*args, **kwargs):
        raise RateLimitBanError(status_code=418)

    monkeypatch.setattr(sdk, "_backoff_seconds", lambda exc, n: 999999.0)
    with pytest.raises(RuntimeError, match="封禁"):
        sdk.sync_call_with_rate_limit(
            fn, name="test", attempts=2, weight=1, budget=sdk.WeightBudget(limit=100)
        )


# ── 现货 24hr ticker ────────────────────────────────────────


def test_fetch_ticker_24h_all_mock() -> None:
    rows = binance_ds.fetch_ticker_24h_all()
    assert rows is not None
    assert len(rows) == len(MOCK_TOKENS)
    symbols = {r["symbol"] for r in rows}
    assert symbols == {s + "USDT" for s in MOCK_TOKENS}
    for row in rows:
        assert set(row) == {"symbol", "price", "price_change_pct", "quote_volume"}
        assert isinstance(row["price"], float)
        assert isinstance(row["price_change_pct"], float)
        assert isinstance(row["quote_volume"], float)


def test_fetch_ticker_24h_all_mock_zero_external_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SR_MOCK=1 时零外部请求：SDK 单例被调用即爆炸也不影响。"""
    monkeypatch.setattr(
        "strategy_research.datasources._binance_sdk.get_spot_data_client",
        lambda: pytest.fail("mock 模式不应触碰 SDK"),
    )
    rows = binance_ds.fetch_ticker_24h_all()
    assert rows is not None and len(rows) == 6


def test_fetch_ticker_24h_all_real_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失败即失败：网络异常 → None（装配层标 UNKNOWN），不回退 mock。"""
    monkeypatch.setenv("SR_MOCK", "0")

    def boom(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(
        "strategy_research.datasources._binance_sdk.sync_call_with_rate_limit", boom
    )
    assert binance_ds.fetch_ticker_24h_all() is None


def test_fetch_ticker_24h_all_real_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实响应 → {symbol, price, price_change_pct, quote_volume}（float 化）。"""
    monkeypatch.setenv("SR_MOCK", "0")

    def fake_call(fn, **kwargs):
        return [
            {
                "symbol": "BTCUSDT",
                "lastPrice": "70000.0",
                "priceChangePercent": "2.5",
                "quoteVolume": "123456.78",
            },
            {
                "symbol": "ETHUSDT",
                "lastPrice": "3500.0",
                "priceChangePercent": "-1.2",
                "quoteVolume": "90000.0",
            },
        ]

    monkeypatch.setattr(
        "strategy_research.datasources._binance_sdk.sync_call_with_rate_limit",
        fake_call,
    )
    rows = binance_ds.fetch_ticker_24h_all()
    assert rows == [
        {
            "symbol": "BTCUSDT",
            "price": 70000.0,
            "price_change_pct": 2.5,
            "quote_volume": 123456.78,
        },
        {
            "symbol": "ETHUSDT",
            "price": 3500.0,
            "price_change_pct": -1.2,
            "quote_volume": 90000.0,
        },
    ]


# ── 日线收益单一口径（预测能力 02 票：适配器唯一实现） ──


_DAY_MS = 86_400_000
_ANCHOR = datetime(2026, 8, 13, tzinfo=timezone.utc)  # 决策日 00:00 UTC
_ANCHOR_MS = int(_ANCHOR.timestamp() * 1000)
_RUN_TS_MS = _ANCHOR_MS + 12 * 3_600_000  # 决策发生在当日盘中


def _kline_rows(spec: dict[int, float], anchor_ms: int = _ANCHOR_MS) -> list[dict]:
    """造日线：spec = {day_offset: close_price}（offset=0 为决策日 00:00 UTC）。"""
    return [
        {"open_time": anchor_ms + off * _DAY_MS, "close_price": price}
        for off, price in spec.items()
    ]


#: 标准价格路径：D-1 收 100（基准），D0 收 110，D6 收 130
_STD_KLINES = _kline_rows({-10: 90, -2: 95, -1: 100, 0: 110, 3: 120, 6: 130, 10: 140})


class TestPairSymbol:
    def test_bare_symbol_gets_usdt(self):
        assert binance_ds.pair_symbol("BTC") == "BTCUSDT"
        assert binance_ds.pair_symbol("1000PEPE") == "1000PEPEUSDT"

    def test_quote_suffixed_unchanged(self):
        for q in binance_ds.QUOTES:
            assert binance_ds.pair_symbol(f"BTC{q}") == f"BTC{q}"


class TestTrailingReturn:
    def test_basic(self):
        """最新收盘 vs N 日前收盘（live 口径：含未收盘 bar）。"""
        rows = _kline_rows({-3: 90, -2: 95, -1: 100, 0: 110})
        assert binance_ds.trailing_return(rows, 1) == pytest.approx(10.0)
        assert binance_ds.trailing_return(rows, 2) == pytest.approx(110 / 95 * 100 - 100)

    def test_insufficient_window_none(self):
        assert binance_ds.trailing_return(_kline_rows({0: 100}), 1) is None
        assert binance_ds.trailing_return(None, 1) is None

    def test_bad_close_none(self):
        rows = [{"open_time": 1, "close_price": None}, {"open_time": 2, "close_price": 100}]
        assert binance_ds.trailing_return(rows, 1) is None


class TestClosedDailyReturns:
    def test_basic_positioning_no_lookahead(self):
        """base = 决策时最近已收盘日线（D-1），非决策日当根（无前视）。"""
        out = binance_ds.closed_daily_returns(_STD_KLINES, _RUN_TS_MS)
        assert out["base_price"] == 100
        assert out["ret_1d"] == 10.0  # 110/100
        assert out["ret_7d"] == 30.0  # 130/100

    def test_insufficient_window_returns_none(self):
        out = binance_ds.closed_daily_returns(
            _kline_rows({-1: 100, 0: 110, 3: 120}), _RUN_TS_MS
        )
        assert out["ret_1d"] == 10.0
        assert out["ret_7d"] is None  # D+6 无数据

    def test_before_window_empty(self):
        assert binance_ds.closed_daily_returns(_kline_rows({1: 110, 2: 120}), _RUN_TS_MS) == {}

    def test_tolerates_disorder_and_bad_rows(self):
        """乱序 + 坏行（缺字段）不干扰定位（mock 降序防御）。"""
        rows = [
            {"open_time": None, "close_price": 1},
            *_STD_KLINES[::-1],  # 降序
            {"open_time": 5, "close_price": None},
        ]
        out = binance_ds.closed_daily_returns(rows, _RUN_TS_MS)
        assert out["base_price"] == 100
        assert out["ret_7d"] == 30.0

    def test_gap_day_is_none_not_adjacent(self):
        """D+6 缺失 → ret_7d None（不误用相邻日 D+3）。"""
        out = binance_ds.closed_daily_returns(
            _kline_rows({-1: 100, 0: 110, 3: 120, 7: 140}), _RUN_TS_MS
        )
        assert out["ret_7d"] is None

    def test_empty_inputs(self):
        assert binance_ds.closed_daily_returns(None, _RUN_TS_MS) == {}
        assert binance_ds.closed_daily_returns([], _RUN_TS_MS) == {}

    def test_custom_days_keys(self):
        out = binance_ds.closed_daily_returns(_STD_KLINES, _RUN_TS_MS, days=(7,))
        assert set(out) == {"base_price", "ret_7d"}
