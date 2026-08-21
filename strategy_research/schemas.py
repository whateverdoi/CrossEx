"""LLM 结构化输出 schema + 宽容 validator + prompt 常量 + _extract_json（06 票）。

宽容纪律：LLM 输出是弱契约——null / 字符串 / 字段名漂移全部在
``model_validator(mode="before")`` 归一清洗，非法值置默认，保证
``model_validate`` 永不抛 ValidationError（坏条目丢弃在装配层做，见 ③ 伪代码）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# ── 宽容归一 helpers ─────────────────────────────────────

_NULLISH = {"", "null", "none", "nan", "nil", "-"}


def _text(value: Any) -> str:
    """任意值 → 干净字符串；null/缺失/占位 → ""。"""
    if value is None:
        return ""
    if isinstance(value, str):
        s = value.strip()
        return "" if s.lower() in _NULLISH else s
    return str(value).strip()


def _pick(value: Any, allowed: tuple[str, ...], default: str) -> str:
    """白名单归一：合法取值原样返回（大小写不敏感），其余置默认。"""
    s = _text(value)
    for a in allowed:
        if s.lower() == a.lower():
            return a
    return default


def _float(value: Any, default: float | None = None) -> float | None:
    """数值归一：数字/数字字符串 → float；非法 → default。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if s and s.lower() not in _NULLISH:
            try:
                return float(s)
            except ValueError:
                pass
    return default


def _list(value: Any) -> list:
    """列表归一：list 原样（逐项清洗由调用方做）；其余 → []。"""
    return value if isinstance(value, list) else []


#: dimension 变体映射（字段名/翻译漂移 → 规范值，非法置 fundamentals）
_DIMENSION_KEYS = {
    "fundamentals": "fundamentals",
    "fundamental": "fundamentals",
    "基本面": "fundamentals",
    "base": "fundamentals",
    "market": "market",
    "markets": "market",
    "价格": "market",
    "sentiment": "sentiment",
    "情绪": "sentiment",
    "positioning": "sentiment",
    "news": "news",
    "新闻": "news",
    "media": "news",
}


def _dimension(value: Any) -> str:
    return _DIMENSION_KEYS.get(_text(value).lower(), "fundamentals")


#: topic 变体映射（研究主题 7 值 + unknown，非法置 unknown）
_TOPIC_KEYS = {
    "project": "project",
    "项目": "project",
    "protocol": "project",
    "team": "team",
    "团队": "team",
    "founder": "team",
    "social": "social",
    "社交": "social",
    "社媒热度": "social",
    "community": "social",
    "adoption": "adoption",
    "采用": "adoption",
    "usage": "adoption",
    "unlock": "unlock",
    "解锁": "unlock",
    "tokenomics": "unlock",
    "catalyst": "catalyst",
    "催化剂": "catalyst",
    "event": "catalyst",
    "news": "news",
    "新闻": "news",
    "unknown": "unknown",
}


def _topic(value: Any) -> str:
    return _TOPIC_KEYS.get(_text(value).lower(), "unknown")


#: 数据源白名单（source 字段取值域；白名单过滤在装配层做，validator 只清洗）
SOURCE_WHITELIST = ("binance", "binance_futures", "defillama", "bing", "mock")


def _source(value: Any) -> str:
    s = _text(value)
    return s if s in SOURCE_WHITELIST else ""


# ── Schema（④⑤⑥ + ③ 的证据条目）────────────────────────


class EvidenceItem(BaseModel):
    """证据三要素（TokenAnalysis.evidence 的条目）。"""

    claim: str = Field(default="", description="论断内容")
    source: str = Field(
        default="", description="白名单: binance/binance_futures/defillama/bing/mock"
    )
    timestamp: str = Field(default="", description="数据时间戳")

    @model_validator(mode="before")
    @classmethod
    def _tolerate(cls, data: Any) -> dict:
        if not isinstance(data, dict):
            return {}
        return {
            "claim": _text(data.get("claim")),
            "source": _source(data.get("source")),
            "timestamp": _text(data.get("timestamp")),
        }


class TokenAnalysis(BaseModel):
    """④ 决策输出（json_mode）。无 max_loss/invalidation（Q5 决策：
    LLM 主观值不配进确定性核验）；TRADE/WATCH 时 trade_structure 必填
    （进交易计划，不参与风控核验）。"""

    symbol: str = Field(default="", description="交易对符号")
    decision: Literal["TRADE", "WATCH", "PASS"] = Field(
        default="PASS", description="TRADE/WATCH/PASS"
    )
    direction: Literal["long", "short", ""] = Field(
        default="", description="白名单 long/short；TRADE 必填，其余可为空"
    )
    confidence: float = Field(default=0.0, description="置信度 0-1")
    fundamental_thesis: str = Field(default="", description="基本面论点")
    market_thesis: str = Field(default="", description="市场定价论点")
    market_implied_expectation: str = Field(default="", description="市场隐含预期")
    mispricing: str = Field(default="", description="错价判断")
    catalyst: str = Field(default="", description="催化剂")
    risks: list[str] = Field(default_factory=list, description="风险清单")
    evidence: list[EvidenceItem] = Field(
        default_factory=list, description="证据三要素列表"
    )
    data_quality: str = Field(default="", description="数据质量说明")
    fundamental_score: float | None = Field(default=None, description="基本面评分")
    quadrant: str = Field(default="", description="信号四象限 I/II/III/IV")
    valuation_summary: str = Field(default="", description="估值解读")
    trade_structure: str = Field(default="", description="交易结构（TRADE/WATCH 必填）")

    @model_validator(mode="before")
    @classmethod
    def _tolerate(cls, data: Any) -> dict:
        """宽容归一：字段漂移/非法值全部清洗，null 输出可解析不抛异常。"""
        if not isinstance(data, dict):
            return {}
        confidence = _float(data.get("confidence"), 0.0) or 0.0
        risks = [_text(r) for r in _list(data.get("risks"))]
        evidence = [
            EvidenceItem.model_validate(e)
            for e in _list(data.get("evidence"))
            if isinstance(e, dict)
        ]
        return {
            "symbol": _text(data.get("symbol")),
            "decision": _pick(data.get("decision"), ("TRADE", "WATCH", "PASS"), "PASS"),
            "direction": _pick(data.get("direction"), ("long", "short", ""), ""),
            "confidence": max(0.0, min(1.0, confidence)),
            "fundamental_thesis": _text(data.get("fundamental_thesis")),
            "market_thesis": _text(data.get("market_thesis")),
            "market_implied_expectation": _text(data.get("market_implied_expectation")),
            "mispricing": _text(data.get("mispricing")),
            "catalyst": _text(data.get("catalyst")),
            "risks": [r for r in risks if r],
            "evidence": evidence,
            "data_quality": _text(data.get("data_quality")),
            "fundamental_score": _float(data.get("fundamental_score")),
            "quadrant": _text(data.get("quadrant")),
            "valuation_summary": _text(data.get("valuation_summary")),
            "trade_structure": _text(data.get("trade_structure")),
        }


class FactItem(BaseModel):
    """一条事实证据（禁止结论性表述）。dimension = 四分析师视角；topic = 研究主题（7 值 + unknown）"""

    claim: str = Field(default="", description="论断内容")
    source: str = Field(
        default="", description="白名单: binance/binance_futures/defillama/bing/mock"
    )
    timestamp: str = Field(default="", description="数据时间戳")
    direction: Literal["bull", "bear", "neutral"] = "neutral"  # 供 challenge 预筛反方
    dimension: Literal["fundamentals", "market", "sentiment", "news"] = "fundamentals"
    topic: Literal[
        "project", "team", "social", "adoption", "unlock", "catalyst", "news", "unknown"
    ] = "unknown"

    @model_validator(mode="before")
    @classmethod
    def _tolerate(cls, data: Any) -> dict:
        """宽容归一：_DIMENSION_KEYS/_TOPIC_KEYS 映射，非法值置默认。"""
        if not isinstance(data, dict):
            return {}
        return {
            "claim": _text(data.get("claim")),
            "source": _source(data.get("source")),
            "timestamp": _text(data.get("timestamp")),
            "direction": _pick(
                data.get("direction"), ("bull", "bear", "neutral"), "neutral"
            ),
            "dimension": _dimension(data.get("dimension")),
            "topic": _topic(data.get("topic")),
        }


class ChallengeItem(BaseModel):
    """一条反方挑战。stance = 风控三人组视角：aggressive 质疑催化剂 /
    conservative 质疑错价依据 / neutral 质疑过程"""

    claim: str = Field(default="", description="反方论断")
    evidence: str = Field(default="", description="支撑数据（来源+数值，禁止编造）")
    severity: Literal["high", "medium", "low"] = "medium"
    refutes: str = Field(
        default="", description="指向被挑战的决策理由字段；空=整体质疑"
    )
    stance: Literal["aggressive", "conservative", "neutral"] = "conservative"

    @model_validator(mode="before")
    @classmethod
    def _tolerate(cls, data: Any) -> dict:
        if not isinstance(data, dict):
            return {}
        return {
            "claim": _text(data.get("claim")),
            "evidence": _text(data.get("evidence")),
            "severity": _pick(
                data.get("severity"), ("high", "medium", "low"), "medium"
            ),
            "refutes": _text(data.get("refutes")),
            "stance": _pick(
                data.get("stance"),
                ("aggressive", "conservative", "neutral"),
                "conservative",
            ),
        }


class RebuttalItem(BaseModel):
    """finalize 产出：对单条挑战的回应"""

    challenge_claim: str = Field(default="", description="对应哪条挑战（原文引用）")
    response: str = Field(default="", description="反驳理由（引用数据）或承认说明")
    outcome: Literal["rebutted", "accepted"] = "rebutted"  # accepted → 自动降级

    @model_validator(mode="before")
    @classmethod
    def _tolerate(cls, data: Any) -> dict:
        if not isinstance(data, dict):
            return {}
        return {
            "challenge_claim": _text(data.get("challenge_claim")),
            "response": _text(data.get("response")),
            "outcome": _pick(data.get("outcome"), ("rebutted", "accepted"), "rebutted"),
        }


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
    "（进交易计划，不参与风控核验）；不设价格锚点与有效期，失效由周期性重跑信号对比管理。\n"
    "8. 输出 JSON，字段：symbol、decision、direction、confidence、fundamental_thesis、"
    "market_thesis、market_implied_expectation、mispricing、catalyst、risks、evidence、"
    "data_quality、fundamental_score、quadrant、valuation_summary、trade_structure。"
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


# ── _extract_json（宽容解析）─────────────────────────────


def _match_brace(text: str, start: int, opener: str, closer: str) -> int | None:
    """括号配对扫描：返回与 start 处 opener 配对的 closer 下标；不闭合 → None。"""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return i
    return None


def _missing_closers(raw: str) -> str:
    """补全未闭合结构：配对栈扫描 → 逆序返回缺失的 closer（如 ``"]}"``）。

    比 count 差值更精确：嵌套 ``{"facts": [{"claim": "b"`` 缺的是 ``]}"``
    而非单个 ``}"``；扫描结束时字符串未闭合则先补闭引号。
    """
    stack: list[str] = []
    in_str = False
    esc = False
    for c in raw:
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            stack.append("}")
        elif c == "[":
            stack.append("]")
        elif c in "}]":
            if stack and stack[-1] == c:
                stack.pop()
            else:
                stack = []  # 括号不匹配 → 放弃补全
    suffix = "".join(reversed(stack))
    if in_str:
        suffix = '"' + suffix  # 字符串截断未闭合：先补闭引号再补括号
    return suffix


def _try_loads(raw: str) -> Any:
    """逐级容错解析：标准 JSON → 单引号键修复 → 补闭合括号 → 尾部截断重试。"""
    variants = [raw]
    fixed = re.sub(r"'([^']*)'\s*:", r'"\1":', raw)
    if fixed != raw:
        variants.append(fixed)
    for v in variants:
        try:
            return json.loads(v)
        except (json.JSONDecodeError, ValueError):
            pass
    suffix = _missing_closers(raw)
    if suffix:
        for v in variants:  # 尾部缺闭合括号：配对栈补全后再试
            try:
                return json.loads(v + suffix)
            except (json.JSONDecodeError, ValueError):
                pass
        # 尾部截断容错：在键值边界（,或{后接键）截断 + 补闭合括号
        boundary = re.compile(r'[,{]\s*"(?:[^"\\]|\\.)*"\s*:')
        for v in variants:
            for m in reversed(list(boundary.finditer(v))[:32]):
                try:
                    return json.loads(v[: m.start() + 1] + suffix)
                except (json.JSONDecodeError, ValueError):
                    continue
    return None


def _extract_json(text: Any) -> Any:
    """宽容 JSON 提取：文本中第一个 ``{...}`` 或 ``[...]`` 结构。

    容忍：代码块围栏 / 前后杂质文本 / 单引号键 / 尾部截断；不可解析 → None
    （调用方 ``(obj or {}).get(...)`` 按空处理，永不抛异常）。
    只处理第一个（最小下标）结构：外层对象不闭合时不回退内层数组——
    facts/challenges 契约是对象，回退内层会让 ``.get("facts")`` 崩溃。
    """
    if not isinstance(text, str):
        return None
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    if not t:
        return None
    candidates = [
        (t.find("{"), "{", "}"),
        (t.find("["), "[", "]"),
    ]
    valid = [(i, o, c) for i, o, c in candidates if i != -1]
    if not valid:
        return None
    start, opener, closer = min(valid, key=lambda x: x[0])
    end = _match_brace(t, start, opener, closer)
    raw = t[start : end + 1] if end is not None else t[start:]
    return _try_loads(raw)
