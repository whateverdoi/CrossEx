# 15 — 筛选排序改象限优先：错价榜替代纯波动榜（④ 提升准确性）

**What to build:** 默认排序从「24h 波动绝对值」改为「错价候选优先」：
价格弱（24h 涨跌幅小/负）+ 成交活跃（24h 成交额大）优先——筛选阶段
无基本面数据，用价格/成交两维近似「基本面强/价格弱」（象限 III）的
价格侧代理：价格弱 = 潜在低估，成交活跃 = 有流动性可执行（错价
候选必须先能建仓）。

**Blocked by:** 14（信号层衍生特征）

**Status:** done（全量 270 passed + 2 skipped，ruff 干净，mock 全链冒烟
meta.screening.rules 含 mispricing_24h）

- [x] `MispricingRanker`：pct 升序排名 + vol 降序排名等权合成（score 越小
      越优先）；任一指标缺失（None/UNKNOWN）排最后（保守纪律）；注册
      RANKERS["mispricing_24h"]；波动榜等既有 ranker 保留（用户可配置）
- [x] `DEFAULT_RULES` rank 换 `mispricing_24h`（删除 abs 参数）
- [x] 测试：新 ranker 排序（弱价高量优先/缺失排最后）+ 默认规则用例更新
      （规则描述 + 居首候选断言）
- [x] 文档同步：AgentArchitecture_Combined.md 波动榜 → 错价榜描述
- [x] 回归：全量 pytest + ruff + mock 全链冒烟（meta.screening.rules 含
      mispricing_24h）
