"""screener — 确定性币种筛选（图外入口 ⑨，零 LLM）。

main.py 装配顺序：``screener.select_tokens(rules, top_n)`` → ``graph.invoke``。
规则引擎：Filter 依次 AND 过滤 → 单一 Rank 排序 → 板块上限截断 top_n。
快照失败抛 ``ScreeningError`` 批终止（全架构唯一允许终止的节点，入口没有
静默降级的意义）；mock 模式返回固定候选（规格纪律 6/9）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .datasources import binance_futures, defillama
from .datasources.base import UNKNOWN
from .datasources.mock import mock_screening_candidates
from .env import is_mock_mode


@dataclass
class ScreenRule:
    """一条筛选规则：kind = "filter" | "rank"，name = 注册表 key。"""

    kind: str
    name: str
    params: dict = field(default_factory=dict)


class ScreeningError(Exception):
    """快照失败/规则配置错误：批终止（区别于数据点失败不中断批）。"""


@dataclass
class ScreeningResult:
    """筛选结果：mode（"auto"|"mock"）+ 规则描述 + 候选（带 reason/metrics）。"""

    mode: str
    rules: list[str]
    candidates: list[dict]


class _Rule:
    """规则基类：构造接收 params，apply(rows) 返回新 rows（不修改入参）。"""

    def __init__(self, params: dict | None = None):
        self.params = params or {}

    def apply(self, rows: list[dict]) -> list[dict]:
        raise NotImplementedError


# ── Filter：AND 依次过滤，UNKNOWN 保守排除 ─────────────────


class ListingDaysLtFilter(_Rule):
    """次新：合约上线 ≤ max_days 天；listing_days 缺失（UNKNOWN）不通过。"""

    def apply(self, rows: list[dict]) -> list[dict]:
        max_days = self.params.get("max_days", 100)
        return [
            r
            for r in rows
            if isinstance(r.get("listing_days"), int) and r["listing_days"] <= max_days
        ]


class MinQuoteVolumeFilter(_Rule):
    """流动性下限：24h 成交额 ≥ min_quote_volume；缺失不通过。"""

    def apply(self, rows: list[dict]) -> list[dict]:
        floor = self.params.get("min_quote_volume", 1e7)
        return [
            r
            for r in rows
            if isinstance(r.get("quote_volume"), (int, float))
            and r["quote_volume"] >= floor
        ]


#: 稳定币集合（计价后缀与标的双重判定用）
_STABLE_BASES = {
    "USDT",
    "USDC",
    "FDUSD",
    "TUSD",
    "BUSD",
    "DAI",
    "USDD",
    "FRAX",
    "LUSD",
    "USDE",
    "USDP",
    "PYUSD",
    "GUSD",
    "AEUR",
    "EURT",
    "EURI",
}


def _is_stablecoin_pair(symbol: str) -> bool:
    """稳定币对：以稳定币计价且标的也为稳定币（如 USDCUSDT / TUSDUSDT）。

    BTCUSDT 标的为 BTC 不排除；仅排除稳定币之间的计价对。
    """
    for suffix in _STABLE_BASES:
        if symbol.endswith(suffix) and len(symbol) > len(suffix):
            return symbol[: -len(suffix)] in _STABLE_BASES
    return False


class ExcludeStablecoinsFilter(_Rule):
    """排除 USDT/USDC/FDUSD/TUSD 等稳定币计价对（symbol 双重判定）。"""

    def apply(self, rows: list[dict]) -> list[dict]:
        return [r for r in rows if not _is_stablecoin_pair(r["symbol"])]


# ── Rank：单一规则排序，缺失指标排最后（保守） ─────────────


class _Ranker(_Rule):
    """排序基类：_key 返回 (是否缺失, 排序值)，缺失恒排最后。"""

    def apply(self, rows: list[dict]) -> list[dict]:
        return sorted(rows, key=self._key, reverse=True)

    def _key(self, row: dict) -> tuple:
        raise NotImplementedError

    @staticmethod
    def _metric(row: dict, key: str) -> tuple[int, float]:
        value = row.get(key)
        if value is None or value == UNKNOWN:
            return (0, 0.0)
        return (1, float(value))


class Volatility24hRanker(_Ranker):
    """波动榜：24h 涨跌幅降序（abs=True 时取绝对值，默认开启）。"""

    def _key(self, row: dict) -> tuple:
        present, value = self._metric(row, "price_change_pct")
        if self.params.get("abs", True):
            value = abs(value)
        return (present, value)


class Gain24hRanker(_Ranker):
    """涨榜：24h 涨跌幅降序。"""

    def _key(self, row: dict) -> tuple:
        return self._metric(row, "price_change_pct")


class Loss24hRanker(_Ranker):
    """跌榜：24h 涨跌幅升序（最跌居首）。"""

    def _key(self, row: dict) -> tuple:
        present, value = self._metric(row, "price_change_pct")
        return (present, -value)


class QuoteVolumeRanker(_Ranker):
    """成交额榜：24h 成交额降序。"""

    def _key(self, row: dict) -> tuple:
        return self._metric(row, "quote_volume")


class MispricingRanker(_Ranker):
    """错价榜：价格弱 + 成交活跃优先（潜在错价候选）。

    筛选阶段无基本面数据，用价格/成交两维近似「基本面强/价格弱」（象限
    III）的价格侧代理：pct 升序排名 + vol 降序排名等权合成（score 越小
    越优先）；任一指标缺失（None/UNKNOWN）排最后（保守纪律）。
    """

    def apply(self, rows: list[dict]) -> list[dict]:
        valid = [
            r
            for r in rows
            if isinstance(r.get("price_change_pct"), (int, float))
            and isinstance(r.get("quote_volume"), (int, float))
        ]
        valid_ids = {id(r) for r in valid}
        invalid = [r for r in rows if id(r) not in valid_ids]
        by_pct = sorted(valid, key=lambda r: r["price_change_pct"])
        by_vol = sorted(valid, key=lambda r: r["quote_volume"], reverse=True)
        pct_rank = {id(r): i for i, r in enumerate(by_pct)}
        vol_rank = {id(r): i for i, r in enumerate(by_vol)}
        valid.sort(key=lambda r: pct_rank[id(r)] + vol_rank[id(r)])
        return valid + invalid


FILTERS: dict[str, type[_Rule]] = {
    "listing_days_lt": ListingDaysLtFilter,
    "min_quote_volume": MinQuoteVolumeFilter,
    "exclude_stablecoins": ExcludeStablecoinsFilter,
}

RANKERS: dict[str, type[_Rule]] = {
    "volatility_24h": Volatility24hRanker,
    "gain_24h": Gain24hRanker,
    "loss_24h": Loss24hRanker,
    "quote_volume": QuoteVolumeRanker,
    "mispricing_24h": MispricingRanker,
}

#: 默认规则（规格用户示例）：次新 ≤100 天 + 流动性下限 + 排除稳定币 → 错价榜
DEFAULT_RULES = [
    ScreenRule("filter", "listing_days_lt", {"max_days": 100}),
    ScreenRule("filter", "min_quote_volume", {"min_quote_volume": 1e7}),
    ScreenRule("filter", "exclude_stablecoins"),
    ScreenRule("rank", "mispricing_24h"),
]

#: 板块多样性上限（09 票）：同一板块最多进 Top N 的个数，0 = 关闭约束。
#: 单一 rank 榜取前 N 会把候选押在同一板块的同一波次上（实测一次 6 币实跑的
#: 候选里 BTW 与 SLX 同属 Basis Trading），整批证据随之高度相关，等于用 N 个
#: 位置赌 1 条注。默认 3：既留得出同板块的横向对比，又不让某一板块独占。
DEFAULT_MAX_PER_CATEGORY = 3


def _diversify(rows: list[dict], top_n: int, cap: int) -> tuple[list[dict], int]:
    """榜上取 Top N，同板块超额的行让位给榜后段（返回选中行 + 让位数）。

    行的 ``category`` 缺失时以自身 symbol 独占一桶：DeFiLlama 板块索引对全市场
    永续合约覆盖约四成（次新/meme 更低），把缺分类的行并成同一桶会凭空造出一个
    「其他」板块并优先剔除其后段——UNKNOWN 纪律不允许拿缺数据当惩罚。代价照实
    说明：纯 meme 波次仍能占满候选，所以板块一律写进 reason 供人工复核。
    """
    picked: list[dict] = []
    counts: dict[str, int] = {}
    skipped = 0
    for row in rows:
        if len(picked) >= top_n:
            break
        bucket = row.get("category") or f"#unknown:{row['symbol']}"
        if counts.get(bucket, 0) >= cap:
            skipped += 1
            continue
        counts[bucket] = counts.get(bucket, 0) + 1
        picked.append(row)
    return picked, skipped


def _params_str(params: dict) -> str:
    if not params:
        return "()"
    return "(" + ", ".join(f"{k}={v}" for k, v in params.items()) + ")"


def describe(rules: list[ScreenRule]) -> list[str]:
    """规则人类可读描述（写入 meta.screening.rules 供报告审计）。"""
    return [f"{r.name}{_params_str(r.params)}" for r in rules]


def _candidate(row: dict, rules_desc: str) -> dict:
    """候选：symbol + category（板块，未知 None）+ reason（命中规则 + 指标值）+ metrics。"""
    metrics = {
        k: row[k]
        for k in ("price_change_pct", "quote_volume", "listing_days")
        if row.get(k) not in (None, UNKNOWN)
    }
    metric_desc = "、".join(f"{k}={v}" for k, v in metrics.items())
    reason = rules_desc if not metric_desc else f"{rules_desc} | {metric_desc}"
    category = row.get("category")
    if category:
        reason = f"{reason} | 板块={category}"
    return {
        "symbol": row["symbol"],
        "category": category,
        "reason": reason,
        "metrics": metrics,
    }


def select_tokens(
    rules: list[ScreenRule],
    top_n: int = 10,
    max_per_category: int = DEFAULT_MAX_PER_CATEGORY,
) -> ScreeningResult:
    """确定性选币：全市场快照各 1 次 → 合约永续 USDT 白名单 → Filter AND → Rank
    → 板块上限截断 Top N。

    快照任一失败抛 ``ScreeningError`` 批终止（失败即失败，不回退 mock）；
    无 rank 规则时跳过排序（防 StopIteration）。
    白名单（规格：ticker + exchangeInfo 各 1 次）：与 FuturesApi 筛选同款——
    仅保留永续合约 ``TRADING``（contractType=PERPETUAL，排除 XAUUSDT/TSLAUSDT
    等交割合约）且以 USDT 计价、标的非稳定币的交易对，排除 ETHBTC/BTCU 等
    非规范 symbol 进入候选。
    ``max_per_category`` 为 0 时关闭板块约束；板块索引是辅助数据，拉取失败只
    让约束不生效（并写进 rules 留痕），不终止批。
    """
    if is_mock_mode():
        return ScreeningResult(
            mode="mock",
            rules=describe(rules),
            candidates=mock_screening_candidates(),
        )

    tickers = binance_futures.fetch_fapi_ticker_24h_all()
    listing = binance_futures.fetch_listing_days()
    info = binance_futures.fetch_exchange_info()
    if tickers is None or listing is None or info is None:
        raise ScreeningError("全市场快照拉取失败，批终止（失败即失败，不回退 mock）")

    whitelist = {
        s["symbol"]
        for s in info.get("symbols") or []
        if s.get("status") == "TRADING"
        and s.get("contractType") == "PERPETUAL"
        and str(s.get("symbol", "")).endswith("USDT")
        and s.get("baseAsset") not in _STABLE_BASES
    }

    rows: list[dict] = []
    for symbol, t in tickers.items():
        if symbol not in whitelist:
            continue  # 非合约 USDT 永续（交割合约/交叉对/非交易状态）不参与筛选
        rows.append(
            {
                "symbol": symbol,
                "price_change_pct": t.get("price_change_pct"),
                "quote_volume": t.get("quote_volume"),
                "listing_days": listing.get(symbol, UNKNOWN),
            }
        )

    rank_rules: list[ScreenRule] = []
    for rule in rules:
        if rule.kind == "filter":
            try:
                rows = FILTERS[rule.name](rule.params).apply(rows)
            except KeyError as exc:
                raise ScreeningError(f"未知筛选规则: {rule.name}") from exc
        elif rule.kind == "rank":
            rank_rules.append(rule)
        else:
            raise ScreeningError(f"未知规则类别: {rule.kind!r}（仅支持 filter/rank）")
    if len(rank_rules) > 1:
        raise ScreeningError(f"只支持单一 rank 规则，收到 {len(rank_rules)} 条")
    if rank_rules:
        try:
            rows = RANKERS[rank_rules[0].name](rank_rules[0].params).apply(rows)
        except KeyError as exc:
            raise ScreeningError(f"未知排序规则: {rank_rules[0].name}") from exc

    rules_desc_list = describe(rules)
    picked = rows[:top_n]
    if max_per_category > 0 and rows:
        # 板块索引（一次请求，约 6.7MB）：辅助数据，失败只让约束不生效、不终止批
        categories = defillama.fetch_protocol_categories() or {}
        for row in rows:
            row["category"] = categories.get(row["symbol"].removesuffix("USDT"))
        if not categories:
            rules_desc_list.append(
                f"sector_cap(max_per_category={max_per_category}) 未生效：板块索引不可用"
            )
        else:
            picked, skipped = _diversify(rows, top_n, max_per_category)
            if skipped:
                rules_desc_list.append(
                    f"sector_cap(max_per_category={max_per_category})："
                    f"{skipped} 个同板块超额候选让位"
                )
    rules_desc = "、".join(rules_desc_list)
    return ScreeningResult(
        mode="auto",
        rules=rules_desc_list,
        candidates=[_candidate(r, rules_desc) for r in picked],
    )
