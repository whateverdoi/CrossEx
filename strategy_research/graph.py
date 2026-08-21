"""图组装：START → ①→②→③→④→⑤→⑥→⑦→⑧ → END，9 条边全实线。

无条件路由 / Command / interrupt / checkpointer；行为分化全部在节点内部。
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from strategy_research import nodes
from strategy_research.state import State


def build_graph():
    """装配 8 节点线性图并编译（规格：9 条边全实线直连）。"""
    g = StateGraph(State)
    g.add_node("collect_data", nodes.collect_data)
    g.add_node("compute_signals", nodes.compute_signals)
    g.add_node("research_facts", nodes.research_facts)
    g.add_node("decide", nodes.decide)
    g.add_node("challenge", nodes.challenge)
    g.add_node("finalize", nodes.finalize)
    g.add_node("risk_check", nodes.risk_check)
    g.add_node("write_report", nodes.write_report)

    g.add_edge(START, "collect_data")
    g.add_edge("collect_data", "compute_signals")
    g.add_edge("compute_signals", "research_facts")
    g.add_edge("research_facts", "decide")
    g.add_edge("decide", "challenge")
    g.add_edge("challenge", "finalize")
    g.add_edge("finalize", "risk_check")
    g.add_edge("risk_check", "write_report")
    g.add_edge("write_report", END)
    return g.compile()


if __name__ == "__main__":
    # 编译冒烟：python -m strategy_research.graph
    build_graph()
    print("graph compiled OK")
