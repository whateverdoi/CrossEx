"""④ 报告与工件落盘：evidence.md + run.json + candidates.json + snapshot/diff。

04 票：证据 md（总览表 + 每 token 做多/做空证据表 + 剔除附录）替代 overview.md；
run.json 含数据快照投影 + 证据清单 + 信号快照 + llm_calls；candidates 仅候选列表
（机会分级退役）；snapshot/diff 为确定性信号快照对比（decision 型 stop 语义退役）。
渲染异常仅记 meta 不中断批；工件永远可生成（无证据/剔除渲染空节不报错）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from strategy_research.env import (
    _MOCK_CALL_COUNTS,
    LIVE_CALL_COUNTS,
    is_mock_mode,
)
from strategy_research.scanner_snapshot import _strip_quote


def _write_json(path: Path, obj: dict) -> None:
    """统一落盘：JSON 序列化写文件（ensure_ascii=False + indent=2）。"""
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_dir() -> Path:
    """reports/<ts>/ 运行目录 + reports/latest/ 软链目标（微秒级防同秒碰撞）。"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ%f")
    return Path("reports") / ts


def _llm_calls() -> dict:
    """LLM 调用计数（成本统计）：mock 从假模型计数；live 从 callback 计数汇总。"""
    counts = dict(_MOCK_CALL_COUNTS) if is_mock_mode() else dict(LIVE_CALL_COUNTS)
    counts["total"] = sum(v for k, v in counts.items() if k != "total")
    return counts


def build_report(state: dict, meta: dict) -> tuple[Path, dict]:
    """落盘 run.json + evidence.md + candidates.json + snapshot/signal_diff。

    04 票：run.json = meta + 数据快照投影 + 证据清单 + 信号快照（spec D8）；
    candidates 仅候选列表（分级退役）；evidence.md 替代 overview.md（spec D7）。
    返回 (报告目录, artifacts)。工件/快照异常仅记 meta.report_error，
    不拖累已落盘的 run.json/evidence.md。
    """
    run_dir = _run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)

    mode = "mock" if is_mock_mode() else "live"
    run_ts = datetime.now(timezone.utc).isoformat()
    meta["llm_calls"] = _llm_calls()

    run_meta: dict = {
        "mode": mode,
        "tokens": state["tokens"],
        "screening": meta.get("screening") or {"mode": "manual"},
        "run_ts": run_ts,
        "node_order": meta.get("node_order") or [],
        "llm_calls": meta["llm_calls"],
        "market_env": meta.get("market_env"),
    }
    if meta.get("scanner") is not None:  # 仅真实模式（main 注入补跑状态）落盘
        run_meta["scanner"] = meta["scanner"]
    # 分支异常留痕（06 票补漏：LLM 失败/空证据可诊断，不再黑盒）
    run_meta["bull_errors"] = state.get("bull_errors") or {}
    run_meta["bear_errors"] = state.get("bear_errors") or {}
    if meta.get("incomplete_tokens"):
        run_meta["incomplete_tokens"] = meta["incomplete_tokens"]
    if meta.get("scan_error"):
        run_meta["scan_error"] = meta["scan_error"]
    run = {
        "meta": run_meta,
        "evidence": state.get("evidence") or {},
        "rejected_evidence": state.get("rejected_evidence") or {},
        "data_snapshot": _build_data_snapshot(state),
        "signals": {s: _signal_snapshot(s, state) for s in state["tokens"]},
    }
    _write_json(run_dir / "run.json", run)

    # 04 票：candidates 工件 + 信号快照/对比（独立 try：失败仅记 report_error）
    artifacts: dict = {"candidates": []}
    try:
        artifacts = _build_artifacts(state)
        _write_json(run_dir / "candidates.json", artifacts)
        _write_snapshot_and_diff(state, run_ts, mode)
    except Exception as exc:  # 规格：快照/对比失败不中断批
        meta["report_error"] = f"工件/快照落盘失败: {exc}"

    (run_dir / "evidence.md").write_text(
        _render_evidence_md(state, run), encoding="utf-8"
    )

    latest = Path("reports") / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    for f in ("run.json", "evidence.md", "candidates.json"):
        if not (run_dir / f).exists():
            continue  # 工件落盘失败时不断链
        dest = latest / f
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        # 软链目标相对 latest/ 解析：reports/latest/../<ts>/<f>
        dest.symlink_to(Path("..") / run_dir.name / f)
    return run_dir, artifacts


def _build_artifacts(state: dict) -> dict:
    """candidates 工件（04 票简化）：仅候选列表（机会分级/流动性分层退役，spec D8）。"""
    return {"candidates": list(state["tokens"])}


# ── 04 票：信号快照 + 数据快照投影（确定性） ──────────────────


_SIGNAL_KEYS = (
    "momentum",
    "quadrant",
    "funding_pctile_90d",
    "oi_price_divergence",
    "liq_imbalance",
)
#: 第一层派生（08 票）：funding_z 从 market 快照读，其余从 signals.market_metrics.value 读
_MARKET_METRIC_KEYS = (
    "funding_z",
    "rv_7d",
    "rv_30d",
    "drawdown_1y",
    "vol_adj_ret_7d",
    "vol_adj_ret_30d",
    "beta_7d",
    "beta_30d",
    "alpha_7d",
    "alpha_30d",
    "turnover",
)
_TREND_KEYS = ("tvl_trend_30d", "fees_trend_30d", "stablecoin_change_30d")


def _signal_snapshot(symbol: str, state: dict) -> dict:
    """确定性信号快照（04 票）：quadrant/momentum/funding_pctile_90d/
    oi_price_divergence + 第一层派生（08 票） + 趋势特征（spec D8）。

    任一缺失 → None（UNKNOWN 纪律）；signals 层失败（error 条目）→ 全 None。
    趋势特征值：tvl/fees 为趋势分类（rising/flat/falling），stablecoin 为变化 %。
    派生键：funding_z 从 market 快照读；rv/beta/alpha/vol_adj/turnover 从
    signals.market_metrics.value 读（直读数值，快照可校准）。
    """
    sig = (state.get("signals") or {}).get(symbol) or {}
    out: dict[str, Any] = {
        k: None for k in (*_SIGNAL_KEYS, *_MARKET_METRIC_KEYS, *_TREND_KEYS)
    }
    if sig and not sig.get("error"):
        mom = (sig.get("momentum") or {}).get("value")
        quad = ((sig.get("divergence") or {}).get("value") or {}).get("quadrant")
        mkt = (state.get("market_data") or {}).get(symbol) or {}
        ms = (state.get("microstructure_data") or {}).get(symbol) or {}
        pct = (mkt.get("funding_pctile_90d") or {}).get("value")
        od = (ms.get("oi_price_divergence") or {}).get("value") or {}
        imb = (ms.get("liq_imbalance") or {}).get("value") or {}
        out.update(
            {
                "momentum": mom if isinstance(mom, (int, float)) else None,
                "quadrant": quad if quad in ("I", "II", "III", "IV") else None,
                "funding_pctile_90d": pct if isinstance(pct, (int, float)) else None,
                "oi_price_divergence": od.get("label")
                if isinstance(od, dict)
                else None,
                "liq_imbalance": imb.get("label") if isinstance(imb, dict) else None,
            }
        )
        fz = (mkt.get("funding_z") or {}).get("value")
        out["funding_z"] = fz if isinstance(fz, (int, float)) else None
        mm = (sig.get("market_metrics") or {}).get("value") or {}
        for k in _MARKET_METRIC_KEYS[1:]:  # 其余派生键从 signals.market_metrics 直读
            v = mm.get(k)
            out[k] = v if isinstance(v, (int, float)) else None
    fund = (state.get("fundamental_data") or {}).get(symbol) or {}
    for k in _TREND_KEYS:
        out[k] = (fund.get(k) or {}).get("value")
    return out


def _build_data_snapshot(state: dict) -> dict:
    """数据快照投影（spec D8）：per-token 各数据域轻量投影（证据可复核的原始数据）。"""
    scanner = state.get("scanner_snapshot") or {}
    snap: dict[str, dict] = {}
    for s in state["tokens"]:
        base = _strip_quote(s)  # 快照 key 为裸符号，消费点对齐命名空间
        snap[s] = {
            "signals": (state.get("signals") or {}).get(s) or {},
            "market_data": (state.get("market_data") or {}).get(s) or {},
            "fundamental_data": (state.get("fundamental_data") or {}).get(s) or {},
            "microstructure_data": (state.get("microstructure_data") or {}).get(s)
            or {},
            "web_data": (state.get("web_data") or {}).get(s) or {},
            "scanner_snapshot": {
                "date": scanner.get("date"),
                "market": (scanner.get("market") or {}).get(base) or {},
                "microstructure": (scanner.get("microstructure") or {}).get(base)
                or {},
            },
        }
    return snap


def _build_snapshot(state: dict, run_ts: str, mode: str) -> dict:
    """当前批信号快照（spec D8）：确定性信号投影 per-token，无 decision 语义。"""
    return {
        "run_ts": run_ts,
        "mode": mode,
        "tokens": state["tokens"],
        "signals": {s: _signal_snapshot(s, state) for s in state["tokens"]},
    }


def _read_prev_snapshot() -> dict | None:
    """读 reports/latest/snapshot.json 为 prev；不存在/损坏 → None（首次运行语义）。"""
    path = Path("reports") / "latest" / "snapshot.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # 含 JSONDecodeError/UnicodeDecodeError
        return None


def _build_signal_diff(prev: dict | None, cur: dict) -> dict:
    """跨运行信号对比：action = new / changed / unchanged（信号快照语义）。

    规则：prev 缺失 → new；信号全字段一致 → unchanged；任一字段变化 → changed。
    旧 decision 型 stop_short/stop_long 失效语义退役（spec D8）。
    """
    prev_map = (prev or {}).get("signals") or {}
    cur_map = (cur or {}).get("signals") or {}
    diff: dict[str, dict] = {}
    for symbol, cur_sig in cur_map.items():
        p = prev_map.get(symbol)
        action = "new" if p is None else "unchanged" if p == cur_sig else "changed"
        diff[symbol] = {"prev": p, "cur": cur_sig, "action": action}
    return diff


def _write_snapshot_and_diff(state: dict, run_ts: str, mode: str) -> dict:
    """信号快照覆盖 + 对比落盘（reports/latest/，先读旧为 prev 再覆盖）。"""
    latest = Path("reports") / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    snapshot = _build_snapshot(state, run_ts, mode)
    prev = _read_prev_snapshot()
    _write_json(latest / "snapshot.json", snapshot)
    diff = _build_signal_diff(prev, snapshot)
    _write_json(latest / "signal_diff.json", diff)
    return diff


# ── 证据 md 渲染（04 票，替代 overview.md，spec D7） ──────────


def _domains(items: list[dict]) -> list[str]:
    """证据引用数据域（按出现顺序去重，总览表“数据域覆盖”列）。"""
    seen: list[str] = []
    for item in items:
        d = (item.get("basis") or {}).get("domain")
        if d and d not in seen:
            seen.append(d)
    return seen


def _price_text(value) -> str:
    """最新价格渲染（06 票：总览表核对列）：保留小价格精度、去尾零；缺失 → —。"""
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value:.8f}".rstrip("0").rstrip(".")


def _evidence_section_lines(
    symbol: str,
    ev: dict,
    scan_date: str | None = None,
    errors: dict | None = None,
) -> list[str]:
    """单 token 证据节：### 做多证据 / ### 做空证据 两张表（# | claim | basis | source）。

    scan_date：扫描器快照日期（存在时在节头标注，防旧快照被误读为实时，07 票）；
    errors：{side: 错误消息}（LLM 失败/空证据留痕，非空时节头标注，不再静默 0 证据）。
    """
    lines = [f"## {symbol}", ""]
    if errors:
        for side, msg in errors.items():
            if msg:
                label = "做多" if side == "bull" else "做空"
                lines += [f"- {label}分支异常：{msg}", ""]
    if scan_date:
        lines += [f"- 扫描器快照日期：{scan_date}", ""]
    for title, items in (
        ("做多证据", ev.get("bull_case") or []),
        ("做空证据", ev.get("bear_case") or []),
    ):
        lines.append(f"### {title}")
        lines.append("")
        if not items:
            lines.append("（无做多证据）" if title == "做多证据" else "（无做空证据）")
            lines.append("")
            continue
        lines.append("| # | claim | basis | source |")
        lines.append("|---|---|---|---|")
        for i, item in enumerate(items, 1):
            b = item.get("basis") or {}
            basis = (
                f"{b.get('domain')}.{b.get('field')} = {b.get('value')}"
                if b.get("field")
                else ""
            )
            lines.append(
                f"| {i} | {item.get('claim', '')} | {basis} | {item.get('source', '')} |"
            )
        lines.append("")
    return lines


def _rejected_lines(rejected: dict, tokens: list[str]) -> list[str]:
    """剔除记录附录：| token | claim | 原因 |；无剔除空节占位。"""
    lines = ["## 剔除记录", ""]
    rows = [(s, r) for s in tokens for r in (rejected.get(s) or [])]
    if not rows:
        lines.append("（本批无剔除记录）")
        lines.append("")
        return lines
    lines.append("| token | claim | 原因 |")
    lines.append("|---|---|---|")
    for s, r in rows:
        lines.append(f"| {s} | {r.get('claim', '')} | {r.get('reason', '')} |")
    lines.append("")
    return lines


def _render_evidence_md(state: dict, run: dict) -> str:
    """evidence.md（04 票）：头部元信息 + 总览表（token/多头/空头/数据域覆盖）
    + 每 token 证据节（做多/做空两张表）+ 文档末尾剔除记录附录（spec D7）。

    空节渲染不报错（无证据/无剔除时输出占位说明）。
    """
    evidence = state.get("evidence") or {}
    rejected = state.get("rejected_evidence") or {}
    meta = run["meta"]
    # 分支异常合并：{symbol: {"bull": msg, "bear": msg}}（空/无 → 不标注）
    branch_errors: dict[str, dict] = {}
    for s in state["tokens"]:
        e = {
            side: (meta.get(f"{side}_errors") or {}).get(s)
            for side in ("bull", "bear")
        }
        if any(e.values()):
            branch_errors[s] = e
    lines = [
        "# 证据报告",
        "",
        f"- 运行模式：`{meta['mode']}`",
        f"- 时间：{meta['run_ts']}",
        f"- tokens：{', '.join(state['tokens'])}",
        f"- LLM 调用：{meta.get('llm_calls', {}).get('total', 0)}",
        "",
        "## 总览",
        "",
        "| token | 最新价格 | 多头证据数 | 空头证据数 | 数据域覆盖 |",
        "|---|---|---|---|---|",
    ]
    for s in state["tokens"]:
        ev = evidence.get(s) or {}
        bull = ev.get("bull_case") or []
        bear = ev.get("bear_case") or []
        domains = ", ".join(_domains(bull + bear)) or "—"
        price = ((state.get("market_data") or {}).get(s) or {}).get("price") or {}
        lines.append(
            f"| {s} | {_price_text(price.get('value'))} | {len(bull)} | {len(bear)} | {domains} |"
        )
    lines.append("")
    for s in state["tokens"]:
        lines += _evidence_section_lines(
            s,
            evidence.get(s) or {},
            ((state.get("scanner_snapshot") or {}).get("date")),
            branch_errors.get(s),
        )
    lines += _rejected_lines(rejected, state["tokens"])
    return "\n".join(lines)
