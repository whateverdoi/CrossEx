# 03 — 拓扑切换

**What to build:** graph 装配新拓扑：collect_data → compute_signals → [bull_research ‖ bear_research 并行] → evidence_verify → write_report；旧节点（research_facts / decide / challenge / finalize / risk_check）从图中移除（函数保留为死代码，票 05 清理）；分支 prompt 落地（多头侧重增长/趋势/资金流入类，空头侧重估值/拥挤/风险类，全量快照可见无硬边界）；llm_calls 计数键改为 bull/bear；全链测试改造为新链路断言。

**Blocked by:** 02 — 证据组件

**Status:** ready-for-agent

- [x] mock 全链 invoke：两分支均执行且互不可见（状态字段分离验证）
- [x] 单 token 分支异常 → 空清单，批不中断
- [x] llm_calls 反映 bull/bear 调用次数（运行入口 reset 语义保持）
- [x] 全链测试（graph / nodes_llm / risk_check / e2e_smoke）切换新链路断言全绿
