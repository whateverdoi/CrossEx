"""okx 爆仓数据源测试（httpx sync + mock 同构 + 失败即失败）。"""

from __future__ import annotations

import time

import httpx
import pytest

from strategy_research import nodes
from strategy_research.datasources import okx
from strategy_research.datasources.okx import BUCKET_MS, WINDOW_BUCKETS


def _client(body, status: int = 200) -> httpx.Client:
    """用 MockTransport 构造测试客户端（零外部请求）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _client_raise(exc: Exception) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.Client(transport=httpx.MockTransport(handler))


def _liq_body(orders: list[dict]) -> dict:
    return {"code": "0", "msg": "", "data": [{"details": orders}]}


def _order(ts: int, sz: float, bk_px: float, side: str) -> dict:
    return {"bkPx": str(bk_px), "sz": str(sz), "side": side, "ts": str(ts)}


def test_fetch_mock_shape() -> None:
    """mock 模式：序列与真实解析后字段同构（时间升序，最新在末尾）。"""
    rows = okx.fetch_liquidation_24h("BTC")
    assert rows is not None and len(rows) >= 6
    assert set(rows[0]) == {"time", "long_liq_usd", "short_liq_usd"}
    times = [r["time"] for r in rows]
    assert times == sorted(times)


def test_fetch_aggregates(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实形状：逐笔订单按 4h 桶聚合多空爆仓额；窗口外丢弃；坏行跳过。"""
    monkeypatch.setenv("SR_MOCK", "0")
    now_ms = int(time.time() * 1000)
    cutoff = (now_ms // BUCKET_MS) * BUCKET_MS - (WINDOW_BUCKETS - 1) * BUCKET_MS
    mid = BUCKET_MS // 2  # 桶中点，避开时间边界
    orders = [
        _order(cutoff + mid, 1.0, 100.0, "sell"),  # 桶0：多头强平 100
        _order(cutoff + mid, 0.5, 200.0, "buy"),  # 桶0：空头强平 100
        _order(cutoff + 1 * BUCKET_MS + mid, 2.0, 50.0, "sell"),  # 桶1：多头 100
        _order(cutoff - BUCKET_MS, 9.0, 9.0, "sell"),  # 窗口外（旧）丢弃
        _order(cutoff + WINDOW_BUCKETS * BUCKET_MS, 8.0, 8.0, "buy"),  # 窗口外（新）丢弃
        {"bkPx": "x", "sz": "1", "side": "sell", "ts": str(cutoff + mid)},  # 坏行跳过
    ]
    rows = okx.fetch_liquidation_24h("BTC", client=_client(_liq_body(orders)))
    assert rows is not None and len(rows) == WINDOW_BUCKETS
    assert [r["time"] for r in rows] == [
        cutoff + i * BUCKET_MS for i in range(WINDOW_BUCKETS)
    ]
    assert rows[0]["long_liq_usd"] == pytest.approx(100.0)
    assert rows[0]["short_liq_usd"] == pytest.approx(100.0)
    assert rows[1]["long_liq_usd"] == pytest.approx(100.0)
    assert rows[2]["long_liq_usd"] == 0.0  # 空桶补 0
    assert sum(r["long_liq_usd"] for r in rows) == pytest.approx(200.0)
    assert sum(r["short_liq_usd"] for r in rows) == pytest.approx(100.0)


def test_fetch_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """失败即失败：code!=0 / 非 dict / 空 data / 无订单 / HTTP 异常 / 全 0 → None。"""
    monkeypatch.setenv("SR_MOCK", "0")
    assert okx.fetch_liquidation_24h("BTC", client=_client({"code": "1"})) is None
    assert okx.fetch_liquidation_24h("BTC", client=_client([1, 2])) is None
    assert okx.fetch_liquidation_24h("BTC", client=_client(_liq_body([]))) is None
    assert (
        okx.fetch_liquidation_24h(
            "BTC", client=_client_raise(httpx.ConnectError("x"))
        )
        is None
    )
    # 全窗口无有效订单（只有坏行）→ 全 0 桶 → None
    bad_only = _liq_body([{"bkPx": "x", "sz": "1", "side": "sell", "ts": "1"}])
    assert okx.fetch_liquidation_24h("BTC", client=_client(bad_only)) is None


def test_liquidation_assembly_mock() -> None:
    """mock 全链装配：4h 序列聚合 24h；mock 常量 → long_heavy + OI 比例。"""
    mkt = {"oi": {"value": 1000.0}, "price": {"value": 2.0}}
    out = nodes._liquidation("BTC", mkt)
    assert out["liq_long_24h"]["value"] == pytest.approx(6 * 800_000.0)
    assert out["liq_short_24h"]["value"] == pytest.approx(6 * 500_000.0)
    assert out["liq_total_24h"]["value"] == pytest.approx(6 * 1_300_000.0)
    assert out["liq_total_oi_ratio"]["value"] == pytest.approx(
        6 * 1_300_000.0 / (1000.0 * 2.0)
    )
    assert out["liq_imbalance"]["value"]["label"] == "long_heavy"
    # oi/price 缺失 → OI 比例 None（其余照常，UNKNOWN 纪律）
    out2 = nodes._liquidation("BTC", {})
    assert out2["liq_total_oi_ratio"]["value"] is None
    assert out2["liq_imbalance"]["value"]["label"] == "long_heavy"
