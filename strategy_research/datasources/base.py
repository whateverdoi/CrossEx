"""数据点四元组包装原语（规格十节纪律 3/4）。

所有数据源产出统一形状的数据点：

``{value, source, timestamp, confidence}``

- 失败数据点：``value="UNKNOWN"``、``confidence=0.0``、附 ``error`` 信息
- 缺失字段一律 UNKNOWN，绝不猜测、不用默认值填充
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

#: 数据点失败标记（UNKNOWN 纪律）
UNKNOWN = "UNKNOWN"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def wrap(
    value: Any,
    source: str,
    confidence: float = 1.0,
    timestamp: str | None = None,
) -> dict:
    """把原始值包装成标准四元组数据点。

    Parameters
    ----------
    value : Any
        数据点原始值（数值 / dict / list）。
    source : str
        数据源白名单名（binance / binance_futures / defillama / bing / mock）。
    confidence : float
        数据点置信度，默认 1.0；来源不可靠时由调用方降低。
    timestamp : str | None
        ISO 8601 UTC 时间戳，默认当前时间。
    """
    return {
        "value": value,
        "source": source,
        "timestamp": timestamp or _now_iso(),
        "confidence": confidence,
    }


def error_point(source: str, error: str, timestamp: str | None = None) -> dict:
    """失败数据点：value=UNKNOWN、confidence=0.0、保留错误信息。"""
    point = wrap(UNKNOWN, source, confidence=0.0, timestamp=timestamp)
    point["error"] = error
    return point
