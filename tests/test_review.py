"""12 票测试：决策追踪与校准（评估回路）。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from strategy_research import report as report_mod
from strategy_research import review
from strategy_research.datasources import mock as mock_ds

_DAY_MS = 86_400_000
_ANCHOR = datetime(2026, 8, 13, tzinfo=timezone.utc)  # 到期 run 的决策日
_ANCHOR_MS = int(_ANCHOR.timestamp() * 1000)
_RUN_TS_MS = _ANCHOR_MS + 12 * 3_600_000  # 决策发生在当日盘中
_NOW = datetime(2026, 8, 21, tzinfo=timezone.utc)


def _kline_rows(spec: dict[int, float], anchor_ms: int = _ANCHOR_MS) -> list[dict]:
    """造日线：spec = {day_offset: close_price}（offset=0 为决策日 00:00 UTC）。"""
    return [
        {"open_time": anchor_ms + off * _DAY_MS, "close_price": price}
        for off, price in spec.items()
    ]


#: 标准价格路径：D-1 收 100（基准），D0 收 110，D6 收 130
_STD_KLINES = _kline_rows(
    {-10: 90, -2: 95, -1: 100, 0: 110, 3: 120, 6: 130, 10: 140}
)


# ── _returns（纯函数） ───────────────────────────────────


class TestReturns:
    def test_basic_positioning_no_lookahead(self):
        """base = 决策时最近已收盘日线（D-1），非决策日当根（无前视）。"""
        out = review._returns(_STD_KLINES, _RUN_TS_MS)
        assert out["base_price"] == 100
        assert out["ret_1d"] == 10.0  # 110/100
        assert out["ret_7d"] == 30.0  # 130/100

    def test_insufficient_window_returns_none(self):
        out = review._returns(_kline_rows({-1: 100, 0: 110, 3: 120}), _RUN_TS_MS)
        assert out["ret_1d"] == 10.0
        assert out["ret_7d"] is None  # D+6 无数据

    def test_before_window_empty(self):
        assert review._returns(_kline_rows({1: 110, 2: 120}), _RUN_TS_MS) == {}

    def test_tolerates_disorder_and_bad_rows(self):
        """乱序 + 坏行（缺字段）不干扰定位（mock 降序防御）。"""
        rows = [
            {"open_time": None, "close_price": 1},
            *_STD_KLINES[::-1],  # 降序
            {"open_time": 5, "close_price": None},
        ]
        out = review._returns(rows, _RUN_TS_MS)
        assert out["base_price"] == 100
        assert out["ret_7d"] == 30.0

    def test_gap_day_is_none_not_adjacent(self):
        """D+6 缺失 → ret_7d None（不误用相邻日 D+3）。"""
        out = review._returns(_kline_rows({-1: 100, 0: 110, 3: 120, 7: 140}), _RUN_TS_MS)
        assert out["ret_7d"] is None

    def test_empty_inputs(self):
        assert review._returns(None, _RUN_TS_MS) == {}
        assert review._returns([], _RUN_TS_MS) == {}


# ── _hit / _calibrate（纯函数） ─────────────────────────


class TestHitAndCalibrate:
    def test_hit_direction(self):
        assert review._hit("long", 5.0) is True
        assert review._hit("long", -5.0) is False
        assert review._hit("short", -5.0) is True
        assert review._hit("short", 5.0) is False
        assert review._hit("long", None) is None

    def test_calibrate_bins_and_groups(self):
        records = [
            {"hit_7d": True, "confidence": 0.8, "decision": "TRADE"},
            {"hit_7d": False, "confidence": 0.3, "decision": "WATCH"},
            {"hit_7d": True, "confidence": 0.6, "decision": "WATCH"},
            {"hit_7d": None, "confidence": 0.9, "decision": "TRADE"},  # UNAVAILABLE
            {"hit_7d": True, "confidence": 1.0, "decision": "TRADE"},
        ]
        stats = review._calibrate(records)
        assert stats["n"] == 4  # UNAVAILABLE 不计入
        assert stats["hit_rate"] == 0.75
        bins = {b["range"]: b for b in stats["by_confidence"]}
        assert bins["[0.0, 0.25]"]["n"] == 0
        assert bins["[0.25, 0.5]"]["n"] == 1
        assert bins["[0.5, 0.75]"]["n"] == 1
        assert bins["[0.75, 1.0]"]["n"] == 2  # 0.8 + 1.0（末箱含 1.0）
        assert stats["by_decision"]["TRADE"] == {"n": 2, "hit_rate": 1.0}
        assert stats["by_decision"]["WATCH"] == {"n": 2, "hit_rate": 0.5}

    def test_calibrate_empty(self):
        stats = review._calibrate([])
        assert stats["n"] == 0
        assert stats["hit_rate"] is None
        assert all(b["n"] == 0 for b in stats["by_confidence"])


# ── review_past_decisions（扫描） ────────────────────────


def _mk_run(reports: Path, name: str, run_ts_iso: str, results: list[dict]) -> None:
    d = reports / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(
        json.dumps(
            {
                "meta": {
                    "mode": "live",
                    "tokens": [r.get("symbol") for r in results],
                    "screening": {"mode": "auto"},
                    "run_ts": run_ts_iso,
                    "node_order": [],
                    "llm_calls": {},
                },
                "results": results,
            }
        ),
        encoding="utf-8",
    )


_EXPIRED_TS = (_ANCHOR + timedelta(hours=12)).isoformat()  # 8 天前（相对 _NOW）
_PENDING_TS = (_NOW - timedelta(days=3)).isoformat()


@pytest.fixture()
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return Path("reports")


class TestReviewPastDecisions:
    def test_only_expired_reviewed(self, reports_dir, monkeypatch):
        monkeypatch.setattr(review.binance, "fetch_klines", lambda *a, **k: _STD_KLINES)
        _mk_run(reports_dir, "old", _EXPIRED_TS, [
            {"symbol": "REUSDT", "decision": "TRADE", "direction": "long",
             "confidence": 0.55},
        ])
        _mk_run(reports_dir, "recent", _PENDING_TS, [
            {"symbol": "BTCUSDT", "decision": "TRADE", "direction": "short",
             "confidence": 0.7},
        ])
        out = review.review_past_decisions(now=_NOW)
        assert out["expired_runs"] == 1
        assert out["pending_runs"] == 1
        assert len(out["new_records"]) == 1
        rec = out["new_records"][0]
        assert rec["symbol"] == "REUSDT"
        assert rec["base_price"] == 100
        assert rec["ret_1d"] == 10.0
        assert rec["ret_7d"] == 30.0
        assert rec["hit_7d"] is True  # long +30%
        assert rec["status"] == "OK"

    def test_pass_and_directionless_skipped(self, reports_dir, monkeypatch):
        monkeypatch.setattr(review.binance, "fetch_klines", lambda *a, **k: _STD_KLINES)
        _mk_run(reports_dir, "old", _EXPIRED_TS, [
            {"symbol": "XRPUSDT", "decision": "PASS", "direction": None,
             "confidence": 0.9},
            {"symbol": "UNIUSDT", "decision": "WATCH", "direction": None,
             "confidence": 0.4},
            {"symbol": "SOLUSDT", "decision": "TRADE", "direction": "short",
             "confidence": 0.65},
        ])
        out = review.review_past_decisions(now=_NOW)
        syms = [r["symbol"] for r in out["new_records"]]
        assert syms == ["SOLUSDT"]
        assert out["new_records"][0]["hit_7d"] is False  # short +30% 未命中

    def test_bare_symbol_pair_completed(self, reports_dir, monkeypatch):
        seen = []

        def fake_klines(symbol, **kwargs):
            seen.append(symbol)
            return _STD_KLINES

        monkeypatch.setattr(review.binance, "fetch_klines", fake_klines)
        _mk_run(reports_dir, "old", _EXPIRED_TS, [
            {"symbol": "BTC", "decision": "TRADE", "direction": "long",
             "confidence": 0.6},
        ])
        review.review_past_decisions(now=_NOW)
        assert seen == ["BTCUSDT"]  # 裸名补 USDT

    def test_klines_none_unavailable_then_retry(self, reports_dir, monkeypatch):
        """klines 失败 → UNAVAILABLE 且 run 不标记；下次重试成功补上。"""
        calls = {"n": 0}

        def flaky(symbol, **kwargs):
            calls["n"] += 1
            return None if calls["n"] == 1 else _STD_KLINES

        monkeypatch.setattr(review.binance, "fetch_klines", flaky)
        _mk_run(reports_dir, "old", _EXPIRED_TS, [
            {"symbol": "REUSDT", "decision": "WATCH", "direction": "long",
             "confidence": 0.55},
        ])
        out1 = review.review_past_decisions(now=_NOW)
        assert out1["unavailable_records"][0]["status"] == "UNAVAILABLE"
        assert out1["unavailable_records"][0]["hit_7d"] is None
        assert out1["new_records"] == []
        assert out1["records"] == []  # 不进累积池

        out2 = review.review_past_decisions(now=_NOW)
        assert out2["expired_runs"] == 1  # run 未标记，重新回看
        rec = out2["new_records"][0]
        assert rec["status"] == "OK"
        assert rec["hit_7d"] is True
        assert len(out2["records"]) == 1  # 累积池不含 UNAVAILABLE 那次

    def test_latest_dir_excluded(self, reports_dir, monkeypatch):
        monkeypatch.setattr(review.binance, "fetch_klines", lambda *a, **k: _STD_KLINES)
        results = [
            {"symbol": "REUSDT", "decision": "TRADE", "direction": "long",
             "confidence": 0.55},
        ]
        _mk_run(reports_dir, "20260813T000000Z000000", _EXPIRED_TS, results)
        _mk_run(reports_dir, "latest", _EXPIRED_TS, results)  # 复制品
        out = review.review_past_decisions(now=_NOW)
        assert out["expired_runs"] == 1
        assert len(out["records"]) == 1

    def test_dedup_across_calls(self, reports_dir, monkeypatch):
        calls = {"n": 0}

        def counted(symbol, **kwargs):
            calls["n"] += 1
            return _STD_KLINES

        monkeypatch.setattr(review.binance, "fetch_klines", counted)
        _mk_run(reports_dir, "old", _EXPIRED_TS, [
            {"symbol": "REUSDT", "decision": "TRADE", "direction": "long",
             "confidence": 0.55},
        ])
        first = review.review_past_decisions(now=_NOW)
        second = review.review_past_decisions(now=_NOW)
        assert calls["n"] == 1  # 只拉一次 klines
        assert second["new_records"] == []
        assert second["records"] == first["records"]
        assert second["expired_runs"] == 0

    def test_corrupted_log_tolerated(self, reports_dir, monkeypatch):
        monkeypatch.setattr(review.binance, "fetch_klines", lambda *a, **k: _STD_KLINES)
        _mk_run(reports_dir, "old", _EXPIRED_TS, [
            {"symbol": "REUSDT", "decision": "TRADE", "direction": "long",
             "confidence": 0.55},
        ])
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "review_log.json").write_text("{broken json", encoding="utf-8")
        out = review.review_past_decisions(now=_NOW)
        assert len(out["records"]) == 1  # 容错归零后重扫

    def test_mock_mode_run_excluded(self, reports_dir, monkeypatch):
        """mock 决策非真实判断，不进评估回路（防污染校准统计）。"""
        monkeypatch.setattr(review.binance, "fetch_klines", lambda *a, **k: _STD_KLINES)
        _mk_run(reports_dir, "mockrun", _EXPIRED_TS, [
            {"symbol": "BTCUSDT", "decision": "TRADE", "direction": "long",
             "confidence": 0.9},
        ])
        (reports_dir / "mockrun" / "run.json").write_text(
            (reports_dir / "mockrun" / "run.json")
            .read_text(encoding="utf-8")
            .replace('"mode": "live"', '"mode": "mock"'),
            encoding="utf-8",
        )
        out = review.review_past_decisions(now=_NOW)
        assert out["scanned_runs"] == 0  # mock run 不计入扫描
        assert out["records"] == []

    def test_accumulates_across_runs(self, reports_dir, monkeypatch):
        monkeypatch.setattr(review.binance, "fetch_klines", lambda *a, **k: _STD_KLINES)
        _mk_run(reports_dir, "run1", _EXPIRED_TS, [
            {"symbol": "REUSDT", "decision": "TRADE", "direction": "long",
             "confidence": 0.55},
        ])
        _mk_run(reports_dir, "run2", _EXPIRED_TS, [
            {"symbol": "GRAMUSDT", "decision": "WATCH", "direction": "short",
             "confidence": 0.35},
        ])
        out = review.review_past_decisions(now=_NOW)
        assert out["expired_runs"] == 2
        assert len(out["records"]) == 2
        assert out["stats"]["n"] == 2
        assert out["stats"]["hit_rate"] == 0.5  # long 命中 / short 未命中
        log = json.loads((reports_dir / "review_log.json").read_text(encoding="utf-8"))
        assert sorted(log["reviewed"]) == ["run1", "run2"]
        assert len(log["records"]) == 2


# ── mock_klines 单位修复锁定 ────────────────────────────


def test_mock_klines_open_time_ms_and_daily_gap():
    """open_time 毫秒级（与真实 fetch_klines 同构）+ 日线间隔正确。"""
    rows = mock_ds.mock_klines("BTCUSDT", "1d", 10)
    assert all(r["open_time"] > 1e12 for r in rows)  # 毫秒量级（秒级约 1.7e9）
    times = [r["open_time"] for r in rows]
    assert max(times) - min(times) == 9 * _DAY_MS


# ── build_report 接线 ───────────────────────────────────


def _minimal_state() -> dict:
    return {
        "tokens": ["REUSDT"],
        "results": [
            {"symbol": "REUSDT", "decision": "WATCH", "direction": "long",
             "confidence": 0.55}
        ],
        "facts": {},
        "challenges": {},
        "signals": {},
    }


def test_build_report_includes_review_in_run_and_overview(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    fixed = {
        "as_of": _NOW.isoformat(),
        "expired_runs": 1,
        "pending_runs": 0,
        "new_records": [
            {"symbol": "REUSDT", "decision": "TRADE", "direction": "long",
             "confidence": 0.55, "ret_1d": 10.0, "ret_7d": 30.0,
             "hit_7d": True, "status": "OK"}
        ],
        "records": [
            {"symbol": "REUSDT", "decision": "TRADE", "direction": "long",
             "confidence": 0.55, "hit_7d": True}
        ],
        "stats": {"n": 1, "hit_rate": 1.0, "by_confidence": [], "by_decision": {}},
    }
    monkeypatch.setattr(report_mod.review_mod, "review_past_decisions", lambda: fixed)
    run_dir, _ = report_mod.build_report(_minimal_state(), {"screening": None})

    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run["decision_review"]["stats"]["hit_rate"] == 1.0
    md = (run_dir / "overview.md").read_text(encoding="utf-8")
    assert "## 决策复盘" in md
    assert "命中率 1.0" in md
    assert "REUSDT" in md


def test_build_report_review_empty_placeholder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        report_mod.review_mod, "review_past_decisions", lambda: {"records": []}
    )
    run_dir, _ = report_mod.build_report(_minimal_state(), {})
    md = (run_dir / "overview.md").read_text(encoding="utf-8")
    assert "## 决策复盘" in md
    assert "无到期决策可回看" in md
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run["decision_review"]["records"] == []


def test_build_report_review_exception_recorded_not_fatal(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    def boom():
        raise RuntimeError("klines 全挂")

    monkeypatch.setattr(report_mod.review_mod, "review_past_decisions", boom)
    meta: dict = {}
    run_dir, _ = report_mod.build_report(_minimal_state(), meta)
    assert "review_error" in meta
    assert "klines 全挂" in meta["review_error"]
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert "decision_review" not in run  # 失败不写节点
    md = (run_dir / "overview.md").read_text(encoding="utf-8")
    assert "## 决策复盘" in md  # 空节占位，报告仍生成
