# 02 — 数据源层（官方 SDK 薄适配）

**What to build:** 五个数据源（binance / binance_futures / defillama / web / mock）各端点可用，所有数据点一律 `{value, source, timestamp, confidence}` 四元组包装；注入断网后数据点=error/UNKNOWN，零 mock 数据混入（失败即失败）。行情数据走 Binance 官方 SDK 薄适配：惰性单例 `Spot(config_rest_api=...).rest_api` + `to_plain()` 解包（pydantic→dict）+ 429/418 权重限流退避（参考 BinanceApi 项目已验证的 WeightBudget 模式）。

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [ ] `binance.py`（官方 SDK 薄适配）：`fetch_ticker_24h_all()` 全市场 24hr ticker（现货 `ticker24hr`）；SDK 调用模式 = 惰性单例 + `to_plain()` 解包 + 权重限流退避（429/418 处理，429 默认等待 60s、418 默认 120s，Retry-After 优先，封禁超过上限中止本轮）
- [ ] `binance_futures.py`（官方 SDK 薄适配）：`mark_price`（premium/funding）、openInterestHist、globalLongShortAccountRatio、topLongShortAccountRatio|PositionRatio、takerlongshortRatio、exchangeInfo（含 `fetch_listing_days` 全量 onboardDate）
- [ ] `defillama.py`（httpx sync，协议 TVL / 链稳定币 / 链 DEX 交易量按需拉取）
- [ ] `web.py`（Bing News RSS + Web RSS，零 key）
- [ ] `mock.py`：与真实路径字段同构；`SR_MOCK=1` 时零外部请求
- [ ] 数据点四元组包装：`{value, source, timestamp, confidence}`，缺失字段标 UNKNOWN（规格十节纪律 3/4）
- [ ] **失败即失败**：注入断网后该数据点=error/UNKNOWN，绝不回退 mock（mock 仅限 `SR_MOCK=1` 显式离线模式）
- [ ] 真实模式冒烟：现货 ticker24hr + 合约 mark_price / ticker24hr_price_change_statistics 各拉一次返回真实数据（调用模式已实测，直接复用）
