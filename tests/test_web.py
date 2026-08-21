"""web 数据源测试（Bing News RSS + Web RSS，零 key，xml 解析）。"""

from __future__ import annotations

import httpx
import pytest

from strategy_research.datasources import web

# 与 Bing News RSS 真实响应同构的样例
RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:news="http://www.bing.com/news">
  <channel>
    <title>BTC 检索结果</title>
    <item>
      <title>比特币突破新高</title>
      <pubDate>Wed, 20 Aug 2026 08:00:00 GMT</pubDate>
      <source url="https://coindesk.com">CoinDesk</source>
      <link>https://example.com/1</link>
    </item>
    <item>
      <title>以太坊升级进展</title>
      <link>https://example.com/2</link>
    </item>
  </channel>
</rss>
"""


def _client(body: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _client_raise(exc: Exception) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_rss() -> None:
    """纯函数解析：title/date/source/link，date 转 ISO 8601。"""
    items = web._parse_rss(RSS_XML)
    assert len(items) == 2
    first = items[0]
    assert first["title"] == "比特币突破新高"
    assert first["source"] == "CoinDesk"
    assert first["link"] == "https://example.com/1"
    assert first["date"].startswith("2026-08-20T08:00:00")
    assert first["date"].endswith("+00:00")


def test_parse_rss_missing_fields_none() -> None:
    """缺失字段 → None（UNKNOWN 纪律，不填充默认值）。"""
    items = web._parse_rss(RSS_XML)
    second = items[1]
    assert second["title"] == "以太坊升级进展"
    assert second["source"] is None
    assert second["date"] is None


def test_parse_rss_empty_channel() -> None:
    """无 item 的 RSS → 空列表。"""
    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>"""
    assert web._parse_rss(xml) == []


def test_fetch_news_rss_mock() -> None:
    """mock 模式：与真实路径字段同构。"""
    items = web.fetch_news_rss("BTC")
    assert isinstance(items, list) and len(items) > 0
    assert set(items[0]) == {"title", "date", "source", "link"}


def test_fetch_news_rss_real_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实路径：Bing News RSS 解析 + URL 带 q 与 format=rss。"""
    monkeypatch.setenv("SR_MOCK", "0")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, text=RSS_XML)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    items = web.fetch_news_rss("BTC", limit=1, client=client)
    assert len(items) == 1
    assert items[0]["title"] == "比特币突破新高"
    assert "news/search" in captured["url"]
    assert "q=BTC" in captured["url"]
    assert "format=rss" in captured["url"]


def test_fetch_web_rss_uses_search_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Web RSS 走 /search 端点（与 News RSS 同族）。"""
    monkeypatch.setenv("SR_MOCK", "0")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, text=RSS_XML)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    web.fetch_web_rss("BTC", client=client)
    assert "/search?" in captured["url"]


def test_fetch_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """失败即失败：网络异常 → None（装配层标 UNKNOWN）。"""
    monkeypatch.setenv("SR_MOCK", "0")
    client = _client_raise(httpx.ConnectError("refused"))
    assert web.fetch_news_rss("BTC", client=client) is None
    assert web.fetch_web_rss("BTC", client=client) is None


def test_mock_zero_external_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """SR_MOCK=1 时零外部请求：httpx.Client 被调用即爆炸也不影响。"""
    monkeypatch.setattr(
        "strategy_research.datasources.web._get_client",
        lambda: pytest.fail("mock 模式不应发起 HTTP"),
    )
    assert web.fetch_news_rss("BTC") is not None
    assert web.fetch_web_rss("BTC") is not None
