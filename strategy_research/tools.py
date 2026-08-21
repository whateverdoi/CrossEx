"""react agent 工具注册表（06 票）：FACTS_TOOLS 5 / CHALLENGE_TOOLS 4。

工具层纪律（规格 ③/⑤）：**永不抛异常**——失败返回错误文本（agent 按
UNKNOWN 处理，不中断）；无结果返回"无结果"；历史序列降采样 ≤10 点；
mock 模式零外部请求（数据源层内部走 mock）。CHALLENGE_TOOLS 不含
search_web（对抗者不给联网搜索）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from langchain_core.tools import tool

from strategy_research import env
from strategy_research.datasources import binance_futures, defillama
from strategy_research.datasources import web as web_ds

_MAX_SERIES_POINTS = 10  # 历史序列降采样上限（规格：≤10 点）

#: mock 数据标识（prompt 约定：带该标识的数据可信度降级）
_MOCK_FLAG = "（mock 数据）"


def _slug_of(symbol: str) -> str:
    """symbol → defillama slug：静态映射优先，未命中小写兜底。"""
    base = defillama._strip_quote(symbol)
    return defillama.TOKEN_SLUG_MAP.get(base, "") or base.lower()


def _chain_of(symbol: str) -> str | None:
    """symbol → 链名（TOKEN_SLUG_MAP 的 ``chain:`` 值）；非链类 → None。"""
    base = defillama._strip_quote(symbol)
    slug = defillama.TOKEN_SLUG_MAP.get(base, "")
    if slug.startswith("chain:"):
        return slug[6:]
    return None


def _fapi_symbol(symbol: str) -> str:
    """交易对符号归一：无 USDT 后缀补全（fapi 需要 BTCUSDT 形式）。"""
    s = symbol.strip().upper()
    return s if s.endswith("USDT") else f"{s}USDT"


def _fmt_date(value: object) -> str:
    """日期统一 MM-DD：unix 秒（真实 API）与 ISO 字符串（mock）都兼容。"""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%m-%d")
        except (OSError, ValueError, OverflowError):
            return str(value)
    s = str(value)
    return s[5:10] if len(s) >= 10 else s


def _fmt_ts(ms: object) -> str:
    """unix 毫秒时间戳 → MM-DD HH:MM。"""
    if isinstance(ms, (int, float)):
        try:
            return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime(
                "%m-%d %H:%M"
            )
        except (OSError, ValueError, OverflowError):
            return str(ms)
    return str(ms)


def _value_str(value: object, decimals: int = 2) -> str:
    """数值 → 定点小数文本；None → NA（工具层永不抛异常）。

    费率等小数值用 decimals=6 保留精度（2 位会把 0.0001 打成 0.00）。
    """
    if value is None:
        return "NA"
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_series(
    rows: list[dict], keys: tuple[str, ...], decimals: int = 2, ts: bool = False
) -> str:
    """历史序列降采样 ≤10 点 → 文本行（每点 ``MM-DD: k1/k2``，含最新）。

    decimals 用于费率等小数值（6 位保留 0.0001）；ts=True 时时间戳按毫秒格式化。
    """
    step = max(1, len(rows) // _MAX_SERIES_POINTS)
    idxs = sorted({len(rows) - 1 - i * step for i in range(_MAX_SERIES_POINTS)})
    points = []
    for i in idxs:
        r = rows[i]
        raw = r.get("date") or r.get("funding_time")
        date = _fmt_ts(raw) if ts else _fmt_date(raw)
        vals = "/".join(_value_str(r.get(k), decimals) for k in keys)
        points.append(f"{date}: {vals}")
    return "; ".join(points)


@tool
def get_tvl_history(symbol: str) -> str:
    """协议 TVL 历史序列（每日，降采样 ≤10 点）。

    参数 symbol：交易对符号（如 "UNI"）或协议 slug（如 "uniswap"）。
    返回日期+TVL 序列（最近约 90 天抽样，USD）；链类代币（BTC/ETH/SOL 等）
    无协议 TVL 数据，返回提示。失败返回"数据不可用（UNKNOWN）"。
    """
    try:
        if _chain_of(symbol):
            return "链类代币无协议 TVL 历史序列（可用 get_stablecoin_history）"
        rows = defillama.fetch_protocol_tvl_history(_slug_of(symbol))
    except Exception:
        return "数据不可用（UNKNOWN）"
    if not rows:
        return "无历史数据"
    return "TVL 历史（MM-DD: USD）: " + _fmt_series(rows, ("tvl",))


@tool
def get_fees_history(symbol: str) -> str:
    """协议费用/收入历史序列（每日，降采样 ≤10 点）。

    参数 symbol：交易对符号（如 "UNI"）或协议 slug（如 "uniswap"）。
    返回日期+fees/revenue 序列（USD）；链类代币（BTC/ETH/SOL 等）无此数据。
    失败返回"数据不可用（UNKNOWN）"。
    """
    try:
        if _chain_of(symbol):
            return "链类代币无协议费用历史（可用 get_stablecoin_history）"
        rows = defillama.fetch_protocol_fees_history(_slug_of(symbol))
    except Exception:
        return "数据不可用（UNKNOWN）"
    if not rows:
        return "无历史数据"
    return "费用/收入历史（MM-DD: fees/revenue, USD）: " + _fmt_series(
        rows, ("fees", "revenue")
    )


@tool
def get_funding_history(symbol: str) -> str:
    """合约资金费率历史（每 8 小时一根，降采样 ≤10 点）。

    参数 symbol：交易对符号（如 "BTC" / "BTCUSDT"）。
    返回时间+费率序列（小数，如 0.0001 = 万分之一）；该代币无 USDT 永续
    合约时返回无数据。失败返回"数据不可用（UNKNOWN）"。
    """
    try:
        fapi = _fapi_symbol(symbol)
        rows = binance_futures.fetch_funding_rate_history(fapi, limit=80)
    except Exception:
        rows = None
    if not rows:
        return "无资金费率数据（该代币可能无 USDT 永续合约）"
    return "资金费率历史（MM-DD HH:MM: 费率）: " + _fmt_series(
        rows, ("funding_rate",), decimals=6, ts=True
    )


@tool
def get_stablecoin_history(symbol: str) -> str:
    """链稳定币总量历史序列（每日，降采样 ≤10 点）。

    参数 symbol：链名（如 "ethereum"）或交易对符号（如 "ETH" / "BTC"）。
    返回日期+供应量序列（USD）；协议类代币（UNI 等）无此数据。
    失败返回"数据不可用（UNKNOWN）"。
    """
    try:
        chain = _chain_of(symbol) or symbol.strip().lower()
        rows = defillama.fetch_stablecoin_history(chain)
    except Exception:
        return "数据不可用（UNKNOWN）"
    if not rows:
        return "无稳定币数据（该链可能无稳定币统计）"
    return "稳定币总量历史（MM-DD: USD）: " + _fmt_series(rows, ("supply",))


@tool
def search_web(query: str) -> str:
    """通用联网搜索（Bing Web RSS，零 key 免费）。

    适用场景：研究项目官网/团队/融资/社交/解锁计划/催化剂等输入数据未覆盖的
    宏观维度。参数 query：具体搜索词，如 "UNI Uniswap token unlock schedule"。
    返回标题+链接+摘要列表；无结果返回"无结果"。
    禁止用其查询价格/K线/链上数据（用专门数据源工具）。
    预算：单 token 研究内至多调用 3 次（六维 query 预算，prompt 约束）。
    """
    if env.is_mock_mode():
        return (
            f"{_MOCK_FLAG}{query} 搜索结果: 官网: 项目官网; "
            f"团队: 匿名核心团队; unlock: 2026-Q3 解锁流通量 1.2%; "
            f"社交: X 粉丝百万级; 融资: 2024 年 A 轮"
        )
    try:
        items = web_ds.search_web(query)
    except Exception:
        return "搜索不可用（UNKNOWN）"
    if items is None:
        return "搜索不可用（UNKNOWN）"
    if not items:
        return "无搜索结果"
    return "; ".join(
        f"{it['title']} - {it['url']}: {it['snippet'] or '无摘要'}" for it in items
    )


#: ③ research_facts 工具（5 个：4 历史序列 + search_web 六维查询模板）
FACTS_TOOLS = [
    get_tvl_history,
    get_fees_history,
    get_funding_history,
    get_stablecoin_history,
    search_web,
]

#: ⑤ challenge 工具（4 个，不含 search_web——对抗者不给联网搜索）
CHALLENGE_TOOLS = [
    get_tvl_history,
    get_fees_history,
    get_funding_history,
    get_stablecoin_history,
]
