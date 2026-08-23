"""OKX 公开爆仓数据源（免费，无需认证）。

- ``fetch_liquidation_24h``：GET /api/v5/public/liquidation-orders
  （instType=SWAP + instFamily={symbol}-USDT，公开端点无需 API key），
  单次返回最近 24h+ 逐笔强平订单，按 4h UTC 桶聚合成
  ``[{time, long_liq_usd, short_liq_usd}]``（固定 6 桶 = 24h 窗口，
  时间升序，空桶补 0，与 mock 同构）。
- 单笔爆仓金额 ≈ sz × bkPx（破产价）；side=sell = 多头被强平（卖出平仓），
  side=buy = 空头被强平（买入回补）。
- 口径：OKX 单所 USDT 永续（零成本替代付费的 Coinglass 聚合源；
  仅最近 7 天窗口，装配层只消费最近 24h）。

所有 fetch 失败返回 ``None``（装配层标 UNKNOWN，失败即失败不回退 mock）；
测试可注入 ``httpx.Client``（MockTransport）零外部请求。
"""

from __future__ import annotations

import atexit
import time

import httpx

from .. import env
from . import mock

BASE_URL = "https://www.okx.com"
TIMEOUT_SECONDS = 15.0
#: 4h 桶粒度（与解读口径一致）；固定 6 桶 = 24h 窗口
BUCKET_MS = 4 * 3600 * 1000
WINDOW_BUCKETS = 6

_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    """惰性单例 httpx 客户端（sync，进程退出时关闭连接池）。"""
    global _client
    if _client is None:
        _client = httpx.Client(timeout=TIMEOUT_SECONDS)
        atexit.register(_client.close)
    return _client


def _bucketize(orders: list[dict], cutoff_ms: int) -> list[dict] | None:
    """逐笔强平订单 → 6 个 4h 桶（升序，空桶补 0，全 0 → None）。

    cutoff_ms 为窗口起点（4h UTC 对齐）；订单按 ts 落桶，窗口外丢弃；
    坏行（缺 ts/sz/bkPx）跳过。
    """
    buckets: list[list[float]] = [[0.0, 0.0] for _ in range(WINDOW_BUCKETS)]
    for o in orders:
        try:
            ts = int(o["ts"])
            usd = float(o["sz"]) * float(o["bkPx"])
        except (KeyError, TypeError, ValueError):
            continue
        idx = (ts - cutoff_ms) // BUCKET_MS
        if not 0 <= idx < WINDOW_BUCKETS:
            continue
        if o.get("side") == "sell":
            buckets[idx][0] += usd  # 多头被强平（卖出平仓）
        elif o.get("side") == "buy":
            buckets[idx][1] += usd  # 空头被强平（买入回补）
    if all(l == 0.0 and s == 0.0 for l, s in buckets):
        return None
    return [
        {
            "time": cutoff_ms + i * BUCKET_MS,
            "long_liq_usd": buckets[i][0],
            "short_liq_usd": buckets[i][1],
        }
        for i in range(WINDOW_BUCKETS)
    ]


def fetch_liquidation_24h(
    symbol: str, client: httpx.Client | None = None
) -> list[dict] | None:
    """币种最近 24h 多空爆仓序列（OKX 公开端点，免费无需 key）。

    返回 ``[{time, long_liq_usd, short_liq_usd}]`` 时间升序（最新在末尾，
    与 mock 同构）；失败 / code!=0 / 非 dict / 无订单 / 全 0 → ``None``。
    """
    if env.is_mock_mode():
        return mock.mock_liquidation(symbol)
    http = client or _get_client()
    try:
        resp = http.get(
            f"{BASE_URL}/api/v5/public/liquidation-orders",
            params={
                "instType": "SWAP",
                "instFamily": f"{symbol}-USDT",
                "state": "filled",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict) or str(data.get("code")) != "0":
        return None
    rows = data.get("data")
    if not isinstance(rows, list) or not rows:
        return None
    details: list[dict] = []
    for r in rows:
        if isinstance(r, dict) and isinstance(r.get("details"), list):
            details.extend(x for x in r["details"] if isinstance(x, dict))
    if not details:
        return None
    now_ms = int(time.time() * 1000)
    cutoff_ms = (now_ms // BUCKET_MS) * BUCKET_MS - (WINDOW_BUCKETS - 1) * BUCKET_MS
    return _bucketize(details, cutoff_ms)
