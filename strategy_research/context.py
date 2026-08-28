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

# ── 字段口径注记（单一来源：快照侧 SENTIMENT_NOTE + LLM 侧摘要行）────

#: 口径注记表：每条 = 摘要中一行的正文（渲染时冠以「口径」前缀）。
#:
#: 09 票修复：本表此前只以 ``SENTIMENT_NOTE`` 形式落进
#: ``state.signals[s].sentiment.note``，而 ``_signal_lines`` 只渲染
#: ``sentiment.components`` —— 分支 LLM 从未见过任何解读规则，
#: 「费率为正=多头收到资金费」「funding 分位高=偏多」这类方向反写的
#: claim 因此畅通无阻（核验只管数据出处，不管语义一致性）。现在本表
#: 逐行随摘要送达 LLM，与快照注记同源拼接，不会两头漂移。
#:
#: 纪律：注记正文不引入渲染字段名之外的新数值——摘要是核验
#: ``_claim_unknown_numbers`` 的合法数值源，注记里凭空出现的数字会变成放行
#: 编造值的后门（提及「近 7 天」这类窗口是因为键名本身已带该数字）。
_FIELD_NOTES: tuple[str, ...] = (
    (
        "funding 高=拥挤反向：funding 为本期资金费率原始小数，正值=多头支付给空头"
        "（多头付费=多头拥挤），负值=空头支付给多头；对多头而言费率是持仓成本不是收益"
    ),
    "funding_avg_7d / funding_trend：近 7 天费率均值与趋势档，rising=拥挤加剧",
    (
        "funding_pctile_90d：费率绝对值在该币自身近 90 天分布中的分位，"
        "高=处于自身历史极端（只表极端，不表方向）"
    ),
    (
        "funding_x_pctile：费率在全市场合约横截面中的分位，高=多头付费远高于全市场"
        "（多头拥挤，反向看空），低=空头付费主导（空头拥挤，反向看多）"
    ),
    (
        "funding_interval_hours / funding_carry_7d_pct / funding_carry_30d_pct："
        "结算间隔小时数（各合约不同，并非一律同周期）与按当前费率持有若干天的资金费"
        "成本 %——carry 与涨跌幅同尺度可直接比较，正=多头付出"
    ),
    (
        "divergence_7d / divergence_30d：基本面增速 减 价格涨幅（正=基本面跑赢价格）"
        "，不是价格相对均线的乖离"
    ),
    "momentum：TVL 变化加权得到的基本面动量分，不含价格维度",
    (
        "ls_ratio_all / ls_ratio_top_acc / ls_ratio_top_pos：多头除以空头的比值"
        "（依次按全体账户数、顶级账户数、顶级账户持仓量），大于 1 仅表示多头更多，"
        "本身不含方向对错"
    ),
    (
        "taker_bs_ratio_1h 与 taker_buy_ratio_24h 是两个不同窗口：前者为最近一个整小时"
        "的主动买量/主动卖量，后者为近 24 小时按量加权的同一比值；两者数值可以不同且"
        "都正确，禁止据其差异立论"
    ),
    (
        "liq_long_24h / liq_short_24h / liq_total_24h：单一交易所口径的强平名义额，"
        "不代表全市场；liq_total_oi_ratio 的分子来自该所、分母来自另一所的 OI，"
        "属跨所比值，只作量级参照、不是同口径占用率；liq_imbalance.value.ratio = "
        "多头爆仓额 / 空头爆仓额（>1 为多头被强平更多，下行压力），label 只是该比值"
        "的档位"
    ),
    "spread_pct：盘口最优买卖价差占中价的比例 %，即市价单立即付出的成本，越薄越好",
    (
        "bid_depth_usd_2pct / ask_depth_usd_2pct：中价两侧同幅百分比带内可成交的名义额"
        "（USDT），决定仓位能不能上量；depth_band_state 为 band_exhausted 时该值只是"
        "下限（档位已被带宽截断），band_complete 才是实测"
    ),
    (
        "beta_30d / alpha_30d：相对 BTC 的日收益回归系数与日超额收益（正=跑赢 BTC）；"
        "短窗口估出的 β 是噪声，故系统不提供短窗口版本"
    ),
    (
        "rv_7d / rv_30d：年化已实现波动率 %；vol_adj_ret_7d / vol_adj_ret_30d："
        "区间收益折算成该区间波动的 σ 倍数，跨资产可比；drawdown_1y：最新价距一年"
        "最高收盘的回撤 %（非正，零=创新高）"
    ),
    (
        "turnover：成交额占流通市值的日内换手比例；basis：合约价相对现货价的溢价 %："
        "正=合约升水"
    ),
    (
        "social_heat_trend：近期推文互动强度相对更早推文中位数的变化 %，高=热度上升；"
        "social_heat_window.value 为本次读数实际用的样本档（档名即算式，两档不可跨比）；"
        "post_frequency.value 为每条推文平均间隔天数，越小越活跃"
    ),
    (
        "posts[i]：单条推文的页面原样文本（如 120 / 1.2万，非归一化数值），i 为摘要中"
        "标注的原序列下标（时间升序，末条最新）；互动强度 = likes + reposts + "
        "comments，views 是触达人数不是互动，禁止计入强度"
    ),
    (
        "tvl_trend_30d / fees_trend_30d：近 30 天历史序列的确定性趋势档"
        "（rising / flat / falling）；stablecoin_change_30d：近 30 天稳定币供应变化 %，"
        "正=供应扩张"
    ),
)

#: sentiment 解读规则锚点（快照侧留痕）：与摘要口径注记同源拼接，不再两处维护
SENTIMENT_NOTE = "持仓指标原始直读；" + "；".join(_FIELD_NOTES)


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
    "microstructure_data / web_data / social_data / market_env；禁止使用数据源名（binance / "
    "binance_futures / defillama / bing / x_social 等）或节标题（市场/基本面/新闻/社交）作为 domain。\n"
    "4. basis.field 必须引用到输入中的标量层（含 .value 后缀），如 momentum.value、"
    "divergence.value.quadrant、sentiment.components.funding、tvl.value、"
    "oi_change_24h.value；社交节推文序列为裸字段（posts[i].likes，无 .value 后缀，"
    "i 只能取摘要中实际出现过的下标，禁止引用未渲染的更早推文）。\n"
    "5. basis.value 必须逐字引用输入中该字段的显示值，禁止改写、重算或四舍五入。\n"
    "6. 只提取"
)

_BRANCH_OUTPUT = (
    "证据，禁止给出决策、结论或建议（那是后续决策者的工作）。\n"
    "7. claim 中出现的每个数值必须来自其 basis 引用的字段（允许该字段的派生"
    "表述），禁止把其他字段的数值归因到本字段（如把全账户多空比变化说成"
    "顶级账户变化），禁止使用输入中不存在的计算值（如比率换算、合成指标）。\n"
    "8. 每条证据必须引用与已有条目不同的数据点（basis 三元组不同）；禁止把"
    "同一事实改写为多条证据凑数（如同一数值换 claim 表述重复引用）。\n"
    "9. 数量上限 15 条，按重要性降序，只保留最重要的独立事实。\n"
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


#: 摘要里渲染的单条推文上限（原始序列不截断，只取最近的若干条送 LLM）
_POST_RENDER_CAP = 10


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
    mm = (sig.get("market_metrics") or {}).get("value") or {}
    if mm:
        lines.extend(f"market_metrics.value.{k}: {_num_text(v)}" for k, v in mm.items())
    else:
        lines.append("market_metrics: UNKNOWN")
    return lines


def _market_env_lines(state: dict) -> list[str]:
    """市场环境节（08 票：全市场宽度聚合，分支摘要头部，域=market_env）。"""
    env = (state.get("meta") or {}).get("market_env") or {}
    lines = ["== 市场环境（market_env，全市场聚合）=="]
    if not env:
        lines.append("UNKNOWN")
        return lines
    lines.extend(f"market_env.{k}: {_num_text(v)}" for k, v in env.items())
    return lines


def _facts_summary_lines(symbol: str, state: dict) -> list[str]:
    """确定性快照 → LLM 摘要骨架（分支摘要共用）。

    只喂数字 + 变化率 + source 标签（含微观结构节）；缺失一律 UNKNOWN；
    新闻 ≤3 条、推文明细 ≤10 条（``_POST_RENDER_CAP``）；mock 全字段摘要实测
    约 126 行 / 6k 字符，其中推文明细占 11 行——行数唯一的可变部分是推文条数
    （未登录抓取只有 5-7 条）。
    """
    fund = (state.get("fundamental_data") or {}).get(symbol) or {}
    mkt = (state.get("market_data") or {}).get(symbol) or {}
    ms = (state.get("microstructure_data") or {}).get(symbol) or {}
    web = (state.get("web_data") or {}).get(symbol) or {}
    lines = [f"研究标的: {symbol}", "", "== 基本面（fundamental_data）=="]
    kind = fund.get("kind") or "unknown"
    name = fund.get("name")
    category = fund.get("category")
    lines.append(
        f"kind: {kind}"
        + (f" ({name})" if name else "")
        + (f" category: {category}" if category else "")
    )
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
        lines.append(
            f"tvl_trend_30d: {_dp_text(fund.get('tvl_trend_30d'))}"
            f" fees_trend_30d: {_dp_text(fund.get('fees_trend_30d'))}"
        )
    else:
        for key in ("stablecoin_supply", "dex_volume_24h"):
            lines.append(f"{key}: {_dp_text(fund.get(key))}")
        lines.append(
            f"stablecoin_change_30d: {_dp_text(fund.get('stablecoin_change_30d'))}"
        )
    lines += ["", "== 市场（market_data）=="]
    for key in ("price", "quote_volume_24h"):
        lines.append(f"{key}: {_dp_text(mkt.get(key))}")
    for key in ("change_24h", "change_7d", "change_30d", "change_90d", "change_1y"):
        lines.append(f"{key}: {_pct_text(mkt.get(key))}")
    lines.append(
        f"funding: {_dp_text(mkt.get('funding'), 6)}"
        f" funding_avg_7d: {_dp_text(mkt.get('funding_avg_7d'), 6)}"
        f" funding_trend: {_dp_text(mkt.get('funding_trend'))}"
        f" funding_pctile_90d: {_dp_text(mkt.get('funding_pctile_90d'), 1)}"
        f" funding_x_pctile: {_dp_text(mkt.get('funding_x_pctile'), 1)}"
    )
    lines.append(
        f"funding_interval_hours: {_dp_text(mkt.get('funding_interval_hours'))}"
        f" funding_carry_7d_pct: {_dp_text(mkt.get('funding_carry_7d_pct'))}"
        f" funding_carry_30d_pct: {_dp_text(mkt.get('funding_carry_30d_pct'))}"
    )
    lines.append(f"oi: {_dp_text(mkt.get('oi'))} basis: {_pct_text(mkt.get('basis'))}")
    lines.append(
        f"taker_buy_ratio_24h: {_dp_text(mkt.get('taker_buy_ratio_24h'))}"
        f" listing_days: {_dp_text(mkt.get('listing_days'))}"
    )
    lines += ["", "== 交易结构（market_data，可执行性：能不能成交、代价多少）=="]
    lines.append(
        f"spread_pct: {_dp_text(mkt.get('spread_pct'), 4)}"
        f" bid_depth_usd_2pct: {_dp_text(mkt.get('bid_depth_usd_2pct'))}"
        f" ask_depth_usd_2pct: {_dp_text(mkt.get('ask_depth_usd_2pct'))}"
        f" depth_band_state: {_dp_text(mkt.get('depth_band_state'))}"
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
        "taker_bs_ratio_1h",
        "liq_long_24h",
        "liq_short_24h",
        "liq_total_24h",
        "liq_total_oi_ratio",
    ):
        lines.append(f"{key}: {_dp_text(ms.get(key))}")
    imb = (ms.get("liq_imbalance") or {}).get("value") or {}
    lines.append(
        f"liq_imbalance.value.label: {imb.get('label') or 'UNKNOWN'}"
        + (
            f"  liq_imbalance.value.ratio: {imb['ratio']:.2f}"
            if isinstance(imb.get("ratio"), (int, float))
            else "  liq_imbalance.value.ratio: UNKNOWN"
        )
        + (f"  liq_imbalance.value.note: {imb['note']}" if imb.get("note") else "")
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
    lines += ["", "== 社交（social_data）=="]
    soc = (state.get("social_data") or {}).get(symbol) or {}
    fc = (soc.get("follower_count") or {}).get("value")
    pf = (soc.get("post_frequency") or {}).get("value")
    lines.append(
        f"follower_count.value: {fc or 'UNKNOWN'}"
        + (
            f"  post_frequency.value: {pf} 天/条"
            if pf is not None
            else "  post_frequency: UNKNOWN"
        )
    )
    # 整体趋势指标（确定性派生）：热度趋势 + 样本档 + 社交/价格背离
    heat = (soc.get("social_heat_trend") or {}).get("value")
    window = (soc.get("social_heat_window") or {}).get("value")
    lines.append(
        f"social_heat_trend.value: {heat if heat is not None else 'UNKNOWN'}"
        f"  social_heat_window.value: {window or 'UNKNOWN（样本不足，未派生）'}"
    )
    div = (soc.get("social_price_divergence") or {}).get("value") or {}
    lines.append(f"social_price_divergence.value.label: {div.get('label') or 'UNKNOWN'}")
    if div.get("note"):
        lines.append(f"social_price_divergence.value.note: {div.get('note')}")
    # 单条推文明细：整体趋势只有 1 个读数，明细才是趋势/脉冲的可复核来源
    posts = soc.get("posts") or []
    if posts:
        shown = posts[-_POST_RENDER_CAP:]
        lines.append(
            f"posts（共 {len(posts)} 条，时间升序末条最新；下为最近 {len(shown)} 条，"
            "下标沿用原序列，basis 只能引用下方出现过的下标）"
        )
        for off, p in enumerate(shown):
            i = len(posts) - len(shown) + off
            lines.append(
                " ".join(
                    f"posts[{i}].{k}: {p.get(k) if p.get(k) is not None else 'UNKNOWN'}"
                    for k in ("likes", "reposts", "comments", "views", "time")
                )
            )
    else:
        lines.append("posts: UNKNOWN")
    return lines


def _field_note_lines() -> list[str]:
    """口径注记块（09 票）：解读规则必须随数据一起送达 LLM 才可能被遵守。

    放在数据之前（首因位置），且与快照侧 ``SENTIMENT_NOTE`` 同源。
    """
    return [
        "== 字段口径（claim 中的方向表述必须与此一致，冲突即为错误）==",
        *[f"口径 {note}" for note in _FIELD_NOTES],
    ]


def build_branch_summary(symbol: str, state: dict) -> str:
    """分支摘要（02 票：bull/bear 共用同一确定性快照）：骨架 + 指令行。

    两分支各自从同一份确定性快照提取证据，不含任何决策产物（05 票）。
    """
    return "\n".join(
        [
            *_market_env_lines(state),
            "",
            *_field_note_lines(),
            "",
            *_facts_summary_lines(symbol, state),
            "",
            "只提取证据，禁止结论。",
        ]
    )
