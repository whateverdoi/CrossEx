# 12 — 决策追踪与校准（评估回路）

**What to build:** 评估回路：每批运行扫描 `reports/` 历史 run.json，对到期
（run_ts + 7d ≤ now）的带方向决策（TRADE/WATCH）用日线 klines 回看 T+1d/T+7d
收益，输出方向命中率与置信度分箱校准——写入当前 run.json `decision_review`
节 + overview.md 第六节「决策复盘」。`review_log.json` 跨批去重并累积
records，统计口径为全历史累积（供后续 prompt 置信度锚定）。

**Blocked by:** 10（报告渲染）

**Status:** done（22 项测试全过；全量 261 passed + 2 skipped，ruff 干净；
真实扫描 14 run 全 pending、mock 全链复盘节渲染验证通过）

- [x] 基准价无前视：open_time + 1d ≤ run_ts 的最后一根日 close（决策时
      已知价，不用决策日当根收盘）
- [x] 到期纪律：run_ts + 7d ≤ now 才回看；未到期记 pending；latest 目录
      跳过（防复制品重复统计）
- [x] review_log.json：已回看 run 去重 + 累积 records；klines 失败的 run
      不标记 reviewed（下次重试）；mock 模式 run 排除（防污染校准统计）
- [x] 校准统计：总体命中率 + 置信度四分箱（[0,0.25)/[0.25,0.5)/
      [0.5,0.75)/[0.75,1.0]）+ TRADE/WATCH 分组；ret_7d 判方向命中
      （long: >0 / short: <0）；UNAVAILABLE 不计入
- [x] klines 失败 → 条目 status=UNAVAILABLE 不中断批；整体异常 →
      meta.review_error，报告仍可生成
- [x] overview 第六节「决策复盘」（无到期记录时空节占位）；run.json 落
      decision_review（累积 records + stats + 本批新增明细）
- [x] 前置修复：mock_klines open_time 秒 → 毫秒（与真实 fetch_klines
      同构，消除单位漂移；_ret 按索引不受影响）
- [x] 测试：纯函数（收益定位/乱序容忍/缺日 None/分箱）+ 扫描（到期
      过滤/去重/容错/latest 与 mock 排除/bare 名补全/重试）+ build_report
      接线（节渲染/meta 键/异常不中断）
- [x] 实现中发现并修复：_returns 按天数定位而非索引偏移（缺日不错位）
