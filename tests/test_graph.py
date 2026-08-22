"""01/03 票 RED：图结构 + SR_MOCK=1 端到端跑通（03 票切换证据分支拓扑）。

新拓扑：collect_data → compute_signals → [bull_research ‖ bear_research 并行]
→ evidence_verify → write_report；05 票：旧决策链节点已退役，图仅 6 节点。
"""

from __future__ import annotations

from strategy_research import env
from strategy_research.graph import build_graph

HEAD = ["collect_data", "compute_signals"]
TAIL = ["evidence_verify", "write_report"]
BRANCHES = {"bull_research", "bear_research"}


def test_graph_compiles_with_6_nodes():
    """图编译成功，且业务节点 = 6（03 票：两分支并行，旧决策链退役）。"""
    app = build_graph()
    nodes = list(app.get_graph().nodes)
    # langgraph 图包含 START/END 虚拟节点 + 6 个业务节点
    business = [n for n in nodes if n not in ("__start__", "__end__", "START", "END")]
    assert len(business) == 6


def test_invoke_runs_branch_topology(monkeypatch, tmp_path):
    """SR_MOCK=1 下 invoke：线性头尾 + 两分支并行（顺序不定），批不中断。

    互不可见验证：mock 分支固定 JSON 措辞无交集（若接线把同一产出复制给
    两分支，本断言即红）；核验合并后 bull_case/bear_case 各归其位。
    """
    monkeypatch.chdir(tmp_path)  # 落盘隔离：不写项目 reports/
    env.reset_call_counts()  # 计数归零：run.json 的 llm_calls = 本次运行
    result = build_graph().invoke({"tokens": ["BTC", "ETH"], "meta": {}})
    order = result["meta"].get("node_order", [])
    assert order[:2] == HEAD
    assert order[-2:] == TAIL
    assert BRANCHES == set(order[2:4])  # 两分支均执行（并行顺序不定）
    for s in ("BTC", "ETH"):
        bull_claims = {i["claim"] for i in result["bull_evidence"][s]}
        bear_claims = {i["claim"] for i in result["bear_evidence"][s]}
        assert bull_claims and bear_claims
        assert not (bull_claims & bear_claims)  # 分支隔离：无共享措辞
        assert result["evidence"][s]["bull_case"] and result["evidence"][s]["bear_case"]
        assert s not in result["rejected_evidence"]  # mock 恒定引用全通过


def test_state_carries_tokens_and_meta(monkeypatch, tmp_path):
    """state 基础字段：tokens 原样传入，meta 有 report_path。"""
    monkeypatch.chdir(tmp_path)  # 落盘隔离：不写项目 reports/
    env.reset_call_counts()
    app = build_graph()
    result = app.invoke({"tokens": ["BTC"], "meta": {}})
    assert result["tokens"] == ["BTC"]
    assert result["meta"].get("report_path")
