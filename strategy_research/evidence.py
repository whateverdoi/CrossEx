"""证据体系：schema + 确定性核验（02 票，图接线在 03 票）。

- ``EvidenceItem`` / ``BranchOutput``：分支结构化输出契约——claim +
  basis（domain/field/value 三元组）+ source，**无 confidence 字段**
  （LLM 自评信心分是主观臆想，不进入证据体系，D3 决策）。
- ``verify_evidence``：确定性核验纯函数（终审）——basis 逐级解引用存在且
  值一致 → 通过；否则剔除并留痕（claim + reason），证据体系的机器强制位置
  （D8 决策；05 票：旧终审路径已退役）。

宽容纪律：LLM 输出是弱契约，model_validator 归一清洗，非法值置默认，
model_validate 永不抛异常（坏条目丢弃在节点装配层做）。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, model_validator

#: basis.domain 白名单（核验解引用根；未知域在核验层剔除，schema 层不设
#: 枚举——宽容解析 + 核验兜底。scanner_snapshot 特例：field 自带完整路径）
_DOMAIN_KEYS = (
    "signals",
    "market_data",
    "fundamental_data",
    "microstructure_data",
    "web_data",
    "social_data",
)

_NULLISH = {"", "null", "none", "nan", "nil", "-"}

#: field 旧域前缀（06 票宽容：LLM 曾用旧数据源名当 field 首段，如
#: sentiment.funding_pctile_90d；核验解引用失败时剥首段重试一次）
_LEGACY_FIELD_PREFIXES = (
    "sentiment",
    "valuation",
    "divergence",
    "market",
    "defillama",
    "binance_futures",
    "binance",
    "fundamental",
    "x_social",
)


def _text(value: Any) -> str:
    """任意值 → 干净字符串；null/缺失/占位 → ""。"""
    if value is None:
        return ""
    if isinstance(value, str):
        s = value.strip()
        return "" if s.lower() in _NULLISH else s
    return str(value).strip()


class Basis(BaseModel):
    """结构化数据引用三元组：domain（数据域）+ field（点号路径）+ value（引用值）。"""

    domain: str = Field(default="", description="数据域（signals/market_data/...）")
    field: str = Field(default="", description="点号路径，如 divergence.value.quadrant")
    value: str = Field(default="", description="引用时点的快照值（转字符串）")

    @model_validator(mode="before")
    @classmethod
    def _tolerate(cls, data: Any) -> dict:
        if not isinstance(data, dict):
            return {}
        return {
            "domain": _text(data.get("domain")),
            "field": _text(data.get("field")),
            "value": _text(data.get("value")),
        }


class EvidenceItem(BaseModel):
    """一条证据：claim（自由文本）+ basis（结构化引用）+ source（数据域）。"""

    claim: str = Field(default="", description="主张（自由文本）")
    basis: Basis = Field(default_factory=Basis, description="结构化数据引用三元组")
    source: str = Field(default="", description="与 basis.domain 一致的数据域")

    @model_validator(mode="before")
    @classmethod
    def _tolerate(cls, data: Any) -> dict:
        if not isinstance(data, dict):
            return {}
        return {
            "claim": _text(data.get("claim")),
            "basis": Basis.model_validate(data.get("basis")),
            "source": _text(data.get("source")),
        }


class BranchOutput(BaseModel):
    """分支单次结构化输出：evidence 列表（按重要性降序，数量不设上限）。"""

    evidence: list[EvidenceItem] = Field(
        default_factory=list, description="证据列表（按重要性降序）"
    )

    @model_validator(mode="before")
    @classmethod
    def _tolerate(cls, data: Any) -> dict:
        if not isinstance(data, dict):
            return {}
        raw = data.get("evidence")
        items = raw if isinstance(raw, list) else []
        return {"evidence": [EvidenceItem.model_validate(x) for x in items]}


# ── 确定性核验（纯函数，无 IO 可单测） ──────────────────────


_MISSING = object()


def _resolve(root: dict, path: str) -> Any:
    """点号路径逐级解引用；任一级缺失/非 dict → _MISSING。

    支持列表索引段（06 票）：items[0].title → items[0]["title"]（web_data 新闻）。
    """
    node: Any = root
    for part in path.split("."):
        if "[" in part and part.endswith("]"):
            key, _, idx = part.partition("[")
            idx = idx.rstrip("]")
            if not key:
                return _MISSING
            node = node.get(key) if isinstance(node, dict) else _MISSING
            try:
                node = node[int(idx)] if isinstance(node, list) else _MISSING
            except (IndexError, ValueError, TypeError):
                return _MISSING
            continue
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _values_match(actual: Any, expected: str) -> bool:
    """快照值 vs 引用值（字符串）规范化比较：数值近似 / 其余严格相等。

    None（UNKNOWN 纪律）不可作为证据引用，一律不匹配。
    数值容差（06 票）：固定绝对 0.05——LLM 引用值来自摘要渲染（最长 1 位小数，
    最坏舍入 0.05），允许渲染精度误差；不用相对容差，防止 0.1% 量级的错值被放行。
    """
    if actual is None:
        return False
    if isinstance(actual, bool):
        return str(actual) == expected
    if isinstance(actual, (int, float)):
        exp = expected.rstrip("%").strip()  # 百分比字段摘要把值渲染成 6.62%，
        # LLM 按逐字契约引用带 % 后缀——比较前剥离（06 票）
        try:
            expected_num = float(exp)
        except (TypeError, ValueError):
            return False
        return abs(float(actual) - expected_num) <= max(1e-6, 0.05)
    if isinstance(actual, str) and expected.startswith(actual + "（"):
        # 标签型复合值渲染为 {label}（{note}），LLM 可能整串引用（含注释）：
        # 剥离全角括号注释后比较（宽容纪律，同 label 下钻；仅限“（”前缀防误放）
        return True
    return str(actual) == expected


def _claim_unknown_numbers(
    claim: str, symbol: str, state: dict, visible: str
) -> list[str]:
    """claim 中在分支摘要（LLM 可见文本）不存在的数值（07 票弱检查）。

    摘要与分支 LLM 所见完全同源（context.build_branch_summary）；claim 每个
    数值必须与摘要中任一数值在数量级上对应，否则视为编造剔除。匹配宽容：
    千分位逗号（309,759,196.53）、负值字段的绝对值表述（下跌 15.59% ↔ 快照
    -15.59）、单位换算（39.9 亿 ↔ 3994766092.68，10^k 缩放）。只抓数字真实
    性，不判归属——归属错误（借其他字段数值）靠 prompt 纪律，核验无语义能力。
    """
    if not claim:
        return []
    hay = [float(m) for m in re.findall(r"-?\d+(?:\.\d+)?", visible.replace(",", ""))]
    missing: list[str] = []
    for num in re.findall(r"-?\d+(?:\.\d+)?", claim.replace(",", "")):
        try:
            v = abs(float(num))
        except ValueError:
            continue
        if not any(
            abs(abs(h) * (10**k) - v) <= max(1e-6, 0.05)
            for h in hay
            for k in range(-9, 1)  # 只允许缩小（亿/万/千分位换算），禁止放大——
            # 放大会把编造值放行（99.99 ≈ 1.0×10²）
        ):
            missing.append(num)
    return missing


def _verify_item(
    item: dict, symbol: str, state: dict, visible: str
) -> tuple[dict | None, dict | None]:
    """单条证据核验：通过 → (item, None)；剔除 → (None, {claim, reason})。"""
    basis = item.get("basis") or {}
    domain = _text(basis.get("domain"))
    field = _text(basis.get("field"))
    expected = _text(basis.get("value"))
    if domain == "scanner_snapshot":  # field 自带完整路径（market.BTC.price）
        root: Any = state.get("scanner_snapshot") or {}
    elif domain == "market_env":  # 全市场聚合（meta 级，非 per-token）
        root = (state.get("meta") or {}).get("market_env") or {}
    elif domain in _DOMAIN_KEYS:
        root = (state.get(domain) or {}).get(symbol) or {}
    else:
        return None, {
            "claim": item.get("claim") or "",
            "reason": f"basis 数据域未知: {domain}",
        }
    if not field:
        return None, {"claim": item.get("claim") or "", "reason": "basis 字段路径为空"}
    actual = _resolve(root, field)
    if (
        actual is _MISSING
        and "." in field
        and field.split(".", 1)[0] in _LEGACY_FIELD_PREFIXES
    ):
        # 旧域前缀宽容（06 票）：sentiment.funding_pctile_90d → 剥首段重试
        field = field.split(".", 1)[1]
        actual = _resolve(root, field)
    if actual is _MISSING:
        return None, {
            "claim": item.get("claim") or "",
            "reason": f"字段不存在: {field}",
        }
    if isinstance(actual, dict) and "value" in actual:
        # 数据点包装（{value, source, timestamp, confidence}）自动下钻 value：
        # 宽容 field 漏 .value 后缀（LLM 弱契约，宽容纪律）
        actual = actual["value"]
    if isinstance(actual, dict) and "label" in actual and isinstance(expected, str):
        # 标签型复合值（如 liq_imbalance: {ratio, label, note}）自动下钻 label：
        # LLM 通常只引用 label 字符串，不引用整个 dict（宽容纪律，同 .value 下钻）
        actual = actual["label"]
    if not _values_match(actual, expected):
        return (
            None,
            {
                "claim": item.get("claim") or "",
                "reason": f"值不一致: 引用 {expected or '空'} vs 快照 {actual}",
            },
        )
    missing = _claim_unknown_numbers(item.get("claim") or "", symbol, state, visible)
    if missing:
        return None, {
            "claim": item.get("claim") or "",
            "reason": f"claim 含输入中不存在的数值: {', '.join(missing)}",
        }
    return item, None


def _verify_list(
    items: list[dict] | None, symbol: str, state: dict
) -> tuple[list[dict], list[dict]]:
    """单分支证据清单核验：返回（通过清单, 剔除记录）。"""
    from strategy_research import context  # 局部导入避免模块初始化顺序耦合

    # 弱检查的合法数值源 = 分支摘要（与 LLM 所见同源），每 token 构建一次
    visible = context.build_branch_summary(symbol, state)
    ok: list[dict] = []
    rej: list[dict] = []
    seen: set[str] = set()  # 07 票：claim 规范化去重（防同一事实拆条凑数）
    for item in items or []:
        passed, rejected = _verify_item(item, symbol, state, visible)
        if passed is not None:
            norm = "".join((passed.get("claim") or "").split())
            if norm in seen:
                passed, rejected = None, {
                    "claim": item.get("claim") or "",
                    "reason": "重复 claim（同一事实拆条凑数）",
                }
            else:
                seen.add(norm)
        if passed is not None:
            ok.append(passed)
        if rejected is not None:
            rej.append(rejected)
    return ok, rej


def verify_evidence(
    bull_evidence: dict[str, list[dict]] | None,
    bear_evidence: dict[str, list[dict]] | None,
    state: dict,
) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """确定性核验：basis 逐级解引用存在且值一致 → 通过；否则剔除留痕。

    返回 ``(verified, rejected)``：
    - verified: {symbol: {"bull_case": [...], "bear_case": [...]}}（仅通过项）
    - rejected: {symbol: [{"claim": ..., "reason": ...}, ...]}
    """
    verified: dict[str, dict] = {}
    rejected: dict[str, list[dict]] = {}
    for symbol in sorted(set(bull_evidence or {}) | set(bear_evidence or {})):
        bull_ok, bull_rej = _verify_list(
            (bull_evidence or {}).get(symbol), symbol, state
        )
        bear_ok, bear_rej = _verify_list(
            (bear_evidence or {}).get(symbol), symbol, state
        )
        verified[symbol] = {"bull_case": bull_ok, "bear_case": bear_ok}
        if bull_rej or bear_rej:
            rejected[symbol] = bull_rej + bear_rej
    return verified, rejected
