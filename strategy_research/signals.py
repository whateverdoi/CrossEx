"""② 确定性信号计算：纯函数（无 IO、可单测），输入缺失 → None。

- ``valuation_ratios``：估值比率（年化口径）
- ``momentum_score``：基本面动量分（tvl_change 7d/30d 加权）
- ``divergence``：价格 vs 基本面背离 + 四象限推导
- ``sentiment_raw``：持仓指标原始值直读（无阈值打分，LLM 按 prompt 解读）
- ``series_change`` / ``series_trend``：历史序列确定性趋势特征（01 票）
- ``funding_cross_sectional_pctile``：资金费率全市场横截面分位（08 票第一层派生；
  原 4 主币 z 分因参照系 σ 近 0 放大噪声而退役）
- ``volatility_metrics`` / ``beta_alpha``：波动率家族 / β·α 分解（klines 纯算；
  β/α 仅 30d 窗口，7d 因 n=7 无统计意义退役）
- ``turnover`` / ``market_metrics``：换手率与市场派生指标汇总
- ``market_width``：全市场宽度聚合（涨跌家数比 / 中位数 / BTC 尾窗收益）
- ``social_heat_trend`` / ``social_price_divergence``：社交热度趋势与社交/价格背离
  （X 社区活跃度维度，与 oi_price_divergence 同构四象限）

UNKNOWN 纪律：任何输入缺失/非法 → None，绝不猜测（规格 ②）。
"""

from __future__ import annotations

from datetime import datetime
from itertools import pairwise
from typing import Any

from strategy_research.context import SENTIMENT_NOTE

#: 趋势分档阈值（±3%，30 天窗口半段均值比）：≥+3% rising / ≤-3% falling
_TREND_THRESHOLD_PCT = 3.0


def _v(snap: dict | None, key: str) -> Any:
    """取四元组包装数据点的 value（快照缺失/字段缺失 → None）。

    不做类型过滤：funding_trend 等字符串字段也要直读。
    """
    if not snap:
        return None
    dp = snap.get(key)
    return dp.get("value") if isinstance(dp, dict) else None


def _num(value: Any) -> float | None:
    """数值校验：int/float 原样返回，其余（含 None/字符串）→ None。"""
    return value if isinstance(value, (int, float)) else None


def _ratio(num: Any, den: Any) -> float | None:
    """安全除法：任一非数值或分母 ≤0 → None（UNKNOWN 纪律）。"""
    num, den = _num(num), _num(den)
    if num is None or den is None or den <= 0:
        return None
    return num / den


def valuation_ratios(fund: dict | None, mkt: dict | None) -> dict:
    """估值比率（年化口径）：mc_fees / fdv_revenue / mc_tvl / fees_tvl。

    fees/revenue 用 24h 值 ×365 年化；chain 类无 mcap/fdv/fees → 自然 None；
    任一输入缺失 → 对应比率 None，绝不猜测。
    """
    fees_ann = _num(_v(fund, "fees_24h"))
    rev_ann = _num(_v(fund, "revenue_24h"))
    if fees_ann is not None:
        fees_ann *= 365.0
    if rev_ann is not None:
        rev_ann *= 365.0
    return {
        "value": {
            "mc_fees": _ratio(_v(fund, "mcap"), fees_ann),
            "fdv_revenue": _ratio(_v(fund, "fdv"), rev_ann),
            "mc_tvl": _ratio(_v(fund, "mcap"), _v(fund, "tvl")),
            "fees_tvl": _ratio(fees_ann, _v(fund, "tvl")),
        }
    }


def momentum_score(fund: dict | None) -> dict:
    """基本面动量分：tvl_change_7d / tvl_change_30d 各 0.5 权重加权均值（%）。

    任一项缺失 → None（UNKNOWN 纪律，不用单窗口凑数）。
    """
    c7 = _num(_v(fund, "tvl_change_7d"))
    c30 = _num(_v(fund, "tvl_change_30d"))
    if c7 is None or c30 is None:
        return {"value": None}
    return {"value": (c7 + c30) / 2.0}


def divergence(fund: dict | None, mkt: dict | None) -> dict:
    """背离：divergence = 基本面增速 - 价格涨幅（7d/30d 双窗口）。

    四象限（7d 窗口，0 为界）：I=双强 / II=弱基本强价格 /
    III=强基本弱价格（潜在做多候选）/ IV=双弱；任一缺失 → quadrant=None。
    """
    f7 = _num(_v(fund, "tvl_change_7d"))
    f30 = _num(_v(fund, "tvl_change_30d"))
    p7 = _num(_v(mkt, "change_7d"))
    p30 = _num(_v(mkt, "change_30d"))
    div7 = f7 - p7 if f7 is not None and p7 is not None else None
    div30 = f30 - p30 if f30 is not None and p30 is not None else None
    quad: str | None = None
    if f7 is not None and p7 is not None:
        if f7 > 0 and p7 > 0:
            quad = "I"
        elif f7 <= 0 and p7 > 0:
            quad = "II"
        elif f7 > 0 and p7 <= 0:
            quad = "III"
        else:
            quad = "IV"
    return {"value": {"divergence_7d": div7, "divergence_30d": div30, "quadrant": quad}}


def funding_percentile(hist: list[dict] | None) -> float | None:
    """资金费率极值分位：最新 |funding| 在窗口分布中的分位（0-100）。

    分位高 = 费率处于历史极端（多头或空头拥挤加剧），对短窗口单根异常平滑化。
    样本 <10 / 分布退化（常数序列）/ 非法值 → None（UNKNOWN 纪律）。
    入参时间升序，最新在末尾（fetch_funding_rate_history 同构）。
    """
    if not hist:
        return None
    last_raw = hist[-1].get("funding_rate")
    if not isinstance(last_raw, (int, float)):
        return None
    vals = [
        abs(r.get("funding_rate"))
        for r in hist
        if isinstance(r.get("funding_rate"), (int, float))
    ]
    if len(vals) < 10:
        return None
    if max(vals) == min(vals):
        return None
    last = abs(last_raw)
    return round(sum(1 for x in vals if x <= last) / len(vals) * 100.0, 1)


# ── 趋势特征（01 票：历史序列确定性提炼） ────────────────


def _date_ts(value: Any) -> float | None:
    """日期 → unix 秒：int/float 原样；ISO 字符串解析；其余 → None。"""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None
    return None


def _series_points(rows: list[dict] | None, key: str) -> list[tuple[float, float]]:
    """历史序列 → (unix 秒, 数值) 点表（升序，最新在末尾）；非法点过滤。"""
    pts = [
        (ts, val)
        for r in rows or []
        if (ts := _date_ts(r.get("date"))) is not None
        and (val := _num(r.get(key))) is not None
    ]
    return pts


def series_change(rows: list[dict] | None, key: str, days: int) -> float | None:
    """历史序列 → 最新 vs N 天前变化 %（确定性，UNKNOWN 纪律）。

    取最新点与 cutoff（最新日 - N 天）前最后一条对比；数据不足/基准 ≤0 → None。
    入参时间升序，最新在末尾（fetch_*_history 同构）。
    """
    pts = _series_points(rows, key)
    if len(pts) < 2 or days <= 0:
        return None
    last_ts, last_val = pts[-1]
    cutoff = last_ts - days * 86_400
    prev = next((p for p in reversed(pts[:-1]) if p[0] <= cutoff), None)
    if prev is None or prev[1] <= 0:
        return None
    return (last_val / prev[1] - 1.0) * 100.0


def series_trend(rows: list[dict] | None, key: str, days: int) -> str | None:
    """历史序列 → N 天趋势分类（前后半段均值比，±3% 阈值）。

    窗口内较旧半段 vs 较新半段均值变化率 ≥+3% → rising / ≤-3% → falling /
    其余 flat；窗口 <4 点（各半段至少 2 点）→ None（UNKNOWN 纪律）。
    """
    pts = _series_points(rows, key)
    if len(pts) < 2 or days <= 0:
        return None
    last_ts = pts[-1][0]
    cutoff = last_ts - days * 86_400
    window = [p for p in pts if p[0] >= cutoff]
    if len(window) < 4:
        return None
    mid = len(window) // 2
    mean_old = sum(p[1] for p in window[:mid]) / mid
    mean_new = sum(p[1] for p in window[mid:]) / (len(window) - mid)
    if mean_old <= 0 or mean_new <= 0:
        return None
    change = (mean_new / mean_old - 1.0) * 100.0
    if change >= _TREND_THRESHOLD_PCT:
        return "rising"
    if change <= -_TREND_THRESHOLD_PCT:
        return "falling"
    return "flat"


# ── 第一层派生（08 票：零成本派生数据维度） ────────────────


#: 横截面分位最小有效样本（全市场 premiumIndex 约数百合约，20 为兜底门槛）
_FUNDING_X_MIN_SAMPLES = 20


def funding_cross_sectional_pctile(rates: dict | None, symbol: str) -> float | None:
    """资金费率横截面分位：目标币费率在全市场合约分布中的有符号百分位（0-100）。

    替代 4 主币 z 分——主币费率彼此几乎相等 → σ 近 0 → 离差被放大成 +8σ/−28σ
    这类无意义值。分位口径与 funding_pctile_90d 一致，跨运行可比。
    高分位 = 多头付费远高于全市场（多头拥挤，反向）；低分位 = 空头付费主导
    （空头拥挤）。有效样本 <_FUNDING_X_MIN_SAMPLES / 目标缺失或非数值 → None；
    分布退化（全市场费率相同）→ 50.0（无横截面离差信息，不猜方向）。
    """
    if not rates:
        return None
    xs = [v for v in rates.values() if isinstance(v, (int, float))]
    x = rates.get(symbol)
    if not isinstance(x, (int, float)) or len(xs) < _FUNDING_X_MIN_SAMPLES:
        return None
    if max(xs) == min(xs):
        return 50.0
    return round(sum(1 for v in xs if v <= x) / len(xs) * 100.0, 1)


def _daily_returns(klines: list[dict] | None, days: int) -> list[float] | None:
    """最近 N 个日收益（小数，含未收盘 bar 口径，与 trailing_return 一致）。

    需要 N+1 根收盘价；任一非法 → None（UNKNOWN 纪律）。
    """
    if not klines or len(klines) < days + 1:
        return None
    closes = [k.get("close_price") for k in klines[-days - 1 :]]
    if any(not isinstance(c, (int, float)) for c in closes):
        return None
    rets: list[float] = []
    for prev, cur in pairwise(closes):
        if prev == 0:
            return None
        rets.append(cur / prev - 1.0)
    return rets


def volatility_metrics(klines: list[dict] | None) -> dict:
    """波动率家族：rv_7d / rv_30d 年化波动率 + drawdown_1y + 波动调整收益。

    rv_N = 最近 N 个日收益的 std × √365 × 100（年化 %）；
    drawdown_1y = 最新收盘距 365 天窗口最高收盘的回撤 %（≤0，新高 → 0.0）；
    vol_adj_ret_N = ret_N / (rv_N / √(365/N))，即 N 天收益占 N 天波动率的
    σ 单位数（同尺度可比，跨资产/跨窗口去波动率差异）。
    样本不足 / 非法值 → 对应字段 None（UNKNOWN 纪律）。
    """
    out = {
        "rv_7d": None,
        "rv_30d": None,
        "drawdown_1y": None,
        "vol_adj_ret_7d": None,
        "vol_adj_ret_30d": None,
    }
    closes = [
        k.get("close_price")
        for k in klines or []
        if isinstance(k.get("close_price"), (int, float))
    ]
    if len(closes) >= 2:
        win = closes[-365:]
        peak = max(win)
        if peak:
            out["drawdown_1y"] = round((closes[-1] / peak - 1.0) * 100.0, 4)
    for days, rv_key, adj_key in (
        (7, "rv_7d", "vol_adj_ret_7d"),
        (30, "rv_30d", "vol_adj_ret_30d"),
    ):
        rets = _daily_returns(klines, days)
        if not rets:
            continue
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        out[rv_key] = round(var**0.5 * (365.0**0.5) * 100.0, 4)
        prev = closes[-1 - days]
        ret_pct = (closes[-1] / prev - 1.0) * 100.0 if prev else None
        if ret_pct is not None and out[rv_key] != 0:  # rv=0（横盘）→ 调整收益无意义
            out[adj_key] = round(ret_pct / out[rv_key] * (365.0 / days) ** 0.5, 4)
    return {"value": out}


def _aligned_returns(
    token_klines: list[dict] | None, btc_klines: list[dict] | None, days: int
) -> list[tuple[float, float]] | None:
    """按 open_time 交集对齐的 (token 日收益, BTC 日收益) 序列（小数）。

    相邻 open_time 同日收盘价成对；对齐键 = open_time（毫秒）；
    对齐后收益不足 days 个 → None。
    """
    if not token_klines or not btc_klines:
        return None
    t_by_ts = {k.get("open_time"): k.get("close_price") for k in token_klines}
    b_by_ts = {k.get("open_time"): k.get("close_price") for k in btc_klines}
    ts = sorted(set(t_by_ts) & set(b_by_ts))
    pairs: list[tuple[float, float]] = []
    for prev_ts, cur_ts in pairwise(ts):
        tp, tc = t_by_ts[prev_ts], t_by_ts[cur_ts]
        bp, bc = b_by_ts[prev_ts], b_by_ts[cur_ts]
        if not all(
            isinstance(x, (int, float)) and x != 0 for x in (tp, bp, tc, bc)
        ):
            continue
        pairs.append((tc / tp - 1.0, bc / bp - 1.0))
    if len(pairs) < days:
        return None
    return pairs[-days:]


#: β/α 回看窗口 = 最小对齐样本数（二者同值，避免「窗口 30d 但只有 7 个点」的
#: 伪精度）：7 个日收益点估出的 β 是噪声（实测同一币 7d=2.29 与 30d=−0.65
#: 并存，两分支各取一端当论据），故 7d 窗口退役
_BETA_MIN_SAMPLES = 30


def beta_alpha(
    token_klines: list[dict] | None, btc_klines: list[dict] | None
) -> dict:
    """β·α 分解（相对 BTC，30d 窗口）：β = cov(r_t, r_b)/var(r_b)；
    α = mean(r_t) − β×mean(r_b)（日超额收益，正 = 相对 BTC 跑赢）。

    7d 窗口退役：n=7 的 β 估计无统计意义。对齐样本 <_BETA_MIN_SAMPLES /
    BTC 无离散（var=0，除零）→ 字段 None（UNKNOWN 纪律）。
    """
    out = {"beta_30d": None, "alpha_30d": None}
    pairs = _aligned_returns(token_klines, btc_klines, _BETA_MIN_SAMPLES)
    if pairs:
        n = len(pairs)
        t_mean = sum(p[0] for p in pairs) / n
        b_mean = sum(p[1] for p in pairs) / n
        cov = sum((p[0] - t_mean) * (p[1] - b_mean) for p in pairs) / n
        var_b = sum((p[1] - b_mean) ** 2 for p in pairs) / n
        if var_b != 0:
            beta = cov / var_b
            out["beta_30d"] = round(beta, 4)
            out["alpha_30d"] = round(t_mean - beta * b_mean, 6)
    return {"value": out}


def turnover(quote_volume: Any, mcap: Any) -> float | None:
    """24h 换手率：成交额 / 市值（0.05 = 5% 流通盘换手）。

    任一非数值或 ≤0 → None（UNKNOWN 纪律）。
    """
    qv, mc = _num(quote_volume), _num(mcap)
    if qv is None or mc is None or qv <= 0 or mc <= 0:
        return None
    return round(qv / mc, 4)


def market_metrics(fund: dict | None, mkt: dict | None) -> dict:
    """市场派生指标汇总：波动率家族 / β·α / 换手率。

    波动率与 β 在装配层（有 klines 处）算好写入 mkt，本函数仅直读组装；
    换手率 = quote_volume_24h / mcap（mcap 来自基本面）；
    输入缺失的字段 → None（UNKNOWN 纪律）。
    """
    out = {
        k: _v(mkt, k)
        for k in (
            "rv_7d",
            "rv_30d",
            "drawdown_1y",
            "vol_adj_ret_7d",
            "vol_adj_ret_30d",
            "beta_30d",
            "alpha_30d",
        )
    }
    out["turnover"] = turnover(_v(mkt, "quote_volume_24h"), _v(fund, "mcap"))
    return {"value": out}


#: 市场宽度轻流动性过滤阈值（24h 成交额 USDT）
_MARKET_WIDTH_MIN_QUOTE_VOL = 1e6


def market_width(fapi_tickers: dict | None, btc_klines: list[dict] | None) -> dict:
    """市场宽度（全市场聚合）：涨跌家数比 / 涨跌幅中位数 / BTC 尾窗收益。

    up_ratio_24h = 上涨家数占比（quote_volume ≥ 1e6 过滤，剔除无流动性噪音）；
    median_change_24h = 过滤后涨跌幅中位数；btc_ret_24h 直读 BTC 合约 ticker；
    btc_ret_7d/30d 从 BTC 日线尾窗收益（与 trailing_return 同口径）。
    过滤后空 / 输入缺失 → None（UNKNOWN 纪律）。
    """
    out = {
        "up_ratio_24h": None,
        "median_change_24h": None,
        "btc_ret_24h": None,
        "btc_ret_7d": None,
        "btc_ret_30d": None,
    }
    if fapi_tickers:
        rows = [
            t
            for t in fapi_tickers.values()
            if isinstance(t.get("quote_volume"), (int, float))
            and t["quote_volume"] >= _MARKET_WIDTH_MIN_QUOTE_VOL
            and isinstance(t.get("price_change_pct"), (int, float))
        ]
        if rows:
            chgs = sorted(t["price_change_pct"] for t in rows)
            n = len(chgs)
            med = chgs[n // 2] if n % 2 else (chgs[n // 2 - 1] + chgs[n // 2]) / 2.0
            out["up_ratio_24h"] = round(sum(1 for c in chgs if c > 0) / n, 4)
            out["median_change_24h"] = round(med, 4)
        btc = fapi_tickers.get("BTCUSDT")
        if isinstance(btc, dict) and isinstance(
            btc.get("price_change_pct"), (int, float)
        ):
            out["btc_ret_24h"] = btc["price_change_pct"]
    for days, key in ((7, "btc_ret_7d"), (30, "btc_ret_30d")):
        if not btc_klines or len(btc_klines) < days + 2:
            continue
        last = btc_klines[-1].get("close_price")
        prev = btc_klines[-1 - days].get("close_price")
        if last is None or prev in (None, 0):
            continue
        out[key] = round((last / prev - 1.0) * 100.0, 4)
    return {"value": out}


#: 多空爆仓失衡阈值：ratio >= 1.2 多头爆仓主导（下行压力）；<= 1/1.2 空头主导
_LIQ_IMBALANCE_THRESHOLD = 1.2

#: 爆仓失衡最小名义额门槛（USDT，24h 多空爆仓合计）：低于此额的 ratio 由尘埃
#: 爆仓构成，标签无信息量（实测几十美元级爆仓也能打出 long_heavy）
_LIQ_IMBALANCE_MIN_TOTAL_USD = 10_000.0


def liquidation_imbalance(long_liq: Any, short_liq: Any) -> dict | None:
    """多空爆仓失衡（24h 聚合额）：ratio = 多头爆仓额 / 空头爆仓额。

    ratio 高 = 多头被迫平仓主导（下行压力）；低 = 空头被爆主导（回补反弹
    压力）；任一缺失 → None；双方合计 <_LIQ_IMBALANCE_MIN_TOTAL_USD → None
    （尘埃爆仓不出标签）；双方均无爆仓 → None（零爆仓无信息不猜方向）；
    仅多头有爆仓（空头为零）→ ratio=None + long_heavy（避免 inf 序列化）。
    """
    long_liq, short_liq = _num(long_liq), _num(short_liq)
    if long_liq is None or short_liq is None:
        return None
    if long_liq <= 0 and short_liq <= 0:
        return None
    if long_liq + short_liq < _LIQ_IMBALANCE_MIN_TOTAL_USD:
        return None
    ratio = long_liq / short_liq if short_liq > 0 else None
    if ratio is None or ratio >= _LIQ_IMBALANCE_THRESHOLD:
        label, note = "long_heavy", "多头爆仓额高于空头（多头被迫平仓，下行压力大）"
    elif ratio <= 1.0 / _LIQ_IMBALANCE_THRESHOLD:
        label, note = "short_heavy", "空头爆仓额高于多头（空头被迫回补，反弹压力大）"
    else:
        label, note = "balanced", "多空爆仓额接近（强平压力均衡）"
    return {"ratio": ratio, "label": label, "note": note}


# ── 交易结构（可执行性维度：证据表此前完全不涉及能不能成交、代价多少） ──


def funding_interval_hours(rows: list[dict] | None) -> float | None:
    """结算间隔（小时）：费率历史相邻时间戳差值的中位数。

    不能一律按 8h——部分合约 4h/1h 结算，按 8h 算持仓成本会低估 2~8 倍。
    样本 <2 / 时间戳非法 → None（UNKNOWN 纪律）。
    """
    ts = sorted(
        r.get("funding_time")
        for r in rows or []
        if isinstance(r.get("funding_time"), (int, float))
    )
    diffs = [b - a for a, b in pairwise(ts) if b > a]
    if len(diffs) < 2:
        return None
    median_ms = sorted(diffs)[len(diffs) // 2]
    hours = median_ms / 3_600_000.0
    return round(hours, 2) if hours > 0 else None


def funding_carry_pct(
    funding: Any, interval_hours: Any, days: int
) -> float | None:
    """按当前费率持有 N 天的资金费成本（%，正 = 多头支付、负 = 空头支付）。

    = funding × (24 / 结算间隔) × days × 100；与价格涨跌同尺度，可直接
    判断「一个 -30% 的做空论证是否被持仓成本吃掉」。任一输入缺失/非法
    或间隔 ≤0 或 days ≤0 → None（UNKNOWN 纪律）。
    """
    f, h = _num(funding), _num(interval_hours)
    if f is None or h is None or h <= 0 or days <= 0:
        return None
    return round(f * (24.0 / h) * days * 100.0, 4)


#: 盘口深度带：最优价向两侧各 2% 以内的累计名义额
_DEPTH_BAND_PCT = 2.0


def book_structure(book: dict | None, band_pct: float = _DEPTH_BAND_PCT) -> dict:
    """盘口 → 交易结构：点差 % + 中价两侧 band_pct% 带内可成交名义额（USDT）。

    spread_pct = (最优卖 - 最优买) / 中价 × 100（市价单立即付出的成本）；
    bid/ask_depth_usd = 带内各档 price×qty 累计——决定能不能上量；
    档位不足覆盖整带时该深度只是**下限**，用 ``*_exhausted`` 标记（不把
    下限当实测值呈现，UNKNOWN 纪律的延伸）。
    任一侧盘口为空 → 对应字段 None；盘口整体缺失 → 全 None。
    """
    out = {
        "spread_pct": None,
        "bid_depth_usd": None,
        "ask_depth_usd": None,
        "depth_band_exhausted": None,
    }
    bids = (book or {}).get("bids") or []
    asks = (book or {}).get("asks") or []
    if not bids or not asks:
        return out
    best_bid, best_ask = bids[0][0], asks[0][0]
    mid = (best_bid + best_ask) / 2.0
    if mid <= 0:
        return out
    out["spread_pct"] = round((best_ask - best_bid) / mid * 100.0, 4)
    lo, hi = mid * (1.0 - band_pct / 100.0), mid * (1.0 + band_pct / 100.0)

    def _side(levels: list, within) -> tuple[float | None, bool]:
        rows = [lv for lv in levels if within(lv[0])]
        total = sum(lv[0] * lv[1] for lv in rows if isinstance(lv[1], (int, float)))
        # 末档仍在带内 = 档位被带截断，累计值只是下限
        return round(total, 2), bool(levels and within(levels[-1][0]))

    bid_total, bid_ex = _side(bids, lambda p: lo <= p <= mid)
    ask_total, ask_ex = _side(asks, lambda p: mid <= p <= hi)
    out["bid_depth_usd"] = bid_total
    out["ask_depth_usd"] = ask_total
    out["depth_band_exhausted"] = bid_ex or ask_ex
    return out


def oi_price_divergence(
    price_ret: float | None, oi_change: float | None
) -> dict | None:
    """OI/价格背离四象限：价 OI 同向 = 新仓进场（趋势确认），背离 = 存量换手。

    confirm_long / weak_long / confirm_short / weak_short / none；
    输入缺失 → None（UNKNOWN 纪律）；零值 → none（零值无方向）。
    """
    if price_ret is None or oi_change is None:
        return None
    if price_ret == 0 or oi_change == 0:
        return {"label": "none", "note": "价格或 OI 变化为零，无法判向"}
    if price_ret > 0 and oi_change > 0:
        return {"label": "confirm_long", "note": "价涨 OI 增：新多进场，趋势确认"}
    if price_ret > 0:
        return {"label": "weak_long", "note": "价涨 OI 缩：空头回补驱动，持续性弱"}
    if oi_change > 0:
        return {"label": "confirm_short", "note": "价跌 OI 增：新空进场，趋势确认"}
    return {"label": "weak_short", "note": "价跌 OI 缩：存量平仓驱动，趋势健康度弱"}


def _heat_split(posts: list[dict]) -> tuple[list[dict], list[dict]] | None:
    """样本分档 → (近期侧, 更早侧)；不足最小样本 → None（两函数共用，档名不漂移）。

    门槛取小样本兼容：未登录 x.com profile 页仅渲染约 5-7 条推文（登录墙截断），
    ≥7 条走 5 vs 其余，恰好 6 条退化为 3 vs 3，<6 条不派生（UNKNOWN 纪律）。
    """
    n = len(posts)
    if n >= 7:
        return posts[-5:], posts[:-5]
    if n == 6:
        return posts[-3:], posts[:3]
    return None


def social_heat_window(posts: list[dict] | None) -> str | None:
    """热度读数的样本档名（档名即算式，两档之间不可互相比较）。

    ``recent5_vs_prior_median`` / ``recent3_vs_prior3_median``；样本不足 → None。
    与 ``social_heat_trend`` 同源分档，供摘要与快照标注该数值是哪种算式产出的。
    """
    split = _heat_split(posts or [])
    if split is None:
        return None
    return "recent5_vs_prior_median" if len(split[0]) == 5 else "recent3_vs_prior3_median"


def social_heat_trend(posts: list[dict] | None) -> float | None:
    """社交热度变化：近期侧均值 vs 更早侧中位数的互动强度比 - 1（%）。

    posts 为 X 互动序列（时间升序，最新在末尾），每条含 likes/reposts/comments
    原样文本（如 "14" / "120"）；互动强度 = likes + reposts + comments（views
    是触达非互动，不计）；中位数抗单条爆款脉冲；样本分档见 ``_heat_split``，
    不足档或任一侧无有效数值 → None（UNKNOWN 纪律）。
    """
    from .datasources.x_social import parse_compact_number  # 延迟导入避免循环

    split = _heat_split(posts or [])
    if split is None:
        return None
    recent_posts, prior_posts = split

    def _eng_sum(p: dict) -> float:
        vals = [parse_compact_number(p.get(k)) for k in ("likes", "reposts", "comments")]
        vals = [v for v in vals if v is not None]
        return sum(vals) if vals else 0.0

    recent = [v for v in (_eng_sum(p) for p in recent_posts) if v > 0]
    prior = [v for v in (_eng_sum(p) for p in prior_posts) if v > 0]
    if not recent or not prior:
        return None
    recent_avg = sum(recent) / len(recent)
    prior_med = sorted(prior)[len(prior) // 2]  # 中位数（偶数侧取偏上位，不取均值）
    if prior_med <= 0:
        return None
    return round((recent_avg / prior_med - 1.0) * 100.0, 1)


def social_price_divergence(
    price_ret: float | None, heat_trend: float | None
) -> dict | None:
    """社交/价格背离四象限：价与社区热度同向 = 趋势确认，背离 = 缺社区支撑。

    confirm_long / weak_long / confirm_short / weak_short / none；
    输入缺失 → None（UNKNOWN 纪律）；零值 → none（零值无方向）。
    """
    if price_ret is None or heat_trend is None:
        return None
    if price_ret == 0 or heat_trend == 0:
        return {"label": "none", "note": "价格或社交热度变化为零，无法判向"}
    if price_ret > 0 and heat_trend > 0:
        return {"label": "confirm_long", "note": "价涨社区热度升：趋势确认，市场关注度同步"}
    if price_ret > 0:
        return {"label": "weak_long", "note": "价涨社区热度降：上涨缺社区支撑，持续性弱"}
    if heat_trend > 0:
        return {"label": "weak_short", "note": "价跌社区热度升：社区逆势活跃，可能错杀"}
    return {"label": "confirm_short", "note": "价跌社区热度降：趋势确认，关注度同步退潮"}


def sentiment_raw(mkt: dict | None, ms: dict | None = None, soc: dict | None = None) -> dict:
    """情绪维度：持仓指标原始值直读，不做阈值加减分。

    阈值离散化会丢失信息（连续值压成 ±0.25 三档），下游 LLM 按 prompt
    直接解读原始值；输入缺失的字段 → None（UNKNOWN 纪律）。
    soc 为社交快照（social_data），直读社交热度趋势与社交/价格背离标签。
    """
    return {
        "components": {
            "funding": _v(mkt, "funding"),
            "funding_pctile_90d": _v(mkt, "funding_pctile_90d"),
            "funding_trend": _v(mkt, "funding_trend"),
            "ls_ratio_all": _v(ms, "ls_ratio_all"),
            "ls_ratio_top_acc": _v(ms, "ls_ratio_top_acc"),
            "ls_ratio_top_pos": _v(ms, "ls_ratio_top_pos"),
            "taker_bs_ratio_1h": _v(ms, "taker_bs_ratio_1h"),
            "oi_change_24h": _v(ms, "oi_change_24h"),
            "oi_price_divergence": _v(ms, "oi_price_divergence"),
            "funding_x_pctile": _v(mkt, "funding_x_pctile"),
            "social_heat_trend": _v(soc, "social_heat_trend"),
            "social_price_divergence": _v(soc, "social_price_divergence"),
        },
        "note": SENTIMENT_NOTE,
    }
