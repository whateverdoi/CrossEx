"""LLM 上下文装配模块：prompt 常量 + 摘要构建器 + 解读规则注记（02 票分支体系）。

LLM 的输入接口 = 两条分支 prompt（BULL/BEAR）+ 一个摘要构建器 + 情绪解读
规则注记，全部收敛在本模块一处定义（05 票：旧决策链 prompt/摘要退役，仅存
分支证据链）；signals/mock 共用同一注记常量，不再逐字复制。
契约测试（test_context.py）保证 prompt 引用的字段在渲染输出中必现，改动
一侧先红后绿。

本模块零依赖（不 import 项目内其他模块），保持叶子纯模块地位。
"""

from __future__ import annotations

from typing import Any

# ── 情绪解读规则注记（单一来源）────────────────────────

#: sentiment 解读规则锚点（与分支 prompt 一致）
SENTIMENT_NOTE = (
    "持仓指标原始直读；解读规则：funding 高=拥挤反向，多空比高=偏多；"
    "funding_pctile_90d 高分位=费率极端拥挤；oi_price_divergence 同向=趋势确认，背离=弱势"
)


# ── Prompt（分支证据 prompt，spec 四：BULL/BEAR）─────────────────

#: 分支证据 prompt 共性头部（02 票：basis 引用契约——domain/field/value 逐字来自输入）
_BRANCH_RULES = (
    "严格遵守：\n"
    "1. 严禁编造：claim 与 basis 只能引用输入数据中的既有字段与数值；缺失写 UNKNOWN，禁止猜测。\n"
    "2. 每条证据必须包含：claim（主张）、basis（结构化数据引用三元组：domain 数据域 / "
    "field 点号路径 / value 引用时点的快照值，逐字来自输入）、source（与 basis.domain 一致）。\n"
    "3. basis.field 必须引用到输入中的标量层（含 .value 后缀），如 momentum.value、"
    "divergence.value.quadrant、sentiment.components.funding；扫描器快照节字段自带完整"
    "路径（如 market.BTC.price）。\n"
    "4. 只提取"
)

_BRANCH_OUTPUT = (
    "证据，禁止给出决策、结论或建议（那是后续决策者的工作）。\n"
    "5. 数量 1-8 条，按重要性降序。\n"
    "6. 输出 JSON：{\"evidence\": [{\"claim\": \"...\", \"basis\": "
    "{\"domain\": \"...\", \"field\": \"...\", \"value\": \"...\"}, "
    "\"source\": \"...\"}]}。"
)

BULL_PROMPT = """你是一名多头证据研究员。基于给定数据，列出支持做多该资产的结构化证据条目。
""" + _BRANCH_RULES + "多头视角" + _BRANCH_OUTPUT

BEAR_PROMPT = """你是一名空头证据研究员。基于给定数据，列出支持做空该资产的结构化证据条目。
""" + _BRANCH_RULES + "空头视角" + _BRANCH_OUTPUT


# ── 摘要构建器（分支摘要，从 nodes 收敛至此）────────────────


def _dp_text(dp: dict | None, decimals: int = 2) -> str:
    """四元组 → 数值文本（费率等小数值用 decimals 保精度）；缺失 → UNKNOWN。"""
    v = (dp or {}).get("value")
    if v is None:
        return "UNKNOWN"
    if isinstance(v, float):
        return f"{v:.{decimals}f}"
    return str(v)


def _pct_text(dp: dict | None) -> str:
    """百分比字段渲染（value 本身是 % 数值）；缺失 → UNKNOWN。"""
    v = (dp or {}).get("value")
    return "UNKNOWN" if v is None else f"{v:.2f}%"


def _num_text(value: Any) -> str:
    """裸数值 → 2 位小数文本；非数值 → UNKNOWN。"""
    return "UNKNOWN" if not isinstance(value, (int, float)) else f"{value:.2f}"


def _snap_num(value: Any, decimals: int = 2) -> str:
    """扫描器裸数值 → 指定精度文本；缺失 → UNKNOWN。"""
    return (
        "UNKNOWN" if not isinstance(value, (int, float)) else f"{value:.{decimals}f}"
    )


def _snap_pct(value: Any) -> str:
    """扫描器百分比字段（value 本身是 % 数值）；缺失 → UNKNOWN。"""
    return "UNKNOWN" if not isinstance(value, (int, float)) else f"{value:.2f}%"


def _signal_lines(symbol: str, state: dict) -> list[str]:
    """信号节渲染（分支摘要共用）：估值/动量/背离/情绪原始直读。"""
    sig = (state.get("signals") or {}).get(symbol) or {}
    lines: list[str] = []
    val = (sig.get("valuation") or {}).get("value") or {}
    if val:
        lines.append(
            "valuation: " + " ".join(f"{k}={_num_text(v)}" for k, v in val.items())
        )
    else:
        lines.append("valuation: UNKNOWN")
    mom = (sig.get("momentum") or {}).get("value")
    lines.append(f"momentum: {_num_text(mom)}")
    div = (sig.get("divergence") or {}).get("value") or {}
    lines.append(
        "divergence: "
        + " ".join(
            (
                f"divergence_7d={_num_text(div.get('divergence_7d'))}",
                f"divergence_30d={_num_text(div.get('divergence_30d'))}",
                f"quadrant={div.get('quadrant') or 'UNKNOWN'}",
            )
        )
    )
    sent = (sig.get("sentiment") or {}).get("components") or {}
    if sent:
        lines.append(
            "sentiment: "
            + " ".join(
                f"{k}={v if v is not None else 'UNKNOWN'}" for k, v in sent.items()
            )
        )
    else:
        lines.append("sentiment: UNKNOWN")
    return lines


def _facts_summary_lines(symbol: str, state: dict) -> list[str]:
    """确定性快照 → LLM 摘要骨架（分支摘要共用）。

    只喂数字 + 变化率 + source 标签（含微观结构节）；缺失一律 UNKNOWN；
    新闻 ≤3 条；总行数 ≤50（约 1200 token/token，规格 ③-1）。
    """
    fund = (state.get("fundamental_data") or {}).get(symbol) or {}
    mkt = (state.get("market_data") or {}).get(symbol) or {}
    ms = (state.get("microstructure_data") or {}).get(symbol) or {}
    web = (state.get("web_data") or {}).get(symbol) or {}
    lines = [f"研究标的: {symbol}", "", "== 基本面（defillama）=="]
    kind = fund.get("kind") or "unknown"
    name = fund.get("name")
    lines.append(f"kind: {kind}" + (f" ({name})" if name else ""))
    for key in (
        "tvl",
        "tvl_change_1d",
        "tvl_change_7d",
        "tvl_change_30d",
        "mcap",
        "fdv",
    ):
        lines.append(f"{key}: {_dp_text(fund.get(key))}")
    if kind == "protocol":
        for key in ("fees_24h", "fees_7d", "revenue_24h", "revenue_7d"):
            lines.append(f"{key}: {_dp_text(fund.get(key))}")
    else:
        for key in ("stablecoin_supply", "dex_volume_24h"):
            lines.append(f"{key}: {_dp_text(fund.get(key))}")
    lines += ["", "== 市场（binance / binance_futures）=="]
    for key in ("price", "quote_volume_24h"):
        lines.append(f"{key}: {_dp_text(mkt.get(key))}")
    for key in ("change_24h", "change_7d", "change_30d", "change_90d", "change_1y"):
        lines.append(f"{key}: {_pct_text(mkt.get(key))}")
    lines.append(
        f"funding: {_dp_text(mkt.get('funding'), 6)}"
        f" funding_avg_7d: {_dp_text(mkt.get('funding_avg_7d'), 6)}"
        f" funding_trend: {_dp_text(mkt.get('funding_trend'))}"
    )
    lines.append(f"oi: {_dp_text(mkt.get('oi'))} basis: {_pct_text(mkt.get('basis'))}")
    lines.append(
        f"taker_buy_ratio_24h: {_dp_text(mkt.get('taker_buy_ratio_24h'))}"
        f" listing_days: {_dp_text(mkt.get('listing_days'))}"
    )
    lines += ["", "== 微观结构（binance_futures）=="]
    for key in (
        "oi_change_24h",
        "oi_change_48h",
        "oi_value_change_24h",
        "ls_ratio_all",
        "ls_ratio_all_change_24h",
        "ls_ratio_top_acc",
        "ls_ratio_top_pos",
        "taker_bs_ratio",
    ):
        lines.append(f"{key}: {_dp_text(ms.get(key))}")
    snap = state.get("scanner_snapshot") or {}
    msnap = (snap.get("market") or {}).get(symbol) or {}
    micsnap = (snap.get("microstructure") or {}).get(symbol) or {}
    if msnap or micsnap:  # 快照缺失 → 跳过该节，仅确定性信号照常
        lines += ["", "== 扫描器快照（BinanceApi）=="]
        if msnap:
            boards = "、".join(msnap.get("boards") or []) or "UNKNOWN"
            lines.append(
                f"scan_price: {_snap_num(msnap.get('price'), 6)}"
                f" scan_quote_volume_24h: {_snap_num(msnap.get('quote_volume_24h'))}"
                f" scan_open_interest_value: {_snap_num(msnap.get('open_interest_value'))}"
            )
            lines.append(
                f"scan_ret_1h: {_snap_pct(msnap.get('ret_1h'))}"
                f" scan_ret_4h: {_snap_pct(msnap.get('ret_4h'))}"
                f" scan_ret_24h: {_snap_pct(msnap.get('ret_24h'))}"
                f" scan_ret_7d: {_snap_pct(msnap.get('ret_7d'))}"
                f" scan_ret_24h_official: {_snap_pct(msnap.get('price_change_pct_24h'))}"
            )
            lines.append(
                f"scan_funding_rate: {_snap_num(msnap.get('funding_rate'), 6)}"
                f" scan_futures_premium_pct: {_snap_pct(msnap.get('futures_premium_pct'))}"
                f" scan_listing_days: {_snap_num(msnap.get('listing_days'), 1)}"
                f" scan_onboard_date: {msnap.get('onboard_date') or 'UNKNOWN'}"
                f" scan_boards: {boards}"
            )
        if micsnap:
            lines.append(
                f"scan_oi_change_24h: {_snap_pct(micsnap.get('oi_change_24h'))}"
                f" scan_oi_change_48h: {_snap_pct(micsnap.get('oi_change_48h'))}"
                f" scan_oi_value_change_24h: {_snap_pct(micsnap.get('oi_value_change_24h'))}"
                f" scan_ls_ratio_all: {_snap_num(micsnap.get('ls_ratio_all'))}"
                f" scan_ls_ratio_all_change_24h: {_snap_pct(micsnap.get('ls_ratio_all_change_24h'))}"
            )
            lines.append(
                f"scan_ls_ratio_top_acc: {_snap_num(micsnap.get('ls_ratio_top_acc'))}"
                f" scan_ls_ratio_top_pos: {_snap_num(micsnap.get('ls_ratio_top_pos'))}"
                f" scan_taker_bs_ratio: {_snap_num(micsnap.get('taker_bs_ratio'))}"
                f" scan_funding_avg_7d: {_snap_num(micsnap.get('funding_avg'), 6)}"
                f" scan_funding_trend: {micsnap.get('funding_trend') or 'UNKNOWN'}"
            )
    lines += ["", "== 信号（确定性计算）=="]
    lines += _signal_lines(symbol, state)
    lines += ["", "== 新闻（bing，≤3 条）=="]
    items = (web.get("items") or [])[:3]
    if not items:
        lines.append("UNKNOWN")
    for it in items:
        lines.append(
            f"{it.get('date')} | {it.get('title')} (source: {it.get('source')})"
        )
    return lines


def build_branch_summary(symbol: str, state: dict) -> str:
    """分支摘要（02 票：bull/bear 共用同一确定性快照）：骨架 + 指令行。

    两分支各自从同一份确定性快照提取证据，不含任何决策产物（05 票）。
    """
    return "\n".join(
        [*_facts_summary_lines(symbol, state), "", "只提取证据，禁止结论。"]
    )
