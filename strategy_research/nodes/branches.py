"""多空 LLM 证据分支（bull/bear 共用模板）+ JSON 宽容解析与截断恢复。

分支节点只写本分支独占字段（{side}_evidence / {side}_errors）——两分支
并行时写共享键（meta 等）会触发 LangGraph 并行写冲突（spec D1）。
"""

from __future__ import annotations

import re
from typing import Any

from strategy_research import context, env
from strategy_research import evidence as ev_mod
from strategy_research.evidence import EvidenceItem
from strategy_research.schemas import _extract_json, _match_brace, _try_loads


def _evidence_from_object(obj: Any) -> list[dict]:
    """解析对象（整体或单条）→ 证据列表：宽容解析 + EvidenceItem 校验。

    坏条目（claim/source 空或字段非法）丢弃在装配层（02 票）；
    对象非 dict（如数组）→ []（调用方容忍，永不抛异常）。
    """
    if not isinstance(obj, dict):
        return []
    out: list[dict] = []
    for x in obj.get("evidence") or []:
        try:
            item = EvidenceItem.model_validate(x).model_dump()
            if item["claim"] and item["source"]:
                out.append(item)
        except Exception:  # noqa: S112 —— 坏条目丢弃（02 票）
            continue
    return out


def _recover_item_objects(content: str) -> list[dict]:
    """整体 JSON 不可解析时的逐条恢复：每个 ``{"claim"...}`` 对象片段独立解析。

    json_object 输出中间损坏（非法转义/引号破损）时整体解析失败，但未损坏的
    条目仍完整——按 claim 键定位条目起点（向前找最近 {），逐段宽容解析，
    恢复完整条目；损坏条目丢弃（宁缺毋滥，核验层兜底）。
    """
    out: list[dict] = []
    for m in re.finditer(r'"\s*claim\s*"\s*:', content):
        start = content.rfind("{", 0, m.start())
        if start == -1:
            continue
        end = _match_brace(content, start, "{", "}")
        piece = content[start : end + 1] if end is not None else content[start:]
        obj = _try_loads(piece)
        if not isinstance(obj, dict):
            continue
        # 片段可能是条目本体（{claim,...}）或包装对象（{"evidence": [...]}）
        rows = obj.get("evidence") if isinstance(obj.get("evidence"), list) else [obj]
        for x in rows:
            try:
                item = EvidenceItem.model_validate(x).model_dump()
                if item["claim"] and item["source"]:
                    out.append(item)
            except Exception:  # noqa: S112 —— 坏条目丢弃（02 票）
                continue
    return out


def _truncation_probe(exc: Exception) -> dict:
    """截断异常探测：沿异常链找 completion，返回 content 形态摘要（诊断用）。

    恢复失败时留痕，run.json 错误消息可诊断：content 长度 / 整体解析结果 /
    evidence 条数 / 头尾片段——下次运行直接可见失败形态，避免黑盒。
    """
    seen: set[int] = set()
    queue: list[Exception] = [exc]
    while queue:
        e = queue.pop(0)
        if id(e) in seen:
            continue
        seen.add(id(e))
        comp = getattr(e, "completion", None)
        if comp is not None:
            try:
                content = comp.choices[0].message.content or ""
            except Exception:
                content = ""
            if content:
                obj = _extract_json(content)
                ev = (obj or {}).get("evidence") if isinstance(obj, dict) else None
                return {
                    "content_len": len(content),
                    "extract": "ok" if obj is not None else "fail",
                    "evidence": len(ev) if isinstance(ev, list) else type(ev).__name__,
                    "head": repr(content[:100]),
                    "tail": repr(content[-100:]),
                }
        cause = getattr(e, "__cause__", None)
        ctx = getattr(e, "__context__", None)
        if cause is not None:
            queue.append(cause)
        if ctx is not None and ctx is not cause:
            queue.append(ctx)
    return {}


def _extract_partial_evidence(exc: Exception) -> list[dict]:
    """LengthFinishReasonError（输出截断）的部分输出 → 宽容解析证据列表。

    json_object 模式输出达 max_tokens（8192）被截断时，openai SDK 抛
    LengthFinishReasonError（langchain 原样传播，消息含 CompletionUsage）；
    异常携带 ``completion`` 字段含被截断的 JSON——用 _extract_json 尾部截断
    容错恢复完整证据条目（截断点之前的证据有效，宁可部分不丢全部）。
    整体解析失败时逐条对象恢复兜底（_recover_item_objects）。
    沿异常链（__cause__/__context__）探测 completion，不依赖 openai 类型。
    返回恢复的证据列表；不可恢复 → []。
    """
    seen: set[int] = set()
    queue: list[Exception] = [exc]
    while queue:
        e = queue.pop(0)
        if id(e) in seen:
            continue
        seen.add(id(e))
        comp = getattr(e, "completion", None)
        if comp is not None:
            try:
                content = comp.choices[0].message.content
            except Exception:
                content = None
            if content:
                out = _evidence_from_object(_extract_json(content))
                if out:
                    return out
                # 整体解析失败/证据为空：逐条对象恢复（中间损坏兜底）
                recovered = _recover_item_objects(content)
                if recovered:
                    return recovered
        cause = getattr(e, "__cause__", None)
        ctx = getattr(e, "__context__", None)
        if cause is not None:
            queue.append(cause)
        if ctx is not None and ctx is not cause:
            queue.append(ctx)
    return []


def _invoke_branch(
    symbol: str, state: dict, side: str
) -> tuple[list[dict], str | None]:
    """分支单 token 证据提取（02 票）：json_mode 单次调用 + 宽容解析。

    坏条目（claim/source 空）丢弃在装配层；prompt 约束条数上限 15、basis 三元组
    互不相同（核验前机器去重兜底）；异常 → ([], 错误消息)，批不中断。重试 2 次
    （共 3 次尝试）——LLM 偶发失败（限流/网络抖动）不应直接产出 0 证据。
    返回 (items, error)。
    """
    prompt = context.BULL_PROMPT if side == "bull" else context.BEAR_PROMPT
    items: list[dict] = []
    try:
        summary = context.build_branch_summary(symbol, state)
        out = (
            env.get_llm(json_mode=True)
            .with_retry(stop_after_attempt=2)
            .invoke(
                [("system", prompt), ("human", summary)],
                config={"callbacks": [env.live_call_counter(side)]},
            )
        )
        obj = _extract_json(getattr(out, "content", out))
        for x in (obj or {}).get("evidence") or []:
            try:
                item = EvidenceItem.model_validate(x).model_dump()
                if item["claim"] and item["source"]:
                    items.append(item)
            except Exception:  # noqa: S112 —— 坏条目丢弃（02 票）
                continue
        return items, None
    except Exception as exc:
        # 输出截断（max_tokens 上限）→ 部分输出宽容恢复，宁可有据的部分不丢全部
        partial = _extract_partial_evidence(exc)
        if partial:
            return partial, f"分支截断恢复（{len(partial)} 条，原错误: {type(exc).__name__}）"
        # 恢复失败：截断探测留痕（content 形态摘要），错误可诊断而非黑盒
        probe = _truncation_probe(exc)
        if probe:
            brief = "，".join(f"{k}={v}" for k, v in probe.items())
            return [], f"分支异常: {type(exc).__name__}（截断探测: {brief}）"
        return [], f"分支异常: {type(exc).__name__}: {exc}"


def _branch(state: dict, side: str) -> dict:
    """分支节点模板（bull/bear 共用，02 票）：每 token 独立证据提取，批不中断。

    只写本分支独占字段（{side}_evidence / {side}_errors）——两分支并行时
    写共享键（meta 等）会触发 LangGraph 并行写冲突（spec D1）；node_order
    由串行的 evidence_verify 统一记录。
    """
    errors: dict[str, str] = {}
    items: dict[str, list[dict]] = {}
    for s in state["tokens"]:
        got, err = _invoke_branch(s, state, side)
        items[s] = ev_mod.dedup_evidence(got)  # 同 basis 三元组/同 claim 改写去重
        if err:
            errors[s] = err
        elif not got:  # 成功但空证据：留痕可诊断（区分失败与合法空）
            errors[s] = "分支返回空证据（0 条）"
    out: dict[str, Any] = {f"{side}_evidence": items}
    if errors:
        out[f"{side}_errors"] = errors
    return out


def bull_research(state: dict) -> dict:
    """多头证据研究员（02 票）：单 token 结构化证据（上限 15 条，逐条机器核验）。"""
    return _branch(state, "bull")


def bear_research(state: dict) -> dict:
    """空头证据研究员（02 票）：单 token 结构化证据（数量不设上限，逐条机器核验）。"""
    return _branch(state, "bear")
