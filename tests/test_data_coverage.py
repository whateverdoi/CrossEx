"""数据覆盖契约（「抓了必须喂」）：state 里有值的字段，必须出现在分支摘要里。

动机（本票根因）：数据域字段一路抓到 state、写进 run.json，却没进摘要——
分支 LLM 看不见，等于白抓。实测三例：``social_data.posts``（30 条明细从不渲染，
而 prompt 第 4 条一直要求引用 ``posts[i].likes``）、``fundamental_data.
tvl_trend_30d``/``fees_trend_30d``/``stablecoin_change_30d``（早已进信号快照，
却从不送达 LLM）、``microstructure_data.liq_imbalance.ratio``（只渲染标签、
丢掉比值）。这类漏喂靠人工比对不可持续，故用契约测试把「有值未渲染」变成红灯。

口径：
- 「有值」= 数据点 ``value`` 非 None（UNKNOWN 的字段不必渲染，渲染了也没内容）；
- 「喂到」= 字段的点号路径出现在摘要左值集合中（下标与 ``.value`` 段归一化后
  比较），或字符串型字段的**取值文本**出现在摘要中（name/kind/category 这类
  作为标签内联渲染，不构成独立左值）；
- 显式隐藏清单 ``_HIDDEN`` 逐条给理由——加了白名单就是承认「这个字段故意不
  喂」，必须写下为什么，不允许默默漏喂。
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from strategy_research import context, nodes

#: 簿记段（末段命中即跳过）：数据点元信息与快照级状态位，不是研究内容
_BOOKKEEPING = {
    "symbol",
    "error",
    "web_error",
    "futures_error",
    "incomplete",
    "resolved",
    "source",
    "timestamp",
    "confidence",
}

#: 显式隐藏清单：路径 → 不喂的理由（新增条目必须写理由）
_HIDDEN: dict[str, str] = {
    "signals.sentiment.note": (
        "与摘要「字段口径」注记块同源（SENTIMENT_NOTE 逐行送达 LLM），"
        "整串重复渲染只是加 token"
    ),
}

#: 参与契约的 per-token 数据域（scanner_snapshot / market_env 为 meta 级，另测）
_DOMAINS = (
    "fundamental_data",
    "market_data",
    "microstructure_data",
    "web_data",
    "social_data",
    "signals",
)

_DP_KEYS = {"value", "source", "timestamp", "confidence"}
_PATH_SEG_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_\.\[\]]*)\s*:")
_INDEX_RE = re.compile(r"\[\d+\]")


@pytest.fixture(scope="module")
def mock_state() -> dict:
    """真实 mock 管线产出的 state（不走手搓 fixture：本测试要覆盖装配过程）。"""
    st: dict = {"tokens": ["BTCUSDT", "UNIUSDT", "USDCUSDT"]}
    st = {**st, **nodes.collect_data(st)}
    st = {**st, **nodes.compute_signals(st)}
    for dom in _DOMAINS:  # 装配失败的域会让契约退化为空断言
        assert st.get(dom), f"mock 装配缺域 {dom}"
    return st


def _rendered_paths(summary: str) -> set[str]:
    """摘要左值路径集合：``a.b: v`` / ``a.b: v  c.d: v`` 里的字段路径。

    归一化：剥去列表下标（``posts[20].likes`` ↔ ``posts.likes``）与 ``.value``
    段（摘要按数据点全路径渲染，state 侧路径不含它）。只认 ASCII 冒号——口径
    注记块用全角「：」书写，不会污染本集合。
    """
    out: set[str] = set()
    for line in summary.splitlines():
        for m in _PATH_SEG_RE.finditer(line):
            no_idx = _INDEX_RE.sub("", m.group(1))
            segs = [s for s in no_idx.split(".") if s and s != "value"]
            if segs:
                out.add(".".join(segs))
    return out


def _walk(node: Any, path: str, out: dict[str, Any]) -> None:
    """收集 叶路径 → 值：容器（dict / dict 序列）只下钻，不记为叶。

    - 数据点 ``{value, source, ...}``：取 value（None → 跳过，UNKNOWN 不必喂）；
      value 为 dict 时继续下钻（label/note/ratio 各自算一条契约）。
    - 普通 dict：逐键下钻，簿记段跳过。
    - list[dict]：按元素键并集展开为 ``path.key``（摘要渲染为 ``posts[i].key``）。
    - list[标量]：记容器路径本身。
    """
    if isinstance(node, dict):
        if "value" in node and set(node) <= _DP_KEYS:
            inner = node["value"]
            if isinstance(inner, dict):
                _walk(inner, path, out)
            elif inner is not None:
                out[path] = inner
            return
        for k, v in node.items():
            if k in _BOOKKEEPING:
                continue
            _walk(v, f"{path}.{k}" if path else k, out)
        return
    if isinstance(node, list):
        if not node:
            return  # 空序列无内容可喂
        if all(isinstance(it, dict) for it in node):
            keys: set[str] = set()
            for it in node:
                keys |= set(it)
            for k in sorted(keys):
                if k in _BOOKKEEPING:
                    continue
                val = next(
                    (it.get(k) for it in node if it.get(k) is not None), None
                )
                if val is not None:
                    out[f"{path}.{k}"] = val
            return
        out[path] = node
        return
    if node is not None:
        out[path] = node


def _label_like(value: Any) -> bool:
    """可作「取值即标签」匹配的字符串：够长且不是数字文本。

    变异检查实测：``"8"`` / ``"40"`` 这类短数值文本会在摘要他处巧合出现，
    等于给漏喂开后门，故只放行 Uniswap / confirm_long 这种标签串。
    """
    return (
        isinstance(value, str)
        and len(value) >= 3
        and not value.replace(".", "", 1).replace("-", "", 1).isdigit()
    )


def _surfaced(
    path: str, value: Any, rendered: set[str], leafs: set[str], text: str
) -> bool:
    """字段是否喂到了 LLM 眼前（三条通过路径，按严格度降序，任一即算）。"""
    rel = path.split(".", 1)[1]  # 剥域前缀：摘要按节渲染，路径不含域名
    if rel in rendered:
        return True
    if _label_like(value) and value in text:
        return True  # 标签内联渲染（kind/name/category/label 不构成独立左值）
    # 弱匹配：同一数值在另一节以不同前缀渲染（market_data.rv_7d ↔ 信号节
    # market_metrics.value.rv_7d）。只判末段名，会放过同名不同源的字段——
    # 故它只作兜底，排在精确路径与取值文本之后。
    return rel.rsplit(".", 1)[-1] in leafs


def _violations(state: dict, tokens: list[str]) -> list[str]:
    """有值字段中既无路径、取值文本也不可见的项。"""
    rendered: set[str] = set()
    text: list[str] = []
    for symbol in tokens:
        summary = context.build_branch_summary(symbol, state)
        rendered |= _rendered_paths(summary)
        text.append(summary)
    leafs = {p.rsplit(".", 1)[-1] for p in rendered}
    joined = "\n".join(text)
    bad: list[str] = []
    for symbol in tokens:
        for dom in _DOMAINS:
            snap = (state.get(dom) or {}).get(symbol) or {}
            fields: dict[str, Any] = {}
            _walk(snap, dom, fields)
            for path, value in sorted(fields.items()):
                if path in _HIDDEN or _surfaced(path, value, rendered, leafs, joined):
                    continue
                bad.append(f"{symbol} {path} = {value!r}")
    return bad


def test_every_populated_field_is_surfaced(mock_state):
    """契约：mock 全字段装配下，state 有值字段必进摘要（否则先红）。"""
    bad = _violations(mock_state, mock_state["tokens"])
    assert not bad, "以下字段有值却未送达 LLM 摘要（抓了不喂）：\n" + "\n".join(bad)


def test_hidden_list_is_still_needed(mock_state):
    """隐藏清单反向守卫：白名单里的字段若已改为渲染，必须删掉该白名单条目。

    没有这条，``_HIDDEN`` 会变成 permanent 后门——字段一旦进白名单就再没人看。
    """
    summary = context.build_branch_summary(mock_state["tokens"][0], mock_state)
    rendered = _rendered_paths(summary)
    stale = [p for p in _HIDDEN if p.split(".", 1)[1] in rendered]
    assert not stale, f"_HIDDEN 已失效（字段其实已渲染），请删除：{stale}"


def test_bookkeeping_segments_are_not_research_fields():
    """簿记段白名单不得吞掉真字段：这些名字在数据域里只作为状态位存在。"""
    assert "value" not in _BOOKKEEPING  # value 是数据点主体，必须参与契约
    assert not {"label", "note", "quadrant", "posts"} & _BOOKKEEPING
