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
确定性纯函数终审：逐条检查证据的 basis 引用是否真实存在于数据快照（domain/field 解引用 + value 一致性），无效证据剔除并留痕。系统唯一的"机器强制"层。
_Avoid_: risk check

### 信号

**Sentiment Signal（情绪信号）**:
持仓相关指标的确定性汇总（funding、funding 趋势、全市场/大户多空比、taker 买卖比、OI 变化），以原始值直读呈现，不做阈值加减分——阈值化会丢失信息，且下游 LLM 本就按 prompt 规则直接解读原始值。
_Avoid_: sentiment score, crowding score

**Trend Feature（趋势特征）**:
历史序列（TVL/fees/stablecoin 供应）经确定性计算提炼的特征值（如 tvl_trend_30d、tvl_change_30d），随 fundamental_data 落盘供分支直读——LLM 只读数值，不转述曲线。
_Avoid_: history series

**Signal Snapshot（信号快照）**:
每次运行把确定性信号（quadrant、momentum、funding_pctile_90d、oi_price_divergence、趋势特征）落盘 `reports/latest/snapshot.json`，覆盖前先读旧快照为 prev——跨运行回看的基础，兑现"信号假设可证伪"。
_Avoid_: checkpoint, state dump

**Signal Diff（信号变化）**:
与上一批同 symbol 对比的确定性信号差异。决策时代的 stop_short/stop_long 已退役——系统不再产生任何方向性失效信号。
_Avoid_: stop loss, price target

### 运行模式

**Manual Token Mode（手动币种模式）**:
通过 `--tokens`/`SR_TOKENS` 指定币种跳过筛选，与自动筛选互斥二选一，`meta.screening.mode="manual"`。用于定向研究与手动持仓监控。
_Avoid_: override mode
