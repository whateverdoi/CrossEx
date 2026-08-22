"""宽容 JSON 解析器（_extract_json 系列，06 票）。

LLM 输出是弱契约：代码块围栏 / 单引号键 / 尾部截断 / 未闭合括号全部在
``_try_loads`` 逐级容错归一；不可解析 → None，调用方按空处理永不抛异常。
分支证据条目的字段校验在 evidence.EvidenceItem（05 票起 schema 本体收敛
至 evidence.py，本模块仅保留解析器）。
"""

from __future__ import annotations

import json
import re
from typing import Any

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

    比 count 差值更精确：嵌套 ``{"evidence": [{"claim": "b"`` 缺的是 ``]}"``
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
    分支证据契约是对象（evidence 列表在对象内），回退内层会让
    ``.get("evidence")`` 崩溃。
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
