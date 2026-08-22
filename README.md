# CryptoResearch

加密资产策略研究 Agent：一条 8 节点线性 LangGraph 管线——确定性信号层 → 多分析师事实收集 → 对抗式审查（挑战/反驳）→ 确定性风控终审，最终产出可验证的决策报告工件。

## 快速开始

```bash
# 1. 配置环境变量（.env 只需 DEEPSEEK_API_KEY 一行）
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY

# 2. 真实模式运行（自动筛选币种 → 全管线 → 报告落盘）
source /home/lhh/Projects/python_projects/.venv/bin/activate
python -m strategy_research.main

# 3. 全离线 mock 模式（零外部请求，无需任何 Key，回归验证用）
SR_MOCK=1 python -m strategy_research.main
```

## 环境要求与安装

- Python 3.14（本机虚拟环境 `/home/lhh/Projects/python_projects/.venv`，包管理用 `uv pip`）
- 依赖：`langgraph` / `langchain` / `langchain-deepseek`、`pydantic`、Binance 官方 SDK（`binance-sdk-spot` / `binance-sdk-derivatives-trading-usds-futures` / `binance-common`）、`httpx`、`python-dotenv`；开发依赖 `pytest` / `ruff`

## 配置（.env）

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 真实模式必填 | DeepSeek API Key；mock 模式不需要 |
| `SR_MOCK` | 可选 | `1` = 全离线 mock 模式（零外部请求） |
| `SR_TOKENS` | 可选 | 手动指定币种（逗号分隔），与 `--tokens` 互斥 |
| `DEEPSEEK_MODEL` | 可选 | 默认 `deepseek-chat`，可换 `deepseek-reasoner` 等 |

数据源层（Binance / DefiLlama / 新闻 RSS）零环境变量，无需配置。

## 使用方式

### 1. 真实模式（默认）

自动筛选币种并跑完整管线：

```bash
python -m strategy_research.main
```

默认筛选规则（确定性、零 LLM）：次新（合约上线 ≤100 天）+ 流动性下限（24h 成交额 ≥1e7）+ 排除稳定币 → **错价榜** Top 10（24h 涨跌幅升序排名 + 成交额降序排名等权合成：价格弱 + 成交活跃优先）。

### 2. 手动指定币种（跳过筛选）

```bash
# 命令行参数
python -m strategy_research.main --tokens BTC,ETH

# 或环境变量（与 --tokens 互斥二选一）
SR_TOKENS=BTC,ETH python -m strategy_research.main
```

手动模式 `meta.screening.mode="manual"`，全管线（含微观结构数据）只处理指定币种。

### 3. Mock 离线模式

```bash
SR_MOCK=1 python -m strategy_research.main
```

- 零外部请求、无需任何 Key，数据由 `strategy_research/datasources/mock.py` 提供
- 与真实路径同构：同一批纯函数计算，交叉验证测试保证逐值一致
- 用于回归验证与开发调试

### 4. 筛选参数

```bash
python -m strategy_research.main --top-n 20   # 候选数（默认 10）
```

### 5. 图编译冒烟

```bash
python -m strategy_research.graph   # 输出 "graph compiled OK"
```

### 6. 测试与 lint

```bash
python -m pytest                    # 全量单测（mock 模式，零外部请求）
python -m ruff check strategy_research/ tests/
```

## 管线概览

```
screener（图外入口，确定性筛选）
   ↓ tokens
① collect_data      确定性：行情/估值/微观结构快照（含 funding 分位、OI 价格背离）
② compute_signals   确定性：估值/动量/背离 + sentiment 拥挤度纯函数
③ research_facts    多分析师：六维事实收集（TVL/费率/稳定币/搜索等）
④ decide            LLM 结构化决策（方向 + 置信度 + 交易结构）
⑤ challenge         对抗者：逐项挑战，LLM 反驳
⑥ finalize          整合裁决（TRADE / PASS / WAIT）
⑦ risk_check        确定性风控：EV 边界 + 组合集中度（只降不升）
⑧ write_report      工件落盘
```

决策失效机制：**无价格锚点、无有效期**——由周期性重跑 + 跨运行信号快照对比实现（重跑时 `stop_long`/`stop_short` 即信号反转失效）。

## 输出工件

每次运行写入 `reports/<时间戳>/`，`reports/latest/` 软链指向最近一次：

| 文件 | 内容 |
| --- | --- |
| `overview.md` | 人类可读报告（决策 + 依据 + 信号变化 + 币种筛选节） |
| `run.json` | 运行元数据（模式/规则/LLM 成本）+ 全部决策明细 |
| `candidates.json` | 候选币种及筛选指标 |
| `snapshot.json` | 决策快照（symbol/direction/confidence），下次运行前先读旧值 |
| `signal_diff.json` | 与上次运行的信号对比（new/hold/stop_short/stop_long） |

## 目录结构

```
strategy_research/
├── main.py             # CLI 入口：tokens 解析 → 建图 → invoke
├── graph.py            # 8 节点装配 + 编译冒烟
├── nodes.py            # 各节点实现（数据装配/信号/风控）
├── signals.py          # 确定性信号纯函数（无 IO，缺失 → None）
├── screener.py         # 币种筛选规则引擎（Filter AND → Rank Top N）
├── schemas.py          # LLM 结构化输出 schema + 4 份 prompt
├── tools.py            # react agent 工具注册表（facts / challenge）
├── review.py           # 风控核验（EV 边界 + 组合集中度）
├── report.py           # 报告落盘与工件生成
├── state.py            # LangGraph state 定义
├── env.py              # LLM 装配（DeepSeek / mock）
└── datasources/        # 数据源装配层（binance / binance_futures / defillama / web / mock）
```

## 相关文档

- `AgentArchitecture_Combined.md` — 完整架构规格（节点设计、数据契约、关键决策）
- `CONTEXT.md` — 领域词汇表
- `AGENTS.md` — Agent 协作约定（issue tracker、规范源）
