# CryptoResearch

加密资产策略研究 Agent：一条 6 节点并行分支 LangGraph 管线——确定性信号层 → 多空证据双分支并行采证（bull/bear 互不可见）→ 确定性证据核验 → 证据工件落盘，最终产出可验证的证据陈列报告工件。

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
| `SR_SCAN_AUTO` | 可选 | `0` = 关闭扫描器快照陈旧自动补跑（默认开） |
| `SR_SCAN_DIR` | 可选 | 扫描器快照目录覆盖（默认 `~/Projects/python_projects/BinanceApi/data/research`） |
| `SR_SCAN_DATE` | 可选 | 强制指定快照日期（默认取目录内最新） |

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

### 7. 扫描器快照（自动补跑，无需手动操作）

报告中的**榜单/微观结构数据**（boards、多窗口涨跌幅、OI 变化、多空比、taker 比、funding 趋势）来自 BinanceApi 项目的每日扫描器产出 CSV（`data/research/{date}_all/movers/microstructure.csv`），本 agent 只读消费。

**新鲜度由 main 自动保证**：非 mock 模式下，`main` 启动时检查快照日期——若距今天超过 1 天（陈旧），自动以子进程调用 BinanceApi 的 `research/scan.py` 补跑（全市场约 15 分钟），产出当日快照后再继续运行。**你不需要手动跑扫描器**，也不需要任何额外步骤：

```bash
python -m strategy_research.main   # 陈旧 → 自动补跑 → 用当日快照继续
```

- 补跑状态写入 `run.json.meta.scanner`（`fresh` / `refreshed` / `failed` / `unavailable`），失败不阻断运行（沿用旧快照 + 留痕）
- mock 模式不触发补跑（离线纪律）；`SR_SCAN_AUTO=0` 可关闭
- 快照日期会渲染在 `evidence.md` 每币标题下，可随时核对数据新鲜度

## 管线概览

```
screener（图外入口，确定性筛选）
   ↓ tokens
① collect_data      确定性：行情/估值/微观结构快照（含 funding 分位、OI 价格背离）
② compute_signals   确定性：估值/动量/背离 + sentiment 拥挤度纯函数
bull_research       多头证据研究员（json_mode 单次调用，数量不设上限）
bear_research       空头证据研究员（json_mode 单次调用，数量不设上限）
③ evidence_verify   确定性核验：basis 逐级解引用，剔除留痕
④ write_report      工件落盘
```

两分支并行执行（fan-out/fan-in），同消费同一份冻结快照、互不可见——同一数据可被两分支引用为相反证据，分歧点并列呈现。系统不产出任何方向性结论，由使用者依据证据自行裁决。

信号失效机制：**无价格锚点、无有效期**——由周期性重跑 + 跨运行信号快照对比实现（信号反转时 `action` 由 `unchanged` 变 `changed`）。

## 输出工件

每次运行写入 `reports/<时间戳>/`，`reports/latest/` 软链指向最近一次：

| 文件 | 内容 |
| --- | --- |
| `evidence.md` | 证据陈列文档（总览表 + 每 token 做多/做空证据表 + 剔除记录附录） |
| `run.json` | 运行元数据（模式/规则/LLM 成本）+ 证据清单 + 数据快照投影 + 信号快照 |
| `candidates.json` | 候选币种列表（仅候选，分级退役） |
| `snapshot.json` | 信号快照（quadrant/momentum/funding_pctile_90d/oi_price_divergence/趋势特征） |
| `signal_diff.json` | 与上次运行的信号对比（action ∈ new/changed/unchanged） |

## 目录结构

```
strategy_research/
├── main.py             # CLI 入口：tokens 解析 → 建图 → invoke
├── graph.py            # 6 节点装配 + 编译冒烟
├── nodes.py            # 各节点实现（数据装配/信号/分支/核验/报告）
├── signals.py          # 确定性信号纯函数（无 IO，缺失 → None）
├── screener.py         # 币种筛选规则引擎（Filter AND → Rank Top N）
├── scanner_snapshot.py # 扫描器快照读取 + 陈旧自动补跑（子进程）
├── schemas.py          # 宽容 JSON 解析器（_extract_json 系列）
├── evidence.py         # 证据体系（EvidenceItem + 确定性核验）
├── context.py          # 分支 prompt + 摘要构建器 + 情绪解读注记
├── report.py           # 报告落盘与工件生成
├── state.py            # LangGraph state 定义
├── env.py              # LLM 装配（DeepSeek / mock）
└── datasources/        # 数据源装配层（binance / binance_futures / defillama / web / mock）
```

## 相关文档

- `AgentArchitecture_Combined.md` — 完整架构规格（节点设计、数据契约、关键决策）
- `CONTEXT.md` — 领域词汇表
- `AGENTS.md` — Agent 协作约定（issue tracker、规范源）
