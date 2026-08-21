# 07 — 四节点 LLM 链

**What to build:** ③ research_facts（react agent + FACTS_TOOLS 四分析师视角采证）→ ④ decide（json_mode 单次调用决策，含 `_build_summary` 摘要构建）→ ⑤ challenge（react agent + CHALLENGE_TOOLS 对抗，反方预筛按决策方向取反）→ ⑥ finalize（`_invoke_rebuttals` 逐条反驳）四个节点装配；PASS 透传零 LLM 调用。

**Blocked by:** 04（数据收集节点）、06（LLM 基础件）

**Status:** ready-for-agent

- [ ] ③ `research_facts`：react agent 四分析师视角（dimension）采证，facts 含 dimension/topic；`recursion_limit=8` 防工具循环失控
- [ ] ④ `decide`：json_mode 单次调用，`_build_summary` 摘要构建（含 sentiment_raw 原始值解读引导）；decide 异常 → PASS（确定性兜底）
- [ ] ⑤ `challenge`：非 PASS 才调用；≤3 条挑战含 stance；**反方预筛按决策方向取反**（多头取 `direction == "bear"` facts，空头取 `direction == "bull"` facts，规格 Q4 决策）
- [ ] ⑥ `finalize`：`_invoke_rebuttals` 逐条 rebutted/accepted；accepted 只降不升（风控只降不升纪律）
- [ ] PASS 透传零调用（LLM 调用计数可验证：PASS 路径 ③+④ 共 2 次，⑤⑥ 跳过）
- [ ] mock 模式产出非空 facts；字段与 TokenAnalysis 一致（TRADE 必含 direction）
- [ ] 单测：空头用例验证 ⑤ 预筛取 bull facts；异常注入（断网跑 challenge）不中断批
