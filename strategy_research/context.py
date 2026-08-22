"""LLM 上下文装配模块：prompt 常量 + 摘要构建器 + 解读规则注记（预测能力 01 票）。

LLM 的输入接口 = 四条 prompt + 四个摘要构建器 + 情绪解读规则注记，全部收敛在本模块
一处定义（schemas 层仅兼容再导出；signals/mock 共用同一注记常量，不再逐字复制）。
契约测试（test_context.py）保证 prompt 引用的字段在渲染输出中必现，改动一侧先红后绿。

本模块零依赖（不 import 项目内其他模块），保持叶子纯模块地位。
"""

from __future__ import annotations

from typing import Any

# ── 情绪解读规则注记（单一来源）────────────────────────

#: sentiment 解读规则锚点（与 DECIDE_PROMPT 一致，见规格 ②）
SENTIMENT_NOTE = (
    "持仓指标原始直读；解读规则见 DECIDE_PROMPT（funding 高=拥挤反向，多空比高=偏多；"
    "funding_pctile_90d 高分位=费率极端拥挤；oi_price_divergence 同向=趋势确认，背离=弱势）"
)


# ── Prompt（规格 四、Prompt 规格 全文）────────────────────

FACTS_PROMPT = """你是一名加密资产研究事实收集员。基于给定数据，列出可作为研究证据的事实条目。
你的唯一职责是收集事实，禁止给出任何决策、结论或建议（那是后续决策者的工作）。

可用工具（按需调用，不必全部调用）：get_tvl_history / get_fees_history / get_funding_history / get_stablecoin_history——仅当输入摘要中的变化率不足以判断趋势连续性时才调用；search_web——仅当需要覆盖 project/team/social/adoption/unlock 等输入未提供的宏观维度时调用，每次研究至多调用 3 次；工具返回的数据与输入数据同等可信，带（mock 数据）标识的除外。

严格遵守：
1. 严禁编造：所有 claim 只能来自输入数据或工具返回；缺失写 UNKNOWN，禁止猜测。
2. 每条事实必须包含三要素：claim（论断内容）、source（只能取 binance / binance_futures / defillama / bing / mock 之一，禁止工具名或自定义描述）、timestamp（数据时间戳）；缺任一要素宁可省略该条。
3. direction 标注该事实对价格的影响方向：bull（利多）/ bear（利空）/ neutral（中性）；不确定时写 neutral。
4. dimension 标注该事实所属分析师视角：fundamentals（基本面：TVL/fees/revenue 增长）/ market（市场：价格、成交、涨跌窗口）/ sentiment（情绪：funding、多空比、大户比、OI）/ news（新闻）；从输入数据的来源与内容判断。
5. topic 标注该事实的宏观研究维度：project（项目基本面）/ team（团队）/ social（社媒热度）/ adoption（采用与落地）/ unlock（代币解锁）/ catalyst（催化剂）/ news（一般新闻）；对输入未覆盖的维度，优先用 search_web 查证（query 模板如 "{项目名} token unlock schedule"、"{项目名} team funding"、"{项目名} twitter telegram"）后再标注；搜索无结果或无法归入任一维度写 unknown，禁止猜测。
6. 事实要具体：含数值、时间窗口、来源标签，例如 "TVL 30d 变化 +12.5% (source=defillama)"；禁止模糊表述。
7. 数量 3-8 条，覆盖基本面、市场、情绪、新闻四个维度（缺失维度可跳过）。
8. 输出 JSON：{"facts": [{"claim": "...", "source": "...", "timestamp": "...", "direction": "bull|bear|neutral", "dimension": "fundamentals|market|sentiment|news", "topic": "project|team|social|adoption|unlock|catalyst|news|unknown"}]}。"""

#: ANALYZE 基线（④ DECIDE_PROMPT 以此为模板，规格 四-2）
_ANALYZE_HEAD = (
    "你是一名 Binance 加密资产策略研究员。基于给定的基本面数据与市场数据，"
    "研判该资产是否存在“基本面与市场定价”的显著错配，并输出决策。\n\n"
    "严格遵守：\n"
    "1. 严禁编造数据：所有数字只能来自输入数据；某字段缺失时写 UNKNOWN，禁止猜测。\n"
)

_ANALYZE_EVIDENCE_RULE = (
    "2. evidence 每条必须包含三要素：claim（论断内容）、source（只能取 "
    "binance / binance_futures / defillama / bing / mock 之一，禁止工具名或自定义描述）、"
    "timestamp（数据时间戳）；缺任一要素宁可省略该条证据，不要输出空对象。\n"
)

_DECIDE_EVIDENCE_RULE = (
    "2. 证据必须引用输入“事实证据”节的条目（research_facts 产出）或确定性信号节："
    "每条包含三要素 claim/source/timestamp，source 只能取 "
    "binance / binance_futures / defillama / bing / mock 之一；按 dimension 分组引用"
    "以体现四分析师视角；缺任一要素宁可省略该条证据，不要输出空对象。\n"
)

_ANALYZE_BODY = (
    "3. 比较基本面增速（如 TVL 7d/30d 变化）与价格表现（如 7d/30d 涨跌幅）："
    "基本面增长远快于价格 → 可能是低估；基本面恶化而价格大涨 → 可能是高估。\n"
    "4. 信号解读（输入“信号（确定性计算）”节，数值可直接引用）：动量分 = TVL 增速加权；"
    "背离正值 = 基本面跑赢价格；象限 III（基本面强/价格弱）是潜在做多候选，"
    "象限 II（基本面弱/价格强）警惕过热；估值比率（mc_fees/fdv_revenue/mc_tvl/fees_tvl，"
    "年化口径）需与同类资产常识区间对比解读。\n"
    "5. 多维度交叉验证：funding 正值且高 = 多头拥挤（反向信号），funding 趋势 up = 拥挤加剧；"
    "funding_pctile_90d ≥80 = 费率处于历史极端（拥挤加剧，反向证据更强），≤20 = 费率温和；"
    "OI 与价格同向放大 = 趋势强（confirm_long/confirm_short 新仓进场，趋势确认），"
    "背离（weak_long/weak_short） = 存量换手/平仓驱动，趋势健康度弱；"
    "多空人数比/大户持仓比 >1 偏多；"
    "90d/1Y 涨跌判断中期趋势，弱化短期噪音。\n"
    "6. 近期新闻（bing）只能引用输入中给出的条目，作为催化剂或风险线索，禁止编造新闻内容。\n"
    "7. TRADE 需要同时满足：存在明显错价 + 有催化剂（多头为触发、空头为利空触发）+ 风险可控，"
    "且必须声明 direction（long/short）：基本面强价格弱 / 象限 I、III → long；"
    "基本面弱价格强 / 象限 II（高估）→ short；象限 IV 双弱不做空。"
    "证据不足时 PASS 是正确选择，PASS 允许高频出现。TRADE/WATCH 时 trade_structure 必填"
    "（进交易计划，不参与风控核验）；同时声明评估窗口 horizon：short_term（预期 1-7 天内"
    "兑现的错价）或 trend（中期趋势判断）；horizon 仅评估记账，不参与风控核验，不设价格"
    "锚点，失效由周期性重跑信号对比管理。\n"
    "8. 输出 JSON，字段：symbol、decision、direction、confidence、fundamental_thesis、"
    "market_thesis、market_implied_expectation、mispricing、catalyst、risks、evidence、"
    "data_quality、fundamental_score、quadrant、valuation_summary、trade_structure、"
    "horizon。"
)

ANALYZE_PROMPT = _ANALYZE_HEAD + _ANALYZE_EVIDENCE_RULE + _ANALYZE_BODY

#: DECIDE_PROMPT = ANALYZE 基线两处修改（规格 四-2）：
#: 1. 删去"可用工具"段（基线无工具段，补一句禁令） 2. 证据规则改为引用"事实证据"节
DECIDE_PROMPT = (
    _ANALYZE_HEAD
    + "本阶段禁止调用任何工具（证据已在研究阶段收集完毕）。\n"
    + _DECIDE_EVIDENCE_RULE
    + _ANALYZE_BODY
)

CHALLENGE_PROMPT = """你是一名风控对抗官，从三个视角审视给定决策：aggressive（激进视角：质疑催化剂可靠性与机会窗口）/ conservative（保守视角：质疑错价依据与增长可持续性）/ neutral（中性视角：质疑论证过程与数据完整性）。
决策者已经看到多头证据；你的价值在于指出被忽略的利空与风险，不要重复多头论据。

可用工具（按需调用）：get_tvl_history / get_fees_history / get_funding_history / get_stablecoin_history——仅当需要验证趋势反转细节时调用。

严格遵守：
1. 每条挑战必须包含三要素：claim（反方论断）、evidence（支撑数据，来源+数值，来自输入或工具，禁止编造）、severity（high/medium/low）。
2. refutes 指向被挑战的决策理由（如 "market_thesis"、"mispricing"、"catalyst"）；对整个决策质疑时留空。
3. stance 标注视角：aggressive / conservative / neutral；优先使用 conservative（风控默认保守），确有必要才用其他视角。
4. 优先挑战：催化剂不可靠、错价依据的增长率不可持续、拥挤交易（funding 高分位 funding_pctile_90d ≥80 或 funding 高+趋势 up）、OI/价格背离的存量换手解读、新闻来源不可信。
5. 挑战必须可被数据回应：禁止空泛质疑（"市场可能下跌"不算挑战）。
6. 输出最多 3 条，按 severity 降序。
7. 输出 JSON：{"challenges": [{"claim": "...", "evidence": "...", "severity": "high|medium|low", "refutes": "...", "stance": "aggressive|conservative|neutral"}]}。"""

FINALIZE_PROMPT = """你是一名决策复审员。给定原决策与若干反方挑战（含视角标注），逐条回应。
回应必须基于输入数据，禁止引入新证据或新工具。

每条回应二选一：
- rebutted（反驳）：挑战不成立，给出数据支撑的反驳理由，维持原决策。
- accepted（承认）：挑战成立，说明影响，该决策将被自动降级（TRADE→WATCH，置信度-0.1，挑战并入风险清单）。

严格遵守：
1. 每条回应必须包含三要素：challenge_claim（对应哪条挑战，原文引用）、response（反驳理由含数据，或承认说明）、outcome（rebutted/accepted）。
2. 为反驳而反驳无效：挑战数据扎实时必须 accepted；conservative 视角的挑战默认从严。
3. 输出 JSON：{"rebuttals": [{"challenge_claim": "...", "response": "...", "outcome": "rebutted|accepted"}]}。"""


# ── 摘要构建器（③-⑥ 摘要，从 nodes 收敛至此）────────────────


#: 事实证据按 dimension 分组展示顺序（③ 事实证据节、④ 决策证据节共用）
_DIM_ORDER = ("fundamentals", "market", "sentiment", "news")


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
    """信号节渲染（③④⑤⑥ 摘要共用）：估值/动量/背离/情绪原始直读。"""
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
    """确定性快照 → LLM 摘要骨架（③④ 共用）。

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


def _calibration_lines(state: dict) -> list[str]:
    """校准基线节（13 票）：meta.calibration_context 由 review 渲染注入。

    无校准（首跑 / mock 模式 / 加载失败）→ 空列表（摘要不渲染该节）。
    仅④⑥ 摘要携带（决策与复审消费自己的历史命中率）；③ 采证摘要不带（只提取证据）。
    """
    cal = (state.get("meta") or {}).get("calibration_context")
    if not cal:
        return []
    return ["", "== 校准基线（历史决策复盘，T+7d 方向命中）==", cal]


def build_facts_summary(symbol: str, state: dict) -> str:
    """③ 事实摘要：骨架 + 指令行（只提取证据，禁止结论）。"""
    return "\n".join(
        [*_facts_summary_lines(symbol, state), "", "只提取证据，禁止结论。"]
    )


def build_decide_summary(symbol: str, state: dict) -> str:
    """④ 决策摘要：骨架 + 事实证据节（按 dimension 分组，≤10 条）。"""
    lines = _facts_summary_lines(symbol, state)
    lines += ["", "== 事实证据（research_facts 产出）=="]
    facts = (state.get("facts") or {}).get(symbol) or []
    if not facts:
        lines.append("仅依据确定性信号")
    else:
        shown = 0
        for dim in _DIM_ORDER:
            for f in facts:
                if f.get("dimension") != dim:
                    continue
                lines.append(
                    f"[{dim}/{f.get('direction')}] {f.get('claim')}"
                    f" ({f.get('source')}, {f.get('timestamp')})"
                )
                shown += 1
                if shown >= 10:
                    break
            if shown >= 10:
                break
        if not shown:
            lines.append("仅依据确定性信号")
    lines += _calibration_lines(state)
    return "\n".join(lines)


def _decision_lines(symbol: str, state: dict) -> list[str]:
    """原决策全文渲染（⑤⑥ 摘要共用）。"""
    dec = (state.get("decisions") or {}).get(symbol) or {}
    lines = ["== 原决策 =="]
    lines.append(f"symbol: {dec.get('symbol') or symbol}")
    lines.append(
        f"decision: {dec.get('decision') or 'UNKNOWN'}"
        f" direction: {dec.get('direction') or '未声明'}"
        f" confidence: {dec.get('confidence') or 0.0}"
    )
    for key in (
        "fundamental_thesis",
        "market_thesis",
        "market_implied_expectation",
        "mispricing",
        "catalyst",
        "data_quality",
        "valuation_summary",
        "trade_structure",
    ):
        v = dec.get(key)
        if v:
            lines.append(f"{key}: {v}")
    score = dec.get("fundamental_score")
    if score is not None:
        lines.append(f"fundamental_score: {score}")
    quad = dec.get("quadrant")
    if quad:
        lines.append(f"quadrant: {quad}")
    risks = dec.get("risks") or []
    if risks:
        lines.append("risks: " + "; ".join(risks))
    ev = dec.get("evidence") or []
    if ev:
        lines.append(
            "evidence: "
            + "; ".join(
                f"[{e.get('claim')} ({e.get('source')}, {e.get('timestamp')})]"
                for e in ev
            )
        )
    return lines


def build_challenge_summary(symbol: str, state: dict) -> str:
    """⑤ 对抗摘要：决策全文 + 反方事实预筛（按决策方向取反）+ 信号。"""
    dec = (state.get("decisions") or {}).get(symbol) or {}
    lines = _decision_lines(symbol, state)
    lines += ["", "== 反方事实（预筛：多头取 bear / 空头取 bull）=="]
    facts = (state.get("facts") or {}).get(symbol) or []
    direction = dec.get("direction")
    if direction == "long":
        picked = [f for f in facts if f.get("direction") == "bear"]
    elif direction == "short":
        picked = [f for f in facts if f.get("direction") == "bull"]
    else:
        picked = list(facts)
    if not picked:
        lines.append("无反方事实（或 facts 为空）")
    for f in picked:
        lines.append(
            f"[{f.get('direction')}/{f.get('dimension')}/{f.get('topic')}]"
            f" {f.get('claim')} ({f.get('source')}, {f.get('timestamp')})"
        )
    lines += ["", "== 信号（确定性计算）=="]
    lines += _signal_lines(symbol, state)
    lines += _calibration_lines(state)
    return "\n".join(lines)


def build_finalize_summary(symbol: str, state: dict) -> str:
    """⑥ 复审摘要：原决策 + 全部挑战（含 severity/refutes/stance）+ 信号。"""
    chs = (state.get("challenges") or {}).get(symbol) or []
    lines = _decision_lines(symbol, state)
    lines += ["", "== 反方挑战（≤3 条）=="]
    for c in chs:
        lines.append(
            f"[{c.get('severity')}/{c.get('stance')}] {c.get('claim')}"
            f" (refutes: {c.get('refutes') or '整体'})"
        )
        lines.append(f"  证据: {c.get('evidence')}")
    lines += ["", "== 信号（确定性计算）=="]
    lines += _signal_lines(symbol, state)
    lines += _calibration_lines(state)
    return "\n".join(lines)
