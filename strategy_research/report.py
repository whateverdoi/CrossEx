"""报告落盘：overview.md + run.json（+ candidates/snapshot/diff 09-10 票）。

渲染异常仅记 meta 不中断批（规格六节错误矩阵）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from strategy_research.env import is_mock_mode


def _run_dir() -> Path:
    """reports/<ts>/ 运行目录 + reports/latest/ 软链目标（微秒级防同秒碰撞）。"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ%f")
    return Path("reports") / ts


def build_report(state: dict, meta: dict) -> Path:
    """落盘 run.json + overview.md，返回报告目录（08 票后由节点调用）。"""
    run_dir = _run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)

    mode = "mock" if is_mock_mode() else "live"
    run = {
        "meta": {
            "mode": mode,
            "tokens": state["tokens"],
            "screening": meta.get("screening") or {"mode": "manual"},
            "run_ts": datetime.now(timezone.utc).isoformat(),
            "node_order": meta.get("node_order") or [],
        },
        "results": state.get("results") or [],
    }
    (run_dir / "run.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    overview = _render_overview(state, run, meta)
    (run_dir / "overview.md").write_text(overview, encoding="utf-8")

    latest = Path("reports") / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    for f in ("run.json", "overview.md"):
        dest = latest / f
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        # 软链目标相对 latest/ 解析：reports/latest/../<ts>/<f>
        dest.symlink_to(Path("..") / run_dir.name / f)
    return run_dir


def _render_overview(state: dict, run: dict, meta: dict) -> str:
    """overview.md 骨架：币种筛选节 + 每 token 摘要（空节渲染不报错）。"""
    lines = [
        "# 策略研究概览",
        "",
        f"- 运行模式：`{run['meta']['mode']}`",
        f"- 时间：{run['meta']['run_ts']}",
        f"- tokens：{', '.join(state['tokens'])}",
        "",
        "## 币种筛选",
        "",
        f"- 模式：`{run['meta']['screening'].get('mode', 'unknown')}`",
        "",
        "## 逐币分析",
        "",
    ]
    for s in state["tokens"]:
        lines.append(f"### {s}")
        lines.append("")
        lines.append("- 决策：-（待 07 票实现）")
        lines.append("")
    return "\n".join(lines)
