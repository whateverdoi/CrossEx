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
#: 枚举——宽容解析 + 核验兜底）
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


#: 引用文本中的首个数值 token（可选负号；后缀单位与 % 由 search 天然跳过）
_CITED_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _cited_number(expected: str) -> tuple[float, int] | None:
    """引用文本 → (首个数值, 小数位数)；无数值 → None。

    摘要渲染值自带单位后缀（``3.23 天/条``）与 ``%``（``6.62%``），旧实现
    ``float(expected.rstrip("%"))`` 遇到任何后缀直接解析失败 → 把摘要自己
    渲染出的文本判为「值不一致」（误杀）。此处只取数值本体比对，后缀口径
    交给摘要内嵌的字段定义注记。
    """
    match = _CITED_NUM_RE.search(expected.replace(",", ""))
    if not match:
        return None
    token = match.group(0)
    try:
        return float(token), len(token.partition(".")[2])
    except ValueError:
        return None


def _precision_tol(decimals: int) -> float:
    """引用精度的半个最小单位（+ 浮点余量）。

    逐字契约下 LLM 引用值与快照的唯一合法差异 = 摘要渲染时的四舍五入，
    误差半格即够；旧固定绝对 0.05 放在 funding 量级（真实 0.000267）上
    等于放行 100 倍单位错误（引用 0.03 通过）。
    """
    return 0.5 * 10.0 ** (-max(0, decimals)) + 1e-9


def _values_match(actual: Any, expected: str) -> bool:
    """快照值 vs 引用值（字符串）规范化比较：数值按引用精度判等 / 其余严格相等。

    None（UNKNOWN 纪律）不可作为证据引用，一律不匹配。
    """
    if actual is None:
        return False
    if isinstance(actual, bool):
        return str(actual) == expected
    if isinstance(actual, (int, float)):
        cited = _cited_number(expected)
        if cited is None:
            return False
        expected_num, decimals = cited
        return abs(float(actual) - expected_num) <= _precision_tol(decimals)
    if isinstance(actual, str) and expected.startswith(actual + "（"):
        # 标签型复合值渲染为 {label}（{note}），LLM 可能整串引用（含注释）：
        # 剥离全角括号注释后比较（宽容纪律，同 label 下钻；仅限“（”前缀防误放）
        return True
    return str(actual) == expected


#: claim 数值 token + 紧随其后的 %（% 决定允许放大到百分数口径）
_CLAIM_NUM_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(%?)")


def _claim_unknown_numbers(
    claim: str, symbol: str, state: dict, visible: str
) -> list[str]:
    """claim 中在分支摘要（LLM 可见文本）不存在的数值（07 票弱检查）。

    摘要与分支 LLM 所见完全同源（context.build_branch_summary）；claim 每个
    数值必须与摘要中任一数值在数量级上对应，否则视为编造剔除。匹配宽容：
    千分位逗号（309,759,196.53）、负值字段的绝对值表述（下跌 15.59% ↔ 快照
    -15.59）、单位换算（39.9 亿 ↔ 3994766092.68，10^k 缩放）。只抓数字真实
    性，不判归属——归属错误（借其他字段数值）靠 prompt 纪律，核验无语义能力。
    缩放纪律（09 票改）：不带 % 的数值只允许缩小（放大放行编造值，
    99.99 ≈ 1.0×10²）；带 % 的数值额外允许 ×10/×100——比率字段摘要按原始
    小数渲染（up_ratio_24h: 0.60），表述成「60%」是同量的合法派生表述，
    旧实现把它判为编造（误杀）。容差取引用精度的半格，与 _values_match 同构。
    """
    if not claim:
        return []
    hay = [
        abs(float(m)) for m in re.findall(r"-?\d+(?:\.\d+)?", visible.replace(",", ""))
    ]
    missing: list[str] = []
    for match in _CLAIM_NUM_RE.finditer(claim.replace(",", "")):
        token, is_pct = match.group(1), bool(match.group(2))
        try:
            v = abs(float(token))
        except ValueError:
            continue
        tol = _precision_tol(len(token.partition(".")[2]))
        # 不带 %：只允许缩小；带 %：再多允许 ×10 / ×100（小数→百分数）
        scales = range(-9, 3) if is_pct else range(-9, 1)
        if not any(abs(h * (10**k) - v) <= tol for h in hay for k in scales):
            missing.append(token + ("%" if is_pct else ""))
    return missing


_INDEX_SEG_RE = re.compile(r"\[\d+\]")


def _invisible_index(field: str, visible: str) -> str | None:
    """basis 引用的列表下标未在摘要中出现 → 该下标段；否则 None（弱检查）。

    摘要对序列有截断（新闻 ≤3 条、推文明细最近 10 条），核验却按 state 全量
    解引用：不校验下标，LLM 引用一条从未见过的早期推文、只要数值碰巧对得上
    就能通过——等于凭空编造也能过关。下标段含方括号，不会误配（[1] 不匹配
    [10]）。同源纪律：只判是否可见，不判归属。
    """
    for seg in _INDEX_SEG_RE.findall(field):
        if seg not in visible:
            return seg
    return None


def _verify_item(
    item: dict, symbol: str, state: dict, visible: str
) -> tuple[dict | None, dict | None]:
    """单条证据核验：通过 → (item, None)；剔除 → (None, {claim, reason})。"""
    basis = item.get("basis") or {}
    domain = _text(basis.get("domain"))
    field = _text(basis.get("field"))
    expected = _text(basis.get("value"))
    if domain == "market_env":  # 全市场聚合（meta 级，非 per-token）
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
    invisible = _invisible_index(field, visible)
    if invisible:
        return None, {
            "claim": item.get("claim") or "",
            "reason": f"引用了摘要未渲染的列表下标: {field}（{invisible}）",
        }
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


def dedup_evidence(items: list[dict]) -> list[dict]:
    """分支证据去重（机器兜底，纯函数）：同 basis 三元组保留第一条；
    再按去空白归一化 claim 精确去重。

    prompt 第 8/9 条约束 LLM 不重复（basis 三元组互不相同、上限 15 条），但
    截断恢复/表述改写仍可能撞车：(domain, field, value) 相同 = 同一数据点只留
    首条；claim 去空白后相同 = 同一主张的改写只留首条。顺序保持 LLM 输出序
    （按重要性降序，去重不重排）。
    """
    seen_basis: set[tuple] = set()
    seen_claim: set[str] = set()
    out: list[dict] = []
    for item in items:
        b = item.get("basis") or {}
        basis_key = (b.get("domain"), b.get("field"), b.get("value"))
        claim_key = "".join((item.get("claim") or "").split())
        if basis_key in seen_basis or claim_key in seen_claim:
            continue
        seen_basis.add(basis_key)
        seen_claim.add(claim_key)
        out.append(item)
    return out
