"""入口：模式互斥二选一（--tokens/SR_TOKENS 手动 | 筛选器自动）→ 建图。

SR_MOCK=1 全离线回归；01 票骨架：手动/筛选均走 mock 固定候选，
筛选器完整实现见 03 票（screener.select_tokens）。
"""

from __future__ import annotations

import argparse
import os

from strategy_research.datasources.mock import mock_screening_candidates
from strategy_research.env import is_mock_mode
from strategy_research.graph import build_graph


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
    """tokens 解析：--tokens/SR_TOKENS 手动优先，否则筛选（01 票：mock 候选）。"""
    manual = args.tokens or os.environ.get("SR_TOKENS", "")
    if manual:
        tokens = [t.strip().upper() for t in manual.split(",") if t.strip()]
        return tokens, {"mode": "manual"}
    if is_mock_mode():
        cands = mock_screening_candidates()
    else:
        # 03 票：screener.select_tokens(rules, top_n)；未实现前显式失败，
        # 禁止真实模式静默回退 mock（规格十节纪律 6/9）
        raise NotImplementedError(
            "auto 模式筛选器未实现（03 票）；请设置 SR_MOCK=1 或使用 --tokens"
        )
    tokens = [c["symbol"] for c in cands]
    return tokens, {
        "mode": "auto",
        "rules": [],
        "candidates": cands,
    }


def main(argv: list[str] | None = None) -> dict:
    """主流程：解析 tokens → 建图 → invoke → 返回 meta（含 report_path）。"""
    args = parse_args(argv)
    tokens, screening = _resolve_tokens(args)
    meta = {"screening": screening}
    app = build_graph()
    result = app.invoke({"tokens": tokens, "meta": meta})
    return result["meta"]


if __name__ == "__main__":
    main()
