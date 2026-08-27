"""「信号 vs 价格」回看评估器（lookback）：无前视、可复现、读数不越界。

验收映射：run 历史加载（跳 latest/非 live/损坏）/ 同日去重 / 前视窗口成熟度 /
成对观测采集（UNKNOWN 与分类信号跳过、超额口径）/ Spearman（平局、零方差）/
三等分差 / 退役字段不进表 / 渲染含读数边界警告。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from strategy_research import lookback
from strategy_research.report import _SNAPSHOT_KEYS

DAY = 86_400_000
RUN_TS = "2026-08-10T12:00:00+00:00"
_RUN_DT = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
_RUN_MS = int(_RUN_DT.timestamp() * 1000)
# 基准日 = run 日前一根日线（决策时最近已收盘）
_BASE_OPEN = int(datetime(2026, 8, 9, tzinfo=timezone.utc).timestamp() * 1000)
_NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000)


def _klines(closes: dict[int, float]) -> list[dict]:
    """{相对基准日的天数: 收盘价} → 升序日线（open_time 为日界毫秒）。"""
    return [
        {"open_time": _BASE_OPEN + offset * DAY, "close_price": price}
        for offset, price in sorted(closes.items())
    ]


def _fetch_factory(series: dict[str, dict[int, float]]):
    """按 symbol 注入日线；未列出的 symbol 返回 None（拉取失败同构）。"""

    def fetch(symbol: str, interval: str = "1d", limit: int = 365) -> list[dict] | None:
        assert interval == "1d"
        assert limit >= 1  # 窗口必须回盖到基准日
        rows = series.get(symbol)
        return None if rows is None else _klines(rows)

    return fetch


def _write_run(
    root,
    name: str,
    run_ts: str,
    signals: dict,
    mode: str = "live",
) -> None:
    run_dir = root / name
    run_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "meta": {"run_ts": run_ts, "mode": mode, "tokens": sorted(signals)},
        "signals": signals,
    }
    (run_dir / "run.json").write_text(json.dumps(doc), encoding="utf-8")


# ── 历史加载 ───────────────────────────────────────────────


def test_load_runs_skips_latest_nonlive_and_broken(tmp_path) -> None:
    _write_run(tmp_path, "20260810T120000Z", RUN_TS, {"ABCUSDT": {"momentum": 1.0}})
    _write_run(
        tmp_path,
        "20260811T120000Z",
        "2026-08-11T12:00:00+00:00",
        {"ABCUSDT": {"momentum": 2.0}},
        mode="mock",
    )
    (tmp_path / "20260812T120000Z").mkdir()
    (tmp_path / "20260812T120000Z" / "run.json").write_text("{broken", encoding="utf-8")
    # latest/ 是软链目录：同一批 run 会被数第二遍
    latest = tmp_path / "latest"
    latest.mkdir()
    _write_run(latest, "_self", RUN_TS, {"ABCUSDT": {"momentum": 9.0}})

    runs, warns = lookback.load_runs(tmp_path)
    assert [r.run_ts for r in runs] == [RUN_TS]
    assert [list(r.signals) for r in runs] == [["ABCUSDT"]]
    assert len(warns) == 2
    assert any("非 live" in w for w in warns)
    assert any("读取失败" in w for w in warns)


def test_dedupe_per_day_keeps_first_run(tmp_path) -> None:
    """同日两跑（实测相隔 4 分钟）只留首跑：都计入等于把 1 个观测当 2 个。"""
    runs = [
        lookback.Run("a", "2026-08-25T13:25:25+00:00", {}),
        lookback.Run("b", "2026-08-25T13:29:06+00:00", {}),
        lookback.Run("c", "2026-08-27T04:07:30+00:00", {}),
    ]
    kept, dropped = lookback.dedupe_per_day(runs)
    assert [r.path for r in kept] == ["a", "c"]
    assert dropped == ["2026-08-25（b）"]


# ── 前视口径 ───────────────────────────────────────────────


def test_mature_boundary_is_the_forward_candle_close() -> None:
    """第 N 根前视日线收完（+1 日）才算成熟——未收完会让读数在一天内漂移。"""
    assert lookback._mature(_RUN_MS, 1, _ms(2026, 8, 11))  # 基准 8/9 + 2d
    assert not lookback._mature(_RUN_MS, 1, _ms(2026, 8, 11) - 1)
    assert lookback._mature(_RUN_MS, 3, _ms(2026, 8, 13))
    assert not lookback._mature(_RUN_MS, 3, _ms(2026, 8, 12))


def test_collect_samples_no_lookahead_and_unknowns(tmp_path) -> None:
    """成对观测：数值信号才采；超额 = 减基准同期收益；无信号字段/无 K 线即弃。"""
    series = {
        "BTCUSDT": {0: 100.0, 1: 102.0, 3: 99.0},  # ret_1d=+2, ret_3d=-1
        "AAAUSDT": {0: 10.0, 1: 12.0, 3: 10.5},  # ret_1d=+20, ret_3d=+5
        "BBBUSDT": {0: 10.0, 1: 11.0},  # 3 日缺口 → ret_3d None
        # CCCUSDT 未列出 → 拉取失败
    }
    run = lookback.Run(
        "r",
        RUN_TS,
        {
            "AAAUSDT": {"momentum": 5.0, "quadrant": "III", "rv_7d": None},
            "BBBUSDT": {"momentum": -1.0, "turnover": True},
            "CCCUSDT": {"momentum": 2.0},
        },
    )
    samples, notes = lookback.collect_samples(
        [run], horizons=(1, 3), fetch=_fetch_factory(series), now=_NOW
    )
    keyed = {(s.symbol, s.key, s.horizon): s for s in samples}
    # 分类（quadrant）、None、bool 一律不采
    assert set(keyed) == {
        ("AAAUSDT", "momentum", 1),
        ("AAAUSDT", "momentum", 3),
        ("BBBUSDT", "momentum", 1),
    }
    assert keyed[("AAAUSDT", "momentum", 1)].ret == pytest.approx(20.0)
    assert keyed[("AAAUSDT", "momentum", 1)].excess == pytest.approx(18.0)
    assert keyed[("AAAUSDT", "momentum", 3)].ret == pytest.approx(5.0)
    assert keyed[("AAAUSDT", "momentum", 3)].excess == pytest.approx(6.0)
    assert any("CCCUSDT" in n and "前视收益不可得" in n for n in notes)

    # 3 日缺口：BBBUSDT 只有 ret_1d（上一断言已覆盖），基准缺失时超额留空
    no_bench = {k: v for k, v in series.items() if k != "BTCUSDT"}
    samples2, notes2 = lookback.collect_samples(
        [run], horizons=(1,), fetch=_fetch_factory(no_bench), now=_NOW
    )
    assert samples2 and all(s.excess is None for s in samples2)
    assert any("基准 BTC 前视收益不可得" in n for n in notes2)


def test_collect_samples_drops_immature_horizon() -> None:
    """窗口未收盘 → 整档跳过（同日两次回看得到同一份数字的前提）。"""
    series = {
        "BTCUSDT": {0: 100.0, 1: 102.0},
        "AAAUSDT": {0: 10.0, 1: 12.0},
    }
    run = lookback.Run("r", RUN_TS, {"AAAUSDT": {"momentum": 5.0}})
    now = datetime(2026, 8, 10, 23, 0, tzinfo=timezone.utc)  # 8/10 那根还没收
    samples, notes = lookback.collect_samples(
        [run], horizons=(1,), fetch=_fetch_factory(series), now=now
    )
    assert samples == []
    assert any("尚未收盘" in n for n in notes)


def test_forward_limit_covers_the_base_candle() -> None:
    """K 线只向过去取：limit 必须回盖基准日，否则 closed_daily_returns 给空。"""
    seen: list[int] = []

    def fetch(symbol: str, interval: str = "1d", limit: int = 0) -> list[dict]:
        seen.append(limit)
        return _klines({0: 10.0, 1: 11.0})

    run = lookback.Run("r", RUN_TS, {"AAAUSDT": {"momentum": 1.0}})
    lookback.collect_samples(
        [run], horizons=(1,), fetch=fetch, now=datetime(2026, 8, 20, tzinfo=timezone.utc)
    )
    assert seen and min(seen) >= 12  # 距今天数 9 + 窗口 1 + 冗余 2 ≥ 回盖基准日


# ── 统计 ───────────────────────────────────────────────────


def test_spearman_monotonic_ties_and_degenerate() -> None:
    assert lookback.spearman([1.0, 2.0, 3.0], [10.0, 20.0, 30.0]) == pytest.approx(1.0)
    assert lookback.spearman([1.0, 2.0, 3.0], [30.0, 20.0, 10.0]) == pytest.approx(-1.0)
    # 平局用平均秩：[1,1,2] 秩 = [1.5,1.5,3]
    assert lookback.spearman([1.0, 1.0, 2.0], [5.0, 5.0, 9.0]) == pytest.approx(1.0)
    assert lookback.spearman([1.0, 2.0], [1.0, 2.0]) is None  # n<3
    assert lookback.spearman([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None  # 零方差
    assert lookback.spearman([1.0, 2.0, 3.0], [1.0, 2.0]) is None  # 长度不等


def test_group_spread_terciles_and_minimum_n() -> None:
    pairs = [(float(i), float(i * 2)) for i in range(9)]
    low, mid, high = lookback.group_spread(pairs)
    assert (low, mid, high) == pytest.approx((2.0, 8.0, 14.0))
    assert lookback.group_spread(pairs[:8]) is None  # n<9 三等分没有意义
    assert lookback.group_spread([]) is None


def _sample(i: int, value: float, ret: float, key: str = "momentum") -> lookback.Sample:
    return lookback.Sample(
        day=f"2026-08-{10 + i % 3}",
        symbol=f"S{i}USDT",
        horizon=1,
        key=key,
        value=value,
        ret=ret,
        excess=ret - 1.0,
    )


# ── 读数装配 ───────────────────────────────────────────────


def test_evaluate_skips_retired_keys_and_flags_small_n() -> None:
    samples = [_sample(i, float(i), float(-i)) for i in range(4)]
    samples += [
        _sample(i, float(i), float(i), key="funding_z") for i in range(4)
    ]  # 09 票已退役字段
    rows = lookback.evaluate(samples, keys={"momentum"})
    assert [r.key for r in rows] == ["momentum"]
    row = rows[0]
    assert row.n == 4
    assert row.rho == pytest.approx(-1.0)
    assert row.rho_excess == pytest.approx(-1.0)
    assert row.spread_raw is None  # n<9 不分组
    assert "n<6" in row.note

    # keys=None：全部字段照评（单测里的手工键）
    assert {r.key for r in lookback.evaluate(samples)} == {"momentum", "funding_z"}


def test_evaluate_large_sample_reports_spreads() -> None:
    samples = [_sample(i, float(i % 5), float((i % 5) * 2.0)) for i in range(15)]
    (row,) = lookback.evaluate(samples, keys={"momentum"})
    assert row.note == ""
    assert row.n == 15
    # 三等分（每组 5 个）：低档 [0,0,0,1,1]→0.8 / 中档 4.0 / 高档 [3,3,4,4,4]→7.2
    assert row.spread_raw == pytest.approx(6.4)
    assert row.spread_excess == pytest.approx(6.4)  # 减常差不改变组内均值差


# ── 渲染与 CLI ─────────────────────────────────────────────


def test_render_carries_the_readout_caveats() -> None:
    samples = [_sample(i, float(i), float(i)) for i in range(4)]
    rows = lookback.evaluate(samples, keys={"momentum"})
    runs = [lookback.Run("r", RUN_TS, {"AAAUSDT": {"momentum": 1.0}})]
    md = lookback.render(rows, runs, ["2026-08-25（b）"], ["funding_z"], samples)
    assert "# 信号 vs 价格 回看" in md
    assert "无前视偏差" in md
    assert "1. 选币自选择" in md
    assert "2. 窗口重叠" in md
    assert "3. 多重比较" in md
    assert "4. 本表只描述历史相关性" in md
    assert "5. 信号集 schema 漂移：1 个历史字段已退役、未评估（funding_z）" in md
    assert "同日多跑已去重 1 次" in md
    assert "成对观测：4 条，来自 3 个 run 日 × 4 个标的" in md
    assert "| momentum | 1 | 4 |" in md


def test_render_notes_schema_stable_and_table_last() -> None:
    rows = [
        lookback.Row(
            key=k, horizon=1, n=12, rho=0.1, rho_excess=0.2,
            spread_raw=1.0, spread_excess=1.5, note="",
        )
        for k in _SNAPSHOT_KEYS
    ]
    md = lookback.render(rows, [], [], [], [])
    assert "历史信号字段全部落在当前信号集内" in md
    assert "个字段无读数" not in md
    # 警告必须在表格之前：表格是最后一块
    assert md.index("4. 本表只描述") < md.index("| 信号 | 窗口(d)")
    assert md.rstrip().endswith("|")


def test_main_end_to_end(tmp_path, capsys) -> None:
    series = {
        "BTCUSDT": {0: 100.0, 1: 102.0},
        "AAAUSDT": {0: 10.0, 1: 15.0},
        "BBBUSDT": {0: 10.0, 1: 8.0},
    }
    _write_run(
        tmp_path,
        "20260810T120000Z",
        RUN_TS,
        {"AAAUSDT": {"momentum": 9.0}, "BBBUSDT": {"momentum": 1.0}},
    )
    stats = lookback.main(
        ["--root", str(tmp_path), "--horizons", "1"],
        fetch=_fetch_factory(series),
        now=_NOW,
    )
    out = capsys.readouterr().out
    assert stats["days"] == 1
    assert stats["samples"] == 2
    assert stats["rows"] == 1
    assert "| momentum | 1 | 2 |" in out
    assert "n<6：读数无统计意义" in out


def test_main_json_mode_lists_retired_keys(tmp_path, capsys) -> None:
    _write_run(
        tmp_path,
        "20260810T120000Z",
        RUN_TS,
        {"AAAUSDT": {"momentum": 9.0, "funding_z": 3.0}},
    )
    series = {"BTCUSDT": {0: 100.0, 1: 102.0}, "AAAUSDT": {0: 10.0, 1: 15.0}}
    lookback.main(
        ["--root", str(tmp_path), "--horizons", "1", "--json"],
        fetch=_fetch_factory(series),
        now=_NOW,
    )
    out = capsys.readouterr().out
    payload = json.loads(out[out.index("{") :])
    assert payload["retired_keys"] == ["funding_z"]
    assert [r["key"] for r in payload["rows"]] == ["momentum"]


def test_module_does_not_write_artifacts(tmp_path) -> None:
    """只读工具：跑完不新增任何文件（不进管线、不落盘）。"""
    _write_run(tmp_path, "20260810T120000Z", RUN_TS, {"AAAUSDT": {"momentum": 1.0}})
    before = sorted(p.name for p in tmp_path.rglob("*"))
    series = {"BTCUSDT": {0: 100.0, 1: 102.0}, "AAAUSDT": {0: 10.0, 1: 15.0}}
    lookback.main(
        ["--root", str(tmp_path)],
        fetch=_fetch_factory(series),
        now=_NOW,
    )
    assert sorted(p.name for p in tmp_path.rglob("*")) == before
