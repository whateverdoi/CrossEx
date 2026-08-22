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


# ── _hit / calibrate（纯函数） ─────────────────────────


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
        stats = review.calibrate(records)
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
        stats = review.calibrate([])
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


# ── 校准基线渲染（13 票：prompt 侧消费同一 calibrate seam） ──


class TestCalibrationContext:
    def _records(self) -> list[dict]:
        return [
            {"hit_7d": True, "decision": "TRADE", "confidence": 0.8},
            {"hit_7d": False, "decision": "WATCH", "confidence": 0.4},
            {"hit_7d": None, "decision": "TRADE", "confidence": 0.6},  # 不计入
            {"hit_7d": True, "decision": "TRADE", "confidence": 0.9},
        ]

    def test_empty_pool_renders_empty(self):
        assert review.render_calibration_context([]) == ""

    def test_render_contains_stats(self):
        text = review.render_calibration_context(self._records())
        assert "累积方向判断 3 条" in text  # n=3（UNAVAILABLE 不计）
        assert "命中率" in text
        assert "TRADE：2 条" in text
        assert "WATCH：1 条" in text
        assert "置信度分箱" in text

    def test_load_records_tolerant(self, tmp_path):
        assert review.load_records(tmp_path) == []
        (tmp_path / "review_log.json").write_text(
            '{"reviewed": ["x"], "records": [{"hit_7d": true}]}', encoding="utf-8"
        )
        assert review.load_records(tmp_path) == [{"hit_7d": True}]

    def test_load_records_corrupt_falls_back_empty(self, tmp_path):
        (tmp_path / "review_log.json").write_text("not json", encoding="utf-8")
        assert review.load_records(tmp_path) == []


# ── 信号状态分桶（04 票：确定性信号层接受结果检验） ──


class TestCalibrateBySignal:
    def _rec(self, hit, quadrant=None, momentum=None, pct=None, oi=None):
        return {
            "hit_7d": hit,
            "decision": "TRADE",
            "confidence": 0.7,
            "signal_state": {
                "quadrant": quadrant,
                "momentum": momentum,
                "funding_pctile_90d": pct,
                "oi_price_divergence": oi,
            },
        }

    def test_buckets_by_quadrant_and_momentum(self):
        stats = review.calibrate(
            [
                self._rec(True, "III", 5.0),
                self._rec(False, "III", -2.0),
                self._rec(True, "II", 8.0),
                self._rec(True, None, 1.0),  # quadrant 缺失不计入 quadrant 桶
            ]
        )
        q = {b["value"]: b for b in stats["by_signal"]["quadrant"]}
        assert q["III"]["n"] == 2 and q["III"]["hit_rate"] == 0.5
        assert q["II"]["n"] == 1 and q["II"]["hit_rate"] == 1.0
        mom = {b["value"]: b for b in stats["by_signal"]["momentum"]}
        assert mom["positive"]["n"] == 3 and mom["positive"]["hit_rate"] == 1.0
        assert mom["negative"]["n"] == 1

    def test_pctile_bins_and_oi_labels(self):
        stats = review.calibrate(
            [
                self._rec(True, pct=90.0, oi="confirm_long"),
                self._rec(False, pct=10.0, oi="weak_short"),
                self._rec(True, pct=50.0, oi="confirm_long"),
            ]
        )
        pct = {b["value"]: b for b in stats["by_signal"]["funding_pctile"]}
        assert pct["extreme(≥80)"]["n"] == 1 and pct["extreme(≥80)"]["hit_rate"] == 1.0
        assert pct["mild(≤20)"]["n"] == 1
        assert pct["mid(21-79)"]["n"] == 1
        oi = {b["value"]: b for b in stats["by_signal"]["oi_divergence"]}
        assert oi["confirm_long"]["n"] == 2 and oi["confirm_long"]["hit_rate"] == 1.0
        assert oi["weak_short"]["n"] == 1

    def test_legacy_records_no_signal_state_empty_buckets(self):
        """旧记录无 signal_state → 空桶（不误伤，不崩）。"""
        stats = review.calibrate(
            [{"hit_7d": True, "decision": "TRADE", "confidence": 0.7}]
        )
        assert all(v == [] for v in stats["by_signal"].values())

    def test_signal_state_captured_in_records(self, tmp_path, monkeypatch):
        """回看记录捕获 run.json 里的 signal_state（run 装配断言）。"""
        run_dir = tmp_path / "20260101T000000Z000000"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(
            json.dumps(
                {
                    "meta": {
                        "run_ts": "2026-01-01T00:00:00+00:00",
                        "mode": "live",
                    },
                    "results": [
                        {
                            "symbol": "BTC",
                            "decision": "TRADE",
                            "direction": "long",
                            "confidence": 0.7,
                            "signal_state": {"quadrant": "III", "momentum": 5.0},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            review.binance, "fetch_klines", lambda *a, **k: _STD_KLINES
        )
        out = review.review_past_decisions(tmp_path, now=_NOW)
        rec = out["records"][0]
        assert rec["signal_state"] == {"quadrant": "III", "momentum": 5.0}


# ── 评估窗口分桶（05 票：决策声明评估窗口，评估记账对齐尺度） ──


class TestCalibrateByHorizon:
    def test_buckets_by_horizon(self):
        stats = review.calibrate(
            [
                {"hit_7d": True, "decision": "TRADE", "confidence": 0.7, "horizon": "short_term"},
                {"hit_7d": False, "decision": "TRADE", "confidence": 0.7, "horizon": "short_term"},
                {"hit_7d": True, "decision": "WATCH", "confidence": 0.5, "horizon": "trend"},
                {"hit_7d": True, "decision": "TRADE", "confidence": 0.6},  # 旧记录无 horizon
            ]
        )
        h = {b["value"]: b for b in stats["by_horizon"]}
        assert h["short_term"]["n"] == 2 and h["short_term"]["hit_rate"] == 0.5
        assert h["trend"]["n"] == 1 and h["trend"]["hit_rate"] == 1.0
        assert set(h) == {"short_term", "trend"}  # 空串不计入

    def test_horizon_captured_in_records(self, tmp_path, monkeypatch):
        """回看记录捕获 run.json 里的 horizon。"""
        run_dir = tmp_path / "20260102T000000Z000000"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(
            json.dumps(
                {
                    "meta": {"run_ts": "2026-01-02T00:00:00+00:00", "mode": "live"},
                    "results": [
                        {
                            "symbol": "BTC",
                            "decision": "TRADE",
                            "direction": "long",
                            "confidence": 0.7,
                            "horizon": "trend",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(review.binance, "fetch_klines", lambda *a, **k: _STD_KLINES)
        out = review.review_past_decisions(tmp_path, now=_NOW)
        assert out["records"][0]["horizon"] == "trend"
