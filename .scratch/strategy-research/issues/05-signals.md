# 05 — 信号层

**What to build:** ② 节点：四个确定性纯函数（`valuation_ratios` / `momentum_score` / `divergence` / `sentiment_raw`）计算信号 + 四象限（quadrant）推导；任何输入缺失 → None（UNKNOWN 纪律），绝不猜测；sentiment 原始值直读（components 字段，无阈值打分）。

**Blocked by:** 04（数据收集节点）

**Status:** done

- [x] `valuation_ratios`：mc_fees / fdv_revenue / mc_tvl / fees_tvl（年化口径，fees/revenue 24h ×365；缺失 → None）
- [x] `momentum_score`：基本面动量分（tvl_change_7d/30d 各 0.5 加权），输出 value；07 票 risk_check 消费（mom≥0 多头支持）
- [x] `divergence`：背离 = 基本面增速 − 价格涨幅（7d/30d 双窗口）；价涨基本面无改善 = 高估、价跌基本面未恶化 = 低估
- [x] `sentiment_raw`：components = {funding, funding_trend, oi_change_24h, ls_ratio_all, ls_ratio_top_acc, ls_ratio_top_pos, taker_bs_ratio} 原始值直读，**不做阈值加减分**（Q2 决策：删打分机制，LLM 按 prompt 解读原始值）
- [x] 四象限推导：I=基本面强+价格强 / II=基本面弱+价格强 / III=基本面强+价格弱（潜在做多候选）/ IV=双弱（7d 窗口 0 为界；任一缺失 → quadrant=None）
- [x] 单测：四个函数各 3 种输入（正常 / 缺失 / 异常）；缺失 → None，绝不猜测
- [x] `compute_signals` 节点：mock 模式走 mock_signals_data（kind 分支同构）/ 真实模式纯函数装配；单 token 异常置 {symbol, error} 不中断批

## 实现说明

- `signals.py` 新建：4 个纯函数（无 IO），`_v` 取四元组 value（funding_trend 等字符串字段也直读）、`_ratio` 安全除法（分母 ≤0 → None）
- `mock.py` 新增 `mock_signals_data(symbol, kind)`：**值从 mock 数据源动态推导**（`_slug_for` 静态映射优先 / `_price_ret` 日线收益率 / `_funding_trend` 资金趋势，均与 nodes 同口径），与同一快照上的纯函数输出逐值一致（交叉验证测试 `test_mock_signals_consistent_with_pure_functions` 锁定）；protocol → 估值全有值，chain → 全 None（真实结构性缺失同构），unknown → 全 None
- `nodes.compute_signals`：mock 分支 / 真实分支（valuation_ratios(fund, mkt) + momentum_score(fund) + divergence(fund, mkt) + sentiment_raw(mkt, ms)），per-token try 隔离
- 测试 `tests/test_signals.py`：17 项（纯函数 12 + 节点 5：mock 全量 / mock↔真实交叉验证 / 真实装配 / 真实单 token 异常 / mock 单 token 异常）
- 与票 05 验收的两处偏差（以根规格 AgentArchitecture_Combined.md ② 为准）：
  1. 验收 1 "稳定币供应占比" → 规格伪代码为 4 比率（mc_fees/fdv_revenue/mc_tvl/fees_tvl），无 stablecoin 占比
  2. 验收 2 "多窗口价格动量（7d/30d/90d/1y）" → 规格伪代码为**基本面动量**（tvl_change_7d/30d 加权）；07 票 risk_check 消费逻辑（mom≥0 判多头）与基本面动量自洽，价格维度已由 divergence 承载
