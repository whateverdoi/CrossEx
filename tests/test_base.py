"""四元组包装原语测试（规格十节纪律 3/4：数据点包装 + UNKNOWN 纪律）。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from strategy_research.datasources.base import UNKNOWN, error_point, wrap

SOURCE = "binance"


def test_wrap_basic_format() -> None:
    """wrap 产出标准四元组：value/source/timestamp/confidence。"""
    point = wrap(42.5, SOURCE)
    assert set(point) == {"value", "source", "timestamp", "confidence"}
    assert point["value"] == 42.5
    assert point["source"] == SOURCE
    assert point["confidence"] == 1.0


def test_wrap_timestamp_is_utc_iso() -> None:
    """timestamp 为可解析的 UTC ISO 8601 字符串。"""
    point = wrap(1, SOURCE)
    parsed = datetime.fromisoformat(point["timestamp"])
    assert parsed.tzinfo is not None
    assert parsed.tzinfo.utcoffset(parsed).total_seconds() == 0


def test_wrap_custom_confidence_and_timestamp() -> None:
    """confidence/timestamp 可显式指定。"""
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    point = wrap(1, SOURCE, confidence=0.7, timestamp=ts)
    assert point["confidence"] == 0.7
    assert point["timestamp"] == ts


def test_error_point_marks_unknown() -> None:
    """失败数据点：value=UNKNOWN、confidence=0.0、保留错误信息。"""
    point = error_point(SOURCE, "connection refused")
    assert point["value"] == UNKNOWN
    assert point["source"] == SOURCE
    assert point["confidence"] == 0.0
    assert point["error"] == "connection refused"
    assert set(point) >= {"value", "source", "timestamp", "confidence"}


def test_error_point_is_json_serializable() -> None:
    """数据点必须可进 run.json。"""
    import json

    json.dumps(error_point(SOURCE, "boom"))
    json.dumps(wrap([1, 2], SOURCE))
