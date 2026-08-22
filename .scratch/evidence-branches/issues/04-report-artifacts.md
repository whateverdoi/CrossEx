# 04 — 报告与工件改造

**What to build:** 证据 md 渲染——顶部总览表（token / 多头证据数 / 空头证据数 / 数据域覆盖），每 token 一节（做多证据 / 做空证据两张表：claim | basis | source），文档末尾剔除记录附录（被剔 claim + 原因）；run.json 改造（数据快照投影 + 证据清单 + 信号快照 + llm_calls）；candidates.json 简化（仅候选列表，机会分级退役）；snapshot / diff 改造为信号快照（quadrant / momentum / funding_pctile_90d / oi_price_divergence / 趋势特征，decision 型 stop_short/stop_long 失效语义退役）；overview.md 退役。

**Blocked by:** 03 — 拓扑切换

**Status:** ready-for-agent

- [x] 证据 md：总览表、每 token 节 bull/bear 表、剔除记录附录齐全
- [x] run.json：含证据清单与信号快照投影
- [x] snapshot.json 为信号快照；diff 无 stop 语义
- [x] overview.md 退役，报告测试改造全绿
