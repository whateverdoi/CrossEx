"""web 数据源（Bing News RSS + Web RSS，零 key）。

- ``fetch_news_rss``：https://www.bing.com/news/search?q={q}&format=rss
- ``fetch_web_rss``：https://www.bing.com/search?q={q}&format=rss（同族）

条目字段：``{title, date, source, link}``；date 统一转 ISO 8601（RFC822
原文不可解析时置 None）。失败返回 ``None``（装配层标 UNKNOWN，失败即失败
不回退 mock）。测试可注入 ``httpx.Client``（MockTransport）零外部请求。
"""

from __future__ import annotations

import atexit
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import httpx

from .. import env
from . import mock

NEWS_URL = "https://www.bing.com/news/search"
WEB_URL = "https://www.bing.com/search"
TIMEOUT_SECONDS = 15.0

_client: httpx.Client | None = None


class _NoDoctypeBuilder(ET.TreeBuilder):
    """拒绝 DTD 的 TreeBuilder：堵外部实体/实体膨胀（XXE）攻击面。

    RSS 是外部不可信输入，但不需要 DTD；声明 DTD 即拒绝解析。
    """

    def doctype(self, name, pubid, system):
        raise ET.ParseError("DOCTYPE not allowed (XXE guard)")


def _get_client() -> httpx.Client:
    """惰性单例 httpx 客户端（sync，进程退出时关闭连接池）。"""
    global _client
    if _client is None:
        _client = httpx.Client(timeout=TIMEOUT_SECONDS)
        atexit.register(_client.close)
    return _client


def _parse_rss(xml_text: str) -> list[dict]:
    """解析 RSS XML → ``[{title, date, source, link}]``。

    缺失字段置 None（UNKNOWN 纪律）；无标题条目丢弃；整段解析失败返回空列表。
    """
    try:
        root = ET.fromstring(
            xml_text,
            parser=ET.XMLParser(target=_NoDoctypeBuilder()),
        )
    except ET.ParseError:
        return []

    def text(item: ET.Element, tag: str) -> str | None:
        el = item.find(tag)
        if el is None or not el.text:
            return None
        return el.text.strip()

    items: list[dict] = []
    for item in root.iter("item"):
        title = text(item, "title")
        if not title:
            continue

        date = text(item, "pubDate")
        if date:
            try:
                date = parsedate_to_datetime(date).isoformat()
            except (TypeError, ValueError):
                date = None

        source_el = item.find("source")
        source = None
        if source_el is not None and source_el.text:
            source = source_el.text.strip()

        items.append(
            {
                "title": title,
                "date": date,
                "source": source,
                "link": text(item, "link"),
            }
        )
    return items


def _fetch_rss(
    url: str, query: str, limit: int, client: httpx.Client | None
) -> list[dict] | None:
    """公共路径：GET RSS → 解析 → 截取 limit 条；失败返回 None。"""
    try:
        c = client or _get_client()
        resp = c.get(url, params={"q": query, "format": "rss"})
        resp.raise_for_status()
    except Exception:
        return None
    return _parse_rss(resp.text)[:limit]


def fetch_news_rss(
    query: str, limit: int = 10, client: httpx.Client | None = None
) -> list[dict] | None:
    """Bing News RSS 检索（六维 news 维度素材）。"""
    if env.is_mock_mode():
        return mock.mock_news_rss(query)[:limit]
    return _fetch_rss(NEWS_URL, query, limit, client)


def fetch_web_rss(
    query: str, limit: int = 10, client: httpx.Client | None = None
) -> list[dict] | None:
    """Bing Web RSS 检索（六维查询模板素材）。"""
    if env.is_mock_mode():
        return mock.mock_web_rss(query)[:limit]
    return _fetch_rss(WEB_URL, query, limit, client)
