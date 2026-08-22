"""05 票验收：退役符号零死引用 + review.py/tools.py 退役 + master spec 回写。

验收 1 自动化：strategy_research/ 全部 .py 文件对退役符号（旧节点函数 /
旧 schema / 旧 prompt / tools 注册表 / review 校准 / state 旧字段 / env
mock 决策映射）检索无命中。tests/ 的旧引用由各单测文件跑绿兜底（引用即
ImportError/AttributeError 红）。
"""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "strategy_research"
ROOT = Path(__file__).resolve().parent.parent

#: 退役符号（大小写敏感字面量；decide/challenge/finalize 子串覆盖字段变体）
RETIRED = [
    # 旧节点函数（05 票退役清单：research_facts / decide / challenge /
    # finalize / risk_check 及其私有辅助）
    "research_facts",
    "_collect_facts",
    "_invoke_analysis",
    "decide",
    "_invoke_challenge",
    "challenge",
    "_invoke_rebuttals",
    "finalize",
    "_ev_flags",
    "_signal_state",
    "risk_check",
    "create_agent",
    "AGENT_RECURSION_LIMIT",
    # 旧 schema（TokenAnalysis 等 decision 体系）
    "TokenAnalysis",
    "FactItem",
    "ChallengeItem",
    "RebuttalItem",
    # 旧 prompt（FACTS / CHALLENGE / FINALIZE）
    "FACTS_PROMPT",
    "DECIDE_PROMPT",
    "CHALLENGE_PROMPT",
    "FINALIZE_PROMPT",
    "ANALYZE_PROMPT",
    # tools.py 历史序列工具
    "FACTS_TOOLS",
    "CHALLENGE_TOOLS",
    "get_tvl_history",
    "get_fees_history",
    "get_funding_history",
    "get_stablecoin_history",
    # review 校准
    "review_mod",
    "review_past_decisions",
    "render_calibration_context",
    "load_records",
    "calibrat",  # calibrate / calibration_context 同族
    "decision_review",
    "review_error",
    # env mock 决策映射（④⑤⑥ 固定响应）
    "_MOCK_DECISIONS",
    "_MOCK_FACTS_JSON",
    "_MOCK_CHALLENGES_JSON",
    "_MOCK_REBUTTALS_JSON",
    "_mock_decide_json",
]

#: state 旧字段键（引号包裹的 dict 键；docstring 中的解析器示例也须改）
STATE_KEYS = [
    "facts",
    "decisions",
    "challenges",
    "final_decisions",
    "risk_flags",
    "results",
]


def _py_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def test_no_retired_symbols() -> None:
    """验收 1：strategy_research/ 对退役符号检索无命中。"""
    for path in _py_files(SRC):
        text = path.read_text(encoding="utf-8")
        for sym in RETIRED:
            assert sym not in text, f"{path.name} 含退役符号 {sym}"
        for key in STATE_KEYS:
            assert f'"{key}"' not in text, f'{path.name} 含退役 state 字段 "{key}"'


def test_review_and_tools_retired() -> None:
    """验收 2：review.py（校准）与 tools.py（历史序列工具）文件已退役。"""
    assert not (SRC / "review.py").exists(), "review.py 应已退役"
    assert not (SRC / "tools.py").exists(), "tools.py 应已退役"


def test_master_spec_rewritten() -> None:
    """验收 4：master spec 已按 spec 回写——新拓扑/证据 schema/工件定义必现，
    旧决策链节点节（### ③ research_facts ~ ### ⑦ risk_check）已退役。"""
    spec = (ROOT / "AgentArchitecture_Combined.md").read_text(encoding="utf-8")
    for token in (
        "bull_research",
        "bear_research",
        "evidence_verify",
        "rejected_evidence",
        "signal_diff",
    ):
        assert token in spec, f"master spec 缺新契约 {token}"
    for old in (
        "### ③ research_facts",
        "### ④ decide",
        "### ⑤ challenge",
        "### ⑥ finalize",
        "### ⑦ risk_check",
    ):
        assert old not in spec, f"master spec 仍含旧节点节 {old}"
