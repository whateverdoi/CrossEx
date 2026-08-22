"""pytest 共享配置：默认 mock 模式，避免测试误发外部请求。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("SR_MOCK", "1")


@pytest.fixture(autouse=True)
def _isolate_scan_dir(tmp_path, monkeypatch):
    """扫描器快照隔离：SR_SCAN_DIR 指向临时空目录，测试不受本机 CSV 影响。"""
    monkeypatch.setenv("SR_SCAN_DIR", str(tmp_path / "scan_empty"))
    monkeypatch.delenv("SR_SCAN_DATE", raising=False)
