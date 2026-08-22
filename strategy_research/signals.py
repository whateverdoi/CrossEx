"""② 确定性信号计算：四个纯函数（无 IO、可单测），输入缺失 → None。

- ``valuation_ratios``：估值比率（年化口径）
- ``momentum_score``：基本面动量分（tvl_change 7d/30d 加权）
- ``divergence``：价格 vs 基本面背离 + 四象限推导
- ``sentiment_raw``：持仓指标原始值直读（无阈值打分，LLM 按 prompt 解读）

UNKNOWN 纪律：任何输入缺失/非法 → None，绝不猜测（规格 ②）。
"""

from __future__ import annotations

from typing import Any

from strategy_research.context import SENTIMENT_NOTE


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
        },
        "note": SENTIMENT_NOTE,
    }
