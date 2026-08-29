"""图节点：6 节点证据分支拓扑（03 票接线，05 票清理旧决策链）。

① collect_data（确定性数据收集）→ ② compute_signals（确定性信号）→
[bull_research ‖ bear_research]（LLM 证据分支，并行）→ evidence_verify
（确定性核验）→ ④ write_report（报告落盘）。批处理永不中断。

子模块划分：collect（① 数据装配）/ branches（LLM 多空分支与 JSON 恢复）/
wrapup（②③④ 信号·核验·落盘）。本包对外保持原单文件模块的属性面：
节点函数、测试引用的私有助手与数据源模块别名（monkeypatch 按
``strategy_research.nodes.<别名>`` 定位，别名为单例模块，打补丁全局生效）。
"""

from strategy_research import env
from strategy_research.datasources import (
    binance,
    binance_futures,
    defillama,
    okx,
    x_social,
)
from strategy_research.datasources import web as web_ds

from .branches import (
    _extract_partial_evidence,
    _truncation_probe,
    bear_research,
    bull_research,
)
from .collect import (
    _dp,
    _liquidation,
    _meta,
    collect_data,
)
from .wrapup import compute_signals, evidence_verify, write_report

__all__ = [
    # 测试引用的私有助手
    "_dp",
    "_extract_partial_evidence",
    "_liquidation",
    "_meta",
    "_truncation_probe",
    "bear_research",
    "binance",
    "binance_futures",
    "bull_research",
    # 节点函数（graph.py 装配面）
    "collect_data",
    "compute_signals",
    "defillama",
    # 数据源模块别名（test_collect.py patch 路径 + nodes.env monkeypatch）
    "env",
    "evidence_verify",
    "okx",
    "web_ds",
    "write_report",
    "x_social",
]
