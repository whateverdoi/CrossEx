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
    （进交易计划，不参与风控核验）；horizon 为描述性评估窗口（05 票：
    仅评估记账，不参与风控核验，不设价格锚点）。"""

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
    horizon: Literal["short_term", "trend", ""] = Field(
        default="",
        description="评估窗口（05 票，描述性）：short_term=预期 1-7 天内兑现的错价 / "
        "trend=中期趋势判断；仅评估记账，不参与风控核验，不设价格锚点",
    )

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
            "horizon": _pick(data.get("horizon"), ("short_term", "trend", ""), ""),
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
# ── Prompt（13 票起收敛至 strategy_research.context，此处仅兼容再导出）──

from strategy_research.context import (  # noqa: F401 —— 兼容再导出（prompt 本体在 context）
    ANALYZE_PROMPT,
    CHALLENGE_PROMPT,
    DECIDE_PROMPT,
    FACTS_PROMPT,
    FINALIZE_PROMPT,
)

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
