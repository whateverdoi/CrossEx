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

import httpx
from typing import Any

from .. import env
from . import mock

BASE_URL = "https://api.llama.fi"
STABLECOINS_URL = "https://stablecoins.llama.fi"
TIMEOUT_SECONDS = 15.0

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


def _fetch_json(url: str, client: httpx.Client | None = None,
                params: dict | None = None) -> Any:
    """GET + JSON 解析；异常原样抛出由调用方转 None。"""
    c = client or _get_client()
    resp = c.get(url, params=params)
    resp.raise_for_status()
    return resp.json()


def _chain_tvl_sum(data: dict) -> float | None:
    """当前 TVL：currentChainTvls 各链求和（协议详情无单一当前值字段）。"""
    chains = data.get("currentChainTvls")
    if not isinstance(chains, dict):
        return None
    values = [v for v in (_float_or_none(x) for x in chains.values())
              if v is not None]
    return sum(values) if values else None


def _change_7d_from_history(data: dict) -> float | None:
    """7d 变化：从 tvl 历史数组取 7 天前最后一条对比（百分比）。"""
    history = data.get("tvl")
    if not isinstance(history, list) or len(history) < 2:
        return None
    try:
        latest = float(history[-1]["totalLiquidityUSD"])
        cutoff = history[-1]["date"] - 604_800  # 7 天（秒）
    except (KeyError, TypeError, ValueError):
        return None
    prev: dict | None = None
    for point in history:
        if point.get("date", 0) <= cutoff:
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


def fetch_protocol_tvl(protocol: str,
                       client: httpx.Client | None = None) -> dict | None:
    """协议 TVL：``{tvl, change_7d, mcap, fdv}``（缺失字段 None）。"""
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
        "change_7d": _change_7d_from_history(data),
        "mcap": _float_or_none(data.get("mcap")),
        "fdv": _float_or_none(data.get("fdv")),
    }


def fetch_protocol_fees(protocol: str,
                        client: httpx.Client | None = None) -> dict | None:
    """协议费用/收入：``{fees_24h, fees_7d, revenue_24h, revenue_7d}``。"""
    if env.is_mock_mode():
        return mock.mock_protocol_fees(protocol)
    try:
        data = _fetch_json(f"{BASE_URL}/summary/fees/{protocol}",
                           client=client)
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


def fetch_stablecoin_supply(chain: str,
                            client: httpx.Client | None = None) -> dict | None:
    """链稳定币总量：取历史序列最新一天各币种 totalCirculatingUSD 求和。

    缺失/非法项不计入（不按 0 猜测），存在缺失时标 ``incomplete``；
    全部缺失返回 ``None``。
    """
    if env.is_mock_mode():
        return mock.mock_stablecoin_supply(chain)
    try:
        data = _fetch_json(f"{STABLECOINS_URL}/stablecoincharts/{chain}",
                           client=client)
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
    return {"stablecoin_supply": sum(known),
            "incomplete": len(known) < len(values)}


def fetch_dex_volume_24h(chain: str | None = None,
                         client: httpx.Client | None = None) -> dict | None:
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
        data = _fetch_json(f"{BASE_URL}/overview/dexs", client=client,
                           params=params)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return {"dex_volume_24h": _float_or_none(data.get("total24h"))}
