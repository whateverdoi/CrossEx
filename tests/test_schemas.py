"""schemas 测试：_extract_json 宽容解析（06 票）。

05 票：旧决策链 schema（TokenAnalysis/FactItem/ChallengeItem/RebuttalItem）已
退役，证据条目 schema 收敛至 evidence.py（test_evidence 覆盖）；本模块仅剩
纯解析器测试。夹具键名用 evidence，与分支证据契约一致。
"""

from __future__ import annotations

from strategy_research import schemas as s

# ── _extract_json ────────────────────────────────────────


def test_extract_json_quirks() -> None:
    """杂质剥离：代码块围栏 / 前后文本；单引号键容错；尾部缺闭合 → 补闭合。"""
    text = '好的，结果如下：```json\n{"evidence": [{"claim": "a"}]}\n``` 结束'
    assert s._extract_json(text) == {"evidence": [{"claim": "a"}]}
    assert s._extract_json('前缀说明 {"evidence": []} 后缀说明') == {"evidence": []}
    assert s._extract_json("{'evidence': []}") == {"evidence": []}
    obj = s._extract_json('{"evidence": [{"claim": "a"}]')
    assert isinstance(obj, dict)
    assert obj["evidence"] == [{"claim": "a"}]


def test_extract_json_truncated() -> None:
    """截断三分支：键值对间取前缀 / 字符串中间补闭引号 / 非法值中间丢键保骨架。"""
    obj = s._extract_json('{"evidence": [{"claim": "a"}, {"claim": "b"')
    assert isinstance(obj, dict)
    assert obj["evidence"] == [{"claim": "a"}, {"claim": "b"}]
    assert s._extract_json('{"evidence": [{"claim": "ab') == {"evidence": [{"claim": "ab"}]}
    assert s._extract_json('{"evidence": [{"claim": ab') == {"evidence": [{}]}


def test_extract_json_no_json() -> None:
    """无 JSON 结构 → None；顶层数组可解析；外层对象不闭合时不回退内层数组。"""
    assert s._extract_json("模型只输出了文本") is None
    assert s._extract_json("") is None
    assert s._extract_json(None) is None
    assert s._extract_json(42) is None
    assert s._extract_json("[1, 2, 3]") == [1, 2, 3]  # 顶层数组也可解析
    obj = s._extract_json('{"evidence": [{"claim": "a"}]')
    assert isinstance(obj, dict)  # 补闭合成功，而非返回内层列表
