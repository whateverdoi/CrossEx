"""④ 报告与工件落盘：evidence.md + run.json + candidates.json + snapshot/diff。

04 票：证据 md（总览表 + 每 token 做多/做空证据表 + 剔除附录）替代 overview.md；
run.json 含数据快照投影 + 证据清单 + 信号快照 + llm_calls；candidates 仅候选列表
（机会分级退役）；snapshot/diff 为确定性信号快照对比（decision 型 stop 语义退役）。
渲染异常仅记 meta 不中断批；工件永远可生成（无证据/剔除渲染空节不报错）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from strategy_research.env import (
    _MOCK_CALL_COUNTS,
    LIVE_CALL_COUNTS,
    is_mock_mode,
)


def _strip_quote(symbol: str) -> str:
    """交易对 → 裸符号（BTWUSDT → BTW）：快照/对比 key 命名空间对齐。"""
    return symbol.removesuffix("USDT")


def _write_json(path: Path, obj: dict) -> None:
    """统一落盘：JSON 序列化写文件（ensure_ascii=False + indent=2）。"""
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_dir() -> Path:
    """reports/<ts>/ 运行目录 + reports/latest/ 软链目标（微秒级防同秒碰撞）。

    mock 落盘（SR_MOCK_REPORT=1）独立目录 reports/mock/<ts>/：与 live 隔离、
    不触碰 latest/（mock 产物不冒充真实运行）。
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ%f")
    root = Path("reports") / "mock" if is_mock_mode() else Path("reports")
    return root / ts


def _llm_calls() -> dict:
    """LLM 调用计数（成本统计）：mock 从假模型计数；live 从 callback 计数汇总。"""
    counts = dict(_MOCK_CALL_COUNTS) if is_mock_mode() else dict(LIVE_CALL_COUNTS)
    counts["total"] = sum(v for k, v in counts.items() if k != "total")
    return counts


#: degraded 阈值：剔除率（剔除/(通过+剔除)）超过此值 → degraded（用户确认 50%）
_DEGRADED_REJECT_RATIO = 0.5


def _compute_status(run_meta: dict, state: dict) -> tuple[str, str]:
    """运行健康度：failed（LLM 全挂）/ degraded（部分异常或高剔除率）/ ok。

    判定顺序：llm_calls.total == 0 → failed（API key 缺失等全挂，对应 8/25 首份）；
    否则任一分支异常 / incomplete_tokens 非空 / 剔除率超阈值 → degraded；其余 ok。
    返回 (status, reason)；ok 时 reason 为空串（不写噪音）。
    """
    if (run_meta.get("llm_calls") or {}).get("total", 0) == 0:
        return "failed", "LLM 调用数为 0（API key 缺失或模型全挂）"
    reasons: list[str] = []
    if run_meta.get("bull_errors") or run_meta.get("bear_errors"):
        reasons.append("分支异常")
    if run_meta.get("incomplete_tokens"):
        reasons.append("数据不完整: " + ", ".join(run_meta["incomplete_tokens"]))
    evidence = state.get("evidence") or {}
    passed = 0
    for s in state.get("tokens") or []:
        ev = evidence.get(s) or {}
        passed += len(ev.get("bull_case") or []) + len(ev.get("bear_case") or [])
    rejected = sum(
        len(r) for r in (state.get("rejected_evidence") or {}).values()
    )
    total = passed + rejected
    if total and rejected / total > _DEGRADED_REJECT_RATIO:
        reasons.append(f"剔除率 {rejected}/{total} 超阈值")
    if reasons:
        return "degraded", "；".join(reasons)
    return "ok", ""


def build_report(state: dict, meta: dict) -> tuple[Path | None, dict]:
    """落盘 run.json + evidence.md + candidates.json + snapshot/signal_diff。

    04 票：run.json = meta + 数据快照投影 + 证据清单 + 信号快照（spec D8）；
    candidates 仅候选列表（分级退役）；evidence.md 替代 overview.md（spec D7）。
    返回 (报告目录, artifacts)；mock 默认不落盘（SR_MOCK_REPORT=1 才写）——回归
    跑批不污染 reports/，report_path 置 None。工件/快照异常仅记 meta.report_error，
    不拖累已落盘的 run.json/evidence.md。
    """
    # mock 默认不落盘：仅返回 artifacts 与 meta（report_path=None）；
    # llm_calls 仍同步进 meta（成本统计不随落盘开关丢失）
    meta["llm_calls"] = _llm_calls()
    if is_mock_mode() and os.environ.get("SR_MOCK_REPORT") != "1":
        return None, _build_artifacts(state, "mock")

    run_dir = _run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)

    mode = "mock" if is_mock_mode() else "live"
    # candidates 的 mode 三态（auto/manual/mock）：screening.mode 缺失按 manual 兜底
    cand_mode = "mock" if is_mock_mode() else (meta.get("screening") or {}).get(
        "mode", "manual"
    )
    run_ts = datetime.now(timezone.utc).isoformat()

    run_meta: dict = {
        "mode": mode,
        "tokens": state["tokens"],
        "screening": meta.get("screening") or {"mode": "manual"},
        "run_ts": run_ts,
        "node_order": meta.get("node_order") or [],
        "llm_calls": meta["llm_calls"],
        "market_env": meta.get("market_env"),
    }
    # 分支异常留痕（06 票补漏：LLM 失败/空证据可诊断，不再黑盒）
    run_meta["bull_errors"] = state.get("bull_errors") or {}
    run_meta["bear_errors"] = state.get("bear_errors") or {}
    if meta.get("incomplete_tokens"):
        run_meta["incomplete_tokens"] = meta["incomplete_tokens"]
    if meta.get("incomplete_detail"):
        run_meta["incomplete_detail"] = meta["incomplete_detail"]
    # 运行健康度（全挂 failed / 部分异常或高剔除率 degraded / 其余 ok）
    run_meta["status"], run_meta["status_reason"] = _compute_status(run_meta, state)
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
        artifacts = _build_artifacts(state, cand_mode)
        _write_json(run_dir / "candidates.json", artifacts)
        _write_snapshot_and_diff(state, run_ts, mode, run_dir)
    except Exception as exc:  # 规格：快照/对比失败不中断批
        meta["report_error"] = f"工件/快照落盘失败: {exc}"

    (run_dir / "evidence.md").write_text(
        _render_evidence_md(state, run), encoding="utf-8"
    )

    # latest/ 软链仅在 live 更新：mock 产物（SR_MOCK_REPORT=1）不触碰 latest
    if not is_mock_mode():
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


def _build_artifacts(state: dict, mode: str) -> dict:
    """candidates 工件（04 票简化 + mode 标注）：候选列表 + 运行模式
    （auto/manual/mock——消除手动模式歧义，mode 缺失按 manual 兜底）。"""
    return {"mode": mode, "candidates": list(state["tokens"])}


# ── 04 票：信号快照 + 数据快照投影（确定性） ──────────────────


_SIGNAL_KEYS = (
    "momentum",
    "quadrant",
    "funding_pctile_90d",
    "oi_price_divergence",
    "liq_imbalance",
)
#: market_data 来源键（09 票）：横截面费率分位 + 交易结构（可执行性）；
#: 原 funding_z（4 主币 z 分）与 beta_7d/alpha_7d（n=7 噪声）退役
_MARKET_KEYS = (
    "funding_x_pctile",
    "funding_interval_hours",
    "funding_carry_7d_pct",
    "funding_carry_30d_pct",
    "spread_pct",
    "bid_depth_usd_2pct",
    "ask_depth_usd_2pct",
    "depth_band_state",
)
#: signals.market_metrics.value 来源键（08 票第一层派生，纯数值）
_METRIC_KEYS = (
    "rv_7d",
    "rv_30d",
    "drawdown_1y",
    "vol_adj_ret_7d",
    "vol_adj_ret_30d",
    "beta_30d",
    "alpha_30d",
    "turnover",
)
_TREND_KEYS = ("tvl_trend_30d", "fees_trend_30d", "stablecoin_change_30d")
#: social_data 来源键：数值档（可参与回看秩相关）+ 档名/分类标签（只进 diff）
_SOCIAL_KEYS = (
    "social_heat_trend",
    "social_heat_window",
    "post_frequency",
    "social_price_divergence",
)
_SNAPSHOT_KEYS = (
    *_SIGNAL_KEYS,
    *_MARKET_KEYS,
    *_METRIC_KEYS,
    *_TREND_KEYS,
    *_SOCIAL_KEYS,
)


def _scalar(value: Any) -> Any:
    """快照取值：数值/字符串原样，其余（None/dict/list）→ None。

    bool 单独排除——``isinstance(True, int)`` 为真，不过滤会把标记位写成数值。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    return value


def _num_or_none(value: Any) -> float | int | None:
    """快照取数值：非数值（含 bool/字符串）→ None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _signal_snapshot(symbol: str, state: dict) -> dict:
    """确定性信号快照（04 票）：quadrant/momentum/funding_pctile_90d/
    oi_price_divergence + 派生指标与交易结构 + 趋势特征（spec D8）。

    任一缺失 → None（UNKNOWN 纪律）；signals 层失败（error 条目）→ signals 派生键
    全 None，趋势/社交键仍从各自数据域直读（不受信号层牵连）。
    趋势特征值：tvl/fees 为趋势分类（rising/flat/falling），stablecoin 为变化 %。
    键来源（09 票重组）：``_MARKET_KEYS`` 全部从 market 数据点直读（含字符串档
    depth_band_state）；``_METRIC_KEYS`` 从 signals.market_metrics.value 直读。
    原 funding_z（4 主币 z 分）与 beta_7d/alpha_7d（n=7 噪声）退役。
    社交档（``_SOCIAL_KEYS``）从 social_data 直读，与 signals 是否成功无关：
    热度趋势/发帖频率取数值（可参与回看秩相关），样本档名与社交/价格背离取
    字符串标签（复合值只取 label，同 oi_price_divergence），只进 diff 不进相关。
    """
    sig = (state.get("signals") or {}).get(symbol) or {}
    out: dict[str, Any] = {k: None for k in _SNAPSHOT_KEYS}
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
        for k in _MARKET_KEYS:
            out[k] = _scalar((mkt.get(k) or {}).get("value"))
        mm = (sig.get("market_metrics") or {}).get("value") or {}
        for k in _METRIC_KEYS:
            out[k] = _num_or_none(mm.get(k))
    fund = (state.get("fundamental_data") or {}).get(symbol) or {}
    for k in _TREND_KEYS:
        out[k] = _scalar((fund.get(k) or {}).get("value"))
    soc = (state.get("social_data") or {}).get(symbol) or {}
    out["social_heat_trend"] = _num_or_none(
        (soc.get("social_heat_trend") or {}).get("value")
    )
    out["post_frequency"] = _num_or_none((soc.get("post_frequency") or {}).get("value"))
    window = (soc.get("social_heat_window") or {}).get("value")
    out["social_heat_window"] = _scalar(window)
    div = (soc.get("social_price_divergence") or {}).get("value")
    out["social_price_divergence"] = div.get("label") if isinstance(div, dict) else None
    return out


def _build_data_snapshot(state: dict) -> dict:
    """数据快照投影（spec D8）：per-token 各数据域轻量投影（证据可复核的原始数据）。"""
    snap: dict[str, dict] = {}
    for s in state["tokens"]:
        snap[s] = {
            "signals": (state.get("signals") or {}).get(s) or {},
            "market_data": (state.get("market_data") or {}).get(s) or {},
            "fundamental_data": (state.get("fundamental_data") or {}).get(s) or {},
            "microstructure_data": (state.get("microstructure_data") or {}).get(s)
            or {},
            "web_data": (state.get("web_data") or {}).get(s) or {},
            "social_data": (state.get("social_data") or {}).get(s) or {},
        }
    return snap


def _build_snapshot(state: dict, run_ts: str, mode: str) -> dict:
    """当前批信号快照（spec D8）：确定性信号投影 per-token，无 decision 语义。

    signals 键用裸符号（_strip_quote）：手动输入（CYS/AKE）与自动前缀
    （BTWUSDT）在对比层对齐，跨批 diff 的 key 一致才可复核。tokens 列表保持
    原样（记录输入形态，不作归一）。
    """
    return {
        "run_ts": run_ts,
        "mode": mode,
        "tokens": state["tokens"],
        "signals": {
            _strip_quote(s): _signal_snapshot(s, state) for s in state["tokens"]
        },
    }


def _read_prev_snapshot(tokens: list[str]) -> dict | None:
    """跨报告目录找 prev：遍历 reports/2*/snapshot.json（live 归档），取 run_ts
    最新且与当前 tokens（裸符号）有交集的一份。

    替代「仅比上一次运行」：latest 可能被无关批（手动/自动交替）覆盖，交集
    保证对比对象与当前批同标的可比；无交集或全部损坏 → None（首次运行语义）。
    """
    want = {_strip_quote(t) for t in tokens}
    best: tuple[datetime, dict] | None = None
    for path in sorted(Path("reports").glob("2*/snapshot.json")):
        try:
            snap = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # 含 JSONDecodeError/UnicodeDecodeError
            continue
        have = {_strip_quote(t) for t in (snap.get("tokens") or [])}
        if not (want & have):
            continue
        try:
            ts = datetime.fromisoformat(snap["run_ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if best is None or ts > best[0]:
            best = (ts, snap)
    return best[1] if best else None


#: 信号物性阈值（09 票）：变化超过旧值绝对值的 1% 才算「不同」
_DIFF_REL_TOL = 0.01

#: 近零兜底：|Δ| 小于此值一律视为未变（避免 1e-18 级浮点残差被 1% 阈值放大）
_DIFF_ABS_FLOOR = 1e-9


def _field_changed(prev: Any, cur: Any) -> bool:
    """单字段物性判定：浮点噪音不算变化，跨零与数据可用性变化算。

    旧实现用 ``prev == cur`` 裸比较，两次相隔 15 分钟的真实运行把 5/5 标的
    全判为 changed（实测最大相对变化 <0.1%）——stop_long/stop_short 退役后
    这条链是系统唯一的失效机制，饱和在噪音上等于没有失效机制。
    规则：任一侧缺失 ↔ 有值 = 数据可用性事件（物性）；数值跨零（符号翻转）
    = 物性（α 由负转正不是噪音）；其余按相对阈值。
    """
    if prev is None or cur is None:
        return prev != cur
    numeric = (
        not isinstance(prev, bool)
        and not isinstance(cur, bool)
        and isinstance(prev, (int, float))
        and isinstance(cur, (int, float))
    )
    if not numeric:
        return prev != cur
    if prev == cur:
        return False
    if prev * cur < 0:
        return True
    scale = max(abs(prev), abs(cur))
    return abs(cur - prev) > max(_DIFF_ABS_FLOOR, _DIFF_REL_TOL * scale)


def _build_signal_diff(prev: dict | None, cur: dict) -> dict:
    """跨运行信号对比：action = new / changed / unchanged + 物性变化字段清单。

    规则：prev 缺失 → new；全部字段物性一致 → unchanged；任一字段越过阈值 →
    changed，并在 ``changed_fields`` 列出具体键（只说 changed 无法复核）。
    旧 decision 型 stop_short/stop_long 失效语义退役（spec D8）。
    """
    prev_map = (prev or {}).get("signals") or {}
    cur_map = (cur or {}).get("signals") or {}
    diff: dict[str, dict] = {}
    for symbol, cur_sig in cur_map.items():
        p = prev_map.get(symbol)
        if p is None:
            diff[symbol] = {"prev": None, "cur": cur_sig, "changed_fields": []}
            diff[symbol]["action"] = "new"
            continue
        fields = sorted(set(p) | set(cur_sig))
        changed = [k for k in fields if _field_changed(p.get(k), cur_sig.get(k))]
        diff[symbol] = {
            "prev": p,
            "cur": cur_sig,
            "action": "unchanged" if not changed else "changed",
            "changed_fields": changed,
        }
    return diff


def _write_snapshot_and_diff(
    state: dict, run_ts: str, mode: str, run_dir: Path | None = None
) -> dict:
    """信号快照对比落盘：run_dir/ 内归档 snapshot.json + signal_diff.json（历史可回溯）；
    live 模式另更新 reports/latest/（先读旧为 prev 再覆盖），mock 不触碰 latest。"""
    snapshot = _build_snapshot(state, run_ts, mode)
    prev = _read_prev_snapshot(state["tokens"])
    diff = _build_signal_diff(prev, snapshot)
    if run_dir is not None:
        _write_json(run_dir / "snapshot.json", snapshot)
        _write_json(run_dir / "signal_diff.json", diff)
    if not is_mock_mode():
        latest = Path("reports") / "latest"
        latest.mkdir(parents=True, exist_ok=True)
        _write_json(latest / "snapshot.json", snapshot)
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
    errors: dict | None = None,
) -> list[str]:
    """单 token 证据节：### 做多证据 / ### 做空证据 两张表（# | claim | basis | source）。

    errors：{side: 错误消息}（LLM 失败/空证据留痕，非空时节头标注，不再静默 0 证据）。
    """
    lines = [f"## {symbol}", ""]
    if errors:
        for side, msg in errors.items():
            if msg:
                label = "做多" if side == "bull" else "做空"
                lines += [f"- {label}分支异常：{msg}", ""]
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
    """剔除记录附录：| token | claim | 原因 | 条数 |（按条数降序）。

    09 票聚合：实测一次运行 172 条剔除里 168 条是同一句 claim 撞同一条原因
    （来自一个被 max_tokens 截断后恢复了 95 条的分支），逐条罗列把报告里最
    有诊断价值的章节埋成噪音。按 (token, 原因, 归一化 claim) 计数后，同一
    失败模式一眼可见，重复次数本身成了严重度信号。
    """
    lines = ["## 剔除记录", ""]
    counts: dict[tuple[str, str, str], int] = {}
    for s in tokens:
        for r in rejected.get(s) or []:
            key = (s, r.get("reason", ""), "".join((r.get("claim") or "").split()))
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        lines.append("（本批无剔除记录）")
        lines.append("")
        return lines
    rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
    total = sum(counts.values())
    lines.append(f"共 {total} 条剔除，聚合为 {len(counts)} 种（条数=同一失败模式重复次数）。")
    lines.append("")
    lines.append("| token | claim | 原因 | 条数 |")
    lines.append("|---|---|---|---|")
    for (s, reason, claim), n in rows:
        lines.append(f"| {s} | {claim} | {reason} | {n} |")
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
        f"- 时间（UTC）：{meta['run_ts']}",
    ]
    # 本地时间（含时区偏移，如 +08:00）：人工读报告时对照自己的时区
    local_now = datetime.now().astimezone()
    offset = local_now.strftime("%z")
    offset = f"{offset[:3]}:{offset[3:]}" if len(offset) == 5 else offset
    lines.append(
        f"- 本地时间：{local_now.strftime('%Y-%m-%d %H:%M:%S')} {offset}"
    )
    lines += [
        f"- tokens：{', '.join(state['tokens'])}",
        f"- LLM 调用：{meta.get('llm_calls', {}).get('total', 0)}",
    ]
    status = meta.get("status", "ok")
    status_line = f"- 运行状态：{status}"
    if status in ("degraded", "failed") and meta.get("status_reason"):
        status_line += f"（{meta['status_reason']}）"
    lines += [
        status_line,
        "",
        "## 总览",
        "",
        "| token | 最新价格 | 信号缺失 | 做多通过 | 做空通过 | 剔除 | 数据域覆盖 |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in state["tokens"]:
        ev = evidence.get(s) or {}
        bull = ev.get("bull_case") or []
        bear = ev.get("bear_case") or []
        domains = ", ".join(_domains(bull + bear)) or "—"
        price = ((state.get("market_data") or {}).get(s) or {}).get("price") or {}
        # 信号缺失列：确定性信号投影中 None 键计数（数据可用性诊断）
        missing = sum(1 for v in _signal_snapshot(s, state).values() if v is None)
        lines.append(
            f"| {s} | {_price_text(price.get('value'))} | {missing} | {len(bull)} | {len(bear)} "
            f"| {len(rejected.get(s) or [])} | {domains} |"
        )
    lines += [
        "",
        (
            "> 做多/做空两列是两条分支各自的**通过核验条数**，不是方向强度：两分支"
            "被对称地要求「有多少写多少」，条数差只反映该标的哪一侧可引用事实更多，"
            "不构成多空结论。剔除列反映该标的证据的可信度损耗。"
        ),
    ]
    lines.append("")
    for s in state["tokens"]:
        lines += _evidence_section_lines(
            s,
            evidence.get(s) or {},
            branch_errors.get(s),
        )
    lines += _rejected_lines(rejected, state["tokens"])
    return "\n".join(lines)
