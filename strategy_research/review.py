"""决策追踪与校准（12 票 + 13 票闭合回路）：回看历史决策的事后表现。

评估回路：每批运行扫描 ``reports/`` 历史 run.json，对到期（run_ts + 7d ≤ now）
的带方向决策（TRADE/WATCH）用日线 klines 计算相对基准价的 T+1d/T+7d 收益，
输出方向命中率与置信度分箱校准，供 overview「决策复盘」节（12 票）与
prompt 校准基线（13 票：:func:`render_calibration_context` 渲染，① 加载进 meta，
③-⑥ 摘要携带）消费。

纪律（与全架构一致）：
- 纯确定性计算，零 LLM 调用；klines 失败 → 该条 UNAVAILABLE，不中断批
- 基准价 = 决策时最近已收盘日线（open_time + 1d ≤ run_ts 的最后一根 close），
  无前视偏差；不回填历史 run 未落盘的即时价
- ``review_log.json`` 记录已回看 run 目录并累积 records（跨批去重）；
  klines 失败的 run 不标记，下次运行重试
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path

from strategy_research.datasources import binance

#: 回看窗口：run_ts + 7d ≤ now 才回看（保证 T+7d 已收盘）
_REVIEW_HORIZON_DAYS = 7

#: 置信度分箱边界（左闭右开；末箱 [0.75, 1.0] 含 1.0）
_CONF_BINS = (0.25, 0.5, 0.75)



def _parse_ts(raw: object) -> datetime | None:
    """ISO 时间解析（容错：空/坏值 → None）；缺时区按 UTC。"""
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hit(direction: str, ret: float | None) -> bool | None:
    """方向命中判定：long 命中 = ret > 0；short 命中 = ret < 0。"""
    if ret is None:
        return None
    return ret > 0 if direction == "long" else ret < 0


def calibrate(records: list[dict]) -> dict:
    """命中率 + 置信度四分箱 + TRADE/WATCH 分组（ret_7d 判定）。

    UNAVAILABLE（ret_7d 缺失）不计入任何统计。
    """

    def _rate(rows: list[dict]) -> float | None:
        return round(sum(r["hit_7d"] for r in rows) / len(rows), 3) if rows else None

    valid = [r for r in records if r.get("hit_7d") is not None]
    stats: dict = {
        "n": len(valid),
        "hit_rate": _rate(valid),
        "by_confidence": [],
        "by_decision": {},
    }
    edges = [0.0, *_CONF_BINS, 1.0]
    for lo, hi in pairwise(edges):
        last = hi >= 1.0
        bucket = [
            r
            for r in valid
            if lo <= r["confidence"] < hi or (last and r["confidence"] == 1.0)
        ]
        stats["by_confidence"].append(
            {
                "range": f"[{lo}, {hi if not last else 1.0}]",
                "n": len(bucket),
                "hit_rate": _rate(bucket),
            }
        )
    for dec in ("TRADE", "WATCH"):
        sub = [r for r in valid if r.get("decision") == dec]
        if sub:
            stats["by_decision"][dec] = {"n": len(sub), "hit_rate": _rate(sub)}
    return stats


def _load_review_log(root: Path) -> dict:
    """读累积状态：{"reviewed": set[str], "records": list[dict]}（容错归零）。"""
    try:
        data = json.loads((root / "review_log.json").read_text(encoding="utf-8"))
        reviewed = {x for x in data.get("reviewed") or [] if isinstance(x, str)}
        records = [r for r in data.get("records") or [] if isinstance(r, dict)]
        return {"reviewed": reviewed, "records": records}
    except Exception:
        return {"reviewed": set(), "records": []}


def _save_review_log(root: Path, state: dict) -> None:
    """落盘累积状态；失败静默（下次重扫，幂等）。"""
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / "review_log.json").write_text(
            json.dumps(
                {
                    "reviewed": sorted(state["reviewed"]),
                    "records": state["records"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:  # noqa: S110 —— 落盘失败静默：下次重扫，幂等
        pass


def review_past_decisions(
    reports_dir: str | Path = Path("reports"),
    now: datetime | None = None,
) -> dict:
    """扫描历史 run.json 回看到期决策；返回累积 records + 校准统计。

    到期（run_ts + 7d ≤ now）才回看；latest 目录跳过（防复制品重复统计）；
    klines 失败的 run 不标记 reviewed（下次重试）；统计口径为全历史累积。
    """
    root = Path(reports_dir)
    now = now or datetime.now(timezone.utc)
    state = _load_review_log(root)
    new_records: list[dict] = []
    unavailable: list[dict] = []
    scanned = expired = pending = 0
    if root.is_dir():
        for run_file in sorted(root.glob("*/run.json")):
            run_dir = run_file.parent.name
            if run_dir == "latest" or run_dir in state["reviewed"]:
                continue
            try:
                run = json.loads(run_file.read_text(encoding="utf-8"))
            except Exception:
                state["reviewed"].add(run_dir)
                continue
            meta = run.get("meta") or {}
            ts = _parse_ts(meta.get("run_ts"))
            if ts is None or meta.get("mode") == "mock":
                # mock 决策非真实判断，不进评估回路（防污染校准统计）
                state["reviewed"].add(run_dir)
                continue
            scanned += 1
            if now < ts + timedelta(days=_REVIEW_HORIZON_DAYS):
                pending += 1
                continue
            expired += 1
            run_ts_ms = int(ts.timestamp() * 1000)
            run_ok = True
            run_records: list[dict] = []
            for row in run.get("results") or []:
                dec = (row or {}).get("decision")
                direction = (row or {}).get("direction")
                if dec in (None, "PASS") or direction not in ("long", "short"):
                    continue
                symbol = row.get("symbol") or ""
                klines = binance.fetch_klines(
                    binance.pair_symbol(symbol), interval="1d", limit=400
                )
                if klines is None:
                    run_ok = False
                rets = binance.closed_daily_returns(klines, run_ts_ms)
                run_records.append(
                    {
                        "symbol": symbol,
                        "decision": dec,
                        "direction": direction,
                        "confidence": row.get("confidence"),
                        "run_ts": meta.get("run_ts"),
                        "run_dir": run_dir,
                        **rets,
                        "hit_7d": _hit(direction, rets.get("ret_7d")),
                        "status": "OK" if rets else "UNAVAILABLE",
                    }
                )
            if run_ok:
                state["reviewed"].add(run_dir)
                new_records.extend(run_records)
            else:
                # klines 失败：不进累积池、不标记 reviewed（下次重试），
                # 仅本次返回临时展示
                unavailable.extend(run_records)
    state["records"] = state["records"] + new_records
    _save_review_log(root, state)
    return {
        "as_of": now.isoformat(),
        "scanned_runs": scanned,
        "expired_runs": expired,
        "pending_runs": pending,
        "new_records": new_records,
        "unavailable_records": unavailable,
        "records": state["records"],
        "stats": calibrate(state["records"]),
    }


def load_records(reports_root: str | Path = Path("reports")) -> list[dict]:
    """读已回看记录（review_log.json 的 records），容错归零。

    供 ① collect_data 加载校准基线（本批决策尚未入池，统计到上一批为止）；
    不扫描新 run、不拉 klines——那部分仍由 :func:`review_past_decisions` 在 ⑧ 做。
    """
    return _load_review_log(Path(reports_root))["records"]


def render_calibration_context(records: list[dict]) -> str:
    """校准基线文本（prompt 用）：累积命中率 + TRADE/WATCH 分组 + 置信度分箱。

    空池（无已回看记录）→ 空串（摘要不渲染该节，mock 模式同理）；
    纯确定性计算，零 LLM 调用；与 ⑧ 报告「决策复盘」节同源（同一 :func:`calibrate`）。
    """
    stats = calibrate(records)
    n = stats.get("n") or 0
    if n == 0:
        return ""
    lines = [
        f"累积方向判断 {n} 条（T+7d），命中率 {stats.get('hit_rate')}；"
        "置信度分箱："
        + "、".join(
            f"{b['range']} → {b['hit_rate']}（n={b['n']}）"
            for b in stats.get("by_confidence") or []
        )
    ]
    for dec in ("TRADE", "WATCH"):
        s = (stats.get("by_decision") or {}).get(dec)
        if s:
            lines.append(f"{dec}：{s['n']} 条，命中率 {s['hit_rate']}")
    return "\n".join(lines)
