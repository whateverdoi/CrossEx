"""defillama 数据源（httpx sync，零 key）。

- ``fetch_protocol_tvl``：/protocol/{id} 协议 TVL（当前值 + 7d 变化 + mcap/fdv）
- ``fetch_protocol_fees``：/summary/fees/{id} 协议费用与收入
- ``fetch_stablecoin_supply``：stablecoins.llama.fi 链稳定币总量（最新日求和）
- ``fetch_dex_volume_24h``：/overview/dexs 链 DEX 交易量（可指定 chain）

所有 fetch 失败返回 ``None``（装配层标 UNKNOWN，失败即失败不回退 mock）。
测试可注入 ``httpx.Client``（MockTransport）零外部请求。
"""

from __future__ import annotations

import atexit
from typing import Any

import httpx

from .. import env
from . import mock

BASE_URL = "https://api.llama.fi"
STABLECOINS_URL = "https://stablecoins.llama.fi"
TIMEOUT_SECONDS = 15.0

#: token（裸 base 符号）→ DeFiLlama slug 静态映射；``chain:`` 前缀表示链类
#: （/charts/{name}），否则为协议类（/protocol/{slug}）。未命中时装配层惰性
#: 拉取 :func:`fetch_protocols` 兜底（约 8MB，不常触发）。
TOKEN_SLUG_MAP = {
    # 协议类（/protocol/{slug}）
    "UNI": "uniswap",
    "AAVE": "aave",
    "MKR": "makerdao",
    "LDO": "lido",
    "CRV": "curve",
    "SUSHI": "sushi",
    "COMP": "compound-v2",
    "GMX": "gmx",
    "PENDLE": "pendle",
    "ENA": "ethena",
    # 链类（/charts/{name}，chain: 前缀）
    "BTC": "chain:bitcoin",
    "ETH": "chain:ethereum",
    "SOL": "chain:solana",
    "DOGE": "chain:doge",
    "AVAX": "chain:avalanche",
    "ADA": "chain:cardano",
}

_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    """惰性单例 httpx 客户端（sync，进程退出时关闭连接池）。"""
    global _client
    if _client is None:
        _client = httpx.Client(timeout=TIMEOUT_SECONDS)
        atexit.register(_client.close)
    return _client


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fetch_json(
    url: str, client: httpx.Client | None = None, params: dict | None = None
) -> Any:
    """GET + JSON 解析；异常原样抛出由调用方转 None。"""
    c = client or _get_client()
    resp = c.get(url, params=params)
    resp.raise_for_status()
    return resp.json()


def _change_from_history(history: Any, days: int) -> float | None:
    """历史数组（``[{date, totalLiquidityUSD}]``）→ N 天变化百分比。

    取最新点与 cutoff（最新日 - N 天）前最后一条对比；数据不足/非法 → None。
    date 兼容 int/str（真实 /charts 返回字符串时间戳）。
    """
    if not isinstance(history, list) or len(history) < 2:
        return None
    try:
        latest = float(history[-1]["totalLiquidityUSD"])
        last_ts = float(history[-1]["date"])
        cutoff = last_ts - days * 86_400  # N 天（秒）
    except (KeyError, TypeError, ValueError):
        return None
    prev: dict | None = None
    for point in history:
        try:
            ts = float(point.get("date", 0))
        except (TypeError, ValueError):
            continue
        if ts <= cutoff:
            prev = point
    if prev is None:
        return None
    try:
        prev_val = float(prev["totalLiquidityUSD"])
    except (KeyError, TypeError, ValueError):
        return None
    if prev_val <= 0:
        return None
    return (latest - prev_val) / prev_val * 100


def _chain_tvl_sum(data: dict) -> float | None:
    """当前 TVL：currentChainTvls 各链求和（协议详情无单一当前值字段）。"""
    chains = data.get("currentChainTvls")
    if not isinstance(chains, dict):
        return None
    values = [v for v in (_float_or_none(x) for x in chains.values()) if v is not None]
    return sum(values) if values else None


def fetch_protocol_tvl(
    protocol: str, client: httpx.Client | None = None
) -> dict | None:
    """协议 TVL：``{tvl, tvl_change_1d, tvl_change_7d, tvl_change_30d,
    mcap, fdv}``（缺失字段 None）。"""
    if env.is_mock_mode():
        return mock.mock_protocol_tvl(protocol)
    try:
        data = _fetch_json(f"{BASE_URL}/protocol/{protocol}", client=client)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return {
        "tvl": _chain_tvl_sum(data),
        "tvl_change_1d": _change_from_history(data.get("tvl"), 1),
        "tvl_change_7d": _change_from_history(data.get("tvl"), 7),
        "tvl_change_30d": _change_from_history(data.get("tvl"), 30),
        "mcap": _float_or_none(data.get("mcap")),
        "fdv": _float_or_none(data.get("fdv")),
    }


def fetch_protocol_fees(
    protocol: str, client: httpx.Client | None = None
) -> dict | None:
    """协议费用/收入：``{fees_24h, fees_7d, revenue_24h, revenue_7d}``。"""
    if env.is_mock_mode():
        return mock.mock_protocol_fees(protocol)
    try:
        data = _fetch_json(f"{BASE_URL}/summary/fees/{protocol}", client=client)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return {
        "fees_24h": _float_or_none(data.get("total24h")),
        "fees_7d": _float_or_none(data.get("total7d")),
        "revenue_24h": _float_or_none(data.get("revenue24h")),
        "revenue_7d": _float_or_none(data.get("revenue7d")),
    }


def fetch_protocol_tvl_history(
    protocol: str, client: httpx.Client | None = None
) -> list[dict] | None:
    """协议 TVL 历史序列（每日）：``[{date, tvl}]``；失败/空 → None。

    与 fetch_protocol_tvl 同一端点，供 ① 趋势特征确定性计算（series_change / series_trend 输入）。
    """
    if env.is_mock_mode():
        return mock.mock_protocol_tvl_history(protocol)
    try:
        data = _fetch_json(f"{BASE_URL}/protocol/{protocol}", client=client)
    except Exception:
        return None
    rows = data.get("tvl") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        tvl = _float_or_none(r.get("totalLiquidityUSD"))
        if tvl is None:
            continue
        out.append({"date": r.get("date"), "tvl": tvl})
    return out or None


def fetch_protocol_fees_history(
    protocol: str, client: httpx.Client | None = None
) -> list[dict] | None:
    """协议费用历史序列（每日）：``[{date, fees, revenue}]``；失败/空 → None。"""
    if env.is_mock_mode():
        return mock.mock_protocol_fees_history(protocol)
    try:
        data = _fetch_json(f"{BASE_URL}/protocols/{protocol}/fees", client=client)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    out = []
    for r in data:
        if not isinstance(r, dict):
            continue
        fees = _float_or_none(r.get("fees"))
        revenue = _float_or_none(r.get("revenue"))
        if fees is None and revenue is None:
            continue
        out.append({"date": r.get("date"), "fees": fees, "revenue": revenue})
    return out or None


def fetch_stablecoin_supply(
    chain: str, client: httpx.Client | None = None
) -> dict | None:
    """链稳定币总量：取历史序列最新一天各币种 totalCirculatingUSD 求和。

    缺失/非法项不计入（不按 0 猜测），存在缺失时标 ``incomplete``；
    全部缺失返回 ``None``。
    """
    if env.is_mock_mode():
        return mock.mock_stablecoin_supply(chain)
    try:
        data = _fetch_json(f"{STABLECOINS_URL}/stablecoincharts/{chain}", client=client)
    except Exception:
        return None
    if not isinstance(data, list) or not data:
        return None
    latest = data[-1]
    usd = latest.get("totalCirculatingUSD")
    if not isinstance(usd, dict) or not usd:
        return None
    values = [_float_or_none(v) for v in usd.values()]
    known = [v for v in values if v is not None]
    if not known:
        return None
    return {"stablecoin_supply": sum(known), "incomplete": len(known) < len(values)}


def fetch_stablecoin_history(
    chain: str, client: httpx.Client | None = None
) -> list[dict] | None:
    """链稳定币总量历史序列（每日）：``[{date, supply}]``；失败/空 → None。

    与 fetch_stablecoin_supply 同一端点（stablecoincharts 本身是历史序列）。
    """
    if env.is_mock_mode():
        return mock.mock_stablecoin_history(chain)
    try:
        data = _fetch_json(f"{STABLECOINS_URL}/stablecoincharts/{chain}", client=client)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    out = []
    for r in data:
        if not isinstance(r, dict):
            continue
        usd = r.get("totalCirculatingUSD")
        if not isinstance(usd, dict):
            continue
        known = [v for v in (_float_or_none(x) for x in usd.values()) if v is not None]
        if not known:
            continue
        out.append({"date": r.get("date"), "supply": sum(known)})
    return out or None


def fetch_dex_volume_24h(
    chain: str | None = None, client: httpx.Client | None = None
) -> dict | None:
    """链 DEX 交易量：``{dex_volume_24h}``；chain 为 None 时取全局。"""
    if env.is_mock_mode():
        return mock.mock_dex_volume_24h(chain)
    params = {
        "excludeTotalDataChart": "true",
        "excludeTotalDataChartBreakdown": "true",
    }
    if chain:
        params["chains"] = chain
    try:
        data = _fetch_json(f"{BASE_URL}/overview/dexs", client=client, params=params)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return {"dex_volume_24h": _float_or_none(data.get("total24h"))}


# ── 04 共享资源（批内一次 + 惰性兜底） ──────────────────────


_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI")


def _strip_quote(symbol: str) -> str:
    """交易所 symbol → 裸 base 符号（去计价后缀）；无后缀原样返回。"""
    for suffix in _QUOTE_SUFFIXES:
        if symbol.endswith(suffix):
            base = symbol[: -len(suffix)]
            return base or symbol
    return symbol


def fetch_chains(client: httpx.Client | None = None) -> dict[str, dict] | None:
    """全量链列表（/v2/chains，批内一次）：``{链名小写: {tvl, token_symbol}}``。"""
    if env.is_mock_mode():
        return mock.mock_chains()
    try:
        data = _fetch_json(f"{BASE_URL}/v2/chains", client=client)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    out: dict[str, dict] = {}
    for c in data:
        name = c.get("name")
        tvl = _float_or_none(c.get("tvl"))
        if not name or tvl is None:
            continue  # 缺失字段按 UNKNOWN 跳过
        out[name.lower()] = {"tvl": tvl, "token_symbol": c.get("tokenSymbol")}
    return out


#: fetch_protocols 响应体积约 8MB，仅静态 TOKEN_SLUG_MAP 未命中时惰性拉取
def fetch_protocols(client: httpx.Client | None = None) -> dict[str, str] | None:
    """全量协议索引（/protocols，约 8MB，惰性兜底）：``{symbol: slug}``。

    同 symbol 多协议取首见（/protocols 按 TVL 降序，首见即最大）。"""
    if env.is_mock_mode():
        return mock.mock_protocols()
    try:
        data = _fetch_json(f"{BASE_URL}/protocols", client=client)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    out: dict[str, str] = {}
    for p in data:
        symbol = p.get("symbol")
        slug = p.get("slug")
        if symbol and slug and symbol not in out:
            out[symbol] = slug
    return out


def fetch_stablecoins(client: httpx.Client | None = None) -> dict[str, float] | None:
    """全量稳定币聚合表（/stablecoins，批内一次）：
    ``{链名小写: 稳定币总供应量}``（chainCirculating.current.peggedUSD 求和）。"""
    if env.is_mock_mode():
        return mock.mock_stablecoins()
    try:
        data = _fetch_json(
            f"{STABLECOINS_URL}/stablecoins?includePrices=false", client=client
        )
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    total: dict[str, float] = {}
    for p in data.get("peggedAssets", []):
        cc = p.get("chainCirculating")
        if not isinstance(cc, dict):
            continue
        for chain, periods in cc.items():
            if not isinstance(periods, dict):
                continue
            cur = periods.get("current")
            if not isinstance(cur, dict):
                continue
            value = _float_or_none(cur.get("peggedUSD"))
            if value is None:
                continue  # 缺失项不计入（不按 0 猜测）
            key = chain.lower()
            total[key] = total.get(key, 0.0) + value
    return total


def fetch_dexs(client: httpx.Client | None = None) -> dict[str, float] | None:
    """全量 DEX 交易量聚合表（/overview/dexs，批内一次）：
    ``{链名小写: 24h 交易量}``（protocols[].breakdown24h 按链求和）。"""
    if env.is_mock_mode():
        return mock.mock_dexs()
    params = {"excludeTotalDataChart": "true", "excludeTotalDataChartBreakdown": "true"}
    try:
        data = _fetch_json(f"{BASE_URL}/overview/dexs", client=client, params=params)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    total: dict[str, float] = {}
    for p in data.get("protocols", []):
        bd = p.get("breakdown24h")
        if not isinstance(bd, dict):
            continue
        for chain, inner in bd.items():
            if not isinstance(inner, dict):
                continue
            for value in inner.values():
                vf = _float_or_none(value)
                if vf is not None:
                    key = chain.lower()
                    total[key] = total.get(key, 0.0) + vf
    return total


def fetch_fees(slugs: list[str], client: httpx.Client | None = None) -> dict[str, dict]:
    """协议费用聚合表（per-slug /summary/fees/{slug}，批内一次）：
    ``{slug: {fees_24h, fees_7d, revenue_24h, revenue_7d}}``；
    单 slug 失败跳过（不中断批）。"""
    if env.is_mock_mode():
        return mock.mock_fees(slugs)
    out: dict[str, dict] = {}
    for slug in slugs:
        row = fetch_protocol_fees(slug, client=client)
        if row is not None:
            out[slug] = row
    return out


def fetch_chain_tvl(chain: str, client: httpx.Client | None = None) -> dict | None:
    """链 TVL（/charts/{chain}）：与 fetch_protocol_tvl 输出同构
    ``{tvl, tvl_change_1d, tvl_change_7d, tvl_change_30d}``。"""
    if env.is_mock_mode():
        return mock.mock_chain_tvl(chain)
    try:
        data = _fetch_json(f"{BASE_URL}/charts/{chain}", client=client)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    try:
        tvl = float(data[-1]["totalLiquidityUSD"])
    except (IndexError, KeyError, TypeError, ValueError):
        tvl = None
    return {
        "tvl": tvl,
        "tvl_change_1d": _change_from_history(data, 1),
        "tvl_change_7d": _change_from_history(data, 7),
        "tvl_change_30d": _change_from_history(data, 30),
    }
