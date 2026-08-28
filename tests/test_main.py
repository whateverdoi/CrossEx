"""01 票 RED：SR_MOCK=1 主流程产出 run.json + evidence.md 骨架（04 票改）。

mock 默认不落盘（SR_MOCK_REPORT=1 显式开启才写 reports/mock/<ts>/）。
"""

from __future__ import annotations

import json
from pathlib import Path

from strategy_research import main as main_mod
from strategy_research.main import main

REPORTS = Path(__file__).resolve().parent.parent / "reports"


def test_main_produces_run_json_and_evidence(tmp_path, monkeypatch):
    """主入口：SR_MOCK=1 + SR_MOCK_REPORT=1 跑通，产出 run.json + evidence.md。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SR_MOCK_REPORT", "1")
    meta = main(["--tokens", "BTC,ETH"])
    assert meta.get("report_path")
    report_dir = Path(meta["report_path"])
    assert (report_dir / "run.json").exists()
    assert (report_dir / "evidence.md").exists()
    data = json.loads((report_dir / "run.json").read_text(encoding="utf-8"))
    assert data["meta"]["mode"] == "mock"
    assert data["meta"]["tokens"] == ["BTC", "ETH"]
    assert report_dir.parent.name == "mock"  # mock 产物独立目录


def test_mock_no_report_by_default(tmp_path, monkeypatch):
    """mock 默认不落盘：仅返回 meta（report_path=None），reports/ 无新文件。"""
    monkeypatch.chdir(tmp_path)
    meta = main(["--tokens", "BTC,ETH"])
    assert meta.get("report_path") is None
    assert not (Path("reports")).exists() or not any(
        p.is_dir() for p in Path("reports").iterdir()
    )


def test_cleanup_old_reports_keeps_latest_n(monkeypatch, tmp_path):
    """08 票：归档清理只保留最新 N 份 live 报告（目录名 = UTC 时间戳字典序）；
    SR_KEEP_REPORTS=0 不清理；reports/mock/ 与 reports/latest/ 不受影响。"""
    monkeypatch.chdir(tmp_path)
    reports = Path("reports")
    for i in range(5):
        (reports / f"2026010{i + 1}T000000Z000000").mkdir(parents=True)
    (reports / "mock" / "20260101T000000Z000000").mkdir(parents=True)
    (reports / "latest").mkdir()
    main_mod._cleanup_old_reports()  # 默认 30：不删
    assert len(list(reports.glob("2*"))) == 5
    monkeypatch.setenv("SR_KEEP_REPORTS", "2")
    main_mod._cleanup_old_reports()
    remaining = sorted(p.name for p in reports.glob("2*"))
    assert remaining == ["20260104T000000Z000000", "20260105T000000Z000000"]
    assert (reports / "mock" / "20260101T000000Z000000").is_dir()  # mock 产物保留
    assert (reports / "latest").is_dir()  # latest 不清理
    monkeypatch.setenv("SR_KEEP_REPORTS", "0")
    main_mod._cleanup_old_reports()  # 0 = 不清理
    assert len(list(reports.glob("2*"))) == 2
    monkeypatch.setenv("SR_KEEP_REPORTS", "abc")
    main_mod._cleanup_old_reports()  # 非法值兜底默认 30
    assert len(list(reports.glob("2*"))) == 2
