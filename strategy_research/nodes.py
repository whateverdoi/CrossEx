"""图节点：8 个节点，线性装配；条件全在节点内部；批处理永不中断。

01 票骨架：占位实现——mock 模式最小可用（写空结构 + 后写覆盖），
完整逻辑由后续票实现（①=04 / ②=05 / ③-⑥=07 / ⑦=08 / ⑧=09-10）。
"""

from __future__ import annotations

from typing import Any


def _meta(state: dict) -> dict[str, Any]:
    """取 meta（不存在则初始化），并记录节点执行顺序。"""
    meta = dict(state.get("meta") or {})
    order = list(meta.get("node_order") or [])
    return meta, order


def collect_data(state: dict) -> dict:
    """① 数据收集（确定性）：三快照 + 微观结构。04 票完整实现。"""
    meta, order = _meta(state)
    order.append("collect_data")
    meta["node_order"] = order
    tokens = state["tokens"]
    return {
        "market_data": {s: {} for s in tokens},
        "fundamental_data": {s: {} for s in tokens},
        "microstructure_data": {s: {} for s in tokens},
        "web_data": {s: {} for s in tokens},
        "meta": meta,
    }


def compute_signals(state: dict) -> dict:
    """② 信号计算（确定性）：估值/动量/背离/sentiment_raw。05 票完整实现。"""
    meta, order = _meta(state)
    order.append("compute_signals")
    meta["node_order"] = order
    return {"signals": {s: {"symbol": s, "error": "未实现（05 票）"} for s in state["tokens"]}, "meta": meta}


def research_facts(state: dict) -> dict:
    """③ 采证（LLM）：四分析师视角 facts。07 票完整实现。"""
    meta, order = _meta(state)
    order.append("research_facts")
    meta["node_order"] = order
    return {"facts": {s: [] for s in state["tokens"]}, "meta": meta}


def decide(state: dict) -> dict:
    """④ 决策（LLM）：json_mode 单次调用。07 票完整实现。"""
    meta, order = _meta(state)
    order.append("decide")
    meta["node_order"] = order
    return {"decisions": {s: {} for s in state["tokens"]}, "meta": meta}


def challenge(state: dict) -> dict:
    """⑤ 对抗（LLM）：PASS 透传，非 PASS 挖反方。07 票完整实现。"""
    meta, order = _meta(state)
    order.append("challenge")
    meta["node_order"] = order
    return {"challenges": {s: [] for s in state["tokens"]}, "meta": meta}


def finalize(state: dict) -> dict:
    """⑥ 复审（LLM）：逐条 rebutted/accepted，accepted 只降不升。07 票完整实现。"""
    meta, order = _meta(state)
    order.append("finalize")
    meta["node_order"] = order
    return {
        "final_decisions": {
            s: {"analysis": state.get("decisions", {}).get(s) or {}, "rebuttals": []}
            for s in state["tokens"]
        },
        "meta": meta,
    }


def risk_check(state: dict) -> dict:
    """⑦ 风控终审（确定性）：EV 边界 + 集中度两条核验，只降不升。08 票完整实现。"""
    meta, order = _meta(state)
    order.append("risk_check")
    meta["node_order"] = order
    tokens = state["tokens"]
    finals = state.get("final_decisions", {})
    return {
        "risk_flags": {s: [] for s in tokens},
        "results": [
            {**((finals.get(s) or {}).get("analysis") or {}),
             "rebuttals": (finals.get(s) or {}).get("rebuttals") or [],
             "risk_flags": []}
            for s in tokens
        ],
        "meta": meta,
    }


def write_report(state: dict) -> dict:
    """⑧ 报告落盘：overview.md + run.json（+ snapshot/diff 09-10 票）。"""
    from strategy_research.report import build_report

    meta, order = _meta(state)
    order.append("write_report")
    meta["node_order"] = order
    try:
        report_path = build_report(state, meta)
        meta["report_path"] = str(report_path)
    except Exception as exc:  # 规格：落盘异常仅记 meta，不中断批（六节错误矩阵 ⑧）
        meta["report_error"] = f"报告落盘失败: {exc}"
    return {"meta": meta, "research_artifacts": state.get("research_artifacts") or {s: {} for s in state["tokens"]}}
