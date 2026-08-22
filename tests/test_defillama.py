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
    assert set(row) == {
        "tvl",
        "tvl_change_1d",
        "tvl_change_7d",
        "tvl_change_30d",
        "mcap",
        "fdv",
    }
    assert isinstance(row["tvl"], float)


def test_fetch_protocol_tvl_parses(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert row["tvl_change_7d"] == pytest.approx(52.26)
    assert row["tvl_change_30d"] is None  # 历史不足 30 天
    assert row["mcap"] == pytest.approx(123.4)
    assert row["fdv"] is None


def test_fetch_protocol_tvl_missing_fields_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺失字段 → None（UNKNOWN 纪律，不填充默认值）。"""
    monkeypatch.setenv("SR_MOCK", "0")
    row = defillama.fetch_protocol_tvl(
        PROTOCOL, client=_client({"tvl": [{"date": 1, "totalLiquidityUSD": 1.0}]})
    )
    assert row == {
        "tvl": None,
        "tvl_change_1d": None,
        "tvl_change_7d": None,
        "tvl_change_30d": None,
        "mcap": None,
        "fdv": None,
    }


def test_fetch_protocol_fees_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """/summary/fees/{id} → {fees_24h, fees_7d, revenue_24h, revenue_7d}。"""
    monkeypatch.setenv("SR_MOCK", "0")
    body = {"total24h": 100.0, "total7d": 700.0, "revenue24h": 50.0, "revenue7d": 350.0}
    row = defillama.fetch_protocol_fees(PROTOCOL, client=_client(body))
    assert row == {
        "fees_24h": 100.0,
        "fees_7d": 700.0,
        "revenue_24h": 50.0,
        "revenue_7d": 350.0,
    }


def test_fetch_stablecoin_supply(monkeypatch: pytest.MonkeyPatch) -> None:
    """stablecoincharts/{chain}：最新一天求和；缺失项不计入标 incomplete；全缺失 → None。"""
    monkeypatch.setenv("SR_MOCK", "0")
    body = [
        {"date": 1700000000, "totalCirculatingUSD": {"peggedUSD": 100.0}},
        {
            "date": 1700000001,
            "totalCirculatingUSD": {"peggedUSD": 300.0, "peggedUSDC": 200.0},
        },
    ]
    row = defillama.fetch_stablecoin_supply("ethereum", client=_client(body))
    assert row == {"stablecoin_supply": 500.0, "incomplete": False}
    # 缺失项不计入（不按 0 猜测）→ incomplete；全缺失 → None
    partial = [{"date": 1, "totalCirculatingUSD": {"peggedUSD": 300.0, "broken": "x"}}]
    assert defillama.fetch_stablecoin_supply(
        "ethereum", client=_client(partial)
    ) == {"stablecoin_supply": 300.0, "incomplete": True}
    all_missing = [{"date": 1, "totalCirculatingUSD": {"a": "x"}}]
    assert (
        defillama.fetch_stablecoin_supply("ethereum", client=_client(all_missing))
        is None
    )


def test_fetch_dex_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    """overview/dexs：全局 → {dex_volume_24h}；chain 指定时带 chains 查询参数。"""
    monkeypatch.setenv("SR_MOCK", "0")
    body = {"total24h": 9876.5, "total7d": 50000.0}
    row = defillama.fetch_dex_volume_24h(client=_client(body))
    assert row == {"dex_volume_24h": 9876.5}
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"total24h": 1.0})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    defillama.fetch_dex_volume_24h(chain="solana", client=client)
    assert "chains=solana" in captured["url"]
    assert "excludeTotalDataChart=true" in captured["url"]


def test_mock_zero_external_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """SR_MOCK=1 时零外部请求：httpx.Client 被调用即爆炸也不影响。"""
    monkeypatch.setattr(
        "strategy_research.datasources.defillama._get_client",
        lambda: pytest.fail("mock 模式不应发起 HTTP"),
    )
    assert defillama.fetch_protocol_tvl(PROTOCOL) is not None
    assert defillama.fetch_stablecoin_supply("ethereum") is not None


# ── 04 共享资源 ────────────────────────────────────────────


def _client_routes(routes: dict[str, object]) -> httpx.Client:
    """按 URL path 子串路由的 MockTransport 客户端（零外部请求）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        for key, body in routes.items():
            if key in request.url.path:
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": "no route"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_strip_quote() -> None:
    """交易所 symbol → 裸 base 符号（去计价后缀）；剥空/无后缀原样。"""
    assert defillama._strip_quote("BTCUSDT") == "BTC"
    assert defillama._strip_quote("BTCUSDC") == "BTC"
    assert defillama._strip_quote("BTCFDUSD") == "BTC"
    assert defillama._strip_quote("BTC") == "BTC"
    assert defillama._strip_quote("USDT") == "USDT"


def test_token_slug_map_design() -> None:
    """协议类无 chain: 前缀、链类有；未映射 token 为兜底路径（空字符串）。"""
    assert defillama.TOKEN_SLUG_MAP["UNI"] == "uniswap"
    assert defillama.TOKEN_SLUG_MAP["BTC"].startswith("chain:")
    assert defillama.TOKEN_SLUG_MAP.get("XRP", "") == ""


def test_mock_shared_resources() -> None:
    """mock 共享资源表：chains/protocols/stablecoins/dexs/fees/chain_tvl 形状齐备。"""
    chains = defillama.fetch_chains()
    assert isinstance(chains, dict) and len(chains) > 0
    name, row = next(iter(chains.items()))
    assert name == name.lower()  # 键统一小写（与 /charts 链名一致）
    assert set(row) == {"tvl", "token_symbol"}
    assert isinstance(row["tvl"], float)

    index = defillama.fetch_protocols()
    assert isinstance(index, dict) and len(index) > 0
    assert all(isinstance(slug, str) for slug in index.values())

    stable = defillama.fetch_stablecoins()
    assert isinstance(stable, dict) and len(stable) > 0
    assert all(isinstance(v, float) for v in stable.values())

    dexs = defillama.fetch_dexs()
    assert isinstance(dexs, dict) and len(dexs) > 0
    assert all(isinstance(v, float) for v in dexs.values())

    fees = defillama.fetch_fees(["uniswap", "aave"])
    assert set(fees) == {"uniswap", "aave"}
    assert set(fees["uniswap"]) == {"fees_24h", "fees_7d", "revenue_24h", "revenue_7d"}

    chain_tvl = defillama.fetch_chain_tvl("solana")
    assert set(chain_tvl) == {"tvl", "tvl_change_1d", "tvl_change_7d", "tvl_change_30d"}
    assert isinstance(chain_tvl["tvl"], float)


# ── 历史序列（06 票：工具层数据源）──────────────────────


def test_mock_history_consistent() -> None:
    """mock 历史序列与当前快照同构：TVL 90 天回算变化率一致；fees/稳定币最新值一致。"""
    rows = defillama.fetch_protocol_tvl_history("uniswap")
    assert rows is not None and len(rows) == 90
    latest = rows[-1]["tvl"]
    assert latest == pytest.approx(defillama.fetch_protocol_tvl("uniswap")["tvl"])
    assert (latest / rows[-8]["tvl"] - 1) * 100 == pytest.approx(2.5, abs=0.01)
    assert (latest / rows[-31]["tvl"] - 1) * 100 == pytest.approx(10.0, abs=0.01)

    fee_rows = defillama.fetch_protocol_fees_history("uniswap")
    assert fee_rows is not None
    assert fee_rows[-1]["fees"] == pytest.approx(10.0)
    assert fee_rows[-1]["revenue"] == pytest.approx(5.0)

    sc_rows = defillama.fetch_stablecoin_history("ethereum")
    assert sc_rows is not None
    assert sc_rows[-1]["supply"] == pytest.approx(1.5e9)


TVL_HIST_JSON = {
    "tvl": [
        {"date": 1700000000, "totalLiquidityUSD": 100.0},
        {"date": 1700086400, "totalLiquidityUSD": 110.5},
    ]
}


def test_history_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实路径解析：TVL 数组顺序保留；fees 单字段缺失保留；稳定币缺失币种跳过。"""
    monkeypatch.setenv("SR_MOCK", "0")
    tvl_rows = defillama.fetch_protocol_tvl_history(
        "uniswap", client=_client(TVL_HIST_JSON)
    )
    assert tvl_rows == [
        {"date": 1700000000, "tvl": 100.0},
        {"date": 1700086400, "tvl": 110.5},
    ]
    fee_rows = defillama.fetch_protocol_fees_history(
        "uniswap", client=_client(FEES_HIST_JSON)
    )
    assert fee_rows == [
        {"date": 1700000000, "fees": 10.0, "revenue": 5.0},
        {"date": 1700086400, "fees": None, "revenue": 6.0},
    ]
    sc_rows = defillama.fetch_stablecoin_history(
        "ethereum", client=_client(SC_HIST_JSON)
    )
    assert sc_rows == [
        {"date": 1700000000, "supply": 100.0},
        {"date": 1700086400, "supply": 110.0},
    ]


FEES_HIST_JSON = [
    {"date": 1700000000, "fees": 10.0, "revenue": 5.0},
    {"date": 1700086400, "fees": None, "revenue": 6.0},
]


SC_HIST_JSON = [
    {"date": 1700000000, "totalCirculatingUSD": {"usdt": 100.0, "usdc": None}},
    {"date": 1700086400, "totalCirculatingUSD": {"usdt": 110.0}},
]


def test_fetch_chains_real_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """/v2/chains → {链名小写: {tvl, token_symbol}}；缺失字段跳过。"""
    monkeypatch.setenv("SR_MOCK", "0")
    body = [
        {"name": "Ethereum", "tokenSymbol": "ETH", "tvl": 4.7e10},
        {"name": "Solana", "tokenSymbol": "SOL", "tvl": None},
        {"name": None, "tokenSymbol": "X", "tvl": 1.0},
    ]
    chains = defillama.fetch_chains(client=_client(body))
    assert chains == {"ethereum": {"tvl": 4.7e10, "token_symbol": "ETH"}}


def test_fetch_protocols_real_first_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """/protocols → {symbol: slug}；同 symbol 首见胜；缺失字段跳过。"""
    monkeypatch.setenv("SR_MOCK", "0")
    body = [
        {"symbol": "UNI", "slug": "uniswap"},
        {"symbol": "UNI", "slug": "uniswap-v2"},
        {"symbol": "AAVE", "slug": "aave"},
        {"symbol": None, "slug": "x"},
    ]
    index = defillama.fetch_protocols(client=_client(body))
    assert index == {"UNI": "uniswap", "AAVE": "aave"}


def test_fetch_stablecoins_real_aggregates(monkeypatch: pytest.MonkeyPatch) -> None:
    """/stablecoins → {链名小写: 供应量}（current.peggedUSD 求和）。"""
    monkeypatch.setenv("SR_MOCK", "0")
    body = {
        "peggedAssets": [
            {
                "symbol": "USDT",
                "chainCirculating": {
                    "Ethereum": {"current": {"peggedUSD": 1e9}},
                    "Solana": {"current": {"peggedUSD": 2e8}},
                    "Tron": {"current": {"peggedUSD": "bad"}},  # 非法跳过
                },
            },
            {
                "symbol": "USDC",
                "chainCirculating": {
                    "Ethereum": {"current": {"peggedUSD": 5e8}},
                },
            },
            {"symbol": "BAD", "chainCirculating": "nope"},  # 非 dict 跳过
        ]
    }
    table = defillama.fetch_stablecoins(client=_client(body))
    assert table == {"ethereum": 1.5e9, "solana": 2e8}


def test_fetch_dexs_real_aggregates(monkeypatch: pytest.MonkeyPatch) -> None:
    """/overview/dexs → {链名小写: 24h 量}（breakdown24h 嵌套求和）。"""
    monkeypatch.setenv("SR_MOCK", "0")
    body = {
        "protocols": [
            {
                "name": "Curve",
                "breakdown24h": {
                    "ethereum": {"Curve DEX": 1e8},
                    "arbitrum": {"Curve DEX": 2e7},
                },
            },
            {
                "name": "Uniswap",
                "breakdown24h": {
                    "ethereum": {"Uniswap V3": 3e8},
                },
            },
            {"name": "Bad", "breakdown24h": "nope"},  # 非 dict 跳过
        ]
    }
    table = defillama.fetch_dexs(client=_client(body))
    assert table == {"ethereum": 4e8, "arbitrum": 2e7}


def test_fetch_fees_real_partial_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """per-slug 聚合：单 slug 失败跳过（不中断批）。"""
    monkeypatch.setenv("SR_MOCK", "0")
    client = _client_routes(
        {
            "/summary/fees/uniswap": {
                "total24h": 10.0,
                "total7d": 70.0,
                "revenue24h": 5.0,
                "revenue7d": 35.0,
            },
        }
    )  # aave 无路由 → 404 → 跳过
    table = defillama.fetch_fees(["uniswap", "aave"], client=client)
    assert set(table) == {"uniswap"}
    assert table["uniswap"]["fees_24h"] == 10.0


def test_fetch_chain_tvl_real_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """/charts/{chain} → {tvl, tvl_change_*}（与协议 TVL 同构）。"""
    monkeypatch.setenv("SR_MOCK", "0")
    body = [
        {"date": 1700000000, "totalLiquidityUSD": 1e9},
        {"date": 1700086400, "totalLiquidityUSD": 1.05e9},  # +1 天
    ]
    row = defillama.fetch_chain_tvl("ethereum", client=_client(body))
    assert row["tvl"] == 1.05e9
    assert row["tvl_change_1d"] == pytest.approx(5.0)
    assert row["tvl_change_7d"] is None  # 历史不足 7 天
    assert row["tvl_change_30d"] is None


def test_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """失败即失败：网络异常 → None（fetch_fees 例外：空表；装配层标 UNKNOWN）。"""
    monkeypatch.setenv("SR_MOCK", "0")
    client = _client_raise(httpx.ConnectError("refused"))
    # 基础资源
    assert defillama.fetch_protocol_tvl(PROTOCOL, client=client) is None
    assert defillama.fetch_protocol_fees(PROTOCOL, client=client) is None
    assert defillama.fetch_stablecoin_supply("ethereum", client=client) is None
    assert defillama.fetch_dex_volume_24h(client=client) is None
    # 历史序列
    assert defillama.fetch_protocol_tvl_history("uniswap", client=client) is None
    assert defillama.fetch_protocol_fees_history("uniswap", client=client) is None
    assert defillama.fetch_stablecoin_history("ethereum", client=client) is None
    # 共享资源
    assert defillama.fetch_chains(client=client) is None
    assert defillama.fetch_protocols(client=client) is None
    assert defillama.fetch_stablecoins(client=client) is None
    assert defillama.fetch_dexs(client=client) is None
    assert defillama.fetch_chain_tvl("solana", client=client) is None
    assert defillama.fetch_fees(["uniswap"], client=client) == {}
