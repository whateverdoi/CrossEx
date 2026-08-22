"""tools 测试：注册表 + 降采样 ≤10 点 + 成功/无结果/失败 3 态 + 永不抛异常（06 票）。"""

from __future__ import annotations

import re

import pytest

from strategy_research import tools as t
from strategy_research.datasources import binance_futures, defillama
from strategy_research.datasources import web as web_ds

#: 各工具 langchain 名称
_TOOL_NAMES = {
    "get_tvl_history",
    "get_fees_history",
    "get_funding_history",
    "get_stablecoin_history",
    "search_web",
}


def _points(text: str) -> int:
    """统计序列点数：匹配日期模式 ``MM-DD: ``（不含标题里的冒号）。"""
    return len(re.findall(r"\d{2}-\d{2}: ", text))


# ── 注册表 ───────────────────────────────────────────────


def test_tools_registries() -> None:
    """注册表：FACTS_TOOLS 5 个（4 历史序列 + search_web）；CHALLENGE_TOOLS 4 个无联网。"""
    names = {getattr(x, "name", "") for x in t.FACTS_TOOLS}
    assert names == _TOOL_NAMES
    assert len(t.FACTS_TOOLS) == 5

    names = {getattr(x, "name", "") for x in t.CHALLENGE_TOOLS}
    assert names == _TOOL_NAMES - {"search_web"}
    assert len(t.CHALLENGE_TOOLS) == 4


# ── get_tvl_history ──────────────────────────────────────


def test_tvl_history() -> None:
    """mock 成功：序列 + 降采样 ≤10 点；链类 token 无协议 TVL 历史（提示而非数据）。"""
    text = t.get_tvl_history.invoke({"symbol": "UNI"})
    assert text.startswith("TVL 历史")
    assert _points(text) <= 10
    assert "1007" in text  # uniswap len=7 → 最新 tvl=1007.00

    chain_text = t.get_tvl_history.invoke({"symbol": "BTC"})
    assert "链类代币无协议 TVL 历史序列" in chain_text


def test_tvl_history_failure_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    """失败态与无结果态：异常 → 错误文本；空序列 → 无历史数据，均不抛异常。"""

    def boom(_protocol: str, client=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(defillama, "fetch_protocol_tvl_history", boom)
    assert "数据不可用（UNKNOWN）" in t.get_tvl_history.invoke({"symbol": "UNI"})

    monkeypatch.setattr(
        defillama, "fetch_protocol_tvl_history", lambda _p, client=None: None
    )
    assert t.get_tvl_history.invoke({"symbol": "UNI"}) == "无历史数据"


# ── get_fees_history ─────────────────────────────────────


def test_fees_history_mock_success() -> None:
    """mock 成功：fees/revenue 双字段 + 降采样。"""
    text = t.get_fees_history.invoke({"symbol": "UNI"})
    assert "费用/收入历史" in text
    assert _points(text) <= 10
    assert "10.00/5.00" in text


# ── get_funding_history ──────────────────────────────────


def test_funding_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """mock 成功：费率 6 位小数（0.0001 不丢失精度）；无合约态 → 无数据提示。"""
    text = t.get_funding_history.invoke({"symbol": "BTC"})
    assert "资金费率历史" in text
    assert _points(text) <= 10
    assert "0.000" in text

    monkeypatch.setattr(
        binance_futures, "fetch_funding_rate_history", lambda *a, **k: None
    )
    assert "无资金费率数据" in t.get_funding_history.invoke({"symbol": "BTC"})


# ── get_stablecoin_history ───────────────────────────────


def test_stablecoin_history() -> None:
    """mock 成功：链名或 symbol 均可解析；协议类 token（非链）一致地返回无数据。"""
    text = t.get_stablecoin_history.invoke({"symbol": "ETH"})
    assert "稳定币总量历史" in text
    assert _points(text) <= 10

    assert "无稳定币数据" in t.get_stablecoin_history.invoke({"symbol": "UNI"})


# ── search_web ───────────────────────────────────────────


def test_search_web(monkeypatch: pytest.MonkeyPatch) -> None:
    """search_web 三态：mock 带标识；真实失败 → 不可用；真实空 → 无结果。"""
    text = t.search_web.invoke({"query": "UNI unlock"})
    assert text.startswith("（mock 数据）")
    assert "UNI unlock" in text

    monkeypatch.setenv("SR_MOCK", "0")
    monkeypatch.setattr(web_ds, "search_web", lambda *a, **k: None)
    assert t.search_web.invoke({"query": "x"}) == "搜索不可用（UNKNOWN）"

    monkeypatch.setattr(web_ds, "search_web", lambda *a, **k: [])
    assert t.search_web.invoke({"query": "x"}) == "无搜索结果"


# ── 工具层永不抛异常 ─────────────────────────────────────


def test_tools_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """工具层永不抛异常：垃圾输入与数据源异常均产出非空文本。"""
    for fn in t.FACTS_TOOLS:
        for arg in (123, None, "", [], {}):
            try:
                text = fn.func(arg)
            except Exception as exc:  # pragma: no cover - 契约违反即测试失败
                pytest.fail(f"{fn.name}({arg!r}) 抛异常: {exc}")
            assert isinstance(text, str) and text

    def boom(*_a, **_k):
        raise RuntimeError("boom")

    for mod in (defillama, binance_futures):
        monkeypatch.setattr(
            mod,
            "fetch_protocol_tvl_history",
            boom,
            raising=False,
        )
    for fn in t.FACTS_TOOLS:
        try:
            text = fn.func("UNI" if fn.name != "search_web" else "x")
        except Exception as exc:  # pragma: no cover - 契约违反即测试失败
            pytest.fail(f"{fn.name} 数据源异常时抛异常: {exc}")
        assert isinstance(text, str) and text
