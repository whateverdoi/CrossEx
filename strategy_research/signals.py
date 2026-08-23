"""② 确定性信号计算：纯函数（无 IO、可单测），输入缺失 → None。

- ``valuation_ratios``：估值比率（年化口径）
- ``momentum_score``：基本面动量分（tvl_change 7d/30d 加权）
- ``divergence``：价格 vs 基本面背离 + 四象限推导
- ``sentiment_raw``：持仓指标原始值直读（无阈值打分，LLM 按 prompt 解读）
- ``series_change`` / ``series_trend``：历史序列确定性趋势特征（01 票）
- ``funding_cross_sectional_z``：资金费率横截面 Z（08 票，第一层派生）
- ``volatility_metrics`` / ``beta_alpha``：波动率家族 / β·α 分解（klines 纯算）
- ``turnover`` / ``market_metrics``：换手率与市场派生指标汇总
- ``market_width``：全市场宽度聚合（涨跌家数比 / 中位数 / BTC 尾窗收益）

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


#: funding_z 横截面参照系（固定 4 主币，抗候选池漂移）
_FUNDING_Z_UNIVERSE = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT")


def funding_cross_sectional_z(rates: dict | None, symbol: str) -> float | None:
    """资金费率横截面 Z：目标币相对主币参照系的离差（σ 单位）。

    参照系固定 4 主币（不受批次候选池变化影响，跨运行可比）；z>0 = 费率
    高于主流（多头更拥挤），z<0 = 费率低于主流（空头更拥挤）。
    参照系有效样本 <2 / 目标缺失或非数值 → None；参照系无离散（std=0）
    → 0.0（无横截面离差信息，不猜方向）。
    """
    if not rates:
        return None
    xs = [
        rates[s]
        for s in _FUNDING_Z_UNIVERSE
        if isinstance(rates.get(s), (int, float))
    ]
    x = rates.get(symbol)
    if not isinstance(x, (int, float)) or len(xs) < 2:
        return None
    mean = sum(xs) / len(xs)
    var = sum((v - mean) ** 2 for v in xs) / len(xs)
    std = var**0.5
    if std == 0:
        return 0.0
    return (x - mean) / std


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


def beta_alpha(
    token_klines: list[dict] | None, btc_klines: list[dict] | None
) -> dict:
    """β·α 分解（相对 BTC）：β = cov(r_t, r_b)/var(r_b)；α = mean(r_t) − β×mean(r_b)。

    7d/30d 双窗口；α 为日超额收益（小数，正 = 相对 BTC 跑赢）；
    对齐样本不足 / BTC 无离散（var=0，除零）→ 对应字段 None（UNKNOWN 纪律）。
    """
    out = {"beta_7d": None, "beta_30d": None, "alpha_7d": None, "alpha_30d": None}
    for days, b_key, a_key in ((7, "beta_7d", "alpha_7d"), (30, "beta_30d", "alpha_30d")):
        pairs = _aligned_returns(token_klines, btc_klines, days)
        if not pairs:
            continue
        n = len(pairs)
        t_mean = sum(p[0] for p in pairs) / n
        b_mean = sum(p[1] for p in pairs) / n
        cov = sum((p[0] - t_mean) * (p[1] - b_mean) for p in pairs) / n
        var_b = sum((p[1] - b_mean) ** 2 for p in pairs) / n
        if var_b == 0:
            continue
        beta = cov / var_b
        out[b_key] = round(beta, 4)
        out[a_key] = round(t_mean - beta * b_mean, 6)
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
            "beta_7d",
            "beta_30d",
            "alpha_7d",
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


def sentiment_raw(mkt: dict | None, ms: dict | None = None) -> dict:
    """情绪维度：持仓指标原始值直读，不做阈值加减分。

    阈值离散化会丢失信息（连续值压成 ±0.25 三档），下游 LLM 按 prompt
    直接解读原始值；输入缺失的字段 → None（UNKNOWN 纪律）。
    """
    return {
        "components": {
            "funding": _v(mkt, "funding"),
            "funding_pctile_90d": _v(mkt, "funding_pctile_90d"),
            "funding_trend": _v(mkt, "funding_trend"),
            "ls_ratio_all": _v(ms, "ls_ratio_all"),
            "ls_ratio_top_acc": _v(ms, "ls_ratio_top_acc"),
            "ls_ratio_top_pos": _v(ms, "ls_ratio_top_pos"),
            "taker_bs_ratio": _v(ms, "taker_bs_ratio"),
            "oi_change_24h": _v(ms, "oi_change_24h"),
            "oi_price_divergence": _v(ms, "oi_price_divergence"),
            "funding_z": _v(mkt, "funding_z"),
        },
        "note": SENTIMENT_NOTE,
    }
