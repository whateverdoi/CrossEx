"""入口：模式互斥二选一（--tokens/SR_TOKENS 手动 | 筛选器自动）→ 建图。

SR_MOCK=1 全离线回归；筛选器 = screener.select_tokens（03 票：
Filter AND → Rank Top N 零 LLM；快照失败抛 ScreeningError 批终止）。
"""

from __future__ import annotations

import argparse
import os

from strategy_research import env
from strategy_research.graph import build_graph
from strategy_research.scanner_snapshot import ENV_AUTO, refresh_if_stale
from strategy_research.screener import (
    DEFAULT_MAX_PER_CATEGORY,
    DEFAULT_RULES,
    select_tokens,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="加密资产策略研究 Agent")
    p.add_argument(
        "--tokens",
        default=None,
        help="手动指定币种（逗号分隔），与自动筛选互斥；或设 SR_TOKENS",
    )
    p.add_argument("--top-n", type=int, default=10, help="筛选 Top N（auto 模式）")
    p.add_argument(
        "--max-per-category",
        type=int,
        default=DEFAULT_MAX_PER_CATEGORY,
        help=f"同一板块最多候选数（默认 {DEFAULT_MAX_PER_CATEGORY}，0 = 关闭板块约束）",
    )
    return p.parse_args(argv)


def _resolve_tokens(args: argparse.Namespace) -> tuple[list[str], dict]:
    """tokens 解析：--tokens/SR_TOKENS 手动优先，否则确定性筛选（互斥二选一）。"""
    manual = args.tokens or os.environ.get("SR_TOKENS", "")
    if manual:
        tokens = [t.strip().upper() for t in manual.split(",") if t.strip()]
        return tokens, {"mode": "manual"}
    result = select_tokens(
        DEFAULT_RULES, top_n=args.top_n, max_per_category=args.max_per_category
    )
    tokens = [c["symbol"] for c in result.candidates]
    return tokens, {
        "mode": result.mode,
        "rules": result.rules,
        "candidates": result.candidates,
    }


def main(argv: list[str] | None = None) -> dict:
    """主流程：扫描器快照新鲜度检查 → 解析 tokens → 建图 → invoke → meta。

    非 mock 模式且 SR_SCAN_AUTO 未关闭时，快照陈旧会自动补跑扫描器（子进程），
    用户无需手动操作；补跑结果写入 meta["scanner"]（run.json 落盘）。
    """
    env.reset_call_counts()  # 每次运行计数从 0 开始（llm_calls = 本次运行）
    args = parse_args(argv)
    tokens, screening = _resolve_tokens(args)
    meta: dict = {"screening": screening}
    if not env.is_mock_mode() and os.environ.get(ENV_AUTO, "1") != "0":
        meta["scanner"] = refresh_if_stale()
    app = build_graph()
    result = app.invoke({"tokens": tokens, "meta": meta})
    return result["meta"]


if __name__ == "__main__":
    main()
