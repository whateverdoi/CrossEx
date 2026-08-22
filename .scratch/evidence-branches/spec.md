# 多空证据双分支重构 Spec

Status: ready-for-agent

## Problem Statement

当前 8 节点线性链（数据→信号→采证→决策→对抗→复审→风控→报告）以 TRADE/WATCH/PASS 决策为终点，存在三个问题：

1. **线性拓扑未发挥 LangGraph 的并行能力**——每个节点串行等待，图框架沦为装饰。
2. **系统替使用者做方向推荐**——使用者希望自己裁决多空，而非接受一个"名义上的建议"；且 decision/confidence 字段存在时，LLM 必然隐式给倾向，污染使用者判断。
3. **客观性不可证**——决策与置信度是 LLM 主观产物，无法机器复核；"证据"若只是 LLM 转述文本，则不可追溯。

## Solution

重构为"多空证据陈列"体系：确定性数据/信号层 → 两个并行分支（多头分支只找做多证据、空头分支只找做空证据，同时消费同一份冻结数据快照、互不可见）→ 确定性证据核验 → 单文档证据陈列。系统不产出任何方向性结论，由使用者依据证据自行裁决。

新拓扑：

```
START → ① collect_data（含历史序列趋势特征确定性采集）
      → ② compute_signals
      → [ bull_research ‖ bear_research ]（并行，同消费冻结快照，互不可见）
      → ③ evidence_verify（确定性核验）
      → ④ write_report → END
```

## User Stories

1. 作为使用者，我想看到每个 token 的多头证据清单，以便判断做多依据是否成立。
2. 作为使用者，我想看到每个 token 的空头证据清单，以便判断做空依据是否成立。
3. 作为使用者，我想看到每条证据的结构化数据引用（basis：domain/field/value），以便逐条复核 LLM 没有编造数据。
4. 作为使用者，我想确认系统不产出任何方向性推荐（无 decision、无 confidence），以便由我自己裁决。
5. 作为使用者，我想看到同一数据被两分支引用时的两面解读并列呈现，以便理解分歧点。
6. 作为使用者，我想确保多头/空头分支互不可见对方中间产物，以便证据不受相互锚定污染。
7. 作为使用者，我想看到被核验剔除的无效证据及剔除原因，以便核验过程可审计。
8. 作为使用者，我想在报告顶部看到总览表（每 token 证据数 + 数据域覆盖），以便快速定位值得研究的 token。
9. 作为使用者，我想让新闻/web 数据进入证据来源并在 source 中显式标注，以便数据来源多样且可分辨来源等级。
10. 作为使用者，我想后续新增数据源（如链上数据）时无需改动分支逻辑，以便数据扩展成本最小。
11. 作为使用者，我想看到 TVL/fees/stablecoin 的历史趋势特征（确定性计算值），以便评估基本面走势。
12. 作为使用者，我想看到确定性信号快照随每次运行落盘并支持跨运行对比，以便信号假设可回看验证。
13. 作为使用者，我想让单个 token 的分支分析失败不中断整批，以便批处理可靠。
14. 作为使用者，我想在 mock 模式下验证全链行为，以便离线开发调试。
15. 作为使用者，我想看到每次运行的 LLM 调用成本统计，以便评估运行开销。

## Implementation Decisions

1. **拓扑**：`collect_data → compute_signals → [bull_research ‖ bear_research] → evidence_verify → write_report`。两分支为并行边（fan-out），无 reducer（后写覆盖语义不变，两分支写不同 state 字段）。
2. **分支形态**：每个分支 = 单次 LLM 结构化输出调用（Pydantic schema + json_mode），`with_retry(stop_after_attempt=2)` + 宽容解析 + 坏条目丢弃；单 token 异常 → 该 token 该分支产出空清单（记录 error，不中断批）。分支不带工具。
3. **数据消费**：两分支在入口同时消费同一份完整快照（market_data / fundamental_data / microstructure_data / web_data / scanner_snapshot / signals 全部可见）。prompt 侧重引导（多头提示优先关注增长/趋势/资金流入类，空头提示优先关注估值/拥挤/风险类），无硬数据边界。
4. **证据 schema**（结构化输出契约，来自 grill 决策）：

   ```
   EvidenceItem:
     claim: str                      # 主张（自由文本）
     basis:
       domain: str                   # signals / market_data / fundamental_data / microstructure_data / web_data / scanner_snapshot
       field: str                    # 点号路径，如 "divergence.value.quadrant"
       value: str                    # 引用时点的快照值（转字符串）
     source: str                     # 与 domain 一致

   BranchOutput:
     evidence: list[EvidenceItem]    # 按重要性降序，上限 8 条
   ```
   无 confidence 字段（LLM 自评信心分是主观臆想，不进入证据体系）。
5. **证据核验（evidence_verify）**：确定性纯函数。逐条检查：`snapshot[domain][symbol][field]` 逐级解引用存在，且规范化后与 `value` 一致 → 通过；否则剔除并记录（symbol、claim、原因）到剔除记录。产出：`verified_evidence` + `rejected_evidence`。
6. **历史序列确定性化**：tvl/fees/stablecoin 三个 history 数据从 LLM 工具层改为 collect_data 内的确定性采集，纯函数提炼趋势特征（如 `tvl_trend_30d`、`tvl_change_30d`、`fees_trend_30d`、`stablecoin_change_30d`）落盘 fundamental_data；LLM 工具层对应函数退役。
7. **报告**：单证据 md 文档——顶部总览表（token / 多头证据数 / 空头证据数 / 数据域覆盖），每 token 一节（`### 做多证据` / `### 做空证据` 两张表：`# | claim | basis | source`），文档末尾"剔除记录"附录（被剔 claim + 原因）。overview.md 退役。
8. **工件集**：run.json 保留（数据快照投影 + 证据清单 + llm_calls + meta）；candidates.json 简化（仅候选列表，分级退役，或并入 run.json meta）；snapshot.json/diff 改造为**信号快照**（quadrant、momentum、funding_pctile_90d、oi_price_divergence、趋势特征；decision 型 stop_short/stop_long 失效机制退役）。
9. **退役清单**：节点 research_facts / decide / challenge / finalize / risk_check；state 字段 facts / decisions / challenges / final_decisions / risk_flags / results（语义替换为证据字段）；schema 的 TokenAnalysis（decision/direction/confidence/trade_structure 等）；review.py 校准；Decision / Opportunity Level / Downgrade / Research Topic 领域词条。
10. **state 字段**：新增 `evidence: dict[str, dict]`（每 token：bull_case / bear_case 两份分支产出）+ `rejected_evidence`（核验剔除记录）；`results` 替换为证据+信号快照投影结构。
11. **llm_calls 计数**：分支调用挂 callbacks（bull/bear 各一计数键，或复用现有键体系）；live/mock 双路径计数保留，运行入口 reset 语义不变。
12. **数据源扩展机制**：source 为自由字符串（非枚举）；新数据域 = collect_data 加采集函数 + 快照加域，分支零改动自动可见（分支 prompt 提示"消费所有可见数据域"）。

## Testing Decisions

- 好测试的标准：只测外部行为（分支产出的证据结构、核验的剔除判定、报告渲染的节结构、mock 全链的并行与隔离），不测 LLM 内部措辞。
- 测试模块：
  - **graph**：并行拓扑（两分支均执行、互不可见——通过 mock 分支输出验证状态字段分离）。
  - **evidence_verify**：纯函数全用例——引用存在/缺失/值不一致/嵌套路径/domain 未知，剔除留痕（仿现有 test_risk_check.py 纯函数风格）。
  - **趋势特征**：确定性纯函数——序列 → 特征值、空序列、异常兜底（仿 test_signals.py 纯函数风格）。
  - **报告渲染**：证据 md 的节结构、总览表、剔除记录（仿现有 test_report.py 风格）。
  - **mock 全链**：`build_graph().invoke()` 一次性验证装配正确、批不中断、报告落盘（仿现有 test_nodes_llm.py / test_graph.py 风格）。
- 零产物纪律：测试落盘隔离于临时目录，pytest 零产物配置（cacheprovider 禁用 + session 清理 hook）保持有效。

## Out of Scope

- "信号 vs 价格"校准回看（信号快照积累数批后作为后续票）。
- 分支带工具检索（agent 形态）——如某分支后续确需检索，内部升级不改变外部拓扑。
- 新增数据源接入（onchain 等）——机制就绪，数据接入本身不在本 spec。
- 手动 token 模式与筛选器的行为变化（筛选入口保持现状）。

## Further Notes

- 决策依据：ADR-0001（docs/adr/0001-bull-bear-evidence-branches.md）——多空证据双分支替代决策体系。
- 领域词汇：CONTEXT.md 已更新（Evidence / Bull Case / Bear Case / Evidence Verification / Trend Feature / Signal Snapshot / Signal Diff 新定义，Decision / Opportunity Level / Downgrade / Research Topic 退役）。
- 规格追踪：master spec 位于 AgentArchitecture_Combined.md，to-tickets 引用其路径。
