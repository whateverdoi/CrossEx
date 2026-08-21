"""01 票 RED：SR_MOCK=1 主流程产出 run.json + overview.md 骨架。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.main import main

REPORTS = Path(__file__).resolve().parent.parent / "reports"


def test_main_produces_run_json_and_overview(tmp_path, monkeypatch):
    """主入口：SR_MOCK=1 跑通，产出 run.json + overview.md。"""
    monkeypatch.chdir(tmp_path)
    meta = main(["--tokens", "BTC,ETH"])
    assert meta.get("report_path")
    report_dir = Path(meta["report_path"])
    assert (report_dir / "run.json").exists()
    assert (report_dir / "overview.md").exists()
    data = json.loads((report_dir / "run.json").read_text(encoding="utf-8"))
    assert data["meta"]["mode"] == "mock"
    assert data["meta"]["tokens"] == ["BTC", "ETH"]
