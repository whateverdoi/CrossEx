# 08 — 终审与降级

**What to build:** ⑦ 节点：确定性风控核验（纯函数，TRADE 强制核验，LLM 无法覆盖）。两遍扫描：第一遍逐 token EV 核验（多头 `momentum>=0` 或 quadrant∈{I,III}；空头 `momentum<0` 或 quadrant==II；IV 双弱不做空；方向缺失也 flag），第二遍组合集中度核验（TRADE 计数>2）；不达标自动降级（只降不升，`downgraded` 标记）；派生 results（level 映射）。

**Blocked by:** 05（信号层）、07（四节点 LLM 链）

**Status:** done

- [x] 第一遍扫描（逐 token EV 核验）：
  - 多头：`momentum.value >= 0` 或 quadrant∈{"I","III"} 通过，否则 flag
  - 空头：`momentum.value < 0` 或 quadrant=="II" 通过，否则 flag（**IV 双弱不做空**）
  - direction 缺失 → flag
  - 非 TRADE 跳过
- [x] 第二遍扫描（组合集中度）：TRADE 计数 > 2 → 降级为 WATCH（集中度批级约束）
- [x] 终审降级：只降不升；降级原因落 flags（多头 EV 矛盾 / 空头 EV 矛盾 / 方向缺失 / 集中度>2 四种降级路径）；`downgraded` 标记落分析对象
- [x] results 派生：level 映射 = `"D" if analysis.get("downgraded") else {"TRADE": "A", "WATCH": "B", "PASS": "D"}.get(decision, "D")`；`recommended_strategy` 带方向前缀
- [x] 单测：多头 EV 矛盾 / 空头 EV 矛盾（含 IV 象限做空用例）/ 方向缺失 / 集中度>2 四种降级各触发一次
- [x] 批处理永不中断：异常 token 只降级不中断（规格十节纪律 2）

## 实现说明（08 票）

1. **`_ev_flags` 纯函数 + `risk_check` 装配**：EV 核验拆独立纯函数（05 票 signals 先例），节点负责两遍扫描编排；16 项单测全部走节点级构造 state，无 IO。
2. **两遍扫描**：先统计批内 TRADE 数（`trade_count`），再逐 token 核验追加集中度 flag——集中度是批级约束，全部 TRADE 共享同一条 flag。
3. **降级只降不升**：有 flag 的 TRADE → `decision="WATCH"` + `downgraded=flags`；confidence/trade_structure 等其余字段不动（风控不改写 LLM 数值）；PASS/WATCH 不参与核验。
4. **results 派生**（与规格 ⑦ 伪代码一致）：`{**analysis, "rebuttals", "risk_flags"}`，降级后 decision 已改写，`downgraded` 字段带出。
5. **复制不原地改**：`analysis = dict(item["analysis"])` 后改写，`final_decisions` 入参保持原状（纯函数纪律）。
6. **spec 失败矩阵落地**：`signals[symbol]` 缺失 → EV 核验跳过（缺数据不等于矛盾，不误伤），集中度仍照常核验；**补修（code-review）**：信号层失败 error 条目（05 票 `{"symbol", "error"}` 产物）同样跳过——实现与伪代码一致但未覆盖错误条目边界，已按“缺数据不等于矛盾”语义修复并补单测。

## 偏差裁决（以根规格 ⑦ 为准）

- **验收 4 的 level 映射与 recommended_strategy 归属 ⑧ `_build_artifacts`（09 票）**：根规格 ⑦ 伪代码 results 仅 `{**analysis, rebuttals, risk_flags}`，不含 level；`opportunity_level`（downgraded→D / TRADE→A / WATCH→B / PASS→D）与 recommended_strategy 是 ⑧ artifacts 字段，由 09 票 `_build_artifacts` 从 results 现算。本票 results 已带全 analysis 字段（含 downgraded/direction/trade_structure），09 票消费无阻碍。
- **flags 文案中文口径**：规格失败矩阵 `{多头|空头|未声明方向}`，实现用 `{"long": "多头", "short": "空头"}.get(side, "未声明方向")`（规格伪代码 `{side or ...}` 为简化写法）。
