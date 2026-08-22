"""证据体系：schema + 确定性核验（02 票，图接线在 03 票）。

- ``EvidenceItem`` / ``BranchOutput``：分支结构化输出契约——claim +
  basis（domain/field/value 三元组）+ source，**无 confidence 字段**
  （LLM 自评信心分是主观臆想，不进入证据体系，D3 决策）。
- ``verify_evidence``：确定性核验纯函数（终审）——basis 逐级解引用存在且
  值一致 → 通过；否则剔除并留痕（claim + reason），取代旧 risk_check 的
  机器强制位置（D8 决策）。

宽容纪律与 schemas.py 同构：LLM 输出是弱契约，model_validator 归一清洗，
非法值置默认，model_validate 永不抛异常（坏条目丢弃在节点装配层做）。
"""

from __future__ import annotations

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
)

_NULLISH = {"", "null", "none", "nan", "nil", "-"}


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
    """分支单次结构化输出：evidence 列表（按重要性降序，上限 8 条，截断在节点层）。"""

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
    """点号路径逐级解引用；任一级缺失/非 dict → _MISSING。"""
    node: Any = root
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _values_match(actual: Any, expected: str) -> bool:
    """快照值 vs 引用值（字符串）规范化比较：数值近似 / 其余严格相等。

    None（UNKNOWN 纪律）不可作为证据引用，一律不匹配。
    """
    if actual is None:
        return False
    if isinstance(actual, bool):
        return str(actual) == expected
    if isinstance(actual, (int, float)):
        try:
            exp = float(expected)
        except (TypeError, ValueError):
            return False
        return abs(float(actual) - exp) <= 1e-9 * max(1.0, abs(exp))
    return str(actual) == expected


def _verify_item(item: dict, symbol: str, state: dict) -> tuple[dict | None, dict | None]:
    """单条证据核验：通过 → (item, None)；剔除 → (None, {claim, reason})。"""
    basis = item.get("basis") or {}
    domain = _text(basis.get("domain"))
    field = _text(basis.get("field"))
    expected = _text(basis.get("value"))
    if domain == "scanner_snapshot":  # field 自带完整路径（market.BTC.price）
        root: Any = (state.get("scanner_snapshot") or {})
    elif domain in _DOMAIN_KEYS:
        root = ((state.get(domain) or {}).get(symbol) or {})
    else:
        return None, {"claim": item.get("claim") or "", "reason": f"basis 数据域未知: {domain}"}
    if not field:
        return None, {"claim": item.get("claim") or "", "reason": "basis 字段路径为空"}
    actual = _resolve(root, field)
    if actual is _MISSING:
        return None, {"claim": item.get("claim") or "", "reason": f"字段不存在: {field}"}
    if not _values_match(actual, expected):
        return (
            None,
            {
                "claim": item.get("claim") or "",
                "reason": f"值不一致: 引用 {expected or '空'} vs 快照 {actual}",
            },
        )
    return item, None


def _verify_list(items: list[dict] | None, symbol: str, state: dict) -> tuple[list[dict], list[dict]]:
    """单分支证据清单核验：返回（通过清单, 剔除记录）。"""
    ok: list[dict] = []
    rej: list[dict] = []
    for item in items or []:
        passed, rejected = _verify_item(item, symbol, state)
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
        bull_ok, bull_rej = _verify_list((bull_evidence or {}).get(symbol), symbol, state)
        bear_ok, bear_rej = _verify_list((bear_evidence or {}).get(symbol), symbol, state)
        verified[symbol] = {"bull_case": bull_ok, "bear_case": bear_ok}
        if bull_rej or bear_rej:
            rejected[symbol] = bull_rej + bear_rej
    return verified, rejected
