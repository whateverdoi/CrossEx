"""09-10 票验收：⑧ 工件派生（candidates）+ 信号快照/对比 + overview 五节渲染。

09：_build_artifacts/_build_signal_diff 纯函数直接单测；落盘经 build_report 走
10：overview.md 五节（币种筛选/信号变化/对抗复审/候选清单/逐币分析）+ 成本统计
（llm_calls）+ 端到端五工件一致性（mock 全链）。均用 tmp_path 隔离。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research import report


def _row(
    symbol: str,
    decision: str = "WATCH",
    direction: str = "long",
    confidence: float = 0.5,
    **extra,
) -> dict:
    return {
        "symbol": symbol,
        "decision": decision,
        "direction": direction,
        "confidence": confidence,
        **extra,
    }


def _mk_state(tokens, results=None, facts=None, volumes=None) -> dict:
    """构造 report 消费的最小 state（market_data 只含 quote_volume_24h）。"""
    market_data = {}
    for s in tokens:
        market_data[s] = {"quote_volume_24h": {"value": (volumes or {}).get(s)}}
    return {
        "tokens": tokens,
        "results": results or [],
        "facts": facts or {},
        "market_data": market_data,
    }


def test_artifacts_full_fields():
    """验收：artifacts 五字段——分层/分级/策略/置信度/理由/主题统计。"""
    state = _mk_state(
        ["BTC", "ETH"],
        [
            {
                "symbol": "BTC",
                "decision": "TRADE",
                "direction": "long",
                "confidence": 0.7,
                "trade_structure": "分批建仓，回撤 5% 止损",
                "mispricing": "mc_tvl 低估",
                "catalyst": "TVL 增长",
            },
            {"symbol": "ETH", "decision": "PASS", "confidence": 0.0},
        ],
        facts={
            "BTC": [
                {"topic": "unlock"},
                {"topic": "unlock"},
                {"topic": "team"},
                {"topic": ""},
            ]
        },
        volumes={"BTC": 2e8, "ETH": 5e6},
    )
    a = report._build_artifacts(state)
    btc = a["BTC"]
    assert btc["liquidity_tier"] == "high"
    assert btc["opportunity_level"] == "A"
    assert btc["recommended_strategy"] == "long 分批建仓，回撤 5% 止损"
    assert btc["confidence"] == 0.7
    assert btc["rationale"] == "mc_tvl 低估 | catalyst: TVL 增长"
    assert btc["catalysts"] == {"unlock": 2, "team": 1, "unknown": 1}
    eth = a["ETH"]
    assert eth["liquidity_tier"] == "low"
    assert eth["opportunity_level"] == "D"
    assert eth["recommended_strategy"] == "UNKNOWN"  # 无 direction/trade_structure


def test_artifacts_tier_boundaries():
    """liquidity_tier 确定性分层：>=1e8 high / >=1e7 mid / 其余 low。"""
    state = _mk_state(
        ["A", "B", "C", "D"],
        [],
        volumes={"A": 1e8, "B": 1e7, "C": 1e7 - 1, "D": None},
    )
    a = report._build_artifacts(state)
    assert a["A"]["liquidity_tier"] == "high"
    assert a["B"]["liquidity_tier"] == "mid"
    assert a["C"]["liquidity_tier"] == "low"
    assert a["D"]["liquidity_tier"] == "low"  # 缺失 → low


def test_artifacts_downgraded_force_level_d():
    """验收：downgraded 标记强制 D 级（无论底层决策）。"""
    state = _mk_state(
        ["BTC"],
        [
            {
                "symbol": "BTC",
                "decision": "WATCH",
                "direction": "long",
                "confidence": 0.6,
                "downgraded": ["EV 不足: 动量/背离信号与多头决策矛盾"],
            }
        ],
    )
    assert report._build_artifacts(state)["BTC"]["opportunity_level"] == "D"


def test_artifacts_level_mapping():
    """机会分级映射：TRADE→A / WATCH→B / PASS→D / 未知→D。"""
    state = _mk_state(
        ["A", "B", "C", "D"],
        [
            {"symbol": "A", "decision": "TRADE"},
            {"symbol": "B", "decision": "WATCH"},
            {"symbol": "C", "decision": "PASS"},
            {"symbol": "D", "decision": "XXX"},
        ],
    )
    a = report._build_artifacts(state)
    assert [a[s]["opportunity_level"] for s in "ABCD"] == ["A", "B", "D", "D"]


def test_artifacts_empty_state_no_crash():
    """验收：无 facts/results → 工件永远可生成（空节渲染不报错）。"""
    state = {"tokens": ["BTC"], "results": [], "facts": {}, "market_data": {}}
    a = report._build_artifacts(state)
    assert a["BTC"]["catalysts"] == {}
    assert a["BTC"]["liquidity_tier"] == "low"
    assert a["BTC"]["opportunity_level"] == "D"


def test_snapshot_structure():
    """快照投影：results 轻量四字段（symbol/decision/direction/confidence）。"""
    state = _mk_state(["BTC"], [_row("BTC", "TRADE", "long", 0.7, extra=1)])
    snap = report._build_snapshot(state, "2026-08-21T00:00:00+00:00", "mock")
    assert snap["run_ts"] == "2026-08-21T00:00:00+00:00"
    assert snap["mode"] == "mock"
    assert snap["tokens"] == ["BTC"]
    assert snap["results"] == [
        {"symbol": "BTC", "decision": "TRADE", "direction": "long", "confidence": 0.7}
    ]


def test_signal_diff_four_actions():
    """验收：四种 action——new / hold / stop_short / stop_long + 其余→hold。"""
    prev = {
        "results": [
            {"symbol": "HOLD", "decision": "WATCH", "direction": "long"},
            {"symbol": "SS", "decision": "TRADE", "direction": "short"},
            {"symbol": "SL", "decision": "TRADE", "direction": "long"},
            {"symbol": "UP", "decision": "WATCH", "direction": "long"},
            {"symbol": "SAME", "decision": "TRADE", "direction": "long"},
        ]
    }
    cur = {
        "results": [
            {"symbol": "NEW", "decision": "WATCH", "direction": "long"},
            {"symbol": "HOLD", "decision": "WATCH", "direction": "long"},
            {"symbol": "SS", "decision": "WATCH", "direction": "short"},
            {"symbol": "SL", "decision": "PASS", "direction": ""},
            {"symbol": "UP", "decision": "TRADE", "direction": "long"},
            {"symbol": "SAME", "decision": "TRADE", "direction": "long"},
        ]
    }
    diff = report._build_signal_diff(prev, cur)
    assert diff["NEW"]["action"] == "new"  # prev 缺失
    assert diff["HOLD"]["action"] == "hold"  # prev==cur
    assert diff["SS"]["action"] == "stop_short"  # prev TRADE/short 反转
    assert diff["SL"]["action"] == "stop_long"  # prev TRADE/long 反转
    assert diff["UP"]["action"] == "hold"  # 其余 → hold（cur 展示新状态）
    assert diff["SAME"]["action"] == "hold"  # prev==cur 优先于 stop_long
    assert diff["SL"]["cur"]["decision"] == "PASS"
    assert diff["SL"]["prev"]["direction"] == "long"


def test_build_report_writes_artifacts_snapshot_diff(monkeypatch, tmp_path):
    """验收：candidates.json（reports/<ts> + latest 软链）+ snapshot/diff（latest）。"""
    monkeypatch.chdir(tmp_path)
    state = _mk_state(
        ["BTC"],
        [_row("BTC", "WATCH", "long", 0.5)],
        facts={"BTC": [{"topic": "unlock"}, {"topic": "team"}]},
        volumes={"BTC": 2e8},
    )
    run_dir, artifacts = report.build_report(state, {})
    cand = json.loads((run_dir / "candidates.json").read_text(encoding="utf-8"))
    assert cand == artifacts
    assert cand["BTC"]["liquidity_tier"] == "high"
    assert cand["BTC"]["catalysts"] == {"unlock": 1, "team": 1}
    assert (Path("reports") / "latest" / "snapshot.json").is_file()
    assert (Path("reports") / "latest" / "signal_diff.json").is_file()
    assert (Path("reports") / "latest" / "candidates.json").is_symlink()


def test_snapshot_overwrite_roundtrip_stop_long(monkeypatch, tmp_path):
    """验收：先读旧为 prev 再覆盖——二次运行 TRADE→WATCH 得 stop_long。"""
    monkeypatch.chdir(tmp_path)
    report.build_report(_mk_state(["BTC"], [_row("BTC", "TRADE", "long", 0.7)]), {})
    # 第二次运行：信号反转 → WATCH
    report.build_report(_mk_state(["BTC"], [_row("BTC", "WATCH", "long", 0.5)]), {})
    diff = json.loads(
        (Path("reports") / "latest" / "signal_diff.json").read_text(encoding="utf-8")
    )
    assert diff["BTC"]["action"] == "stop_long"
    assert diff["BTC"]["prev"]["decision"] == "TRADE"
    assert diff["BTC"]["cur"]["decision"] == "WATCH"
    snap = json.loads(
        (Path("reports") / "latest" / "snapshot.json").read_text(encoding="utf-8")
    )
    assert snap["results"][0]["decision"] == "WATCH"  # 快照已覆盖为新


def test_first_run_diff_all_new(monkeypatch, tmp_path):
    """首次运行无 prev：全 token action=new（规格：输出空对比节语义）。"""
    monkeypatch.chdir(tmp_path)
    state = _mk_state(["BTC", "ETH"], [_row("BTC", "WATCH"), _row("ETH", "PASS", "")])
    report.build_report(state, {})
    diff = json.loads(
        (Path("reports") / "latest" / "signal_diff.json").read_text(encoding="utf-8")
    )
    assert diff["BTC"]["action"] == "new"
    assert diff["ETH"]["action"] == "new"


def test_corrupt_snapshot_treated_as_no_prev(monkeypatch, tmp_path):
    """损坏快照（非法 UTF-8/坏 JSON）→ 视为无 prev（首次运行语义），不抛异常。"""
    monkeypatch.chdir(tmp_path)
    latest = Path("reports") / "latest"
    latest.mkdir(parents=True)
    (latest / "snapshot.json").write_bytes(b"\xff\xfe\x00broken")
    assert report._read_prev_snapshot() is None
    (latest / "snapshot.json").write_text("{not json", encoding="utf-8")
    assert report._read_prev_snapshot() is None


def test_artifacts_failure_records_report_error(monkeypatch, tmp_path):
    """失败语义：工件失败仅记 report_error，run.json/overview 不丢，批不中断。"""
    monkeypatch.chdir(tmp_path)

    def _boom(state):
        raise RuntimeError("工件计算失败")

    monkeypatch.setattr(report, "_build_artifacts", _boom)
    meta: dict = {}
    run_dir, artifacts = report.build_report(_mk_state(["BTC"], []), meta)
    assert "report_error" in meta
    assert "工件/快照落盘失败" in meta["report_error"]
    assert (run_dir / "run.json").is_file()
    assert (run_dir / "overview.md").is_file()
    assert artifacts == {"BTC": {}}  # 兜底形状与 write_report 异常路径一致
    assert not (run_dir / "candidates.json").exists()  # 失败工件不落盘


# ── 10 票：overview 五节渲染 + 成本统计 ──────────────────────


MOCK_TOKENS = ["BTC", "ETH", "SOL", "UNI", "DOGE", "XRP"]


def _full_state() -> dict:
    """五节全要素 state：results/facts/challenges/rebuttals/market_data。"""
    state = _mk_state(
        ["BTC", "ETH"],
        [
            _row(
                "BTC",
                "TRADE",
                "long",
                0.7,
                trade_structure="分批建仓",
                mispricing="mc_tvl 低估",
                catalyst="TVL 增长",
                downgraded=["杠杆降级"],
            ),
            _row("ETH", "PASS", "", 0.0),
        ],
        facts={
            "BTC": [
                {
                    "dimension": "fundamentals",
                    "topic": "unlock",
                    "claim": "解锁 1.2% 流通量",
                    "source": "mock",
                }
            ]
        },
        volumes={"BTC": 2e8, "ETH": 5e6},
    )
    state["challenges"] = {
        "BTC": [
            {
                "severity": "high",
                "stance": "aggressive",
                "claim": "回调风险",
                "evidence": "动量减弱",
            }
        ]
    }
    state["results"][0]["rebuttals"] = [
        {"outcome": "rejected", "response": "趋势未破位"}
    ]
    return state


def test_overview_five_sections(monkeypatch, tmp_path):
    """验收：overview.md 五节齐备（含 LLM 调用行）。"""
    monkeypatch.chdir(tmp_path)
    screening = {
        "mode": "auto",
        "rules": ["PriceChangePct>=10", "RankQuoteVolume top 2"],
        "candidates": [{"symbol": "BTC", "reason": "PriceChangePct>=10 | price_change_pct=12.5"}],
    }
    run_dir, _ = report.build_report(_full_state(), {"screening": screening})
    md = (run_dir / "overview.md").read_text(encoding="utf-8")
    for section in (
        "## 币种筛选",
        "## 信号变化（相对上一批）",
        "## 对抗复审",
        "## 候选清单",
        "## 逐币分析",
    ):
        assert section in md
    assert "LLM 调用" in md


def test_overview_screening_manual_and_auto(monkeypatch, tmp_path):
    """币种筛选节：manual 无规则/候选；auto 列规则 + 候选带 reason。"""
    monkeypatch.chdir(tmp_path)
    state = _mk_state(["BTC"], [_row("BTC")])
    run_dir, _ = report.build_report(state, {"screening": {"mode": "manual"}})
    md = (run_dir / "overview.md").read_text(encoding="utf-8")
    assert "- 模式：`manual`" in md
    assert "- 规则：" not in md
    assert "- 候选：" not in md
    screening = {
        "mode": "auto",
        "rules": ["PriceChangePct>=10", "RankQuoteVolume top 2"],
        "candidates": [
            {"symbol": "BTC", "reason": "PriceChangePct>=10 | price_change_pct=12.5"}
        ],
    }
    run_dir, _ = report.build_report(state, {"screening": screening})
    md = (run_dir / "overview.md").read_text(encoding="utf-8")
    assert "规则：PriceChangePct>=10、RankQuoteVolume top 2" in md
    assert "BTC：PriceChangePct>=10 | price_change_pct=12.5" in md


def test_overview_signal_diff_table_roundtrip(monkeypatch, tmp_path):
    """信号变化节：二次运行渲染对比表（prev/cur/action 对齐 signal_diff）。"""
    monkeypatch.chdir(tmp_path)
    report.build_report(_mk_state(["BTC"], [_row("BTC", "TRADE", "long", 0.7)]), {})
    run_dir, _ = report.build_report(_mk_state(["BTC"], [_row("BTC", "WATCH", "long", 0.5)]), {})
    md = (run_dir / "overview.md").read_text(encoding="utf-8")
    assert "| BTC | TRADE/long | WATCH/long | stop_long |" in md
    diff = json.loads(
        (Path("reports") / "latest" / "signal_diff.json").read_text(encoding="utf-8")
    )
    assert diff["BTC"]["action"] == "stop_long"  # 与落盘 diff 一致


def test_signal_diff_lines_empty_placeholder():
    """无 diff（快照失败）→ 空节占位不报错（规格十节纪律 2）。"""
    lines = report._signal_diff_lines({})
    assert "（首次运行或快照失败，无上一批对比）" in "\n".join(lines)


def test_overview_no_facts_challenges_empty_sections(monkeypatch, tmp_path):
    """验收：无 facts/challenges → 空节占位不报错（报告永远可生成）。"""
    monkeypatch.chdir(tmp_path)
    state = {"tokens": ["BTC"], "results": [], "facts": {}, "market_data": {}}
    run_dir, _ = report.build_report(state, {})
    md = (run_dir / "overview.md").read_text(encoding="utf-8")
    assert "（无——事实采证未产出）" in md
    assert "（无挑战" in md
    assert "- 挑战与反驳：（无）" in md
    assert "（本批无候选工件）" not in md  # artifacts 兜底有 token 行
    assert "| BTC | D | low | UNKNOWN |" in md


def test_overview_per_token_summary(monkeypatch, tmp_path):
    """逐币摘要：决策/direction/level/关键事实/挑战与反驳 + 降级行。"""
    monkeypatch.chdir(tmp_path)
    run_dir, _ = report.build_report(_full_state(), {})
    md = (run_dir / "overview.md").read_text(encoding="utf-8")
    # TRADE→A；downgraded 强制 D（验收：level 与决策/降级联动）
    assert "决策：**TRADE**（direction: long，置信度 0.7，level D）" in md
    assert "风控降级：杠杆降级" in md
    assert "[fundamentals/unlock] 解锁 1.2% 流通量（mock）" in md
    assert "挑战[high]：回调风险" in md
    assert "回应[rejected]：趋势未破位" in md
    assert "决策：**PASS**（direction: 未声明，置信度 0.0，level D）" in md


def test_llm_calls_mock_and_live(monkeypatch):
    """成本统计：mock 汇总分类计数 + total；live 全 0（未启用计数）。"""
    monkeypatch.setattr(
        report, "_MOCK_CALL_COUNTS", {"facts": 6, "decide": 6, "challenge": 3, "rebuttals": 3}
    )
    assert report._llm_calls() == {
        "facts": 6,
        "decide": 6,
        "challenge": 3,
        "rebuttals": 3,
        "total": 18,
    }
    monkeypatch.setattr(report, "is_mock_mode", lambda: False)
    assert report._llm_calls() == {
        "facts": 0,
        "decide": 0,
        "challenge": 0,
        "rebuttals": 0,
        "total": 0,
    }


def test_run_json_meta_llm_calls(monkeypatch, tmp_path):
    """run.json meta 含 llm_calls，且同步进节点 meta（规格 state.meta.llm_calls）。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        report, "_MOCK_CALL_COUNTS", {"facts": 2, "decide": 1, "challenge": 0, "rebuttals": 0}
    )
    meta: dict = {}
    run_dir, _ = report.build_report(_mk_state(["BTC"], [_row("BTC")]), meta)
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run["meta"]["llm_calls"]["total"] == 3
    assert meta["llm_calls"]["total"] == 3


def test_five_artifacts_latest(monkeypatch, tmp_path):
    """验收：五工件全在 reports/latest/（三软链 + 快照/对比两文件）。"""
    monkeypatch.chdir(tmp_path)
    report.build_report(_mk_state(["BTC"], [_row("BTC")]), {})
    latest = Path("reports") / "latest"
    for f in ("run.json", "overview.md", "candidates.json"):
        assert (latest / f).is_symlink(), f
    assert (latest / "snapshot.json").is_file()
    assert (latest / "signal_diff.json").is_file()


def test_end_to_end_five_artifacts_consistent(monkeypatch, tmp_path):
    """验收（端到端）：mock 全 6 token 跑完后五工件内容互相一致。

    level 与 candidates 对齐（overview 候选清单 == candidates.json）；
    signal_diff 与 snapshot 对照（首次全 new，diff.cur == snapshot.results）；
    成本统计 == mock 调用计数。
    """
    monkeypatch.chdir(tmp_path)
    from strategy_research import env
    from strategy_research.graph import build_graph

    for k in env._MOCK_CALL_COUNTS:
        env._MOCK_CALL_COUNTS[k] = 0
    result = build_graph().invoke({"tokens": MOCK_TOKENS, "meta": {}})
    latest = Path("reports") / "latest"
    run = json.loads((latest / "run.json").read_text(encoding="utf-8"))
    cand = json.loads((latest / "candidates.json").read_text(encoding="utf-8"))
    snap = json.loads((latest / "snapshot.json").read_text(encoding="utf-8"))
    diff = json.loads((latest / "signal_diff.json").read_text(encoding="utf-8"))
    md = (latest / "overview.md").read_text(encoding="utf-8")
    # 成本统计与计数一致（mock：6 facts + 6 decide + 3 challenge + 3 rebuttals）
    assert run["meta"]["llm_calls"]["total"] == sum(env._MOCK_CALL_COUNTS.values())
    assert run["meta"]["llm_calls"]["total"] == 18
    # 全量结果与 meta.tokens 对齐
    assert run["meta"]["tokens"] == MOCK_TOKENS
    assert [r["symbol"] for r in run["results"]] == MOCK_TOKENS
    # level 与 candidates 对齐：overview 候选清单行 == candidates.json
    for s in MOCK_TOKENS:
        level = cand[s]["opportunity_level"]
        assert level in {"A", "B", "D"}
        assert f"| {s} | {level} |" in md
    # diff 与 snapshot 对照：首次运行全 new，cur 侧 == 快照投影
    for s in MOCK_TOKENS:
        assert diff[s]["action"] == "new"
        i = MOCK_TOKENS.index(s)
        assert diff[s]["cur"]["decision"] == snap["results"][i]["decision"]
        assert diff[s]["cur"]["decision"] == run["results"][i]["decision"]
    assert result["meta"]["report_path"]  # 节点 meta 带报告路径
    assert "## 信号变化（相对上一批）" in md


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
