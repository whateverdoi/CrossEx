"""扫描器快照只读接入：CSV 解析 / boards 聚合 / 日期选择 / 缺失与损坏容错。

数据源契约 = BinanceApi research/scan.py 产出（{date}_all/movers/microstructure），
测试用 tmp_path 构造同格式 CSV，不触网、不依赖本机目录。
"""

from __future__ import annotations

import csv
from pathlib import Path

from strategy_research import scanner_snapshot as scan

_ALL_ROWS = [
    {
        "symbol": "AKEUSDT",
        "ret_1h": "3.9425",
        "ret_4h": "0.0952",
        "ret_24h": "-10.3184",
        "ret_7d": "132.2585",
        "price": "0.009465",
        "taker_buy_ratio_24h": "0.4983",
        "quote_volume_24h": "202807691.0",
        "funding_rate": "5e-05",
        "futures_premium_pct": "0.1501",
        "open_interest": "4171661225.0",
        "open_interest_value": "39484773.0",
        "price_change_pct_24h": "-8.668",
        "listing_days": "324.0",
        "onboard_date": "2025-09-26",
    }
]

_MOVERS_ROWS = [
    {"symbol": "AKEUSDT", "board": "gain_1h"},
    {"symbol": "AKEUSDT", "board": "loss_24h"},
    {"symbol": "AKEUSDT", "board": "gain_7d"},
    {"symbol": "AKEUSDT", "board": "gain_1h"},  # 重复 → 去重
]

_MICRO_ROWS = [
    {
        "symbol": "AKEUSDT",
        "oi_change_24h": "-0.5416",
        "oi_change_48h": "-27.8883",
        "oi_value_change_24h": "-10.0886",
        "ls_ratio_all": "0.5172",
        "ls_ratio_all_change_24h": "-3.2186",
        "ls_ratio_top_acc": "0.4286",
        "ls_ratio_top_pos": "0.6445",
        "taker_bs_ratio": "1.0105",
        "funding_avg": "7.9e-05",
        "funding_trend": "flat",
    }
]


def _write_csv(path: Path, rows: list[dict]) -> None:
    """按扫描器格式写 CSV（DictWriter 带列头）。"""
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _scan_dir(tmp_path: Path, date: str = "2026-08-16") -> Path:
    """构造一份完整扫描器目录（all/movers/microstructure 三件套）。"""
    d = tmp_path / "scan"
    d.mkdir()
    _write_csv(d / f"{date}_all.csv", _ALL_ROWS)
    _write_csv(d / f"{date}_movers.csv", _MOVERS_ROWS)
    _write_csv(d / f"{date}_microstructure.csv", _MICRO_ROWS)
    return d


def test_load_snapshots_parses_fields(tmp_path):
    """验收：all.csv 数值化 + 日期字符串保留 + date 回传；movers board 去重保序；
    microstructure 数值化 + funding_trend 字符串保留。"""
    d = _scan_dir(tmp_path)
    snap = scan.load_snapshots(dir=str(d))
    m = snap["market"]["AKE"]  # 交易对 key 归一化为裸符号（AKEUSDT → AKE）
    assert snap["date"] == "2026-08-16"
    assert m["price"] == 0.009465
    assert m["ret_1h"] == 3.9425
    assert m["ret_24h"] == -10.3184
    assert m["ret_7d"] == 132.2585
    assert m["price_change_pct_24h"] == -8.668
    assert m["quote_volume_24h"] == 202807691.0
    assert m["funding_rate"] == 5e-05
    assert m["taker_buy_ratio_24h"] == 0.4983
    assert m["open_interest_value"] == 39484773.0
    assert m["futures_premium_pct"] == 0.1501
    assert m["listing_days"] == 324.0
    assert m["onboard_date"] == "2025-09-26"
    assert m["boards"] == ["gain_1h", "loss_24h", "gain_7d"]  # 同币多行去重保序

    mic = snap["microstructure"]["AKE"]
    assert mic["oi_change_24h"] == -0.5416
    assert mic["oi_change_48h"] == -27.8883
    assert mic["oi_value_change_24h"] == -10.0886
    assert mic["ls_ratio_all"] == 0.5172
    assert mic["ls_ratio_all_change_24h"] == -3.2186
    assert mic["ls_ratio_top_acc"] == 0.4286
    assert mic["ls_ratio_top_pos"] == 0.6445
    assert mic["taker_bs_ratio"] == 1.0105
    assert mic["funding_avg"] == 7.9e-05
    assert mic["funding_trend"] == "flat"


def test_load_snapshots_date_resolution(tmp_path, monkeypatch):
    """验收：显式 date 优先 > 环境变量 > 缺省取最新 *_all.csv。"""
    d = _scan_dir(tmp_path)
    _write_csv(d / "2026-08-15_all.csv", _ALL_ROWS)
    _write_csv(d / "2026-08-15_movers.csv", _MOVERS_ROWS)
    _write_csv(d / "2026-08-15_microstructure.csv", _MICRO_ROWS)
    assert scan.load_snapshots(dir=str(d))["date"] == "2026-08-16"  # 最新
    assert scan.load_snapshots(dir=str(d), date="2026-08-15")["date"] == "2026-08-15"
    monkeypatch.setenv(scan.ENV_DATE, "2026-08-15")
    assert scan.load_snapshots(dir=str(d))["date"] == "2026-08-15"


def test_load_snapshots_missing_and_corrupt(tmp_path, monkeypatch):
    """验收：目录/文件缺失 → {}；损坏文件（非法 UTF-8）→ {}；绝不抛异常。"""
    assert scan.load_snapshots(dir=str(tmp_path / "nope")) == {}
    d = tmp_path / "scan"
    d.mkdir()
    assert scan.load_snapshots(dir=str(d)) == {}  # 无任何 CSV
    monkeypatch.setenv(scan.ENV_DIR, str(tmp_path / "nope2"))
    assert scan.load_snapshots() == {}

    (d / "2026-08-16_all.csv").write_bytes(b"\xff\xfe\x00broken")
    assert scan.load_snapshots(dir=str(d)) == {}  # 损坏文件 → {}，不抛异常


def test_load_snapshots_nan_empty_invalid_cells(tmp_path):
    """验收：空值/NaN/非法数值 → None，不抛异常。"""
    d = tmp_path / "scan"
    d.mkdir()
    _write_csv(
        d / "2026-08-16_all.csv",
        [
            {
                "symbol": "X",
                "price": "",
                "ret_24h": "nan",
                "ret_7d": "abc",
                "onboard_date": "2025-01-01",
            }
        ],
    )
    _write_csv(
        d / "2026-08-16_microstructure.csv",
        [{"symbol": "X", "oi_change_24h": "-0.5", "funding_trend": ""}],
    )
    snap = scan.load_snapshots(dir=str(d))
    m = snap["market"]["X"]
    assert m["price"] is None
    assert m["ret_24h"] is None
    assert m["ret_7d"] is None
    assert m["onboard_date"] == "2025-01-01"
    assert snap["microstructure"]["X"]["funding_trend"] is None
    assert snap["microstructure"]["X"]["oi_change_24h"] == -0.5

if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-q"])
