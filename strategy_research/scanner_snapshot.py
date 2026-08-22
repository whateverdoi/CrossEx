"""BinanceApi 扫描器快照只读接入：本地 CSV，不触网，不改写扫描器产物。

数据源是外部项目 BinanceApi 的 ``research/scan.py`` 每日产出
（``data/research/{date}_all.csv`` 全市场快照 / ``{date}_movers.csv`` 榜单 /
``{date}_microstructure.csv`` 候选币微观结构）。本模块只读解析：

- 文件缺失/损坏/NaN → 字段 None 或整表空，绝不抛异常（决策链与报告占位）；
- 目录与日期可用 ``SR_SCAN_DIR`` / ``SR_SCAN_DATE`` 覆盖（默认取最新日期）；
- 与 mock/live 模式无关：CSV 是本地文件，读取不产生外部请求。
"""

from __future__ import annotations

import csv
import math
import os
from pathlib import Path

#: 默认扫描器输出目录（BinanceApi 项目 data/research）
DEFAULT_DIR = (
    Path.home() / "Projects" / "python_projects" / "BinanceApi" / "data" / "research"
)
ENV_DIR = "SR_SCAN_DIR"
ENV_DATE = "SR_SCAN_DATE"

#: all.csv/movers.csv 的市场快照列（与扫描器 scan.py 输出契约一致；
#: onboard_date 为日期字符串，其余数值化）
_MARKET_COLS = (
    "price",
    "ret_1h",
    "ret_4h",
    "ret_24h",
    "ret_7d",
    "price_change_pct_24h",
    "quote_volume_24h",
    "funding_rate",
    "taker_buy_ratio_24h",
    "open_interest_value",
    "futures_premium_pct",
    "listing_days",
    "onboard_date",
)

#: microstructure.csv 的微观结构列（全部数值化；funding_trend 为 up/down/flat）
_MICRO_COLS = (
    "oi_change_24h",
    "oi_change_48h",
    "oi_value_change_24h",
    "ls_ratio_all",
    "ls_ratio_all_change_24h",
    "ls_ratio_top_acc",
    "ls_ratio_top_pos",
    "taker_bs_ratio",
    "funding_avg",
    "funding_trend",
)


def _strip_quote(symbol: str) -> str:
    """交易对 → 裸符号（AKEUSDT → AKE）：扫描器 CSV 均为 USDT 永续对，

    消费端（context/report）按裸符号 token 查询，key 命名空间须一致。
    """
    return symbol.removesuffix("USDT")


def _num(value: str) -> float | None:
    """CSV 字符串 → float；空/非法/NaN → None（UNKNOWN 纪律）。"""
    s = (value or "").strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return None if math.isnan(f) else f  # NaN → None


def _resolve_dir() -> Path | None:
    """数据目录：SR_SCAN_DIR 覆盖，默认 ~/Projects/python_projects/BinanceApi/data/research。"""
    d = os.environ.get(ENV_DIR) or str(DEFAULT_DIR)
    p = Path(d).expanduser()
    return p if p.is_dir() else None


def _latest_date(d: Path) -> str | None:
    """目录内最新 ``*_all.csv`` 的日期 YYYY-MM-DD；无匹配 → None。"""
    dates = []
    for p in d.glob("*_all.csv"):
        stem = p.stem  # 形如 "2026-08-16_all"
        if len(stem) >= 10 and stem[4] == "-" and stem[7] == "-":
            dates.append(stem[:10])
    return max(dates) if dates else None


def _read_market(d: Path, date: str) -> dict[str, dict]:
    """all.csv 全市场快照 → {symbol: {字段}}；文件缺失 → {}。"""
    out: dict[str, dict] = {}
    path = d / f"{date}_all.csv"
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            symbol = (row.get("symbol") or "").strip()
            if not symbol:
                continue
            entry: dict = {}
            for col in _MARKET_COLS:
                entry[col] = (
                    _num(row.get(col))
                    if col != "onboard_date"
                    else (row.get("onboard_date") or "").strip() or None
                )
            entry["boards"] = []
            out[_strip_quote(symbol)] = entry
    return out


def _boards_by_symbol(d: Path, date: str) -> dict[str, list[str]]:
    """movers.csv 同币多行 board 去重聚合（保持出现顺序）；文件缺失 → {}。"""
    out: dict[str, list[str]] = {}
    path = d / f"{date}_movers.csv"
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            symbol = (row.get("symbol") or "").strip()
            board = (row.get("board") or "").strip()
            if not symbol or not board:
                continue
            boards = out.setdefault(_strip_quote(symbol), [])
            if board not in boards:
                boards.append(board)
    return out


def _read_microstructure(d: Path, date: str) -> dict[str, dict]:
    """microstructure.csv 候选币微观结构 → {symbol: {字段}}；文件缺失 → {}。"""
    out: dict[str, dict] = {}
    path = d / f"{date}_microstructure.csv"
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            symbol = (row.get("symbol") or "").strip()
            if not symbol:
                continue
            entry: dict = {}
            for col in _MICRO_COLS:
                entry[col] = (
                    _num(row.get(col))
                    if col != "funding_trend"
                    else (row.get("funding_trend") or "").strip() or None
                )
            out[_strip_quote(symbol)] = entry
    return out


def load_snapshots(dir: str | None = None, date: str | None = None) -> dict:
    """扫描器快照 → ``{"date", "market": {symbol: {...}}, "microstructure": {symbol: {...}}}``。

    symbol 为裸符号（AKEUSDT → AKE），与消费端 token 命名空间一致；
    目录/文件缺失或解析异常 → ``{}``（绝不抛异常，调用方占位即可）；
    ``date`` 缺省取目录内最新 ``*_all.csv``；``SR_SCAN_DIR`` / ``SR_SCAN_DATE``
    环境变量为兜底配置。
    """
    try:
        d = Path(dir).expanduser() if dir else _resolve_dir()
        if d is None or not d.is_dir():
            return {}
        day = date or os.environ.get(ENV_DATE) or _latest_date(d)
        if not day:
            return {}
        market = _read_market(d, day)
        boards = _boards_by_symbol(d, day)
        for symbol, entry in market.items():
            entry["boards"] = boards.get(symbol, [])
        return {
            "date": day,
            "market": market,
            "microstructure": _read_microstructure(d, day),
        }
    except Exception:  # 只读辅助：任何异常视为快照不可用
        return {}
