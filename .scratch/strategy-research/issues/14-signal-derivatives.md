# 14 — 信号层衍生特征：funding 极值分位 + OI/价格背离（③ 提升准确性）

**What to build:** 信号层新增两个确定性衍生特征，为 LLM 提供「拥挤度」与
「趋势健康度」的结构化证据（③ 提升准确性清单第 3 项）：

1. **funding 极值分位**：最新 |funding_rate| 在 90 天历史分布中的分位
   （0-100）——分位高 = 费率极端（多头或空头拥挤加剧），是对"funding 高
   即拥挤"的平滑化替代（25 根短窗口对单根异常敏感）。
2. **OI/价格背离**：24h 价格变化 × OI 变化四象限——价 OI 同向 = 新仓进场
   （趋势确认），背离 = 存量换手/平仓驱动（趋势健康度弱）。

两者都是纯函数（确定性可核验），数据源全在 binance_futures（当前网络
稳定可用），成本 = 资金费率历史 limit 25 → 270（权重 1，无新增调用）。

**Blocked by:** 13 数据源调研（已完成：② 链上数据受外部数据源限制标记
skipped，按用户指令继续本票）

**Status:** done（全量 268 passed + 2 skipped，ruff 干净，mock 全链 exit=0）

- [x] `signals.funding_percentile(hist)`：|funding| 最新值在窗口分布分位
      0-100；样本 <10 / 空序列 / 非法值 → None（UNKNOWN 纪律）
- [x] `signals.oi_price_divergence(price_ret_24h, oi_change_24h)`：四象限
      label（confirm_long / weak_long / confirm_short / weak_short / none），
      任一输入缺失或 0 → none（零值无方向）；返回 {label, note}
- [x] `nodes._market`：funding_rows limit 25 → 270（90 天 8h 费率）；
      `mkt["funding_pctile_90d"] = _dp(sig.funding_percentile(...))`
- [x] `nodes._microstructure(symbol, taker, price_ret_24h)`：签名加
      price_ret_24h（来自 mkt.change_24h.value）；`ms["oi_price_divergence"]`
      用 oi_change_24h 原始值计算（非四元组）
- [x] `sentiment_raw` components 加 `funding_pctile_90d` +
      `oi_price_divergence` 两键；_SENTIMENT_NOTE 补解读
- [x] mock 同构：`mock.mock_signals_data` sentiment 从 mock 数据源动态
      推导两键（funding_pctile 经同一纯函数算 mock 270 根费率；divergence
      用 mock ticker 的 price_change_pct + 固定 oi_change 0.0）
- [x] prompt：DECIDE_PROMPT sentiment 解读段 + CHALLENGE_PROMPT 拥挤段补
      pctile/divergence 语义
- [x] 测试：纯函数（分位正常/样本不足/空/常数序列；四象限 + 缺失/零值
      边界）+ 装配（mock 全链 sentiment 含新键）+ 交叉验证逐值相等
- [x] 回归：全量 pytest + ruff + mock 全链冒烟（信号经 LLM 消费，决策正常）
