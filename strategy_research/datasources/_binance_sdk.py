"""Binance 官方 SDK 共享适配件（binance.py / binance_futures.py 共用）。

封装官方 SDK（``binance-sdk-spot`` / ``binance-sdk-derivatives-trading-usds-futures``
/ ``binance-common``）的同步 REST 调用：

- 惰性单例客户端（现货 / USDT-M 合约公开数据）
- pydantic 模型 → 普通 dict/list（:func:`to_plain`），业务代码不接触模型
- SDK 429/418 异常 → :class:`RateLimitedError`（Retry-After 优先，SDK 不
  暴露响应头时用固定退避兜底：429=60s、418=120/300/600s 递增）
- 分钟权重记账（:class:`WeightBudget`，默认 2400/min），超限阻塞等待

参考 BinanceApi 项目已验证的同一套模式（去 async / numpy / 认证客户端）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

from binance_common.configuration import ConfigurationRestAPI
from binance_common.constants import (
    DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL,
    SPOT_REST_API_PROD_URL,
)
from binance_common.errors import RateLimitBanError, TooManyRequestsError
from binance_sdk_derivatives_trading_usds_futures import (
    DerivativesTradingUsdsFutures,
)
from binance_sdk_spot import Spot
from pydantic import BaseModel

TIMEOUT_MS = 15_000
RETRIES = 3
BACKOFF_MS = 1000

#: 429/418 固定退避（秒）；Retry-After 存在时优先于它们
_FALLBACK_WAITS = {429: 60, 418: 120}
_418_WAITS = [120, 300, 600]
#: 整批重试前多等 10s，避免刚解封又撞上
_RETRY_AFTER_BUFFER = 10
#: 超过该时长的封禁不原地等，直接中止本轮
_MAX_SYNC_BAN_WAIT = 600

T = TypeVar("T")

# ── 惰性单例客户端 ─────────────────────────────────────────

_LOCK = threading.Lock()
_SPOT_REST: Any | None = None
_FUTURES_REST: Any | None = None


def _rest_config(base_path: str) -> ConfigurationRestAPI:
    """SDK REST 配置：15s 超时、3 次连接重试、1s 退避。"""
    return ConfigurationRestAPI(
        base_path=base_path,
        timeout=TIMEOUT_MS,
        retries=RETRIES,
        backoff=BACKOFF_MS,
    )


def get_spot_data_client():
    """惰性单例：现货公开数据客户端（主网 api.binance.com）。"""
    global _SPOT_REST
    with _LOCK:
        if _SPOT_REST is None:
            _SPOT_REST = Spot(
                config_rest_api=_rest_config(SPOT_REST_API_PROD_URL)
            ).rest_api
        return _SPOT_REST


def get_futures_data_client():
    """惰性单例：USDT-M 合约公开数据客户端（主网 fapi.binance.com）。"""
    global _FUTURES_REST
    with _LOCK:
        if _FUTURES_REST is None:
            _FUTURES_REST = DerivativesTradingUsdsFutures(
                config_rest_api=_rest_config(
                    DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL
                )
            ).rest_api
        return _FUTURES_REST


# ── 模型 → 普通 dict/list ─────────────────────────────────


def to_plain(value: Any) -> Any:
    """把 SDK 响应转换成普通 dict/list。

    - one-of 包装模型先解包 ``actual_instance``；
    - 普通模型走 ``to_dict()``（SDK 生成模型）或 ``model_dump()``（pydantic v2）；
    - 去掉 SDK 生成的空 ``additional_properties`` 字段，与原生 JSON 形状一致。
    """
    if isinstance(value, BaseModel):
        actual = getattr(value, "actual_instance", None)
        if actual is not None:
            return to_plain(actual)
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return to_plain(to_dict())
        return to_plain(value.model_dump())
    if isinstance(value, dict):
        result: dict = {}
        for key, val in value.items():
            if key == "additional_properties" and not val:
                continue
            result[key] = to_plain(val)
        return result
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    return value


# ── 限流与封禁 ─────────────────────────────────────────────


class RateLimitedError(Exception):
    """Binance 返回 429/418 时抛出，由 :func:`sync_call_with_rate_limit` 统一退避。"""

    def __init__(self, status: int, retry_after: float | None) -> None:
        super().__init__(
            f"HTTP {status} (retry after {retry_after:.0f}s)"
            if retry_after is not None
            else f"HTTP {status}"
        )
        self.status = status
        self.retry_after = retry_after


class WeightBudget:
    """分钟权重预算（默认 2400/min）：按请求估算权重累加，超限阻塞。

    Binance 按分钟权重窗口（滚动 60s）限流，触发 429/418 会封禁 IP。
    单请求权重（如 ticker/24hr 全市场 80、mark_price 全市场 10）计入
    滚动窗口，累计将超预算时阻塞等待最老请求过期后再放行。
    """

    def __init__(self, limit: int = 2400, warn_at: int = 2000) -> None:
        self._limit = limit
        self._warn_at = warn_at
        #: (monotonic 秒, 权重) 滚动窗口
        self._events: list[tuple[float, int]] = []
        self._lock = threading.Lock()

    def _used(self, now: float) -> int:
        cutoff = now - 60.0
        self._events = [(t, w) for t, w in self._events if t > cutoff]
        return sum(w for _, w in self._events)

    def acquire_sync(self, weight: int) -> None:
        """同步获取权重额度（不足时等待滚动窗口过期，约 1s 粒度轮询）。"""
        weight = max(1, int(weight))
        while True:
            now = time.monotonic()
            with self._lock:
                used = self._used(now)
                if used + weight <= self._limit:
                    self._events.append((now, weight))
                    return
            time.sleep(1.0)

    def used(self) -> int:
        with self._lock:
            return self._used(time.monotonic())


#: 进程级共享权重预算
_SHARED_WEIGHT_BUDGET: WeightBudget | None = None


def get_shared_weight_budget() -> WeightBudget:
    """进程级共享权重预算：所有数据源调用共用一个滚动窗口。"""
    global _SHARED_WEIGHT_BUDGET
    with _LOCK:
        if _SHARED_WEIGHT_BUDGET is None:
            _SHARED_WEIGHT_BUDGET = WeightBudget()
        return _SHARED_WEIGHT_BUDGET


def _retry_after_from(exc: Exception) -> float | None:
    """尽力从 SDK 异常中读取 Retry-After（SDK 通常不暴露响应头）。"""
    headers = getattr(exc, "headers", None)
    if not isinstance(headers, dict):
        return None
    header = headers.get("Retry-After") or headers.get("retry-after")
    if header is None:
        return None
    try:
        return max(0.0, float(header))
    except (TypeError, ValueError):
        return None


def _backoff_seconds(exc: RateLimitedError, attempt: int) -> float:
    """429/418 的等待时长：Retry-After 优先（+10s 缓冲），缺失时固定退避。"""
    if exc.retry_after is not None:
        return exc.retry_after + _RETRY_AFTER_BUFFER
    if exc.status == 418:
        return _418_WAITS[min(attempt, len(_418_WAITS) - 1)]
    return _FALLBACK_WAITS[429]


def sdk_call(fn: Callable[..., T], *args: Any, **kwargs: Any) -> Any:
    """同步执行一次 SDK 调用并把响应转成普通 dict/list。

    429/418（``TooManyRequestsError`` / ``RateLimitBanError``）桥接为
    :class:`RateLimitedError`，由 :func:`sync_call_with_rate_limit` 统一
    整批退避；其余异常原样抛出（SDK 的连接级重试已在其内部完成）。
    """
    try:
        response = fn(*args, **kwargs)
    except TooManyRequestsError as exc:
        raise RateLimitedError(429, _retry_after_from(exc)) from exc
    except RateLimitBanError as exc:
        raise RateLimitedError(418, _retry_after_from(exc)) from exc
    if hasattr(response, "data"):
        response = response.data()
    return to_plain(response)


def sync_call_with_rate_limit(
    fn: Callable[..., T],
    *args: Any,
    name: str = "",
    attempts: int = 3,
    weight: int = 1,
    budget: WeightBudget | None = None,
    max_wait: int = _MAX_SYNC_BAN_WAIT,
    **kwargs: Any,
) -> Any:
    """同步执行 SDK 调用，429/418 按固定退避重试。

    - 调用前先计入分钟权重预算；
    - 429 等待 60s，418 等待递增（120s/300s/600s）；
    - Retry-After 存在时优先于固定退避（+10s 缓冲）；
    - 418 封禁超 ``max_wait``（默认 600s）不原地等，立即抛错中止。
    """
    if budget is None:
        budget = get_shared_weight_budget()
    budget.acquire_sync(weight)
    for attempt in range(attempts):
        try:
            return sdk_call(fn, *args, **kwargs)
        except RateLimitedError as exc:
            wait = _backoff_seconds(exc, attempt)
            if exc.status == 418 and wait > max_wait:
                raise RuntimeError(
                    f"IP 被封禁（需等待 {wait:.0f} 秒，超过 {max_wait}s 上限）"
                    "— 中止本轮"
                ) from exc
            if attempt == attempts - 1:
                raise RuntimeError(f"{name} API 重试 {attempts} 次后仍失败") from exc
            time.sleep(wait)
    raise RuntimeError(f"{name} API 重试 {attempts} 次后仍失败")
