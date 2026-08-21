"""01 票 RED：图结构 + SR_MOCK=1 端到端跑通。"""
from __future__ import annotations

import pytest

from strategy_research.graph import build_graph

NODE_ORDER = [
    "collect_data",
    "compute_signals",
    "research_facts",
    "decide",
    "challenge",
    "finalize",
    "risk_check",
    "write_report",
]


def test_graph_compiles_with_8_nodes_linear():
    """图编译成功，且节点数 = 8（规格：8 节点 9 条边全实线）。"""
    app = build_graph()
    nodes = list(app.get_graph().nodes)
    # langgraph 图包含 START/END 虚拟节点 + 8 个业务节点
    business = [n for n in nodes if n not in ("__start__", "__end__", "START", "END")]
    assert len(business) == 8


def test_invoke_runs_all_nodes_in_order():
    """SR_MOCK=1 下 invoke 依次执行 8 个节点（线性链验证）。"""
    app = build_graph()
    result = app.invoke({"tokens": ["BTC", "ETH"], "meta": {}})
    order = result["meta"].get("node_order", [])
    assert order == NODE_ORDER


def test_state_carries_tokens_and_meta():
    """state 基础字段：tokens 原样传入，meta 有 report_path。"""
    app = build_graph()
    result = app.invoke({"tokens": ["BTC"], "meta": {}})
    assert result["tokens"] == ["BTC"]
    assert result["meta"].get("report_path")
