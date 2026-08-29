"""②③④ 确定性收尾节点：compute_signals / evidence_verify / write_report。"""

from __future__ import annotations

from typing import Any

from strategy_research import env
from strategy_research import evidence as ev_mod
from strategy_research import signals as sig_mod
from strategy_research.datasources import mock

from .collect import _meta


def compute_signals(state: dict) -> dict:
    """② 信号计算（确定性）：估值/动量/背离/sentiment_raw（05 票）。

    mock 模式走 mock.mock_signals_data（同构字段，kind 分支与真实一致）；
    真实模式 per-token 调 4 个纯函数；单 token 异常置 {symbol, error} 不阻断。
    """
    meta, order = _meta(state)
    order.append("compute_signals")
    meta["node_order"] = order
    signals_out: dict[str, Any] = {}
    for symbol in state["tokens"]:
        try:
            if env.is_mock_mode():
                kind = (state.get("fundamental_data", {}).get(symbol) or {}).get("kind")
                signals_out[symbol] = mock.mock_signals_data(symbol, kind)
            else:
                fund = state.get("fundamental_data", {}).get(symbol)
                mkt = state.get("market_data", {}).get(symbol)
                ms = state.get("microstructure_data", {}).get(symbol)
                soc = state.get("social_data", {}).get(symbol)
                signals_out[symbol] = {
                    "symbol": symbol,
                    "valuation": sig_mod.valuation_ratios(fund, mkt),
                    "momentum": sig_mod.momentum_score(fund),
                    "divergence": sig_mod.divergence(fund, mkt),
                    "sentiment": sig_mod.sentiment_raw(mkt, ms, soc),
                    "market_metrics": sig_mod.market_metrics(fund, mkt),
                    "error": None,
                }
        except Exception as exc:  # 单 token 异常不阻断（规格 ②）
            signals_out[symbol] = {"symbol": symbol, "error": f"信号计算异常: {exc}"}
    return {"signals": signals_out, "meta": meta}


def evidence_verify(state: dict) -> dict:
    """证据核验（03 票，确定性）：两分支产出合并核验 → evidence + rejected_evidence。

    纯函数无 IO（evidence.verify_evidence）：basis 逐级解引用存在且值一致 →
    通过；否则剔除留痕。串行节点补记分支 node_order（并行分支不写共享 meta，
    顺序即图定义顺序）。
    """
    meta, order = _meta(state)
    order += ["bull_research", "bear_research", "evidence_verify"]
    meta["node_order"] = order
    verified, rejected = ev_mod.verify_evidence(
        state.get("bull_evidence"), state.get("bear_evidence"), state
    )
    return {"evidence": verified, "rejected_evidence": rejected, "meta": meta}


def write_report(state: dict) -> dict:
    """④ 报告落盘：evidence.md + run.json + candidates.json + snapshot/diff（04 票）。"""
    from strategy_research.report import build_report

    meta, order = _meta(state)
    order.append("write_report")
    meta["node_order"] = order
    try:
        report_path, artifacts = build_report(state, meta)
        # mock 默认不落盘（SR_MOCK_REPORT=1 才写）→ report_path 为 None
        meta["report_path"] = str(report_path) if report_path else None
    except Exception as exc:  # 规格：落盘异常仅记 meta，不中断批（六节错误矩阵 ④）
        artifacts = {s: {} for s in state["tokens"]}
        meta["report_error"] = f"报告落盘失败: {exc}"
    return {
        "meta": meta,
        "research_artifacts": artifacts,
    }
