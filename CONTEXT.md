# Strategy Research Agent

一个加密资产策略研究 Agent：确定性数据/信号层 + 多空证据双分支采证 + 确定性证据核验，产出可校验工件（多空证据分析报告 + 信号快照）。系统不产出方向性结论，由使用者依据证据自行裁决。

## Language

### 证据

**Evidence（证据）**:
bull/bear 分支产出的最小单元，三元组：claim（主张，自由文本）+ basis（依据，domain/field/value 结构化引用）+ source（数据域）。无置信度字段——LLM 自评置信度是主观臆想；证据强度由使用者依据 basis 自行判断。
_Avoid_: fact, signal（signals 是确定性计算层，勿混用）

**Bull Case / Bear Case（做多证据集 / 做空证据集）**:
每个 token 的两份并行产出：多头分支只找做多证据、空头分支只找做空证据；两分支同时消费同一份冻结数据快照，互不可见对方中间产物——避免相互锚定污染。
_Avoid_: thesis, recommendation

**Evidence Verification（证据核验）**:
确定性纯函数终审：逐条检查证据的 basis 引用是否真实存在于数据快照（domain/field 解引用 + value 一致性 + 列表下标必须是分支摘要里实际渲染过的），无效证据剔除并留痕。系统唯一的"机器强制"层。
_Avoid_: risk check

### 数据

**Coverage Contract（数据覆盖契约，「抓了必须喂」）**:
任何进入 per-token 快照的字段必须有消费者——或渲染进分支摘要供 LLM 引用，或进信号快照键集合（跨运行对比与回看的基础）。采集了却两处都不出现的字段即数据浪费：多付一次外部请求、零产出。该契约由 `tests/test_data_coverage.py` 机器强制：有值字段名必须在摘要里出现，否则必须落在带理由的显式隐藏白名单。
_Avoid_: best effort（"顺手抓一下"没有消费者即浪费）, feature flag

**Social Heat（社交热度）**:
项目方 X 主页互动强度的近期变化 %：近期侧均值 / 更早侧中位数 - 1（中位数抗单条爆款脉冲）。互动强度 = likes + reposts + comments，views 是触达不是互动、不计入。样本分档是读数的一部分——≥7 条用最近 5 vs 其余中位数、恰 6 条用 3 vs 3、不足 6 条不派生（UNKNOWN），档名（`social_heat_window`）与数值同存，**跨档不可比**。
_Avoid_: sentiment score, 舆情指数

### 信号

**Sentiment Signal（情绪信号）**:
持仓与社交指标的确定性汇总（funding、funding 趋势、全市场/大户多空比、taker 买卖比、OI 变化与 OI/价格背离、社交热度与社交/价格背离），以原始值直读呈现，不做阈值加减分——阈值化会丢失信息，且下游 LLM 本就按 prompt 规则直接解读原始值。
_Avoid_: sentiment score, crowding score

**Trend Feature（趋势特征）**:
历史序列（TVL/fees/stablecoin 供应）经确定性计算提炼的特征值（如 tvl_trend_30d、tvl_change_30d），随 fundamental_data 落盘供分支直读——LLM 只读数值，不转述曲线。
_Avoid_: history series

**Signal Snapshot（信号快照）**:
每次运行把确定性信号落盘 `reports/latest/snapshot.json`（每 token 键集合 = `report._SNAPSHOT_KEYS` 28 项：四象限/动量/波动率/回撤/换手/风险调整收益、funding 时间序列分位与横截面分位、持仓成本三项、点差与双边深度与深度档位、爆仓失衡、OI 价格背离、α/β(30d)、趋势特征、社交热度与样本档名、发帖频率、社交/价格背离），覆盖前先读旧快照为 prev——跨运行回看的基础，兑现"信号假设可证伪"。
_Avoid_: checkpoint, state dump

**Trading Structure（交易结构）**:
同一资产"此刻成交与持仓要付多少钱"的确定性度量：买卖点差、±2% 双边深度、funding 持仓成本（7d/30d 年化）与结算间隔。与价格维度正交——同一个信号在厚市场与薄市场里的可操作性完全不同。
_Avoid_: liquidity score, market microstructure signal（微观结构是数据域，交易结构是其中的成本与容量维度）

**Look-back（回看，信号 vs 价格）**:
图外只读离线评估：把历史 `run.json` 里的信号与该时点之后的已收盘日 K 配成对，度量 Spearman 秩相关与三等分收益差。它是描述性历史统计，不是预测——读数永远与四条边界（选币自选择 / 窗口重叠 / 多重比较 / 无方向结论）一起呈现，且禁止写回任何进图字段。
_Avoid_: calibration（决策校准回路已退役）, backtest, accuracy

**Signal Diff（信号变化）**:
与上一批同 symbol 对比的确定性信号差异。决策时代的 stop_short/stop_long 已退役——系统不再产生任何方向性失效信号。
_Avoid_: stop loss, price target

### 候选筛选

**Sector Cap（板块上限）**:
Rank 名次确定后的席位截断——同一板块最多 N 个候选（默认 3），超额者让位给后续名次的异板块候选。它不判断"哪个板块更好"，只保证一批候选不只讲一个叙事；可关（`--max-per-category 0`），板块索引不可用时不生效并留痕，绝不因此终止批。
_Avoid_: sector rotation, diversification score

**Category（板块标签）**:
候选所属板块（来自 DefiLlama 协议索引，对 Binance 永续覆盖率约四成）。索引未命中 → `None`，无标签者各自成桶、不受上限约束——UNKNOWN 不是过错，不惩罚也不伪造标签。随 `meta.screening.candidates` 落盘并渲染进分支摘要。
_Avoid_: sector score, tag

### 运行模式

**Manual Token Mode（手动币种模式）**:
通过 `--tokens`/`SR_TOKENS` 指定币种跳过筛选，与自动筛选互斥二选一，`meta.screening.mode="manual"`。用于定向研究与手动持仓监控。
_Avoid_: override mode
