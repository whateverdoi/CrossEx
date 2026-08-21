# Strategy Research Agent

一个加密资产策略研究 Agent：确定性信号层 + 多分析师视角采证 + 对抗复审 + 确定性风控终审，产出可校验工件（候选清单 + 分级）。

## Language

### 决策与分级

**Decision**:
每个 token 的最终结论，取值为 TRADE / WATCH / PASS 三者之一。TRADE = 可交易且必须声明方向（long/short）；WATCH = 仅观察；PASS = 不交易。
_Avoid_: recommendation, call

**Opportunity Level（机会分级）**:
由最终决策确定性映射的 A/B/C/D 机会等级：TRADE→A、WATCH→B、PASS→D；C 保留给信息不足场景（当前不产生）。被风控降级的 TRADE 映射 D（不复用 WATCH→B）——降级意味着机会被组合级终审否决。
_Avoid_: rating, score

**Downgrade（降级）**:
决策的单向修正（只降不升），TRADE→WATCH，由 finalize 接受挑战或 risk_check 风控标记触发，并记录降级原因。
_Avoid_: demotion, penalty

### 信号

**Sentiment Signal（情绪信号）**:
持仓相关指标的确定性汇总（funding、funding 趋势、全市场/大户多空比、taker 买卖比、OI 变化），以原始值直读呈现，不做阈值加减分——阈值化会丢失信息，且下游 LLM 本就按 prompt 规则直接解读原始值。
_Avoid_: sentiment score, crowding score

**Signal Snapshot（信号快照）**:
每次运行把 results（symbol、decision、direction、confidence）落盘 `reports/latest/snapshot.json`，覆盖前先读旧快照为 prev（历史运行在 `reports/<ts>/`）——跨运行对比的基础。
_Avoid_: checkpoint, state dump

**Signal Diff（信号变化）**:
与上一批同 symbol 对比的决策状态差异，action ∈ new / hold / stop_short / stop_long。`stop_short`/`stop_long` 即“信号反转、停止该方向”——决策的失效机制。
_Avoid_: price target, stop loss

### 运行模式

**Manual Token Mode（手动币种模式）**:
通过 `--tokens`/`SR_TOKENS` 指定币种跳过筛选，与自动筛选互斥二选一，`meta.screening.mode="manual"`。用于定向研究与手动持仓监控。
_Avoid_: override mode

### 研究主题

**Research Topic（研究主题）**:
facts.topic 的取值体系 = 7 个命名主题（project / team / social / adoption / unlock / catalyst / news）+ unknown 兜底。与“六维”区分：**六维 = search_web 查询模板维度**（不含 news，news 来自 web_data）。
_Avoid_: dimension（已用于四分析师视角，勿混用）
