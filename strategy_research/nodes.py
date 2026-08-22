"""图节点：8 个节点，线性装配；条件全在节点内部；批处理永不中断。

01 票骨架：占位实现——mock 模式最小可用（写空结构 + 后写覆盖），
完整逻辑由后续票实现（①=04 / ②=05 / ③-⑥=07 / ⑦=08 / ⑧=09-10）。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langchain.agents import create_agent

from strategy_research import context, env
from strategy_research import evidence as ev_mod
from strategy_research import review as review_mod
from strategy_research import scanner_snapshot as scan_mod
from strategy_research import signals as sig_mod
from strategy_research.datasources import binance, binance_futures, defillama, mock
from strategy_research.datasources import web as web_ds
from strategy_research.evidence import EvidenceItem
from strategy_research.schemas import (
    ChallengeItem,
    FactItem,
    RebuttalItem,
    TokenAnalysis,
    _extract_json,
)
from strategy_research.tools import CHALLENGE_TOOLS, FACTS_TOOLS

#: ③⑤ react agent 递归上限（防工具循环失控，规格）
AGENT_RECURSION_LIMIT = 8


# ── ① collect_data：共享资源 + per-token 装配 ───────────────


def _dp(value: Any, source: str) -> dict:
    """数据点四元组包装（规格十 4）；缺失值 confidence=0.0（UNKNOWN 纪律）。"""
    return {
        "value": value,
        "source": source,
        "timestamp": int(time.time()),
        "confidence": 1.0 if value is not None else 0.0,
    }



def _find(rows: list[dict] | None, symbol: str) -> dict | None:
    """共享全量列表中按 symbol 取行。"""
    if not rows:
        return None
    for r in rows:
        if r.get("symbol") == symbol:
            return r
    return None



def _latest(rows: list[dict] | None, key: str) -> float | None:
    """时间序列最新值（先按 timestamp 升序，兼容 mock 降序）。"""
    if not rows:
        return None
    last = max(rows, key=lambda r: r.get("timestamp") or 0)
    return last.get(key)


def _mean(rows: list[dict] | None, key: str) -> float | None:
    """时间序列数值均值（缺失项不计入）。"""
    if not rows:
        return None
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
    return sum(vals) / len(vals) if vals else None


def _series_pct_change(rows: list[dict] | None, key: str, hours: int) -> float | None:
    """时间序列 → 最新 vs N 小时前变化 %（先升序，兼容 mock 降序）。"""
    pts = sorted(
        (r for r in rows or [] if isinstance(r.get(key), (int, float))),
        key=lambda r: r.get("timestamp") or 0,
    )
    if len(pts) < 2:
        return None
    last = pts[-1]
    cutoff = (last.get("timestamp") or 0) - hours * 3_600_000
    prev = next(
        (p for p in reversed(pts[:-1]) if (p.get("timestamp") or 0) <= cutoff), None
    )
    if prev is None or prev.get(key) in (None, 0):
        return None
    return (last[key] / prev[key] - 1.0) * 100.0


def _funding_stats(rows: list[dict] | None) -> tuple[float | None, str | None]:
    """资金费率历史 → (均值, 趋势)。趋势 = 最新 vs 均值 ±10% 离散。"""
    pts = sorted(
        (r for r in rows or [] if isinstance(r.get("funding_rate"), (int, float))),
        key=lambda r: r.get("funding_time") or 0,
    )
    if not pts:
        return None, None
    pts = pts[-25:]  # 7d 统计窗口：仅最近 25 根（270 根仅喂 funding_percentile）
    last = pts[-1]["funding_rate"]
    avg = sum(r["funding_rate"] for r in pts) / len(pts)
    ratio = last / avg if avg else None
    if ratio is None:
        trend = None
    elif ratio > 1.1:
        trend = "rising"
    elif ratio < 0.9:
        trend = "falling"
    else:
        trend = "flat"
    return avg, trend


def _load_shared(tokens: list[str]) -> dict:
    """共享资源批内一次；mock 模式各 fetch 内部走 mock（零外部请求）。

    /protocols 惰性：仅静态 TOKEN_SLUG_MAP 未命中才拉（约 8MB）；
    聚合表按需：协议类拉 fees，链类拉 stablecoins/dexs。
    """
    bases = {defillama._strip_quote(t) for t in tokens}
    slug_map = {b: defillama.TOKEN_SLUG_MAP.get(b, "") for b in bases}
    protocols: dict[str, str] | None = None
    unresolved = [b for b, s in slug_map.items() if not s]
    if unresolved:
        protocols = defillama.fetch_protocols()
        if protocols:
            for b in unresolved:
                slug_map[b] = protocols.get(b, "")
    protocol_slugs = [s for s in slug_map.values() if s and not s.startswith("chain:")]
    chain_tokens = [
        t
        for t in tokens
        if slug_map.get(defillama._strip_quote(t), "").startswith("chain:")
    ]
    return {
        "tickers": binance.fetch_ticker_24h_all(),
        "listing": binance_futures.fetch_listing_days(),
        "premium": binance_futures.fetch_premium_index_all(),
        "fapi_prices": binance_futures.fetch_fapi_prices_all(),
        "chains": defillama.fetch_chains(),
        "protocols": protocols,
        "slug_map": slug_map,
        "fees": defillama.fetch_fees(protocol_slugs) if protocol_slugs else {},
        "stablecoins": defillama.fetch_stablecoins() if chain_tokens else None,
        "dexs": defillama.fetch_dexs() if chain_tokens else None,
    }


_FUND_CORE = ("tvl", "tvl_change_1d", "tvl_change_7d", "tvl_change_30d")
_FUND_KEYS = (*_FUND_CORE, "mcap", "fdv")


def _fund_incomplete(fund: dict) -> bool:
    """基本面不完整判定：只看该 kind 应有的字段（结构性缺失不算）。"""
    keys: list[str] = list(_FUND_CORE)
    if fund["kind"] == "protocol":
        keys += ["mcap", "fdv", "fees_24h", "fees_7d", "revenue_24h", "revenue_7d"]
    elif fund["kind"] == "chain":
        keys += ["stablecoin_supply", "dex_volume_24h"]
    return any(fund[k]["value"] is None for k in keys if isinstance(fund.get(k), dict))


def _fund(symbol: str, shared: dict) -> dict:
    """基本面装配：slug 解析（静态映射 → 惰性兜底）→ TVL + 聚合字段。"""
    base = defillama._strip_quote(symbol)
    slug = shared["slug_map"].get(base, "")
    fund: dict[str, Any] = {
        "kind": "unknown",
        "name": None,
        "category": None,
        "resolved": False,
        "error": None,
        "incomplete": False,
    }
    if slug:
        if slug.startswith("chain:"):
            fund["kind"], fund["name"] = "chain", slug[6:]
        else:
            fund["kind"], fund["name"] = "protocol", slug
        fund["resolved"] = True
        tvl_row = (
            defillama.fetch_chain_tvl(slug[6:])
            if fund["kind"] == "chain"
            else defillama.fetch_protocol_tvl(slug)
        )
        if tvl_row is None:
            fund["error"] = f"{fund['kind']} TVL 拉取失败"
        for key in _FUND_KEYS:
            fund[key] = _dp(tvl_row.get(key) if tvl_row else None, "defillama")
        if fund["kind"] == "protocol":
            fees = (shared["fees"] or {}).get(slug)
            fund["fees_24h"] = _dp(fees.get("fees_24h") if fees else None, "defillama")
            fund["fees_7d"] = _dp(fees.get("fees_7d") if fees else None, "defillama")
            fund["revenue_24h"] = _dp(
                fees.get("revenue_24h") if fees else None, "defillama"
            )
            fund["revenue_7d"] = _dp(
                fees.get("revenue_7d") if fees else None, "defillama"
            )
            fund["stablecoin_supply"] = _dp(None, "defillama")
            fund["dex_volume_24h"] = _dp(None, "defillama")
            # 趋势特征（01 票）：确定性历史序列 → 纯函数提炼，两分支直读数值
            tvl_hist = defillama.fetch_protocol_tvl_history(slug)
            fund["tvl_trend_30d"] = _dp(
                sig_mod.series_trend(tvl_hist, "tvl", 30), "defillama"
            )
            fees_hist = defillama.fetch_protocol_fees_history(slug)
            fund["fees_trend_30d"] = _dp(
                sig_mod.series_trend(fees_hist, "fees", 30), "defillama"
            )
            fund["stablecoin_change_30d"] = _dp(None, "defillama")
        else:  # chain：稳定币/DEX 聚合字段 + 稳定币趋势特征
            chain = slug[6:]
            fund["fees_24h"] = _dp(None, "defillama")
            fund["fees_7d"] = _dp(None, "defillama")
            fund["revenue_24h"] = _dp(None, "defillama")
            fund["revenue_7d"] = _dp(None, "defillama")
            fund["stablecoin_supply"] = _dp(
                (shared["stablecoins"] or {}).get(chain), "defillama"
            )
            fund["dex_volume_24h"] = _dp((shared["dexs"] or {}).get(chain), "defillama")
            fund["tvl_trend_30d"] = _dp(None, "defillama")
            fund["fees_trend_30d"] = _dp(None, "defillama")
            sc_hist = defillama.fetch_stablecoin_history(chain)
            fund["stablecoin_change_30d"] = _dp(
                sig_mod.series_change(sc_hist, "supply", 30), "defillama"
            )
    else:
        for key in _FUND_KEYS:
            fund[key] = _dp(None, "defillama")
        fund["fees_24h"] = _dp(None, "defillama")
        fund["fees_7d"] = _dp(None, "defillama")
        fund["revenue_24h"] = _dp(None, "defillama")
        fund["revenue_7d"] = _dp(None, "defillama")
        fund["stablecoin_supply"] = _dp(None, "defillama")
        fund["dex_volume_24h"] = _dp(None, "defillama")
        fund["tvl_trend_30d"] = _dp(None, "defillama")
        fund["fees_trend_30d"] = _dp(None, "defillama")
        fund["stablecoin_change_30d"] = _dp(None, "defillama")
    fund["incomplete"] = _fund_incomplete(fund)
    return fund


def _market(symbol: str, shared: dict) -> dict:
    """市场快照：现货 ticker + klines 窗口 + 衍生品（futures_error 独立）。"""
    exch = binance.pair_symbol(symbol)
    mkt: dict[str, Any] = {"error": None, "futures_error": None, "incomplete": False}
    errors: list[str] = []
    ticker = _find(shared["tickers"], exch)
    if ticker is None:
        errors.append("ticker 缺失")
    mkt["price"] = _dp(ticker.get("price") if ticker else None, "binance")
    mkt["change_24h"] = _dp(
        ticker.get("price_change_pct") if ticker else None, "binance"
    )
    mkt["quote_volume_24h"] = _dp(
        ticker.get("quote_volume") if ticker else None, "binance"
    )
    klines = binance.fetch_klines(exch, interval="1d", limit=400)
    if klines is None:
        errors.append("klines 拉取失败")
    for days, key in (
        (7, "change_7d"),
        (30, "change_30d"),
        (90, "change_90d"),
        (365, "change_1y"),
    ):
        mkt[key] = _dp(binance.trailing_return(klines, days), "binance")
    mkt["listing_days"] = _dp((shared["listing"] or {}).get(exch), "binance_futures")

    # 衍生品（并入 market 快照，futures_error 独立标记）
    f_err: list[str] = []
    premium = _find(shared["premium"], exch)
    mkt["funding"] = _dp(
        premium.get("last_funding_rate") if premium else None, "binance_futures"
    )
    if premium is None:
        f_err.append("premium 缺失")
    funding_rows = binance_futures.fetch_funding_rate_history(exch, limit=270)
    avg, trend = _funding_stats(funding_rows)
    mkt["funding_avg_7d"] = _dp(avg, "binance_futures")
    mkt["funding_trend"] = _dp(trend, "binance_futures")
    mkt["funding_pctile_90d"] = _dp(
        sig_mod.funding_percentile(funding_rows), "binance_futures"
    )
    oi_row = binance_futures.fetch_open_interest(exch)
    mkt["oi"] = _dp(oi_row.get("open_interest") if oi_row else None, "binance_futures")
    if oi_row is None:
        f_err.append("openInterest 缺失")
    fapi = (shared["fapi_prices"] or {}).get(exch)
    spot = ticker.get("price") if ticker else None
    mkt["basis"] = _dp(
        ((fapi / spot - 1.0) * 100.0) if fapi and spot else None, "binance_futures"
    )
    if fapi is None:
        f_err.append("fapi price 缺失")
    if f_err:
        mkt["futures_error"] = "; ".join(f_err)
    if errors:
        mkt["error"] = "; ".join(errors)

    # taker 买卖比（market 与 microstructure 共用一次拉取）
    taker = binance_futures.fetch_taker_long_short_ratio(exch, "1h", 24)
    mkt["taker_buy_ratio_24h"] = _dp(_mean(taker, "buy_sell_ratio"), "binance_futures")
    # 核心字段判定：衍生品/长窗口缺失由 futures_error/listing_days 承载，不重复标记
    mkt["incomplete"] = any(
        mkt[k]["value"] is None
        for k in ("price", "change_24h", "quote_volume_24h", "change_7d", "change_30d")
    )
    return mkt, taker


def _microstructure(symbol: str, taker: list[dict] | None, price_ret_24h: float | None) -> dict:
    """微观结构装配：OI 变化 / 多空比 / taker 比（board PoC 阶段 None）。"""
    exch = binance.pair_symbol(symbol)
    ms: dict[str, Any] = {"board": None, "error": None, "incomplete": False}
    # 96 个 1h 点（跨 95h）才能算 48h 变化（48 点仅 47h 跨度，48h 恒缺失）
    oi_hist = binance_futures.fetch_open_interest_hist(exch, "1h", 96)
    if oi_hist is None:
        ms["error"] = "openInterestHist 拉取失败"
    oi_change_24h = _series_pct_change(oi_hist, "sum_open_interest", 24)
    ms["oi_change_24h"] = _dp(oi_change_24h, "binance_futures")
    ms["oi_price_divergence"] = _dp(
        sig_mod.oi_price_divergence(price_ret_24h, oi_change_24h),
        "binance_futures",
    )
    ms["oi_change_48h"] = _dp(
        _series_pct_change(oi_hist, "sum_open_interest", 48), "binance_futures"
    )
    ms["oi_value_change_24h"] = _dp(
        _series_pct_change(oi_hist, "sum_open_interest_value", 24), "binance_futures"
    )
    ls_all = binance_futures.fetch_global_long_short_ratio(exch, "1h", 48)
    ms["ls_ratio_all"] = _dp(_latest(ls_all, "long_short_ratio"), "binance_futures")
    ms["ls_ratio_all_change_24h"] = _dp(
        _series_pct_change(ls_all, "long_short_ratio", 24), "binance_futures"
    )
    top_acc = binance_futures.fetch_top_long_short_account_ratio(exch, "1h", 24)
    ms["ls_ratio_top_acc"] = _dp(
        _latest(top_acc, "long_short_ratio"), "binance_futures"
    )
    top_pos = binance_futures.fetch_top_long_short_position_ratio(exch, "1h", 24)
    ms["ls_ratio_top_pos"] = _dp(
        _latest(top_pos, "long_short_ratio"), "binance_futures"
    )
    ms["taker_bs_ratio"] = _dp(_latest(taker, "buy_sell_ratio"), "binance_futures")
    ms["incomplete"] = any(
        d["value"] is None for d in ms.values() if isinstance(d, dict)
    )
    return ms


def _web(symbol: str) -> dict:
    """Web 新闻快照：items 截取 {date, title, source}。"""
    base = defillama._strip_quote(symbol)
    snap: dict[str, Any] = {
        "symbol": symbol,
        "items": None,
        "web_error": None,
        "incomplete": False,
    }
    items = web_ds.fetch_news_rss(f"{base} crypto", limit=5)
    if items is None:
        snap["web_error"] = "news 拉取失败"
        snap["incomplete"] = True
    else:
        snap["items"] = [
            {k: it.get(k) for k in ("date", "title", "source")} for it in items
        ]
    return snap


def _error_snapshots(
    symbol: str, exc: Exception | None
) -> tuple[dict, dict, dict, dict]:
    """异常兜底快照：全字段四元组 None，结构契约不被破坏（②⑦ 直读安全）。"""
    msg = f"装配异常: {exc}" if exc else "装配异常"
    fund: dict[str, Any] = {
        "kind": "unknown",
        "name": None,
        "category": None,
        "resolved": False,
        "error": msg,
        "incomplete": True,
    }
    for key in (
        *_FUND_KEYS,
        "fees_24h",
        "fees_7d",
        "revenue_24h",
        "revenue_7d",
        "stablecoin_supply",
        "dex_volume_24h",
        "tvl_trend_30d",
        "fees_trend_30d",
        "stablecoin_change_30d",
    ):
        fund[key] = _dp(None, "defillama")
    mkt: dict[str, Any] = {"error": msg, "futures_error": None, "incomplete": True}
    for key in (
        "price",
        "change_24h",
        "quote_volume_24h",
        "change_7d",
        "change_30d",
        "change_90d",
        "change_1y",
    ):
        mkt[key] = _dp(None, "binance")
    for key in (
        "listing_days",
        "funding",
        "funding_avg_7d",
        "funding_trend",
        "funding_pctile_90d",
        "oi",
        "basis",
        "taker_buy_ratio_24h",
    ):
        mkt[key] = _dp(None, "binance_futures")
    ms: dict[str, Any] = {"board": None, "error": msg, "incomplete": True}
    for key in (
        "oi_change_24h",
        "oi_change_48h",
        "oi_value_change_24h",
        "ls_ratio_all",
        "ls_ratio_all_change_24h",
        "ls_ratio_top_acc",
        "ls_ratio_top_pos",
        "taker_bs_ratio",
        "oi_price_divergence",
    ):
        ms[key] = _dp(None, "binance_futures")
    web_snap: dict[str, Any] = {
        "symbol": symbol,
        "items": None,
        "web_error": msg,
        "incomplete": True,
    }
    return fund, mkt, ms, web_snap


def _one(symbol: str, shared: dict) -> tuple[dict, dict, dict, dict]:
    """单 token 装配：基本面 / 市场 / 微观结构 / Web 各步 try 隔离。

    任一步异常仅该步快照标"装配异常"，其余步骤照常（规格 ① 步骤 4）。
    """
    fund, mkt, ms, web_snap = _error_snapshots(symbol, None)
    try:
        fund = _fund(symbol, shared)
    except Exception as exc:
        fund = _error_snapshots(symbol, exc)[0]
    try:
        mkt, taker = _market(symbol, shared)
    except Exception as exc:
        mkt, taker = _error_snapshots(symbol, exc)[1], None
    try:
        ms = _microstructure(symbol, taker, mkt["change_24h"]["value"])
    except Exception as exc:
        ms = _error_snapshots(symbol, exc)[2]
    try:
        web_snap = _web(symbol)
    except Exception as exc:
        web_snap = _error_snapshots(symbol, exc)[3]
    return fund, mkt, ms, web_snap


def _meta(state: dict) -> dict[str, Any]:
    """取 meta（不存在则初始化），并记录节点执行顺序。"""
    meta = dict(state.get("meta") or {})
    order = list(meta.get("node_order") or [])
    return meta, order


def collect_data(state: dict) -> dict:
    """① 数据收集（确定性）：共享批内一次 + 并发 per-token 装配。

    失败即失败：数据源失败该数据点 UNKNOWN 并标记 error/incomplete，
    单 token 异常不中断批；incomplete_tokens 落 meta。
    """
    meta, order = _meta(state)
    order.append("collect_data")
    meta["node_order"] = order
    tokens = state["tokens"]
    # 扫描器快照（外部 BinanceApi CSV，只读；异常/缺失 → {}，③-⑥ 摘要与⑧ 报告占位；
    # 已注入的 state 快照优先，测试注入/外部提供不经文件读取）
    scanner_snapshot: dict = dict(state.get("scanner_snapshot") or {})
    if not scanner_snapshot:
        try:
            scanner_snapshot = scan_mod.load_snapshots()
        except Exception as exc:  # 同 report_error 纪律：仅记录不中断批
            meta["scan_error"] = f"扫描器快照读取失败: {exc}"
    # 校准基线（13 票）：live 模式加载历史已回看记录 → ③-⑥ 摘要携带；
    # mock 模式跳过（mock 决策不进评估池，隔离保持一致）；失败仅记录不中断批
    if not env.is_mock_mode():
        try:
            meta["calibration_context"] = review_mod.render_calibration_context(
                review_mod.load_records()
            )
        except Exception as exc:
            meta["calibration_error"] = f"校准基线加载失败: {exc}"
    shared = _load_shared(tokens)
    market_data: dict[str, dict] = {}
    fundamental_data: dict[str, dict] = {}
    microstructure_data: dict[str, dict] = {}
    web_data: dict[str, dict] = {}
    incomplete: list[str] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {s: pool.submit(_one, s, shared) for s in tokens}
        for symbol, fut in futures.items():
            try:
                fund, mkt, ms, web_snap = fut.result()
            except Exception as exc:  # 终极兜底（_one 步级隔离外的意外异常）
                fund, mkt, ms, web_snap = _error_snapshots(symbol, exc)
            market_data[symbol] = mkt
            fundamental_data[symbol] = fund
            microstructure_data[symbol] = ms
            web_data[symbol] = web_snap
            if (
                fund.get("incomplete")
                or mkt.get("incomplete")
                or ms.get("incomplete")
                or web_snap.get("incomplete")
            ):
                incomplete.append(symbol)
    meta["incomplete_tokens"] = incomplete
    return {
        "market_data": market_data,
        "fundamental_data": fundamental_data,
        "microstructure_data": microstructure_data,
        "web_data": web_data,
        "scanner_snapshot": scanner_snapshot,
        "meta": meta,
    }


def compute_signals(state: dict) -> dict:
    """② 信号计算（确定性）：估值/动量/背离/sentiment_raw（05 票）。

    mock 模式走 mock.mock_signals_data（同构字段，kind 分支与真实一致）；
    真实模式 per-token 调 4 个纯函数；单 token 异常置 {symbol, error} 不阻断。
    """
    meta, order = _meta(state)
    order.append("compute_signals")
    meta["node_order"] = order
    signals_out: dict[str, Any] = {}
    for symbol in state["tokens"]:
        try:
            if env.is_mock_mode():
                kind = (state.get("fundamental_data", {}).get(symbol) or {}).get("kind")
                signals_out[symbol] = mock.mock_signals_data(symbol, kind)
            else:
                fund = state.get("fundamental_data", {}).get(symbol)
                mkt = state.get("market_data", {}).get(symbol)
                ms = state.get("microstructure_data", {}).get(symbol)
                signals_out[symbol] = {
                    "symbol": symbol,
                    "valuation": sig_mod.valuation_ratios(fund, mkt),
                    "momentum": sig_mod.momentum_score(fund),
                    "divergence": sig_mod.divergence(fund, mkt),
                    "sentiment": sig_mod.sentiment_raw(mkt, ms),
                    "error": None,
                }
        except Exception as exc:  # 单 token 异常不阻断（规格 ②）
            signals_out[symbol] = {"symbol": symbol, "error": f"信号计算异常: {exc}"}
    return {"signals": signals_out, "meta": meta}


def _collect_facts(symbol: str, state: dict) -> list[dict]:
    """③ 单 token 采证：react agent + 宽容解析；坏条目丢弃；异常 → []。"""
    items: list[dict] = []
    try:
        summary = context.build_facts_summary(symbol, state)
        agent = create_agent(env.get_llm(), FACTS_TOOLS, system_prompt=context.FACTS_PROMPT)
        result = agent.invoke(
            {"messages": [("human", summary)]},
            config={
                "recursion_limit": AGENT_RECURSION_LIMIT,
                "callbacks": [env.live_call_counter("facts")],
            },
        )
        obj = _extract_json(result["messages"][-1].content)
        for x in (obj or {}).get("facts") or []:
            try:
                item = FactItem.model_validate(x).model_dump()
                if item["claim"] and item["source"]:
                    items.append(item)
            except Exception:  # noqa: S112 —— 坏条目丢弃（规格 ③-4）
                continue
    except Exception:  # noqa: S110 —— 异常 → facts=[]，④ 只见确定性信号（规格 ③ 失败矩阵）
        pass
    return items


def research_facts(state: dict) -> dict:
    """③ 采证（LLM）：四分析师视角 facts（07 票）。

    每 token 串行：react agent（FACTS_TOOLS 5 工具）采证 → 宽容解析 →
    坏条目丢弃；单 token 异常 → facts=[]（④ 只见确定性信号）；批不中断。
    """
    meta, order = _meta(state)
    order.append("research_facts")
    meta["node_order"] = order
    return {
        "facts": {s: _collect_facts(s, state) for s in state["tokens"]},
        "meta": meta,
    }


def _invoke_branch(symbol: str, state: dict, side: str) -> tuple[list[dict], str | None]:
    """分支单 token 证据提取（02 票）：json_mode 单次调用 + 宽容解析。

    坏条目（claim/source 空）丢弃在装配层；上限 8 条截断（BranchOutput 契约）；
    异常 → ([], 错误消息)，批不中断。返回 (items, error)。
    """
    prompt = context.BULL_PROMPT if side == "bull" else context.BEAR_PROMPT
    items: list[dict] = []
    try:
        summary = context.build_branch_summary(symbol, state)
        out = (
            env.get_llm(json_mode=True)
            .with_retry(stop_after_attempt=2)
            .invoke(
                [("system", prompt), ("human", summary)],
                config={"callbacks": [env.live_call_counter(side)]},
            )
        )
        obj = _extract_json(getattr(out, "content", out))
        for x in (obj or {}).get("evidence") or []:
            try:
                item = EvidenceItem.model_validate(x).model_dump()
                if item["claim"] and item["source"]:
                    items.append(item)
            except Exception:  # noqa: S112 —— 坏条目丢弃（02 票）
                continue
        return items[:8], None
    except Exception as exc:
        return [], f"分支异常: {exc}"


def _branch(state: dict, side: str, key: str) -> dict:
    """分支节点模板（bull/bear 共用，02 票）：每 token 独立证据提取，批不中断。

    只写本分支独占字段（{side}_evidence / {side}_errors）——两分支并行时
    写共享键（meta 等）会触发 LangGraph 并行写冲突（spec D1）；node_order
    由串行的 evidence_verify 统一记录。
    """
    errors: dict[str, str] = {}
    items: dict[str, list[dict]] = {}
    for s in state["tokens"]:
        got, err = _invoke_branch(s, state, side)
        items[s] = got
        if err:
            errors[s] = err
    out: dict[str, Any] = {f"{side}_evidence": items}
    if errors:
        out[f"{side}_errors"] = errors
    return out


def bull_research(state: dict) -> dict:
    """多头证据研究员（02 票）：单 token 结构化证据（≤8 条）。

    不接线——旧图照常，03 票拓扑切换后生效。
    """
    return _branch(state, "bull", "bull_research")


def bear_research(state: dict) -> dict:
    """空头证据研究员（02 票）：单 token 结构化证据（≤8 条）。

    不接线——旧图照常，03 票拓扑切换后生效。
    """
    return _branch(state, "bear", "bear_research")


def evidence_verify(state: dict) -> dict:
    """证据核验（03 票，确定性）：两分支产出合并核验 → evidence + rejected_evidence。

    纯函数无 IO（evidence.verify_evidence）：basis 逐级解引用存在且值一致 →
    通过；否则剔除留痕。取代旧 risk_check 的机器强制位置（spec D8）。
    串行节点补记分支 node_order（并行分支不写共享 meta，顺序即图定义顺序）。
    """
    meta, order = _meta(state)
    order += ["bull_research", "bear_research", "evidence_verify"]
    meta["node_order"] = order
    verified, rejected = ev_mod.verify_evidence(
        state.get("bull_evidence"), state.get("bear_evidence"), state
    )
    return {"evidence": verified, "rejected_evidence": rejected, "meta": meta}


def _invoke_analysis(symbol: str, state: dict) -> dict:
    """④ 单 token 决策：json_mode 单次调用 + _extract_json 宽容解析。

    异常/不可解析 → PASS 兜底（保守原则，fallback 记录可审计）。
    """
    try:
        summary = context.build_decide_summary(symbol, state)
        out = (
            env.get_llm(json_mode=True)
            .with_retry(stop_after_attempt=2)
            .invoke(
                [("system", context.DECIDE_PROMPT), ("human", summary)],
                config={"callbacks": [env.live_call_counter("decide")]},
            )
        )
        obj = _extract_json(getattr(out, "content", out))
        if not isinstance(obj, dict):
            raise TypeError("json_mode 输出不可解析")
        return TokenAnalysis.model_validate(obj).model_dump()
    except Exception as exc:
        return {
            "symbol": symbol,
            "decision": "PASS",
            "confidence": 0.0,
            "error": f"LLM 分析失败: {exc}",
            "fallback": "json_mode",
        }


def decide(state: dict) -> dict:
    """④ 决策（LLM）：json_mode 单次调用，禁止 tools（07 票）。

    摘要 = 确定性骨架 + 事实证据节（按 dimension 分组）；异常 → PASS 兜底，
    fallback 记录；批不中断。
    """
    meta, order = _meta(state)
    order.append("decide")
    meta["node_order"] = order
    return {
        "decisions": {s: _invoke_analysis(s, state) for s in state["tokens"]},
        "meta": meta,
    }


def _invoke_challenge(symbol: str, state: dict) -> list[dict]:
    """⑤ 单 token 对抗：PASS 透传零调用；非 PASS react agent，截断 ≤3 条。"""
    dec = (state.get("decisions") or {}).get(symbol) or {}
    if dec.get("decision") == "PASS":
        return []
    items: list[dict] = []
    try:
        summary = context.build_challenge_summary(symbol, state)
        agent = create_agent(
            env.get_llm(), CHALLENGE_TOOLS, system_prompt=context.CHALLENGE_PROMPT
        )
        result = agent.invoke(
            {"messages": [("human", summary)]},
            config={
                "recursion_limit": AGENT_RECURSION_LIMIT,
                "callbacks": [env.live_call_counter("challenge")],
            },
        )
        obj = _extract_json(result["messages"][-1].content)
        for x in (obj or {}).get("challenges") or []:
            try:
                items.append(ChallengeItem.model_validate(x).model_dump())
            except Exception:  # noqa: S112 —— 单条丢弃（规格 ⑤ 失败矩阵）
                continue
    except Exception:  # noqa: S110 —— 异常 → 空列表，⑥ 维持（规格 ⑤ 失败矩阵）
        pass
    return items[:3]


def challenge(state: dict) -> dict:
    """⑤ 对抗（LLM）：PASS 透传零调用，非 PASS 挖反方（07 票）。

    反方事实预筛按决策方向取反（多头取 bear / 空头取 bull）；
    单 token 异常 → 空列表（⑥ 维持）；批不中断。
    """
    meta, order = _meta(state)
    order.append("challenge")
    meta["node_order"] = order
    return {
        "challenges": {s: _invoke_challenge(s, state) for s in state["tokens"]},
        "meta": meta,
    }


def _invoke_rebuttals(symbol: str, state: dict) -> dict:
    """⑥ 单 token 复审：无挑战透传；逐条 rebutted/accepted，accepted 只降不升。"""
    dec = (state.get("decisions") or {}).get(symbol) or {}
    chs = (state.get("challenges") or {}).get(symbol) or []
    if not chs:
        return {"analysis": dec, "rebuttals": []}
    try:
        summary = context.build_finalize_summary(symbol, state)
        out = (
            env.get_llm(json_mode=True)
            .with_retry(stop_after_attempt=2)
            .invoke(
                [("system", context.FINALIZE_PROMPT), ("human", summary)],
                config={"callbacks": [env.live_call_counter("rebuttals")]},
            )
        )
        obj = _extract_json(getattr(out, "content", out))
        rebuttals: list[dict] = []
        for x in (obj or {}).get("rebuttals") or []:
            try:
                rebuttals.append(RebuttalItem.model_validate(x).model_dump())
            except Exception:  # noqa: S112 —— 单条丢弃（规格 ⑥ 失败矩阵）
                continue
        analysis = dict(dec)
        for rb in rebuttals:
            if rb["outcome"] == "accepted":
                analysis["decision"] = "WATCH"  # 只降不升
                analysis["confidence"] = round(
                    max(0.0, float(analysis.get("confidence", 0.0)) - 0.1), 2
                )
                analysis["risks"] = list(analysis.get("risks") or []) + [rb["response"]]
        return {"analysis": analysis, "rebuttals": rebuttals}
    except Exception:
        return {"analysis": dec, "rebuttals": []}


def finalize(state: dict) -> dict:
    """⑥ 复审（LLM）：逐条 rebutted/accepted，accepted 只降不升（07 票）。

    无挑战透传零调用；单 token 异常 → 维持原决策；批不中断。
    """
    meta, order = _meta(state)
    order.append("finalize")
    meta["node_order"] = order
    return {
        "final_decisions": {s: _invoke_rebuttals(s, state) for s in state["tokens"]},
        "meta": meta,
    }


def _ev_flags(symbol: str, analysis: dict, state: dict) -> list[str]:
    """EV 边界核验（Conservative Analyst 视角）：TRADE 方向须与确定性信号一致。

    多头：momentum.value >= 0 或 quadrant ∈ {I, III}；空头：momentum.value < 0 或
    quadrant == II（IV 双弱无错价依据，不做空）；方向缺失即不满足。
    signals[symbol] 缺失 → 跳过核验（规格失败矩阵：缺数据不等于矛盾，不误伤）。
    """
    side = analysis.get("direction")
    sig = (state.get("signals") or {}).get(symbol) or {}
    # 键缺失或信号层失败（05 票 error 条目）→ 跳过核验：缺数据不等于矛盾，不误伤
    if not sig or sig.get("error"):
        return []
    mom = (sig.get("momentum") or {}).get("value")
    quad = ((sig.get("divergence") or {}).get("value") or {}).get("quadrant")
    if side == "long":
        ok = (isinstance(mom, (int, float)) and mom >= 0) or quad in ("I", "III")
    elif side == "short":
        ok = (isinstance(mom, (int, float)) and mom < 0) or quad == "II"
    else:
        ok = False
    if ok:
        return []
    side_text = {"long": "多头", "short": "空头"}.get(side, "未声明方向")
    return [f"EV 不足: 动量/背离信号与{side_text}决策矛盾"]


def _signal_state(symbol: str, state: dict) -> dict:
    """确定性信号状态快照（04 票：随结果落盘，供回看按信号分桶）。

    任一缺失 → None（UNKNOWN 纪律）；signals 层失败（error 条目）→ 全 None。
    """
    sig = (state.get("signals") or {}).get(symbol) or {}
    none_all = {
        "momentum": None,
        "quadrant": None,
        "funding_pctile_90d": None,
        "oi_price_divergence": None,
    }
    if not sig or sig.get("error"):
        return none_all
    mom = (sig.get("momentum") or {}).get("value")
    quad = ((sig.get("divergence") or {}).get("value") or {}).get("quadrant")
    mkt = (state.get("market_data") or {}).get(symbol) or {}
    ms = (state.get("microstructure_data") or {}).get(symbol) or {}
    pct = (mkt.get("funding_pctile_90d") or {}).get("value")
    od = (ms.get("oi_price_divergence") or {}).get("value") or {}
    return {
        "momentum": mom if isinstance(mom, (int, float)) else None,
        "quadrant": quad if quad in ("I", "II", "III", "IV") else None,
        "funding_pctile_90d": pct if isinstance(pct, (int, float)) else None,
        "oi_price_divergence": od.get("label") if isinstance(od, dict) else None,
    }


def risk_check(state: dict) -> dict:
    """⑦ 风控终审（确定性）：EV 边界 + 组合集中度两条核验，只降不升（08 票）。

    纯函数无 IO，LLM 无法覆盖降级。两遍扫描：先统计批内 TRADE 数，再逐 token
    核验；有 flag 的 TRADE 降级 WATCH + downgraded 落分析对象（下游 results/报告
    消费）。signals 缺失的 token 跳过 EV 核验（不误伤）；批处理永不中断。
    """
    meta, order = _meta(state)
    order.append("risk_check")
    meta["node_order"] = order
    tokens = state["tokens"]
    finals = state.get("final_decisions", {})
    trade_count = sum(
        1
        for s in tokens
        if ((finals.get(s) or {}).get("analysis") or {}).get("decision") == "TRADE"
    )
    risk_flags: dict[str, list[str]] = {}
    results: list[dict] = []
    for symbol in tokens:
        item = finals.get(symbol) or {}
        analysis = dict(item.get("analysis") or {})
        flags: list[str] = []
        if analysis.get("decision") == "TRADE":
            flags = _ev_flags(symbol, analysis, state)
            if trade_count > 2:
                flags.append(f"组合集中度超限: 批内 TRADE 数 = {trade_count}")
            if flags:  # 终审降级（Portfolio Manager 裁决权）：只降不升
                analysis["decision"] = "WATCH"
                analysis["downgraded"] = flags
        risk_flags[symbol] = flags
        results.append(
            {
                **analysis,
                "rebuttals": item.get("rebuttals") or [],
                "risk_flags": flags,
                "signal_state": _signal_state(symbol, state),
            }
        )
    return {"risk_flags": risk_flags, "results": results, "meta": meta}


def write_report(state: dict) -> dict:
    """⑧ 报告落盘：evidence.md + run.json + candidates.json + snapshot/diff（04 票）。"""
    from strategy_research.report import build_report

    meta, order = _meta(state)
    order.append("write_report")
    meta["node_order"] = order
    try:
        report_path, artifacts = build_report(state, meta)
        meta["report_path"] = str(report_path)
    except Exception as exc:  # 规格：落盘异常仅记 meta，不中断批（六节错误矩阵 ⑧）
        artifacts = {s: {} for s in state["tokens"]}
        meta["report_error"] = f"报告落盘失败: {exc}"
    return {
        "meta": meta,
        "research_artifacts": artifacts,
    }
