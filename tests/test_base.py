"""四元组包装原语测试（规格十节纪律 3/4：数据点包装 + UNKNOWN 纪律）。"""

from __future__ import annotations

from datetime import datetime, timezone

from strategy_research.datasources.base import UNKNOWN, error_point, wrap

SOURCE = "binance"


def test_wrap() -> None:
    """wrap 标准四元组：value/source/UTC ISO timestamp/confidence，可显式指定。"""
    point = wrap(42.5, SOURCE)
    assert set(point) == {"value", "source", "timestamp", "confidence"}
    assert point["value"] == 42.5
    assert point["source"] == SOURCE
    assert point["confidence"] == 1.0

    parsed = datetime.fromisoformat(point["timestamp"])
    assert parsed.tzinfo is not None
    assert parsed.tzinfo.utcoffset(parsed).total_seconds() == 0

    ts = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    custom = wrap(1, SOURCE, confidence=0.7, timestamp=ts)
    assert custom["confidence"] == 0.7
    assert custom["timestamp"] == ts


def test_error_point() -> None:
    """失败数据点：value=UNKNOWN、confidence=0.0、保留错误信息且可进 run.json。"""
    point = error_point(SOURCE, "connection refused")
    assert point["value"] == UNKNOWN
    assert point["source"] == SOURCE
    assert point["confidence"] == 0.0
    assert point["error"] == "connection refused"
    assert set(point) >= {"value", "source", "timestamp", "confidence"}

    import json

    json.dumps(error_point(SOURCE, "boom"))
    json.dumps(wrap([1, 2], SOURCE))
