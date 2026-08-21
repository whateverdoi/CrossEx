# 09 — 工件与信号快照

**What to build:** ⑧ 节点的工件派生与信号快照：`_build_artifacts` 确定性映射（liquidity_tier 分层 / 机会分级 / 研究主题统计），LLM 只提供素材、分级分层永远是代码说了算；信号快照 snapshot.json（先读旧为 prev 再覆盖）+ 信号对比 signal_diff（action 规则：new / hold / stop_short / stop_long）——决策失效机制，无价格锚点与有效期。

**Blocked by:** 08（终审与降级）

**Status:** ready-for-agent

- [ ] `_build_artifacts`：liquidity_tier 分层（确定性规则）+ 机会分级（`downgraded` → D，复用 TRADE→A / WATCH→B / PASS→D 映射）+ recommended_strategy（带方向前缀）+ 研究主题统计（facts.topic 汇总，7 主题 + unknown）
- [ ] 信号快照：`reports/latest/snapshot.json` = `{run_ts, mode, tokens, results: [{symbol, decision, direction, confidence}]}`；**先读旧为 prev 再覆盖**
- [ ] 信号对比 `signal_diff`：action 规则 = prev 缺失→`new`；prev==cur→`hold`；prev=(TRADE,short) 且 cur≠→`stop_short`；prev=(TRADE,long) 且 cur≠→`stop_long`
- [ ] 无 facts/challenges 时渲染空节不报错（工件永远可生成）
- [ ] 快照覆盖与对比正确：构造 prev 验证 stop_short / stop_long / hold / new 四种 action
- [ ] `reports/latest/signal_diff.json` 落盘（含各 token action 与 prev/cur 对照）
