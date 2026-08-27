"""x_social 数据源测试：数值解析 / handle 解析 / build_stats / 信号纯函数 / mock 同构。

- ``parse_compact_number`` / ``_rel_ago`` / ``post_frequency_days`` 纯函数
- ``resolve_handle``：MockTransport 路由（CoinGecko 命中 / 429 重试 / CoinPaprika 兜底 / 缓存落盘）
- ``build_stats``：置顶排除、时间升序（最新在末尾）、不含推文文本
- ``social_heat_trend`` / ``social_price_divergence``：四象限 + UNKNOWN 纪律
- mock 模式零外部请求；collect_data 装配 social_data 五快照之一
"""

from __future__ import annotations

import json

import httpx
import pytest

from strategy_research import nodes
from strategy_research import signals as sig
from strategy_research.datasources import mock as m
from strategy_research.datasources import x_social


# ── parse_compact_number ───────────────────────────────


def test_parse_compact_number() -> None:
    """页面紧凑数字文本 → float；中英文单位；千分位逗号剥离；其余 → None。"""
    assert x_social.parse_compact_number("16.3万") == 163000.0
    assert x_social.parse_compact_number("163K") == 163000.0
    assert x_social.parse_compact_number("163k") == 163000.0
    assert x_social.parse_compact_number("1.2M") == 1200000.0
    assert x_social.parse_compact_number("6,144") == 6144.0
    assert x_social.parse_compact_number("120") == 120.0
    assert x_social.parse_compact_number(120) is None  # 非字符串
    assert x_social.parse_compact_number("") is None
    assert x_social.parse_compact_number("abc") is None
    assert x_social.parse_compact_number("1.2万 K") is None  # 双单位非法
    assert x_social.parse_compact_number(" 42 ") == 42.0


def test_rel_ago() -> None:
    """相对时间 → 秒（越小越新）；月日格式推算；无法解析 → inf。"""
    assert x_social._rel_ago("now") == 0.0
    assert x_social._rel_ago("2h") == 7200.0
    assert x_social._rel_ago("1d") == 86400.0
    assert x_social._rel_ago("30d") == 30 * 86400.0
    assert x_social._rel_ago("Aug 16") >= 0.0
    assert x_social._rel_ago("8月2日") >= 0.0
    assert x_social._rel_ago(None) == float("inf")
    assert x_social._rel_ago("???") == float("inf")


# ── build_stats / post_frequency_days ──────────────────


def _extracted(posts: list[dict], followers: str = "16.3万") -> str:
    """与 EXTRACT_JS 输出同构的 JSON 文本。"""
    return json.dumps(
        {
            "profile": {"name": "BTC", "handle": "@btc", "followers": followers},
            "posts": posts,
        }
    )


def test_build_stats_shape_and_order() -> None:
    """结构：仅互动字段（无 url/推文文本）；置顶排除；时间升序最新在末尾。"""
    raw = _extracted(
        [
            {
                "id": "1",
                "rel": "2h",
                "pinned": False,
                "eng": {"likes": "120", "reposts": "30", "replies": "18", "views": "5200"},
            },
            {
                "id": "2",
                "rel": "30d",
                "pinned": False,
                "eng": {"likes": "40", "reposts": "8", "replies": "5", "views": "2100"},
            },
            {"id": "3", "rel": "1d", "pinned": True, "eng": {"likes": "999", "reposts": "99", "replies": "9", "views": "9999"}},  # 置顶帖排除
        ]
    )
    stats = x_social.build_stats(raw)
    assert stats is not None
    assert stats["follower_count"] == "16.3万"  # 原样文本保留
    assert stats["handle"] == "@btc"
    assert len(stats["posts"]) == 2
    first, second = stats["posts"]
    assert first["time"] == "30d"  # 旧在前
    assert second["time"] == "2h"  # 最新在末尾
    assert set(first) == {"likes", "reposts", "comments", "views", "time"}
    assert first["likes"] == "40"
    assert first["comments"] == "5"


def test_build_stats_failures() -> None:
    """非法 JSON / 无推文 / 全置顶 → None（UNKNOWN 纪律）。"""
    assert x_social.build_stats("not json") is None
    assert x_social.build_stats(json.dumps({"profile": {}, "posts": []})) is None
    assert x_social.build_stats(_extracted([])) is None


def test_post_frequency_days() -> None:
    """平均发帖间隔：时间跨度 / (n-1)；可解析 <2 条 → None。"""
    posts = [{"time": "30d"}, {"time": "20d"}, {"time": "10d"}, {"time": "1d"}]
    assert x_social.post_frequency_days(posts) == pytest.approx(29.0 / 3, rel=0.01)
    assert x_social.post_frequency_days([{"time": "1d"}]) is None
    assert x_social.post_frequency_days([]) is None
    assert x_social.post_frequency_days(None) is None
    # 全部无法解析（如 "???"）→ None
    assert x_social.post_frequency_days([{"time": None}, {"time": "???"}]) is None


# ── resolve_handle（CoinGecko → CoinPaprika） ───────────


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(x_social.time, "sleep", lambda s: None)


def _isolate_cache(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(x_social, "CACHE_FILE", tmp_path / "coin_handles.json")


def test_resolve_handle_coingecko_hit_and_cache(monkeypatch, tmp_path) -> None:
    """CoinGecko 命中：search → coins/{id} → twitter_screen_name；二次调用零网络（缓存）。"""
    _isolate_cache(monkeypatch, tmp_path)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        url = str(request.url)
        if "/api/v3/search" in url:
            return httpx.Response(
                200, json={"coins": [{"id": "bitcoin", "symbol": "BTC", "market_cap_rank": 1}]}
            )
        if "/api/v3/coins/" in url:
            return httpx.Response(200, json={"links": {"twitter_screen_name": "bitcoin"}})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert x_social.resolve_handle("BTC", client) == "bitcoin"
    assert x_social.resolve_handle("BTC", client) == "bitcoin"  # 缓存命中
    assert len(calls) == 2  # search + detail，无第三次
    assert (tmp_path / "coin_handles.json").exists()  # 缓存落盘


def test_resolve_handle_429_retry(monkeypatch, tmp_path) -> None:
    """CoinGecko 429 限流 → 间隔递增重试至成功（sleep 替换为 no-op）。"""
    _isolate_cache(monkeypatch, tmp_path)
    _no_sleep(monkeypatch)
    state = {"detail": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/api/v3/search" in url:
            return httpx.Response(200, json={"coins": [{"id": "bitcoin", "symbol": "BTC"}]})
        if "/api/v3/coins/" in url:
            state["detail"] += 1
            if state["detail"] < 3:
                return httpx.Response(429)
            return httpx.Response(200, json={"links": {"twitter_screen_name": "bitcoin"}})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert x_social.resolve_handle("BTC", client) == "bitcoin"
    assert state["detail"] == 3


def test_resolve_handle_fallback_paprika(monkeypatch, tmp_path) -> None:
    """CoinGecko 无 twitter_username → 回退 CoinPaprika。"""
    _isolate_cache(monkeypatch, tmp_path)
    _no_sleep(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/api/v3/search" in url:
            return httpx.Response(200, json={"coins": [{"id": "btc", "symbol": "BTC"}]})
        if "/api/v3/coins/" in url:
            return httpx.Response(200, json={"links": {}})  # 无 handle
        if "/v1/search/" in url:
            return httpx.Response(200, json=[{"id": "btc-bitcoin", "symbol": "BTC"}])
        if "/v1/coins/" in url:
            return httpx.Response(200, json={"twitter_username": "btc_org"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert x_social.resolve_handle("BTC", client) == "btc_org"


def test_resolve_handle_failure(monkeypatch, tmp_path) -> None:
    """两源均无匹配 / 网络失败 → None（失败即失败）。"""
    _isolate_cache(monkeypatch, tmp_path)
    _no_sleep(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/search/" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert x_social.resolve_handle("NOPE", client) is None


# ── mock 同构 ──────────────────────────────────────────


def test_mock_x_stats_shape() -> None:
    """mock 与 fetch_x_stats 返回同构：30 条序列、原样文本、最新在末尾。"""
    stats = m.mock_x_stats("BTC")
    assert stats["account_name"] == "BTC"
    assert stats["follower_count"] == "16.3万"  # 页面原样文本
    posts = stats["posts"]
    assert len(posts) == 30
    assert set(posts[0]) == {"likes", "reposts", "comments", "views", "time"}
    assert posts[0]["time"] == "30d"  # 旧在前
    assert posts[-1]["time"] == "1d"  # 最新在末尾


def test_fetch_x_stats_mock_zero_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """mock 模式：返回 mock 同构数据，零外部请求（_get_client 被调即失败）。"""
    stats = x_social.fetch_x_stats("BTC")
    assert stats is not None
    assert len(stats["posts"]) == 30
    assert stats["follower_count"] == "16.3万"

    monkeypatch.setattr(
        "strategy_research.datasources.x_social._get_client",
        lambda: pytest.fail("mock 模式不应发起 HTTP"),
    )
    assert x_social.fetch_x_stats("BTC") is not None


# ── 信号纯函数 ─────────────────────────────────────────


def _posts(recent: int = 5) -> list[dict]:
    """30 条互动序列：最近 recent 条高互动，其余低互动（时间升序最新在末尾）。"""
    out = []
    for i in range(30):
        row = {"likes": "120", "reposts": "30", "comments": "18"} if i >= 30 - recent else {
            "likes": "40", "reposts": "8", "comments": "5"
        }
        out.append({**row, "views": "5200", "time": f"{30 - i}d"})
    return out


def test_social_heat_trend() -> None:
    """最近 5 条均值 vs 其余中位数 → 正（热度上升）；样本/数值不足 → None。"""
    trend = sig.social_heat_trend(_posts())
    assert trend is not None and trend > 0
    assert sig.social_heat_trend(_posts()[:6]) is None  # 样本 <7
    assert sig.social_heat_trend(None) is None
    assert sig.social_heat_trend([]) is None
    zero = [{"likes": "0", "reposts": "0", "comments": "0", "time": f"{30 - i}d"} for i in range(30)]
    assert sig.social_heat_trend(zero) is None  # 互动全 0
    # 7 条小样本（未登录 x.com 实际渲染量）也可算：最近 5 高互动 vs 其余 2 低互动
    small = [
        {"likes": "40", "reposts": "8", "comments": "5", "time": "7d"},
        {"likes": "40", "reposts": "8", "comments": "5", "time": "6d"},
    ]
    small += [
        {"likes": "120", "reposts": "30", "comments": "18", "time": f"{5 - i}d"}
        for i in range(5)
    ]
    assert sig.social_heat_trend(small) is not None
    assert sig.social_heat_trend(small) > 0


def test_social_price_divergence_quadrants() -> None:
    """四象限与 oi_price_divergence 同构；输入缺失 → None；零值 → none。"""
    assert sig.social_price_divergence(5.0, 10.0)["label"] == "confirm_long"
    assert sig.social_price_divergence(5.0, -10.0)["label"] == "weak_long"
    assert sig.social_price_divergence(-5.0, 10.0)["label"] == "weak_short"
    assert sig.social_price_divergence(-5.0, -10.0)["label"] == "confirm_short"
    assert sig.social_price_divergence(0.0, 10.0)["label"] == "none"
    assert sig.social_price_divergence(5.0, 0.0)["label"] == "none"
    assert sig.social_price_divergence(None, 10.0) is None
    assert sig.social_price_divergence(5.0, None) is None


def test_mock_signals_data_social_components() -> None:
    """mock 信号层含社交组件：同一纯函数从 mock 序列推导（BTC 价涨 + 热度升 → confirm_long）。"""
    data = m.mock_signals_data("BTC")
    comp = data["sentiment"]["components"]
    assert comp["social_heat_trend"] is not None and comp["social_heat_trend"] > 0
    assert comp["social_price_divergence"]["label"] == "confirm_long"


# ── collect_data e2e（mock 模式） ──────────────────────


def test_collect_data_social_snapshot() -> None:
    """mock 模式 e2e：social_data 为五快照之一，四元组装配 + 派生信号一致。"""
    state = nodes.collect_data({"tokens": ["BTC"]})
    assert "social_data" in state
    soc = state["social_data"]["BTC"]
    assert soc["incomplete"] is False
    assert soc["follower_count"]["value"] == "16.3万"  # 原样文本
    assert soc["follower_count"]["source"] == "x_social"
    assert len(soc["posts"]) == 30
    assert soc["posts"][-1]["time"] == "1d"
    assert soc["post_frequency"]["value"] == pytest.approx(1.0, abs=0.01)  # 跨度 29d / 29 间隔
    assert soc["social_heat_trend"]["value"] > 0
    assert soc["social_price_divergence"]["value"]["label"] == "confirm_long"
    # 其余四快照照常装配
    assert set(state) >= {
        "market_data",
        "fundamental_data",
        "microstructure_data",
        "web_data",
        "social_data",
        "scanner_snapshot",
    }
