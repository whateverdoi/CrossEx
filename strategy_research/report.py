"""报告落盘：overview.md + run.json + candidates.json + snapshot/signal_diff（09-10 票）。

渲染异常仅记 meta 不中断批（规格六节错误矩阵）；工件永远可生成（无 facts/challenges
渲染空节不报错）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from strategy_research.env import is_mock_mode


def _run_dir() -> Path:
    """reports/<ts>/ 运行目录 + reports/latest/ 软链目标（微秒级防同秒碰撞）。"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ%f")
    return Path("reports") / ts


def build_report(state: dict, meta: dict) -> tuple[Path, dict]:
    """落盘 run.json + overview.md + candidates.json + snapshot/signal_diff。

    09 票扩展：candidates 工件（确定性派生，LLM 不可改）+ 信号快照/对比
    （先读旧为 prev 再覆盖）。返回 (报告目录, artifacts)。工件/快照异常仅记
    meta.report_error，不拖累已落盘的 run.json/overview.md。
    """
    run_dir = _run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)

    mode = "mock" if is_mock_mode() else "live"
    run_ts = datetime.now(timezone.utc).isoformat()
    run = {
        "meta": {
            "mode": mode,
            "tokens": state["tokens"],
            "screening": meta.get("screening") or {"mode": "manual"},
            "run_ts": run_ts,
            "node_order": meta.get("node_order") or [],
        },
        "results": state.get("results") or [],
    }
    (run_dir / "run.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    overview = _render_overview(state, run, meta)
    (run_dir / "overview.md").write_text(overview, encoding="utf-8")

    # 09：candidates 工件 + 信号快照/对比（独立 try：失败仅记 report_error，不中断批）
    artifacts: dict[str, dict] = {}
    try:
        artifacts = _build_artifacts(state)
        (run_dir / "candidates.json").write_text(
            json.dumps(artifacts, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_snapshot_and_diff(state, run_ts, mode)
    except Exception as exc:  # 规格：快照/对比失败不中断批
        meta["report_error"] = f"工件/快照落盘失败: {exc}"

    latest = Path("reports") / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    for f in ("run.json", "overview.md", "candidates.json"):
        if not (run_dir / f).exists():
            continue  # 工件落盘失败时不断链
        dest = latest / f
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        # 软链目标相对 latest/ 解析：reports/latest/../<ts>/<f>
        dest.symlink_to(Path("..") / run_dir.name / f)
    return run_dir, artifacts


def _build_artifacts(state: dict) -> dict:
    """candidates 工件：流动性分层 + 机会分级 + 研究主题统计（确定性，LLM 不可改）。

    liquidity_tier：24h 成交额 >=1e8 → high / >=1e7 → mid / 其余 low；
    opportunity_level：downgraded 强制 D，其余 TRADE→A / WATCH→B / PASS→D；
    catalysts：facts.topic 计数（7 主题 + unknown 计入）。缺失一律容错不报错。
    """
    results = state.get("results") or []
    artifacts: dict[str, dict] = {}
    for i, symbol in enumerate(state["tokens"]):
        analysis = results[i] if i < len(results) else {}
        vol = (
            (state.get("market_data", {}).get(symbol) or {}).get("quote_volume_24h")
            or {}
        ).get("value")
        tier = (
            "high"
            if isinstance(vol, (int, float)) and vol >= 1e8
            else "mid"
            if isinstance(vol, (int, float)) and vol >= 1e7
            else "low"
        )
        level = (
            "D"
            if analysis.get("downgraded")
            else {"TRADE": "A", "WATCH": "B", "PASS": "D"}.get(
                analysis.get("decision"), "D"
            )
        )
        catalysts: dict[str, int] = {}
        for f in state.get("facts", {}).get(symbol) or []:
            t = f.get("topic") or "unknown"
            catalysts[t] = catalysts.get(t, 0) + 1
        artifacts[symbol] = {
            "liquidity_tier": tier,
            "opportunity_level": level,
            "recommended_strategy": (
                f"{analysis.get('direction', '')} "
                f"{analysis.get('trade_structure', 'UNKNOWN')}"
            ).strip(),
            "confidence": analysis.get("confidence", 0.0),
            "rationale": (
                f"{analysis.get('mispricing', '')} | "
                f"catalyst: {analysis.get('catalyst', '')}"
            )[:200],
            "catalysts": catalysts,
        }
    return artifacts


def _build_snapshot(state: dict, run_ts: str, mode: str) -> dict:
    """当前批信号快照（决策失效机制的 cur 侧，results 轻量投影）。"""
    return {
        "run_ts": run_ts,
        "mode": mode,
        "tokens": state["tokens"],
        "results": [
            {
                "symbol": row.get("symbol") or symbol,
                "decision": row.get("decision"),
                "direction": row.get("direction"),
                "confidence": row.get("confidence"),
            }
            for symbol, row in zip(state["tokens"], state.get("results") or [])
        ],
    }


def _read_prev_snapshot() -> dict | None:
    """读 reports/latest/snapshot.json 为 prev；不存在/损坏 → None（首次运行语义）。"""
    path = Path("reports") / "latest" / "snapshot.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _build_signal_diff(prev: dict | None, cur: dict) -> dict:
    """跨运行信号对比：action = new / hold / stop_short / stop_long。

    规则：prev 缺失 → new；prev==cur（decision+direction 同）→ hold；
    prev=(TRADE, short|long) 且 cur≠ → stop_short/stop_long（信号反转、停止该方向）；
    其余 → hold（cur 列展示新状态）。失效由外部调度重跑触发，无价格锚点/有效期。
    """
    prev_map = {
        r["symbol"]: r for r in (prev or {}).get("results") or [] if r.get("symbol")
    }
    diff: dict[str, dict] = {}
    for row in cur.get("results") or []:
        symbol = row.get("symbol")
        if not symbol:
            continue
        p = prev_map.get(symbol)
        cur_state = (row.get("decision"), row.get("direction"))
        if p is None:
            action = "new"
        elif (p.get("decision"), p.get("direction")) == cur_state:
            action = "hold"
        elif p.get("decision") == "TRADE" and p.get("direction") == "short":
            action = "stop_short"
        elif p.get("decision") == "TRADE" and p.get("direction") == "long":
            action = "stop_long"
        else:
            action = "hold"
        diff[symbol] = {"prev": p, "cur": row, "action": action}
    return diff


def _write_snapshot_and_diff(state: dict, run_ts: str, mode: str) -> dict:
    """信号快照覆盖 + 对比落盘（reports/latest/，先读旧为 prev 再覆盖）。"""
    latest = Path("reports") / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    snapshot = _build_snapshot(state, run_ts, mode)
    prev = _read_prev_snapshot()
    (latest / "snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    diff = _build_signal_diff(prev, snapshot)
    (latest / "signal_diff.json").write_text(
        json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return diff


def _render_overview(state: dict, run: dict, meta: dict) -> str:
    """overview.md 骨架：币种筛选节 + 每 token 摘要（空节渲染不报错）。"""
    lines = [
        "# 策略研究概览",
        "",
        f"- 运行模式：`{run['meta']['mode']}`",
        f"- 时间：{run['meta']['run_ts']}",
        f"- tokens：{', '.join(state['tokens'])}",
        "",
        "## 币种筛选",
        "",
        f"- 模式：`{run['meta']['screening'].get('mode', 'unknown')}`",
        "",
        "## 逐币分析",
        "",
    ]
    for s in state["tokens"]:
        lines.append(f"### {s}")
        lines.append("")
        lines.append("- 决策：-（待 07 票实现）")
        lines.append("")
    return "\n".join(lines)
