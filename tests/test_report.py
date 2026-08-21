"""09 票验收：⑧ 工件派生（candidates）+ 信号快照/对比（snapshot/signal_diff）。

_build_artifacts/_build_signal_diff 纯函数直接单测；落盘经 build_report 走
tmp_path（monkeypatch.chdir 隔离，不污染工作区 reports/）。
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
    assert artifacts == {}
    assert not (run_dir / "candidates.json").exists()  # 失败工件不落盘


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
