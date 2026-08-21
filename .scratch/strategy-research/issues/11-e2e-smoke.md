# 11 — 全链路联调与冒烟

**What to build:** 端到端验证：mock 全 6 token 回归（全链降级路径各触发一次）+ 真实 API 冒烟（BTC/UNI，含 search_web 实测）+ 异常注入（断网跑 challenge）+ 筛选器端到端（真实模式产候选、断网验证 `ScreeningError` 批终止、`--tokens` 手动模式互斥）。

**Blocked by:** 03（筛选器与手动模式）、10（报告与对比输出）

**Status:** ready-for-agent

- [ ] mock 端到端回归（`SR_MOCK=1` 全离线）：全链降级路径各触发一次；报告与工件可生成；PASS 透传路径零 LLM 调用可验证
- [ ] 真实 API 冒烟：BTC/UNI 两 token 全链路跑通（含 search_web 实测，六维查询模板出结果）
- [ ] 异常注入：断网跑 challenge 不中断批（该 token 降级，报告仍可生成）
- [ ] 筛选器端到端：真实模式跑 `listing_days_lt(100)+volatility_24h(10)` 产出候选，候选带 reason 与指标，`meta.screening` 落盘
- [ ] 注入断网验证筛选快照失败 → `ScreeningError` 批终止，报错信息明确（全架构唯一允许终止的节点）
- [ ] `--tokens`/`SR_TOKENS` 手动模式验证：与筛选互斥（二选一），`meta.screening.mode="manual"` 落盘，跳过筛选直接判断
- [ ] 成本统计：mock 回归中 LLM 调用计数符合规格七节（PASS 2 次 / TRADE-WATCH 4 次 / 全批平均 ≈2.6/token）
