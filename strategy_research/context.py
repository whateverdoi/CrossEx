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

#: 分支证据 prompt 共性头部（02 票：basis 引用契约——domain/field/value 逐字来自输入；
#: 06 票补 domain 白名单枚举：LLM 曾用旧数据源名（defillama/market/binance 等）当 domain，
#: 被核验全部剔除——数据域契约必须写进 prompt，spec D4 白名单与核验 _DOMAIN_KEYS 对齐）
_BRANCH_RULES = (
    "严格遵守：\n"
    "1. 严禁编造：claim 与 basis 只能引用输入数据中的既有字段与数值；缺失写 UNKNOWN，禁止猜测。\n"
    "2. 每条证据必须包含：claim（主张）、basis（结构化数据引用三元组：domain 数据域 / "
    "field 点号路径 / value 引用时点的快照值，逐字来自输入）、source（与 basis.domain 一致）。\n"
    "3. basis.domain 只能取数据域白名单之一：signals / market_data / fundamental_data / "
    "microstructure_data / web_data / scanner_snapshot；禁止使用数据源名（binance / "
    "binance_futures / defillama / bing 等）或节标题（市场/基本面/新闻）作为 domain。\n"
    "4. basis.field 必须引用到输入中的标量层（含 .value 后缀），如 momentum.value、"
    "divergence.value.quadrant、sentiment.components.funding、tvl.value、"
    "oi_change_24h.value；扫描器快照节字段自带完整路径（如 market.{SYMBOL}.price）。\n"
    "5. basis.value 必须逐字引用输入中该字段的显示值，禁止改写、重算或四舍五入。\n"
    "6. 只提取"
)

_BRANCH_OUTPUT = (
    "证据，禁止给出决策、结论或建议（那是后续决策者的工作）。\n"
    "7. 扫描器快照节是截至节标题标注日期的静态历史数据，引用该节字段时"
    "claim 必须注明快照日期口径，禁止把快照窗口涨跌与实时数据（market_data 等）"
    "当作同一时点并置比较。\n"
    "8. claim 中出现的每个数值必须来自其 basis 引用的字段（允许该字段的派生"
    "表述），禁止把其他字段的数值归因到本字段（如把全账户多空比变化说成"
    "顶级账户变化），禁止使用输入中不存在的计算值（如比率换算、合成指标）。\n"
    "9. 数量 1-8 条，按重要性降序；充分挖掘摘要中的独立事实，有几条写几条，"
    "禁止凑满上限——禁止把同一事实拆成多条（如同一 claim 换 basis 重复引用）。\n"
    '10. 输出 JSON：{"evidence": [{"claim": "...", "basis": '
    '{"domain": "...", "field": "...", "value": "..."}, '
    '"source": "..."}]}。'
)

BULL_PROMPT = (
    """你是一名多头证据研究员。基于给定数据，列出支持做多该资产的结构化证据条目。
"""
    + _BRANCH_RULES
    + "多头视角"
    + _BRANCH_OUTPUT
)

BEAR_PROMPT = (
    """你是一名空头证据研究员。基于给定数据，列出支持做空该资产的结构化证据条目。
"""
    + _BRANCH_RULES
    + "空头视角"
    + _BRANCH_OUTPUT
)


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
    return "UNKNOWN" if not isinstance(value, (int, float)) else f"{value:.{decimals}f}"


def _snap_pct(value: Any) -> str:
    """扫描器百分比字段（value 本身是 % 数值）；缺失 → UNKNOWN。"""
    return "UNKNOWN" if not isinstance(value, (int, float)) else f"{value:.2f}%"


def _signal_lines(symbol: str, state: dict) -> list[str]:
    """信号节渲染（分支摘要共用）：行前缀 = 完整域内路径（06 票）。

    LLM 曾因裸键展平（funding_pctile_90d: 42）猜错 domain/field 被核验剔除；
    现在直接教路径：divergence.value.quadrant / sentiment.components.funding 等，
    与核验 _resolve 的 state[signals][symbol] 结构一一对应。
    """
    sig = (state.get("signals") or {}).get(symbol) or {}
    lines: list[str] = []
    val = (sig.get("valuation") or {}).get("value") or {}
    if val:
        lines.extend(f"valuation.value.{k}: {_num_text(v)}" for k, v in val.items())
    else:
        lines.append("valuation: UNKNOWN")
    mom = (sig.get("momentum") or {}).get("value")
    lines.append(f"momentum: {_num_text(mom)}")
    div = (sig.get("divergence") or {}).get("value") or {}
    for key in ("divergence_7d", "divergence_30d"):
        lines.append(f"divergence.value.{key}: {_num_text(div.get(key))}")
    lines.append(f"divergence.value.quadrant: {div.get('quadrant') or 'UNKNOWN'}")
    comps = (sig.get("sentiment") or {}).get("components") or {}
    if comps:
        for k, v in comps.items():
            if isinstance(v, dict):  # 嵌套标签对象（label/note）继续展平
                lines.extend(
                    f"sentiment.components.{k}.{kk}: {vv if vv is not None else 'UNKNOWN'}"
                    for kk, vv in v.items()
                )
            else:
                lines.append(
                    f"sentiment.components.{k}: {v if v is not None else 'UNKNOWN'}"
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
    lines = [f"研究标的: {symbol}", "", "== 基本面（fundamental_data）=="]
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
    lines += ["", "== 市场（market_data）=="]
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
    lines += ["", "== 微观结构（microstructure_data）=="]
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
        day = snap.get("date") or "未知"
        lines += [
            "",
            f"== 扫描器快照（scanner_snapshot，截至 {day}，field 直接抄写下方完整路径）==",
        ]
        if msnap:
            boards = "、".join(msnap.get("boards") or []) or "UNKNOWN"
            lines.append(
                f"market.{symbol}.price: {_snap_num(msnap.get('price'), 6)}"
                f" market.{symbol}.quote_volume_24h: {_snap_num(msnap.get('quote_volume_24h'))}"
                f" market.{symbol}.open_interest_value: {_snap_num(msnap.get('open_interest_value'))}"
            )
            lines.append(
                f"market.{symbol}.ret_1h: {_snap_pct(msnap.get('ret_1h'))}"
                f" market.{symbol}.ret_4h: {_snap_pct(msnap.get('ret_4h'))}"
                f" market.{symbol}.ret_24h: {_snap_pct(msnap.get('ret_24h'))}"
                f" market.{symbol}.ret_7d: {_snap_pct(msnap.get('ret_7d'))}"
                f" market.{symbol}.price_change_pct_24h: {_snap_pct(msnap.get('price_change_pct_24h'))}"
            )
            lines.append(
                f"market.{symbol}.funding_rate: {_snap_num(msnap.get('funding_rate'), 6)}"
                f" market.{symbol}.futures_premium_pct: {_snap_pct(msnap.get('futures_premium_pct'))}"
                f" market.{symbol}.listing_days: {_snap_num(msnap.get('listing_days'), 1)}"
                f" market.{symbol}.onboard_date: {msnap.get('onboard_date') or 'UNKNOWN'}"
                f" market.{symbol}.boards: {boards}"
            )
        if micsnap:
            lines.append(
                f"microstructure.{symbol}.oi_change_24h: {_snap_pct(micsnap.get('oi_change_24h'))}"
                f" microstructure.{symbol}.oi_change_48h: {_snap_pct(micsnap.get('oi_change_48h'))}"
                f" microstructure.{symbol}.oi_value_change_24h: {_snap_pct(micsnap.get('oi_value_change_24h'))}"
                f" microstructure.{symbol}.ls_ratio_all: {_snap_num(micsnap.get('ls_ratio_all'))}"
                f" microstructure.{symbol}.ls_ratio_all_change_24h: {_snap_pct(micsnap.get('ls_ratio_all_change_24h'))}"
            )
            lines.append(
                f"microstructure.{symbol}.ls_ratio_top_acc: {_snap_num(micsnap.get('ls_ratio_top_acc'))}"
                f" microstructure.{symbol}.ls_ratio_top_pos: {_snap_num(micsnap.get('ls_ratio_top_pos'))}"
                f" microstructure.{symbol}.taker_bs_ratio: {_snap_num(micsnap.get('taker_bs_ratio'))}"
                f" microstructure.{symbol}.funding_avg: {_snap_num(micsnap.get('funding_avg'), 6)}"
                f" microstructure.{symbol}.funding_trend: {micsnap.get('funding_trend') or 'UNKNOWN'}"
            )
    lines += ["", "== 信号（signals）=="]
    lines += _signal_lines(symbol, state)
    lines += ["", "== 新闻（web_data，≤3 条）=="]
    items = (web.get("items") or [])[:3]
    if not items:
        lines.append("UNKNOWN")
    for i, it in enumerate(items):
        lines.append(
            f"items[{i}].title: {it.get('title') or 'UNKNOWN'}"
            f"（date: {it.get('date') or 'UNKNOWN'}，source: {it.get('source') or 'UNKNOWN'}）"
        )
    return lines


def build_branch_summary(symbol: str, state: dict) -> str:
    """分支摘要（02 票：bull/bear 共用同一确定性快照）：骨架 + 指令行。

    两分支各自从同一份确定性快照提取证据，不含任何决策产物（05 票）。
    """
    return "\n".join(
        [*_facts_summary_lines(symbol, state), "", "只提取证据，禁止结论。"]
    )
