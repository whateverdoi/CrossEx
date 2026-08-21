# 10 — 报告与对比输出

**What to build:** ⑧ 节点的报告产出：overview.md（人类可读，含"币种筛选"节与"信号变化"节）+ run.json + candidates.json + snapshot.json + signal_diff.json 五个工件；无 facts/challenges 渲染空节不报错。

**Blocked by:** 09（工件与信号快照）

**Status:** done（10 票验收全过：tests/test_report.py 22 用例含端到端一致性）

- [x] 五个工件全部生成：`overview.md` + `run.json` + `candidates.json` + `snapshot.json` + `signal_diff.json`（`reports/latest/`）
- [x] overview.md 含"币种筛选"节（`meta.screening`：模式 auto/manual、规则、候选带 reason）
- [x] overview.md 含"信号变化"节（signal_diff action 汇总：new / hold / stop_short / stop_long）
- [x] overview.md 含每 token 分析摘要（决策 / direction / level / 关键事实 / 挑战与反驳）
- [x] run.json：meta（时间 / mode / tokens / 成本统计）+ 全量结果；candidates.json：候选清单
- [x] 无 facts/challenges 的 token 渲染空节不报错（报告永远可生成，规格十节纪律 2）
- [x] 端到端：mock 全 6 token 跑完后五个工件内容互相一致（level 与 candidates 对齐、signal_diff 与 snapshot 对照正确）
