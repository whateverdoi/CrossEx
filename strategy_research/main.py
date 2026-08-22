"""入口：模式互斥二选一（--tokens/SR_TOKENS 手动 | 筛选器自动）→ 建图。

SR_MOCK=1 全离线回归；筛选器 = screener.select_tokens（03 票：
Filter AND → Rank Top N 零 LLM；快照失败抛 ScreeningError 批终止）。
"""

from __future__ import annotations

import argparse
import os

from strategy_research import env
from strategy_research.graph import build_graph
from strategy_research.screener import DEFAULT_RULES, select_tokens


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="加密资产策略研究 Agent")
    p.add_argument(
        "--tokens",
        default=None,
        help="手动指定币种（逗号分隔），与自动筛选互斥；或设 SR_TOKENS",
    )
    p.add_argument("--top-n", type=int, default=10, help="筛选 Top N（auto 模式）")
    return p.parse_args(argv)


def _resolve_tokens(args: argparse.Namespace) -> tuple[list[str], dict]:
    """tokens 解析：--tokens/SR_TOKENS 手动优先，否则确定性筛选（互斥二选一）。"""
    manual = args.tokens or os.environ.get("SR_TOKENS", "")
    if manual:
        tokens = [t.strip().upper() for t in manual.split(",") if t.strip()]
        return tokens, {"mode": "manual"}
    result = select_tokens(DEFAULT_RULES, top_n=args.top_n)
    tokens = [c["symbol"] for c in result.candidates]
    return tokens, {
        "mode": result.mode,
        "rules": result.rules,
        "candidates": result.candidates,
    }


def main(argv: list[str] | None = None) -> dict:
    """主流程：解析 tokens → 建图 → invoke → 返回 meta（含 report_path）。"""
    env.reset_call_counts()  # 每次运行计数从 0 开始（llm_calls = 本次运行）
    args = parse_args(argv)
    tokens, screening = _resolve_tokens(args)
    meta = {"screening": screening}
    app = build_graph()
    result = app.invoke({"tokens": tokens, "meta": meta})
    return result["meta"]


if __name__ == "__main__":
    main()
