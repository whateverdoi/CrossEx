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
    """纯函数解析：title/date/source/link，date 转 ISO 8601；缺失字段 → None；空 channel → []。"""
    items = web._parse_rss(RSS_XML)
    assert len(items) == 2
    first = items[0]
    assert first["title"] == "比特币突破新高"
    assert first["source"] == "CoinDesk"
    assert first["link"] == "https://example.com/1"
    assert first["date"].startswith("2026-08-20T08:00:00")
    assert first["date"].endswith("+00:00")

    second = items[1]
    assert second["title"] == "以太坊升级进展"
    assert second["source"] is None  # 缺失字段 → None（UNKNOWN 纪律）
    assert second["date"] is None

    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>"""
    assert web._parse_rss(xml) == []  # 无 item → 空列表


def test_fetch_mock_shape_and_zero_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """mock 模式：news 字段同构；零外部请求（_get_client 被调即失败）。"""
    items = web.fetch_news_rss("BTC")
    assert isinstance(items, list) and len(items) > 0
    assert set(items[0]) == {"title", "date", "source", "link"}

    monkeypatch.setattr(
        "strategy_research.datasources.web._get_client",
        lambda: pytest.fail("mock 模式不应发起 HTTP"),
    )
    assert web.fetch_news_rss("BTC") is not None
    assert web.fetch_web_rss("BTC") is not None


def test_fetch_real_parses_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实路径：News RSS 解析（URL 带 q 与 format=rss）+ Web RSS /search 端点；失败 → None。"""
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

    web.fetch_web_rss("BTC", client=client)
    assert "/search?" in captured["url"]

    fail_client = _client_raise(httpx.ConnectError("refused"))
    assert web.fetch_news_rss("BTC", client=fail_client) is None
    assert web.fetch_web_rss("BTC", client=fail_client) is None


def test_search_web_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    """search_web：mock 与真实同构 {title,url,snippet,source:bing}（snippet 取 description）；
    失败 → None（工具层转 UNKNOWN 文本，不重试）。"""
    items = web.search_web("BTC")
    assert items is not None and len(items) > 0
    assert set(items[0]) == {"title", "url", "snippet", "source"}
    assert items[0]["source"] == "bing"
    assert isinstance(items[0]["snippet"], str)

    monkeypatch.setenv("SR_MOCK", "0")
    xml = RSS_XML.replace(
        "<link>https://example.com/1</link>",
        "<description>BTC 摘要内容</description><link>https://example.com/1</link>",
    )
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=xml))
    )
    items = web.search_web("BTC", client=client)
    assert items is not None and len(items) == 2
    assert set(items[0]) == {"title", "url", "snippet", "source"}
    assert items[0]["source"] == "bing"
    assert items[0]["url"] == "https://example.com/1"
    assert items[0]["snippet"] == "BTC 摘要内容"
    assert items[1]["snippet"] is None  # RSS_XML 无 description

    assert web.search_web("BTC", client=_client_raise(httpx.ConnectError("refused"))) is None



