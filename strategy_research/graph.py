"""图组装（03 票）：START → ① → ② → [bull_research ‖ bear_research] → evidence_verify → ④ → END。

两分支为并行边（fan-out/fan-in，无 reducer）：同消费冻结快照、各写各的字段
（bull_evidence / bear_evidence），后写覆盖语义不变；无条件路由 / Command /
interrupt / checkpointer；行为分化全部在节点内部。旧决策链节点已随 05 票
退役（符号零引用），图仅剩证据分支链。
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from strategy_research import nodes
from strategy_research.state import State


def build_graph():
    """装配证据分支拓扑并编译（03 票：两分支并行，8 条边全实线）。"""
    g = StateGraph(State)
    g.add_node("collect_data", nodes.collect_data)
    g.add_node("compute_signals", nodes.compute_signals)
    g.add_node("bull_research", nodes.bull_research)
    g.add_node("bear_research", nodes.bear_research)
    g.add_node("evidence_verify", nodes.evidence_verify)
    g.add_node("write_report", nodes.write_report)

    g.add_edge(START, "collect_data")
    g.add_edge("collect_data", "compute_signals")
    g.add_edge("compute_signals", "bull_research")
    g.add_edge("compute_signals", "bear_research")
    g.add_edge("bull_research", "evidence_verify")
    g.add_edge("bear_research", "evidence_verify")
    g.add_edge("evidence_verify", "write_report")
    g.add_edge("write_report", END)
    return g.compile()


if __name__ == "__main__":
    # 编译冒烟：python -m strategy_research.graph
    build_graph()
    print("graph compiled OK")
