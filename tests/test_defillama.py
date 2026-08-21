"""defillama 数据源测试（httpx sync + mock 同构 + 失败即失败）。"""

from __future__ import annotations

import httpx
import pytest

from strategy_research.datasources import defillama

PROTOCOL = "uniswap"


def _client(json_body) -> httpx.Client:
    """用 MockTransport 构造测试客户端（零外部请求）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=json_body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _client_raise(exc: Exception) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_protocol_tvl_mock() -> None:
    """mock 模式：与真实路径字段同构。"""
    row = defillama.fetch_protocol_tvl(PROTOCOL)
    assert row is not None
    assert set(row) == {"tvl", "change_7d", "mcap", "fdv"}
    assert isinstance(row["tvl"], float)


def test_fetch_protocol_tvl_parses(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """/protocol/{id} 真实形状：currentChainTvls 求和 + 历史算 7d 变化。"""
    monkeypatch.setenv("SR_MOCK", "0")
    body = {
        "currentChainTvls": {"Ethereum": 4000.0, "Arbitrum": 567.8},
        "tvl": [
            {"date": 1700000000, "totalLiquidityUSD": 3000.0},
            {"date": 1700604800, "totalLiquidityUSD": 4567.8},
        ],
        "mcap": 123.4,
    }
    row = defillama.fetch_protocol_tvl(PROTOCOL, client=_client(body))
    assert row["tvl"] == pytest.approx(4567.8)
    assert row["change_7d"] == pytest.approx(52.26)
    assert row["mcap"] == pytest.approx(123.4)
    assert row["fdv"] is None


def test_fetch_protocol_tvl_missing_fields_none(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """缺失字段 → None（UNKNOWN 纪律，不填充默认值）。"""
    monkeypatch.setenv("SR_MOCK", "0")
    row = defillama.fetch_protocol_tvl(
        PROTOCOL,
        client=_client({"tvl": [{"date": 1, "totalLiquidityUSD": 1.0}]}))
    assert row == {"tvl": None, "change_7d": None, "mcap": None, "fdv": None}


def test_fetch_protocol_fees_parses(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """/summary/fees/{id} → {fees_24h, fees_7d, revenue_24h, revenue_7d}。"""
    monkeypatch.setenv("SR_MOCK", "0")
    body = {"total24h": 100.0, "total7d": 700.0, "revenue24h": 50.0,
            "revenue7d": 350.0}
    row = defillama.fetch_protocol_fees(PROTOCOL, client=_client(body))
    assert row == {"fees_24h": 100.0, "fees_7d": 700.0,
                   "revenue_24h": 50.0, "revenue_7d": 350.0}


def test_fetch_stablecoin_supply_sums_latest_day(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """stablecoincharts/{chain}：取最新一天各币种 totalCirculatingUSD 求和。"""
    monkeypatch.setenv("SR_MOCK", "0")
    body = [
        {"date": 1700000000, "totalCirculatingUSD": {"peggedUSD": 100.0}},
        {"date": 1700000001,
         "totalCirculatingUSD": {"peggedUSD": 300.0, "peggedUSDC": 200.0}},
    ]
    row = defillama.fetch_stablecoin_supply("ethereum", client=_client(body))
    assert row == {"stablecoin_supply": 500.0, "incomplete": False}


def test_fetch_stablecoin_supply_marks_incomplete(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """缺失项不计入（不按 0 猜测），标 incomplete；全缺失 → None。"""
    monkeypatch.setenv("SR_MOCK", "0")
    body = [{"date": 1, "totalCirculatingUSD":
             {"peggedUSD": 300.0, "broken": "x"}}]
    row = defillama.fetch_stablecoin_supply("ethereum", client=_client(body))
    assert row == {"stablecoin_supply": 300.0, "incomplete": True}

    body_all_missing = [{"date": 1, "totalCirculatingUSD": {"a": "x"}}]
    assert defillama.fetch_stablecoin_supply(
        "ethereum", client=_client(body_all_missing)) is None


def test_fetch_dex_volume_global_parses(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """overview/dexs 全局 → {dex_volume_24h}（真实键名 total24h）。"""
    monkeypatch.setenv("SR_MOCK", "0")
    body = {"total24h": 9876.5, "total7d": 50000.0}
    row = defillama.fetch_dex_volume_24h(client=_client(body))
    assert row == {"dex_volume_24h": 9876.5}


def test_fetch_dex_volume_chain_param(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """chain 指定时带 chains 查询参数。"""
    monkeypatch.setenv("SR_MOCK", "0")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"total24h": 1.0})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    defillama.fetch_dex_volume_24h(chain="solana", client=client)
    assert "chains=solana" in captured["url"]
    assert "excludeTotalDataChart=true" in captured["url"]


def test_fetch_failure_returns_none(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """失败即失败：网络异常 → None（装配层标 UNKNOWN）。"""
    monkeypatch.setenv("SR_MOCK", "0")
    client = _client_raise(httpx.ConnectError("refused"))
    assert defillama.fetch_protocol_tvl(PROTOCOL, client=client) is None
    assert defillama.fetch_protocol_fees(PROTOCOL, client=client) is None
    assert defillama.fetch_stablecoin_supply("ethereum", client=client) is None
    assert defillama.fetch_dex_volume_24h(client=client) is None


def test_mock_zero_external_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """SR_MOCK=1 时零外部请求：httpx.Client 被调用即爆炸也不影响。"""
    monkeypatch.setattr(
        "strategy_research.datasources.defillama._get_client",
        lambda: pytest.fail("mock 模式不应发起 HTTP"))
    assert defillama.fetch_protocol_tvl(PROTOCOL) is not None
    assert defillama.fetch_stablecoin_supply("ethereum") is not None
