# 01 — 骨架与占位管线

**What to build:** 从空项目出发，一条 `SR_MOCK=1` 命令跑通 START→①…⑧→END 的占位全链路：8 个节点全部有可执行占位实现（线性装配、9 条实线边），产出 run.json 与 overview 骨架。tokens 来自 mock 固定候选或 `--tokens`。同时验证 LangGraph 与 Python 3.14 兼容性（不兼容则建 3.12 venv 兜底）。

**Blocked by:** None — can start immediately

**Status:** done（2026-08-21 实施完成）

- [x] 项目骨架：`__init__.py`、`main.py` 入口、`.env.example`、依赖清单（langgraph / langchain / httpx / binance-sdk-spot / binance-sdk-derivatives-trading-usds-futures / binance-common / pydantic / pytest），uv pip 安装进 `.venv`（均已装：langgraph 1.2.10 / langchain 1.3.14 / langchain-openai 1.4.1 / pytest 9.1.1 等）
- [x] `python -m strategy_research.graph` 占位可运行（编译通过）
- [x] 8 节点占位实现 + 图装配：9 条边全实线，无条件路由 / Command / interrupt / checkpointer（规格十节纪律 1）
- [x] `SR_MOCK=1` 模式一条命令跑通全图（8 节点依次执行），产出 run.json + overview 骨架（reports/latest/ 软链已验证）
- [x] LangGraph × Python 3.14 兼容性验证有结论（冒烟通过：StateGraph/START/END/compile/invoke 全部正常，无需降级 3.12）
