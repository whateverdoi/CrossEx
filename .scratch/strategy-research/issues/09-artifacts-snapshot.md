# 09 — 工件与信号快照

**What to build:** ⑧ 节点的工件派生与信号快照：`_build_artifacts` 确定性映射（liquidity_tier 分层 / 机会分级 / 研究主题统计），LLM 只提供素材、分级分层永远是代码说了算；信号快照 snapshot.json（先读旧为 prev 再覆盖）+ 信号对比 signal_diff（action 规则：new / hold / stop_short / stop_long）——决策失效机制，无价格锚点与有效期。

**Blocked by:** 08（终审与降级）

**Status:** done

- [x] `_build_artifacts`：liquidity_tier 分层（确定性规则）+ 机会分级（`downgraded` → D，复用 TRADE→A / WATCH→B / PASS→D 映射）+ recommended_strategy（带方向前缀）+ 研究主题统计（facts.topic 汇总，7 主题 + unknown）
- [x] 信号快照：`reports/latest/snapshot.json` = `{run_ts, mode, tokens, results: [{symbol, decision, direction, confidence}]}`；**先读旧为 prev 再覆盖**
- [x] 信号对比 `signal_diff`：action 规则 = prev 缺失→`new`；prev==cur→`hold`；prev=(TRADE,short) 且 cur≠→`stop_short`；prev=(TRADE,long) 且 cur≠→`stop_long`
- [x] 无 facts/challenges 时渲染空节不报错（工件永远可生成）
- [x] 快照覆盖与对比正确：构造 prev 验证 stop_short / stop_long / hold / new 四种 action
- [x] `reports/latest/signal_diff.json` 落盘（含各 token action 与 prev/cur 对照）

## 实现说明（09 票）

1. **`_build_artifacts`（report.py 纯函数）**：确定性映射，消费 `state.results/market_data/facts`；liquidity_tier 按 24h 成交额（>=1e8 high / >=1e7 mid / 其余 low，缺失 → low）；opportunity_level `downgraded → D` 优先于决策映射；recommended_strategy = `"{direction} {trade_structure}".strip()`（trade_structure 缺失兜底 UNKNOWN）；catalysts 按 facts.topic 计数（空 topic 计入 unknown）。
2. **results 容错**：`results[i] if i < len(results) else {}`——results 缺失/不足时该 token 工件仍生成（验收：工件永远可生成）。
3. **快照/对比（决策失效机制核心）**：`_build_snapshot` 投影 results 轻量四字段；`_read_prev_snapshot` 读 `reports/latest/snapshot.json`（损坏 → None 视为首次运行）；**先读旧为 prev 再覆盖**；`_build_signal_diff` 按规格 action 规则五分支（prev==cur 判定先于 stop_short/stop_long）。
4. **落盘**：candidates.json → `reports/<ts>/` + latest 软链（失败不落盘、软链跳过不断链）；snapshot.json / signal_diff.json → `reports/latest/` 覆盖写。
5. **失败语义**：工件/快照独立 try——异常仅记 `meta.report_error`，已落盘的 run.json/overview.md 不丢，批不中断（规格六节错误矩阵 ⑧）。
6. **build_report 返回类型变更**：`(run_dir, artifacts)` 二元组，write_report 节点解包并把 artifacts 写 `research_artifacts`（state 7 产物字段之一，规格十节）。

## 偏差裁决（以根规格 ⑧ 为准）

- **overview.md 新节（对抗复审/候选清单/信号变化）归 10 票**：09 票验收仅工件派生与快照；规格 ⑧ 的 overview 渲染三节与 10 票"报告与对比输出"验收（币种筛选节/信号变化节/逐币摘要/五个工件一致性）重叠，渲染留给 10 票，本票只保证工件落盘正确。
- **llm_calls 归 10 票**：规格 state 表与 run.json 的 `llm_calls`（累计调用与失败记录）无现成实现，09 票验收未列；10 票"成本统计"验收承载。
- **损坏快照视为无 prev**：规格未定义损坏场景；按"快照失败不中断批"精神，JSON/UTF-8 损坏 → None（首次运行语义，全 new），不抛异常（补修：`except (OSError, ValueError)` 覆盖 UnicodeDecodeError）。
- **快照覆盖式语义（code-review 补裁决）**：规格附注"历史运行在 reports/<ts>/"——历史状态由 `reports/<ts>/run.json` 的 results 承载（每批已落盘），快照本身是 latest 单文件覆盖（失效机制的"当前状态"），不做历史归档。
- **run.json 条目说明（code-review 补裁决）**：规格 L665 "run.json 条目追加 rebuttals / risk_flags / llm_calls"——rebuttals/risk_flags 已嵌套于 results 条目（08 票），llm_calls 属 meta 成本统计归 10 票。
- **补修（code-review）**：落盘模式提取 `_write_json` 消除 4 处重复；build_report 失败兜底 artifacts 统一为 `{s: {} for s in tokens}`（与 write_report 异常兜底形状一致）。
