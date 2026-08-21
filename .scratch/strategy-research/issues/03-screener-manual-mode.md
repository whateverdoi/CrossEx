# 03 — 筛选器与手动模式

**What to build:** 确定性币种筛选（图外入口 ⑨）：全市场 24hr ticker + exchangeInfo 各 1 次拉取，规则引擎（Filter AND 依次过滤 → Rank 排序取 Top N）零 LLM 产出候选；快照失败抛 `ScreeningError` 批终止（全架构唯一允许终止的节点）；`--tokens`/`SR_TOKENS` 手动模式与筛选互斥二选一（`meta.screening.mode="manual"`）；无 rank 规则时跳过排序不抛异常。

**Blocked by:** 02（数据源层）

**Status:** done

- [x] Filter 注册表：次新（`listing_days_lt`）/ 流动性下限（`quote_volume`）/ 排除稳定币，AND 依次过滤，缺失字段标 UNKNOWN 规则保守排除（注：排除稳定币 = 仅排除稳定币之间的计价对，如 USDCUSDT/TUSDUSDT；BTCUSDT 以稳定币计价但标的非稳定币，保留）
- [x] Rank 注册表：波动榜 / 涨跌榜 / 成交额榜，排序取 Top N；无 rank 规则时跳过排序（防 StopIteration）；单一 rank 规则（多条报错）
- [x] 候选带 `reason` 与指标；零 LLM 参与选币（规格九节纪律 9）
- [x] 快照失败抛 `ScreeningError` 批终止，报错信息明确（禁止静默产出空批或回退固定候选）；未知规则/类别/多 rank 同属配置错误显式报错
- [x] mock 返回固定 6 候选（`SR_MOCK=1`，零外部请求）
- [x] `--tokens`/`SR_TOKENS` 手动模式：跳过筛选直接判断，与自动筛选互斥二选一，`meta.screening.mode="manual"` 落盘
- [x] 单测：次新过滤 / 波动榜排序 / 稳定币排除 / 快照失败抛 `ScreeningError` / rank 空注册表不抛异常（23 用例）
