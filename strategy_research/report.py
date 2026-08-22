"""报告落盘：overview.md + run.json + candidates.json + snapshot/signal_diff（09-10 票）。

渲染异常仅记 meta 不中断批（规格六节错误矩阵）；工件永远可生成（无 facts/challenges
渲染空节不报错）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from strategy_research import review as review_mod
from strategy_research.env import (
    _MOCK_CALL_COUNTS,
    LIVE_CALL_COUNTS,
    is_mock_mode,
)


def _write_json(path: Path, obj: dict) -> None:
    """统一落盘：JSON 序列化写文件（ensure_ascii=False + indent=2）。"""
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_dir() -> Path:
    """reports/<ts>/ 运行目录 + reports/latest/ 软链目标（微秒级防同秒碰撞）。"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ%f")
    return Path("reports") / ts


def _llm_calls() -> dict:
    """LLM 调用计数（成本统计）：mock 从假模型计数；live 从 callback 计数汇总。"""
    counts = (
        dict(_MOCK_CALL_COUNTS) if is_mock_mode() else dict(LIVE_CALL_COUNTS)
    )
    counts["total"] = sum(v for k, v in counts.items() if k != "total")
    return counts


def build_report(state: dict, meta: dict) -> tuple[Path, dict]:
    """落盘 run.json + overview.md + candidates.json + snapshot/signal_diff。

    09 票：candidates 工件 + 信号快照/对比（先读旧为 prev 再覆盖）；
    10 票：overview 五节渲染（币种筛选/信号变化/对抗复审/候选清单/逐币分析）
    + run.json 成本统计（llm_calls）。返回 (报告目录, artifacts)。工件/快照
    异常仅记 meta.report_error，不拖累已落盘的 run.json/overview.md。
    """
    run_dir = _run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)

    mode = "mock" if is_mock_mode() else "live"
    run_ts = datetime.now(timezone.utc).isoformat()
    meta["llm_calls"] = _llm_calls()

    # 12 票：决策追踪与校准（独立 try：失败仅记 review_error，不拖累报告；
    # 此时当前 run.json 尚未落盘，不会被自己扫到）
    decision_review: dict = {}
    try:
        decision_review = review_mod.review_past_decisions()
    except Exception as exc:  # 同 report_error 纪律：仅记录不中断批
        meta["review_error"] = f"决策复盘失败: {exc}"

    run = {
        "meta": {
            "mode": mode,
            "tokens": state["tokens"],
            "screening": meta.get("screening") or {"mode": "manual"},
            "run_ts": run_ts,
            "node_order": meta.get("node_order") or [],
            "llm_calls": meta["llm_calls"],
        },
        "results": state.get("results") or [],
    }
    if decision_review:
        run["decision_review"] = decision_review
    _write_json(run_dir / "run.json", run)

    # 09/10：candidates 工件 + 信号快照/对比（独立 try：失败仅记 report_error）
    artifacts: dict[str, dict] = {s: {} for s in state["tokens"]}
    signal_diff: dict = {}
    try:
        artifacts = _build_artifacts(state)
        _write_json(run_dir / "candidates.json", artifacts)
        signal_diff = _write_snapshot_and_diff(state, run_ts, mode)
    except Exception as exc:  # 规格：快照/对比失败不中断批
        meta["report_error"] = f"工件/快照落盘失败: {exc}"

    overview = _render_overview(
        state, run, meta, artifacts, signal_diff, review=decision_review
    )
    (run_dir / "overview.md").write_text(overview, encoding="utf-8")

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
        snap = state.get("scanner_snapshot") or {}
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
            "market_snapshot": (snap.get("market") or {}).get(symbol) or {},
            "microstructure_snapshot": (
                (snap.get("microstructure") or {}).get(symbol) or {}
            ),
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
    except (OSError, ValueError):  # 含 JSONDecodeError/UnicodeDecodeError
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
    _write_json(latest / "snapshot.json", snapshot)
    diff = _build_signal_diff(prev, snapshot)
    _write_json(latest / "signal_diff.json", diff)
    return diff


def _render_overview(
    state: dict,
    run: dict,
    meta: dict,
    artifacts: dict,
    signal_diff: dict,
    review: dict | None = None,
) -> str:
    """overview.md 六节：币种筛选 / 信号变化 / 对抗复审 / 候选清单 / 逐币分析 /
    决策复盘（12 票）。

    空节渲染不报错（无 facts/challenges/diff/review 时输出占位说明，规格十节纪律 2）。
    """
    lines = [
        "# 策略研究概览",
        "",
        f"- 运行模式：`{run['meta']['mode']}`",
        f"- 时间：{run['meta']['run_ts']}",
        f"- tokens：{', '.join(state['tokens'])}",
        f"- LLM 调用：{run['meta'].get('llm_calls', {}).get('total', 0)}",
        "",
        *_screening_lines(meta),
        *_signal_diff_lines(signal_diff),
        *_challenge_lines(state),
        *_artifacts_lines(artifacts, state["tokens"]),
        *_per_token_lines(state, artifacts),
        *_review_lines(review),
    ]
    return "\n".join(lines)


def _review_lines(review: dict | None) -> list[str]:
    """决策复盘节（12 票）：累积命中率 + 置信度分箱 + 本批新回看明细。"""
    lines = ["## 决策复盘", ""]
    if not review or not review.get("records"):
        lines.append("（无到期决策可回看——首次运行或历史不足 7 天）")
        lines.append("")
        return lines
    stats = review.get("stats") or {}
    lines.append(
        f"- 累积方向判断 {stats.get('n', 0)} 条，命中率 {stats.get('hit_rate')}；"
        f"本次回看 {review.get('expired_runs', 0)} 批"
        f"（待到期 {review.get('pending_runs', 0)} 批）"
    )
    for dec, s in (stats.get("by_decision") or {}).items():
        lines.append(f"- {dec}：{s['n']} 条，命中率 {s['hit_rate']}")
    by_h = stats.get("by_horizon") or []
    if by_h:
        lines.append(
            "- 按评估窗口："
            + "、".join(f"{b['value']} → {b['hit_rate']}（n={b['n']}）" for b in by_h)
        )
    by_sig = stats.get("by_signal") or {}
    sig_keys = [k for k in ("quadrant", "momentum", "funding_pctile", "oi_divergence") if by_sig.get(k)]
    if sig_keys:
        lines += ["", "按信号状态分桶（T+7d 方向命中，旧运行无信号状态不计）：", ""]
        for k in sig_keys:
            lines.append(f"{k}：")
            lines.append("| 取值 | 条数 | 命中率 |")
            lines.append("|---|---|---|")
            for b in by_sig[k]:
                lines.append(f"| {b['value']} | {b['n']} | {b['hit_rate']} |")
            lines.append("")
    lines.append("")
    lines.append("置信度分箱（T+7d 方向命中）：")
    lines.append("")
    lines.append("| 置信度区间 | 条数 | 命中率 |")
    lines.append("|---|---|---|")
    for b in stats.get("by_confidence") or []:
        lines.append(f"| {b['range']} | {b['n']} | {b['hit_rate']} |")
    new = review.get("new_records") or []
    if new:
        lines += ["", "### 本次回看明细", ""]
        lines.append("| token | 决策 | 方向 | 置信度 | T+1d | T+7d | 命中 |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in new:
            hit = r.get("hit_7d")
            hit_str = hit if hit is not None else r.get("status", "UNAVAILABLE")
            lines.append(
                f"| {r.get('symbol')} | {r.get('decision')} | {r.get('direction')} "
                f"| {r.get('confidence')} | {r.get('ret_1d')} | {r.get('ret_7d')} "
                f"| {hit_str} |"
            )
    lines.append("")
    return lines


def _screening_lines(meta: dict) -> list[str]:
    """币种筛选节：模式 + 规则 + 候选带 reason（manual 模式无规则/候选）。"""
    screening = meta.get("screening") or {"mode": "unknown"}
    lines = ["## 币种筛选", "", f"- 模式：`{screening.get('mode', 'unknown')}`", ""]
    rules = screening.get("rules") or []
    if rules:
        lines.append("- 规则：" + "、".join(rules))
        lines.append("")
    cands = screening.get("candidates") or []
    if cands:
        lines.append("- 候选：")
        for c in cands:
            lines.append(f"  - {c.get('symbol')}：{c.get('reason', '')}")
        lines.append("")
    return lines


def _signal_diff_lines(signal_diff: dict) -> list[str]:
    """信号变化节：对比表（symbol | 上一批 | 当前 | action）；无 prev 空节说明。"""
    lines = ["## 信号变化（相对上一批）", ""]
    if not signal_diff:
        lines.append("（首次运行或快照失败，无上一批对比）")
        lines.append("")
        return lines
    lines.append("| token | 上一批 | 当前 | action |")
    lines.append("|---|---|---|---|")
    for symbol, d in signal_diff.items():
        p = d.get("prev") or {}
        c = d.get("cur") or {}
        prev_state = f"{p.get('decision')}/{p.get('direction') or '-'}"
        cur_state = f"{c.get('decision')}/{c.get('direction') or '-'}"
        lines.append(f"| {symbol} | {prev_state} | {cur_state} | {d.get('action')} |")
    lines.append("")
    return lines


def _challenge_lines(state: dict) -> list[str]:
    """对抗复审节：每 token 挑战（severity/stance/claim/evidence）；无挑战空节。"""
    lines = ["## 对抗复审", ""]
    shown = False
    for s in state["tokens"]:
        chs = (state.get("challenges") or {}).get(s) or []
        if not chs:
            continue
        shown = True
        lines.append(f"### {s}")
        for ch in chs[:3]:
            lines.append(
                f"- [{ch.get('severity')}/{ch.get('stance')}] {ch.get('claim')}"
            )
            lines.append(f"  - 证据：{ch.get('evidence')}")
        lines.append("")
    if not shown:
        lines.append("（无挑战——全部 PASS 透传或对抗未产出）")
        lines.append("")
    return lines


def _artifacts_lines(artifacts: dict, tokens: list[str]) -> list[str]:
    """候选清单节：level/流动性/策略/置信度/理由表；无工件空节。"""
    lines = ["## 候选清单", ""]
    if not artifacts:
        lines.append("（本批无候选工件）")
        lines.append("")
        return lines
    lines.append("| token | level | 流动性 | 策略 | 置信度 | 理由 |")
    lines.append("|---|---|---|---|---|---|")
    for s in tokens:
        a = artifacts.get(s) or {}
        if not a:
            continue
        lines.append(
            f"| {s} | {a.get('opportunity_level', 'D')} | {a.get('liquidity_tier', 'low')} | "
            f"{a.get('recommended_strategy', 'UNKNOWN')} | {a.get('confidence', 0.0)} | "
            f"{a.get('rationale', '')} |"
        )
    lines.append("")
    return lines


def _fmt_num(value: Any, decimals: int = 2) -> str:
    """数值 → 指定小数位文本；非数值 → UNKNOWN。"""
    if not isinstance(value, (int, float)):
        return "UNKNOWN"
    return f"{value:.{decimals}f}"


def _fmt_pct(value: Any, decimals: int = 2) -> str:
    """百分比数值（value 本身是 % 数值）→ decimals 位 + %；非数值 → UNKNOWN。"""
    if not isinstance(value, (int, float)):
        return "UNKNOWN"
    return f"{value:.{decimals}f}%"


def _fmt_usd(value: Any) -> str:
    """美元金额千分位（整数金额不带小数）；非数值 → UNKNOWN。"""
    if not isinstance(value, (int, float)):
        return "UNKNOWN"
    return f"${value:,.0f}"


def _fmt_price(value: Any) -> str:
    """价格：最多 6 位小数去尾零；非数值 → UNKNOWN。"""
    if not isinstance(value, (int, float)):
        return "UNKNOWN"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _market_snapshot_lines(snap: dict) -> list[str]:
    """市场数据快照小节：指标 | 值 两列表（对齐 BinanceApi template.md §1）。"""
    ret = f"{_fmt_pct(snap.get('ret_24h'))} / {_fmt_pct(snap.get('ret_7d'))}"
    if isinstance(snap.get("price_change_pct_24h"), (int, float)):
        ret += f"（交易所官方 24h 口径 {_fmt_pct(snap.get('price_change_pct_24h'))}）"
    taker = snap.get("taker_buy_ratio_24h")
    taker_pct = (
        "UNKNOWN" if not isinstance(taker, (int, float)) else f"{taker * 100:.2f}%"
    )
    funding = snap.get("funding_rate")
    funding_pct = (
        "UNKNOWN" if not isinstance(funding, (int, float)) else f"{funding * 100:.4f}%"
    )
    days = snap.get("listing_days")
    days_str = "UNKNOWN" if not isinstance(days, (int, float)) else f"{days:.1f} 天"
    if isinstance(days, (int, float)) and snap.get("onboard_date"):
        days_str += f"（合约 {snap['onboard_date']} 上线）"
    boards = " / ".join(snap.get("boards") or []) or "—"
    return [
        "#### 市场数据快照",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 价格 | ${_fmt_price(snap.get('price'))} |",
        f"| 24h 涨跌 / 7d 涨跌 | {ret} |",
        f"| 24h 成交额 | {_fmt_usd(snap.get('quote_volume_24h'))} |",
        f"| 资金费率 | {funding_pct} |",
        f"| 主动买入占比 (24h) | {taker_pct} |",
        f"| 持仓量名义价值 | {_fmt_usd(snap.get('open_interest_value'))} |",
        f"| 期现溢价 (mark/index − 1) | {_fmt_pct(snap.get('futures_premium_pct'), 3)} |",
        f"| 上市天数 | {days_str} |",
        f"| 所属榜单 (board) | {boards} |",
        "",
    ]


def _microstructure_snapshot_lines(snap: dict) -> list[str]:
    """市场微观结构快照小节：指标 | 值 | 客观含义 三列表（对齐 template.md §2）。

    “客观含义”列为全币通用解读模板常量（BinanceApi docs/research/template.md），
    不随币种数值变化；值侧为扫描器原始数据渲染。
    """
    oi = f"{_fmt_pct(snap.get('oi_change_24h'))} / {_fmt_pct(snap.get('oi_change_48h'))}"
    ls = _fmt_num(snap.get("ls_ratio_all"))
    if isinstance(snap.get("ls_ratio_all_change_24h"), (int, float)):
        ls += f" ({_fmt_pct(snap.get('ls_ratio_all_change_24h'))})"
    funding = snap.get("funding_avg")
    if isinstance(funding, (int, float)):
        funding_str = f"{funding * 100:.4f}%（年化 {funding * 365 * 100:.1f}%）"
    else:
        funding_str = "UNKNOWN"
    return [
        "#### 市场微观结构快照",
        "",
        "| 指标 | 值 | 客观含义 |",
        "|---|---|---|",
        f"| OI 变化 24h / 48h | {oi} | 增仓（新仓推动）vs 减仓（平仓/逼空） |",
        f"| OI 名义价值变化 24h | {_fmt_pct(snap.get('oi_value_change_24h'))} | 剔除价格因素后的资金进出 |",
        f"| 全市场多空账户比 (24h 变化) | {ls} | 散户拥挤度：>2 偏多头拥挤，<0.5 偏空头拥挤 |",
        f"| 大户多空账户比 | {_fmt_num(snap.get('ls_ratio_top_acc'))} | 与全市场背离时为聪明钱信号 |",
        f"| 大户多空持仓比 | {_fmt_num(snap.get('ls_ratio_top_pos'))} | 同上（按持仓量口径） |",
        f"| 官方 taker 买卖比 | {_fmt_num(snap.get('taker_bs_ratio'))} | >1 主动买盘占优；与 OI 变化交叉验证资金性质 |",
        f"| funding 近 7 天均值 | {funding_str} | 深负 = 空头拥挤，深正 = 多头拥挤 |",
        f"| funding 趋势 (近 3 期 vs 前期) | {snap.get('funding_trend') or 'UNKNOWN'} | 拥挤度变化方向 |",
        "",
    ]


def _per_token_lines(state: dict, artifacts: dict) -> list[str]:
    """逐币分析节：决策/direction/level/关键事实/挑战与反驳（缺失渲染空节）。"""
    results = state.get("results") or []
    lines = ["## 逐币分析", ""]
    for i, s in enumerate(state["tokens"]):
        row = results[i] if i < len(results) else {}
        a = artifacts.get(s) or {}
        lines.append(f"### {s}")
        lines.append("")
        lines.append(
            f"- 决策：**{row.get('decision') or 'UNKNOWN'}**"
            f"（direction: {row.get('direction') or '未声明'}，"
            f"置信度 {row.get('confidence', 0.0)}，"
            f"level {a.get('opportunity_level', 'D')}）"
        )
        # 扫描器快照小节（state 中由 collect_data 读入；缺失 → 占位，空节不报错）
        snap = state.get("scanner_snapshot") or {}
        m_snap = (snap.get("market") or {}).get(s) or {}
        mic_snap = (snap.get("microstructure") or {}).get(s) or {}
        if m_snap:
            lines += _market_snapshot_lines(m_snap)
        if mic_snap:
            lines += _microstructure_snapshot_lines(mic_snap)
        if not m_snap and not mic_snap:
            lines.append(
                "- 扫描器快照：（不可用——未找到 BinanceApi data/research CSV）"
            )
        if row.get("downgraded"):
            lines.append(f"- 风控降级：{'；'.join(row['downgraded'])}")
        facts = (state.get("facts") or {}).get(s) or []
        if facts:
            lines.append("- 关键事实：")
            for f in facts[:5]:
                lines.append(
                    f"  - [{f.get('dimension')}/{f.get('topic')}] "
                    f"{f.get('claim')}（{f.get('source')}）"
                )
        else:
            lines.append("- 关键事实：（无——事实采证未产出）")
        chs = (state.get("challenges") or {}).get(s) or []
        rbs = row.get("rebuttals") or []
        if chs or rbs:
            lines.append("- 挑战与反驳：")
            for ch in chs[:3]:
                lines.append(f"  - 挑战[{ch.get('severity')}]：{ch.get('claim')}")
            for rb in rbs:
                lines.append(f"  - 回应[{rb.get('outcome')}]：{rb.get('response')}")
        else:
            lines.append("- 挑战与反驳：（无）")
        lines.append("")
    return lines
