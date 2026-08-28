"""入口：模式互斥二选一（--tokens/SR_TOKENS 手动 | 筛选器自动）→ 建图。

SR_MOCK=1 全离线回归；筛选器 = screener.select_tokens（03 票：
Filter AND → Rank Top N 零 LLM；快照失败抛 ScreeningError 批终止）。
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from strategy_research import env
from strategy_research.graph import build_graph
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


#: 08 票：live 报告保留份数（SR_KEEP_REPORTS 可调，0 = 不清理）
DEFAULT_KEEP_REPORTS = 30


def _cleanup_old_reports() -> None:
    """报告归档清理：仅保留最新 N 份 live 报告（reports/<ts>/ 目录）。

    SR_KEEP_REPORTS（默认 30，0 = 不清理）：目录名即 UTC 时间戳，按字典序
    删除最旧的溢出份。reports/mock/（mock 落盘产物）与 reports/latest/（软链）
    不在匹配范围，不受影响。
    """
    try:
        keep = int(os.environ.get("SR_KEEP_REPORTS", DEFAULT_KEEP_REPORTS))
    except ValueError:  # 非法值兜底为默认
        keep = DEFAULT_KEEP_REPORTS
    if keep <= 0:
        return
    dirs = sorted(p for p in Path("reports").glob("2*") if p.is_dir())
    for old in dirs[:-keep] if len(dirs) > keep else []:
        shutil.rmtree(old, ignore_errors=True)


def main(argv: list[str] | None = None) -> dict:
    """主流程：解析 tokens → 建图 → invoke → meta。"""
    env.reset_call_counts()  # 每次运行计数从 0 开始（llm_calls = 本次运行）
    args = parse_args(argv)
    tokens, screening = _resolve_tokens(args)
    meta: dict = {"screening": screening}
    app = build_graph()
    result = app.invoke({"tokens": tokens, "meta": meta})
    # 08 票：live 运行结束后归档清理（mock 回归跑批无副作用，不清理）
    if not env.is_mock_mode():
        _cleanup_old_reports()
    return result["meta"]


if __name__ == "__main__":
    main()
