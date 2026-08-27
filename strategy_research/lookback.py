"""lookback — 「信号 vs 价格」回看评估器（离线工具：图外、只读、零 LLM）。

系统只陈列证据、不产方向结论（ADR 0001），所以本模块不进管线、不写任何工件，
只回答一个描述性问题：历史 ``run.json`` 里的确定性信号，与信号做出后 N 日的
价格表现，有没有秩相关？读数只说明「过去的这组数字一起怎么动」，不构成任何
方向、仓位或置信度含义。

口径：

- 基准价与前视收益一律走 :func:`closed_daily_returns`——基准 = 决策时最近
  已收盘日线，前视只用基准之后的 K 线，无前视偏差；快照不记价格，故无需
  改快照 schema。
- 只读 ``mode=live`` 的 run（mock 的价格与时间不具真实性）。
- 同日多跑去重：同一天两次运行的信号与未来收益几乎相同，都计入等于把 1 个
  观测当成 2 个（实测仓库里 8/25 与 8/27 各有一对相隔数分钟的运行）。
- 信号值为 None 或分类字符串（quadrant/label 等）→ 该字段整列不参与相关
  （UNKNOWN 纪律，不编码、不填充）。
- 超额收益 = 标的收益 − 同期 BTC 收益（同基准日规则），用于剥掉市场整体
  波动这一混淆项；BTC 缺失时该样本只计原始收益。
- 窗口必须已收盘：``run_ts + N`` 日尚未走完时该 (run, horizon) 不采——否则
  ``ret_1d`` 会指向正在形成的日 K，相隔几分钟的两次运行给出两个不同的 ρ。
- 只评估当前信号集（``_SNAPSHOT_KEYS``）：历史 run 里的退役字段（如
  ``funding_z``）不静默丢弃，单独列在「schema 漂移」警告里供核对。
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from strategy_research.datasources import binance_futures
from strategy_research.datasources.binance import closed_daily_returns, pair_symbol
from strategy_research.report import _SNAPSHOT_KEYS

#: 前视窗口（自然日）：1 日 = 决策后首个已收盘日线
DEFAULT_HORIZONS = (1, 3, 7, 14)
#: 市场参照：信号层的 β 也以 BTC 为基准，口径保持一致
BENCHMARK = "BTC"
#: 可报告相关性所需的最低成对观测数（低于此值仍计算，但标注样本不足）
MIN_CORR_N = 6
#: 三等分分组收益的最低样本量（每组至少 3 个观测）
MIN_GROUP_N = 9

KlineFetcher = Callable[..., "list[dict] | None"]


@dataclass(frozen=True)
class Run:
    """一次历史运行：run_ts + 当批信号快照（``run.json`` 的 ``signals`` 投影）。"""

    path: str
    run_ts: str
    signals: dict[str, dict]

    @property
    def day(self) -> str:
        """UTC 日期键（同日去重用）。"""
        return self.run_ts[:10]


@dataclass(frozen=True)
class Sample:
    """一个成对观测：某 run 日、某标的、某信号字段、某窗口 → 前视收益。"""

    day: str
    symbol: str
    horizon: int
    key: str
    value: float
    ret: float
    excess: float | None


@dataclass(frozen=True)
class Row:
    """一个 (信号字段, 窗口) 的读数。``note`` 说明该格为什么是空的。"""

    key: str
    horizon: int
    n: int
    rho: float | None
    rho_excess: float | None
    spread_raw: float | None
    spread_excess: float | None
    note: str


def load_runs(root: Path | str = Path("reports")) -> tuple[list[Run], list[str]]:
    """读 ``reports/*/run.json`` 的历史信号快照（只读）。

    跳过 ``latest/``（软链目录，同一批 run 会被数两遍）；非 live、损坏、无
    signals 的目录逐个跳过并留警告——工具没有资格静默少读数据。
    """
    out: list[Run] = []
    warns: list[str] = []
    for path in sorted(Path(root).glob("*/run.json")):
        if path.parent.name == "latest":
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            warns.append(f"{path}: 读取失败（{type(exc).__name__}），跳过")
            continue
        meta = doc.get("meta") or {}
        signals = doc.get("signals") or {}
        run_ts = meta.get("run_ts")
        if meta.get("mode") != "live":
            warns.append(f"{path}: mode={meta.get('mode')!r} 非 live，跳过")
            continue
        if not isinstance(run_ts, str) or not isinstance(signals, dict) or not signals:
            warns.append(f"{path}: run_ts/signals 缺失，跳过")
            continue
        out.append(Run(str(path), run_ts, signals))
    out.sort(key=lambda r: r.run_ts)
    return out, warns


def dedupe_per_day(runs: list[Run]) -> tuple[list[Run], list[str]]:
    """每 UTC 日只保留首跑（入参须已按 run_ts 升序）。"""
    kept: list[Run] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for run in runs:
        if run.day in seen:
            dropped.append(f"{run.day}（{run.path}）")
            continue
        seen.add(run.day)
        kept.append(run)
    return kept, dropped


def _run_ts_ms(run_ts: str) -> int | None:
    """ISO run_ts → 毫秒（naive 视为 UTC；非法 → None，调用方丢弃该 run）。"""
    try:
        parsed = datetime.fromisoformat(run_ts)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


#: 自然日毫秒数（日线口径，与 datasources.binance._DAY_MS 同值）
_DAY_MS = 86_400_000


def _mature(run_ts_ms: int, horizon: int, now_ms: int) -> bool:
    """前视窗口是否已收盘：基准日 = run 日的前一根日线，第 N 根须在 now 前收完。

    未收完的窗口会让同一次运行的读数在一天内不断漂移——实测相隔 6 分钟的两次
    回看，同一字段的 ρ 从 -0.476 变到 -0.381（当日那根日线就是「最新价」）。
    评估器的数字必须可复现，所以未成熟窗口直接不计入，而不是标注后照算。
    """
    base_open = run_ts_ms - (run_ts_ms % _DAY_MS) - _DAY_MS
    return base_open + (horizon + 1) * _DAY_MS <= now_ms


def _forward(
    run: Run,
    symbols: list[str],
    horizons: tuple[int, ...],
    fetch: KlineFetcher,
    now_ms: int,
) -> dict[str, dict]:
    """symbol → ``closed_daily_returns`` 结果（含 ``base_price`` 与 ``ret_Nd``）。

    K 线窗口只向过去取，故 limit 必须回盖到基准日：距今天数 + 最大窗口 + 2 根
    冗余。拉取失败或窗口不足 → 该 symbol 无结果（样本被丢弃，不填充）。
    """
    ts_ms = _run_ts_ms(run.run_ts)
    if ts_ms is None:
        return {}
    age_days = max(0, (now_ms - ts_ms) // _DAY_MS)
    limit = int(age_days + max(horizons) + 2)
    days = tuple(sorted(set(horizons)))
    out: dict[str, dict] = {}
    for symbol in symbols:
        rows = fetch(pair_symbol(symbol), interval="1d", limit=limit)
        result = closed_daily_returns(rows, ts_ms, days=days)
        if result:
            out[symbol] = result
    return out


def collect_samples(
    runs: list[Run],
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    fetch: KlineFetcher = binance_futures.fetch_fapi_klines,
    now: datetime | None = None,
) -> tuple[list[Sample], list[str]]:
    """展平为成对观测：run × 标的 × 信号字段 × 窗口。

    分类/缺失信号跳过；前视收益不可得（新上线、K 线缺口、窗口不足）→ 丢弃该
    标的的观测并留警告，不按 0 计入；未收盘窗口整档跳过（见 :func:`_mature`）。
    """
    now_ms = int((now or datetime.now(timezone.utc)).timestamp() * 1000)
    samples: list[Sample] = []
    notes: list[str] = []
    for run in runs:
        ts_ms = _run_ts_ms(run.run_ts)
        if ts_ms is None:
            notes.append(f"{run.path}: run_ts 非法，整个 run 跳过")
            continue
        mature = tuple(h for h in horizons if _mature(ts_ms, h, now_ms))
        if not mature:
            notes.append(f"{run.day}: 所有前视窗口尚未收盘，本次 run 无观测")
            continue
        if len(mature) < len(horizons):
            dropped_h = "、".join(
                f"{h}d" for h in horizons if h not in set(mature)
            )
            notes.append(f"{run.day}: 窗口 {dropped_h} 尚未收盘，本次 run 不计入")
        symbols = sorted(run.signals)
        outcomes = _forward(run, [*symbols, BENCHMARK], mature, fetch, now_ms)
        if BENCHMARK not in outcomes:
            notes.append(f"{run.day}: 基准 {BENCHMARK} 前视收益不可得，超额列留空")
        bench = outcomes.get(BENCHMARK) or {}
        for symbol in symbols:
            result = outcomes.get(symbol)
            if not result:
                notes.append(f"{run.day} {symbol}: 前视收益不可得，该标的样本丢弃")
                continue
            for key, value in (run.signals[symbol] or {}).items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                for horizon in mature:
                    ret = result.get(f"ret_{horizon}d")
                    if ret is None:
                        continue
                    base_ret = bench.get(f"ret_{horizon}d")
                    samples.append(
                        Sample(
                            day=run.day,
                            symbol=symbol,
                            horizon=horizon,
                            key=key,
                            value=float(value),
                            ret=float(ret),
                            excess=(
                                float(ret) - float(base_ret)
                                if base_ret is not None
                                else None
                            ),
                        )
                    )
    return samples, notes


def _ranks(values: list[float]) -> list[float]:
    """升序平均秩（平局取均值，1..n）。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman 秩相关（平局已用平均秩处理）；n<3 或任一序列零方差 → None。"""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    dx = [v - mx for v in rx]
    dy = [v - my for v in ry]
    denom = (sum(a * a for a in dx) * sum(b * b for b in dy)) ** 0.5
    if denom == 0:
        return None
    return sum(a * b for a, b in zip(dx, dy, strict=True)) / denom


def group_spread(
    pairs: list[tuple[float, float]], groups: int = 3
) -> tuple[float, float, float] | None:
    """按信号值升序三等分 → (低档均值, 中档, 高档)；样本不足 → None。"""
    if len(pairs) < MIN_GROUP_N:
        return None
    ordered = sorted(pairs, key=lambda p: p[0])
    cut = len(ordered) // groups
    low = statistics.fmean(v for _, v in ordered[:cut])
    mid = statistics.fmean(v for _, v in ordered[cut : 2 * cut])
    high = statistics.fmean(v for _, v in ordered[2 * cut :])
    return low, mid, high


def evaluate(
    samples: list[Sample], keys: set[str] | None = None
) -> list[Row]:
    """每个 (信号字段, 窗口) 一行读数（按窗口、字段名排序）。

    ``keys`` 给出当前信号集时，历史里已退役的字段（旧 schema 的 funding_z /
    alpha_7d 等）不进表——回看的对象是「系统现在还在产的这些数字」；被跳过的
    字段名由调用方显式报告，不静默少读。
    """
    buckets: dict[tuple[int, str], list[Sample]] = {}
    for sample in samples:
        if keys is not None and sample.key not in keys:
            continue
        buckets.setdefault((sample.horizon, sample.key), []).append(sample)
    rows: list[Row] = []
    for (horizon, key), group in sorted(buckets.items()):
        values = [s.value for s in group]
        rho = spearman(values, [s.ret for s in group])
        exc = [s for s in group if s.excess is not None]
        rho_exc = spearman([s.value for s in exc], [s.excess for s in exc]) if exc else None
        raw_spread = group_spread(list(zip(values, [s.ret for s in group], strict=True)))
        exc_spread = (
            group_spread([(s.value, s.excess) for s in exc]) if len(exc) >= 3 else None
        )
        note = ""
        if len(group) < MIN_CORR_N:
            note = f"n<{MIN_CORR_N}：读数无统计意义，仅供该字段是否被采到"
        rows.append(
            Row(
                key=key,
                horizon=horizon,
                n=len(group),
                rho=rho,
                rho_excess=rho_exc,
                spread_raw=None if raw_spread is None else raw_spread[2] - raw_spread[0],
                spread_excess=(
                    None if exc_spread is None else exc_spread[2] - exc_spread[0]
                ),
                note=note,
            )
        )
    return rows


def _num(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def render(
    rows: list[Row],
    runs: list[Run],
    dropped: list[str],
    skipped_keys: list[str] | None = None,
    samples: list[Sample] | None = None,
) -> str:
    """markdown 读数（含读数边界警告——警告是本工具的主要产物之一）。"""
    days = sorted({r.day for r in runs})
    samples = samples or []
    horizons = sorted({r.horizon for r in rows})
    keys = {r.key for r in rows}
    obs_days = sorted({s.day for s in samples})
    symbols = sorted({s for r in runs for s in r.signals})
    obs_symbols = sorted({s.symbol for s in samples})
    span = f"{days[0] if days else '—'} → {days[-1] if days else '—'}"
    sample_line = (
        f"- 样本：{len(days)} 个 run 日（{span}）× {len(symbols)} 个标的；"
        f"同日多跑已去重 {len(dropped)} 次"
    )
    obs_line = (
        f"- 成对观测：{len(samples)} 条，来自 {len(obs_days)} 个 run 日 × "
        f"{len(obs_symbols)} 个标的（窗口未收盘或前视收益缺失者不计入）"
    )
    caliber_line = (
        "- 口径：基准 = 决策时最近已收盘日线；前视收益只取基准之后的日 K"
        "（无前视偏差）；超额 = 同期减 BTC 收益"
    )
    compare_line = (
        f"  3. 多重比较：同一批信号在 {len(horizons)} 个窗口 × "
        f"{len(keys)} 个字段上重复读取，"
        "|ρ| ≲ 0.3 在此样本量下属噪音范围。"
    )
    lines = [
        "# 信号 vs 价格 回看",
        "",
        sample_line,
        obs_line,
        caliber_line,
        "- 读数边界（缺一不可这样读）：",
        (
            "  1. 选币自选择：样本只含进过批的标的，读数是「候选池内相对」，"
            "不是全市场规律。"
        ),
        "  2. 窗口重叠：相邻 run 日的前视窗口互相重叠，ρ 不能按独立样本解读。",
        compare_line,
        "  4. 本表只描述历史相关性，不含方向结论、置信度与仓位含义。",
    ]
    if skipped_keys:
        lines.append(
            f"  5. 信号集 schema 漂移：{len(skipped_keys)} 个历史字段已退役、"
            f"未评估（{'、'.join(skipped_keys)}）。"
        )
    else:
        lines.append("  5. 历史信号字段全部落在当前信号集内（schema 未漂移）。")
    absent = sorted(set(_SNAPSHOT_KEYS) - keys)
    if absent:
        lines.append(
            f"  6. 另有 {len(absent)} 个当前字段无读数，不进表"
            "（分类标签本就不参与秩相关，新增字段则尚无历史）："
            f"{'、'.join(absent)}。"
        )
    lines += [
        "",
        "| 信号 | 窗口(d) | n | ρ | ρ(超额) | 三档差 | 三档差(超额) | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row.key} | {row.horizon} | {row.n} | {_num(row.rho)} | "
            f"{_num(row.rho_excess)} | {_pct(row.spread_raw)} | "
            f"{_pct(row.spread_excess)} | {row.note} |"
        )
    if not rows:
        lines.append("| — | — | 0 | — | — | — | — | 无可用成对观测 |")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m strategy_research.lookback",
        description="「信号 vs 价格」回看评估器（只读，不改任何工件）",
    )
    parser.add_argument("--root", default="reports", help="历史 run 目录（默认 reports）")
    parser.add_argument(
        "--horizons",
        default=",".join(str(h) for h in DEFAULT_HORIZONS),
        help="前视窗口（天），逗号分隔",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON 而非 markdown")
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    fetch: KlineFetcher = binance_futures.fetch_fapi_klines,
    now: datetime | None = None,
) -> dict[str, Any]:
    """CLI 入口：加载历史 → 采集成对观测 → 打印读数；返回统计供调用方断言。

    ``fetch``/``now`` 供测试注入（离线 + 可复现）；``now`` 默认取当前时间，
    只影响「哪些窗口已收盘」这一件事。
    """
    args = parse_args(argv)
    horizons = tuple(
        sorted({int(h) for h in str(args.horizons).split(",") if h.strip()})
    )
    runs, warns = load_runs(args.root)
    runs, dropped = dedupe_per_day(runs)
    samples, notes = collect_samples(runs, horizons, fetch=fetch, now=now)
    # 只评估当前信号集：历史 run 里的退役字段（funding_z / beta_7d 等）另行列出
    current = set(_SNAPSHOT_KEYS)
    retired = sorted({s.key for s in samples} - current)
    rows = evaluate(samples, keys=current)
    for line in [*warns, *notes]:
        print(f"[warn] {line}", flush=True)
    if args.json:
        print(
            json.dumps(
                {
                    "days": sorted({r.day for r in runs}),
                    "dropped_same_day": len(dropped),
                    "retired_keys": retired,
                    "samples": len(samples),
                    "rows": [asdict(r) for r in rows],
                    "warnings": [*warns, *notes],
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )
    else:
        print(render(rows, runs, dropped, retired, samples), flush=True)
    return {
        "days": len({r.day for r in runs}),
        "samples": len(samples),
        "rows": len(rows),
        "retired_keys": retired,
        "warnings": len(warns) + len(notes),
    }


if __name__ == "__main__":  # pragma: no cover
    main()
