# 05 — 信号层

**What to build:** ② 节点：四个确定性纯函数（`valuation_ratios` / `momentum_score` / `divergence` / `sentiment_raw`）计算信号 + 四象限（quadrant）推导；任何输入缺失 → None（UNKNOWN 纪律），绝不猜测；sentiment 原始值直读（components 字段，无阈值打分）。

**Blocked by:** 04（数据收集节点）

**Status:** ready-for-agent

- [ ] `valuation_ratios`：市值/FDV / 协议收入 / 稳定币供应占比（缺失 → None）
- [ ] `momentum_score`：多窗口价格动量（7d/30d/90d/1y 加权），输出 value + 符号语义（涨跌方向供多空核验）
- [ ] `divergence`：价格 vs 基本面背离检测（价涨基本面无改善 = 高估信号；价跌基本面未恶化 = 低估信号）
- [ ] `sentiment_raw`：components = {funding_rate, funding_trend, oi_change_24h, ls_ratio_all, ls_ratio_top_acc, ls_ratio_top_pos, taker_bs_ratio} 原始值直读，**不做阈值加减分**（Q2 决策：删打分机制，LLM 按 prompt 解读原始值）
- [ ] 四象限推导：I=基本面强+价格强 / II=基本面弱+价格强 / III=基本面强+价格弱 / IV=双弱
- [ ] 单测：四个函数各 3 种输入（正常 / 缺失 / 异常）；缺失 → None，绝不猜测
- [ ] `compute_signals` 节点雏形：mock 全 token 产出；单 token 异常不中断批
