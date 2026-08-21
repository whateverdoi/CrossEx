# 08 — 终审与降级

**What to build:** ⑦ 节点：确定性风控核验（纯函数，TRADE 强制核验，LLM 无法覆盖）。两遍扫描：第一遍逐 token EV 核验（多头 `momentum>=0` 或 quadrant∈{I,III}；空头 `momentum<0` 或 quadrant==II；IV 双弱不做空；方向缺失也 flag），第二遍组合集中度核验（TRADE 计数>2）；不达标自动降级（只降不升，`downgraded` 标记）；派生 results（level 映射）。

**Blocked by:** 05（信号层）、07（四节点 LLM 链）

**Status:** ready-for-agent

- [ ] 第一遍扫描（逐 token EV 核验）：
  - 多头：`momentum.value >= 0` 或 quadrant∈{"I","III"} 通过，否则 flag
  - 空头：`momentum.value < 0` 或 quadrant=="II" 通过，否则 flag（**IV 双弱不做空**）
  - direction 缺失 → flag
  - 非 TRADE 跳过
- [ ] 第二遍扫描（组合集中度）：TRADE 计数 > 2 → 降级为 WATCH（集中度批级约束）
- [ ] 终审降级：只降不升；降级原因落 flags（多头 EV 矛盾 / 空头 EV 矛盾 / 方向缺失 / 集中度>2 四种降级路径）；`downgraded` 标记落分析对象
- [ ] results 派生：level 映射 = `"D" if analysis.get("downgraded") else {"TRADE": "A", "WATCH": "B", "PASS": "D"}.get(decision, "D")`；`recommended_strategy` 带方向前缀
- [ ] 单测：多头 EV 矛盾 / 空头 EV 矛盾（含 IV 象限做空用例）/ 方向缺失 / 集中度>2 四种降级各触发一次
- [ ] 批处理永不中断：异常 token 只降级不中断（规格十节纪律 2）
