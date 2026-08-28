# CryptoResearch

加密资产策略研究 Agent：一条 6 节点并行分支 LangGraph 管线——确定性信号层 → 多空证据双分支并行采证（bull/bear 互不可见）→ 确定性证据核验 → 证据工件落盘，最终产出可验证的证据陈列报告工件。

## 快速开始

```bash
# 1. 配置环境变量（.env 只需 DEEPSEEK_API_KEY 一行）
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY

# 2. 真实模式运行（自动筛选币种 → 全管线 → 报告落盘）
source /home/lhh/pythonprojects/.venv/bin/activate
python -m strategy_research.main

# 3. 全离线 mock 模式（零外部请求，无需任何 Key，回归验证用）
SR_MOCK=1 python -m strategy_research.main
```

## 环境要求与安装

- Python 3.13（本机虚拟环境 `/home/lhh/pythonprojects/.venv`，实测 3.13.5；包管理用 `uv pip`）
- 依赖：`langgraph` / `langchain` / `langchain-deepseek`、`pydantic`、Binance 官方 SDK（`binance-sdk-spot` / `binance-sdk-derivatives-trading-usds-futures` / `binance-common`）、`httpx`、`python-dotenv`；开发依赖 `pytest` / `ruff`

## 配置（.env）

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 真实模式必填 | DeepSeek API Key；mock 模式不需要 |
| `SR_MOCK` | 可选 | `1` = 全离线 mock 模式（零外部请求） |
| `SR_TOKENS` | 可选 | 手动指定币种（逗号分隔），与 `--tokens` 互斥 |
| `DEEPSEEK_MODEL` | 可选 | 默认 `deepseek-chat`，可换 `deepseek-reasoner` 等 |
| `SR_MOCK_REPORT` | 可选 | mock 模式默认不落盘；`1` = 落盘到 `reports/mock/<ts>/`（不触碰 `latest/`） |
| `SR_KEEP_REPORTS` | 可选 | live 报告保留份数，默认 `30`；`0` = 不清理（`reports/mock/`、`latest/` 不受影响） |

数据源层（Binance / DefiLlama / OKX / X 公开主页 / 新闻 RSS）零环境变量，无需配置。

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
python -m strategy_research.main --top-n 20                # 候选数（默认 10）
python -m strategy_research.main --max-per-category 2      # 同一板块最多候选数（默认 3）
python -m strategy_research.main --max-per-category 0      # 关闭板块约束
```

板块约束是确定性的候选层规则：Top N 排序后按板块截断，同板块超额者让位给后续名次的
异板块候选（避免整批挤在同一叙事上）。板块标签来自 DefiLlama 协议索引，`0` 时不发起该
请求（约 6.7MB）；索引不可用时约束不生效并写入 `meta.screening.rules` 留痕，无板块标签
的候选不受罚（UNKNOWN 纪律）。候选的 `reason` 会附 `板块=xxx`。

### 5. 图编译冒烟

```bash
python -m strategy_research.graph   # 输出 "graph compiled OK"
```

### 6. 测试与 lint

```bash
python -m pytest                    # 全量单测（mock 模式，零外部请求）
python -m ruff check strategy_research/ tests/
```

两条常驻守卫值得知道：`tests/test_data_coverage.py` 是**数据覆盖契约**——遍历 mock
快照各数据域的有值字段，字段名必须出现在分支摘要里，否则必须落在显式隐藏白名单（附
理由）；新增数据抓了不喂，测试先红。mock/live 同构由交叉验证测试钉住（同一批纯函数、
逐值一致），因此 mock 回归不是玩具路径。

### 7. 「信号 vs 价格」回看（离线评估器，不进管线）

系统本身不产方向结论（ADR 0001），但**信号的预测力可以离线描述性地度量**：
`strategy_research/lookback.py` 只读历史 `reports/*/run.json` 里的信号快照，
用 `closed_daily_returns`（基准 = 决策时最近已收盘日线，前视只取其后的日 K）
计算每个信号字段在 N 日窗口上与前视收益（及减 BTC 后的超额收益）的 Spearman
秩相关和三等分分组收益差：

```bash
python -m strategy_research.lookback                                  # markdown 读数
python -m strategy_research.lookback --horizons 1,3,7,14 --json       # 结构化输出
python -m strategy_research.lookback --root reports                    # 历史目录（默认 reports）
```

- **只读、零 LLM、图外**：不写任何工件、不改管线，输出打到 stdout
- 只采信 `mode=live` 的 run；同日多次运行只取当日首次（否则 1 个观测被当成 2 个）
- 窗口未收盘的 (run, horizon) 不采（否则读数不可复现），丢弃项以 `[warn]` 列出
- 值为 None 或分类标签（`quadrant` / `label` / `depth_band_state`）的字段不参与秩相关
- 历史 run 里的退役字段（如 `funding_z`）不进表，单独列为「schema 漂移」警告

读数必须连同报告头部的四条边界一起看：选币自选择（只是候选池内相对）、窗口重叠
（ρ 不能按独立样本解读）、多重比较（同一批信号在多窗口×多字段上重复读取）、
以及**本表不含方向结论、置信度与仓位含义**。

## 管线概览

```
screener（图外入口，确定性筛选）
   ↓ tokens
① collect_data      确定性：行情/估值/微观结构/爆仓/社交（X）快照（含 funding 分位、OI 价格背离）
② compute_signals   确定性：估值/动量/背离/交易结构 + sentiment（持仓与社交指标原始直读）纯函数
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
| `snapshot.json` | 信号快照（当前信号集 28 键 per-token：quadrant/momentum、rv_7d/30d、drawdown_1y、turnover、vol_adj_ret、funding 分位与横截面分位、持仓成本三项、点差/双边深度/深度档位、爆仓失衡、OI 价格背离、α/β(30d)、趋势特征、社交热度趋势与样本档名、发帖频率、社交/价格背离） |
| `signal_diff.json` | 与上次运行的信号对比（action ∈ new/changed/unchanged） |

## 目录结构

```
strategy_research/
├── main.py             # CLI 入口：tokens 解析 → 建图 → invoke
├── graph.py            # 6 节点装配 + 编译冒烟
├── nodes.py            # 各节点实现（数据装配/信号/分支/核验/报告）
├── signals.py          # 确定性信号纯函数（无 IO，缺失 → None）
├── screener.py         # 币种筛选规则引擎（Filter AND → Rank Top N → 板块上限）
├── schemas.py          # 宽容 JSON 解析器（_extract_json 系列）
├── evidence.py         # 证据体系（EvidenceItem + 确定性核验）
├── context.py          # 分支 prompt + 摘要构建器 + 情绪解读注记
├── report.py           # 报告落盘与工件生成
├── lookback.py         # 图外只读工具：「信号 vs 价格」回看评估（离线、零 LLM、不写工件）
├── state.py            # LangGraph state 定义
├── env.py              # LLM 装配（DeepSeek / mock）
└── datasources/        # 数据源装配层（binance / binance_futures / defillama / okx / x_social / web / mock）
```

## 相关文档

- `AgentArchitecture_Combined.md` — 完整架构规格（节点设计、数据契约、关键决策）
- `CONTEXT.md` — 领域词汇表
- `AGENTS.md` — Agent 协作约定（issue tracker、规范源）
