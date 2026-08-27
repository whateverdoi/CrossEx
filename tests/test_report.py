"""09-10/04 票验收：④ 工件派生（candidates）+ 信号快照/对比 + 证据 md 渲染。

09：_build_artifacts/_build_signal_diff 纯函数直接单测（04 票改信号快照语义）；
04：证据 md（总览表 + 每 token 做多/做空表 + 剔除附录）经 _render_evidence_md
纯函数断言（零落盘）；快照覆盖/首运行改走 _write_snapshot_and_diff 轻量落盘
（不建时间戳目录）；落盘收敛为 3 个真用例（build_report 契约/错误路径/run.json
结构）；端到端工件一致性并入 test_e2e_smoke 唯一全链 mock。均用 tmp_path 隔离。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research import report


def _mk_state(tokens, volumes=None) -> dict:
    """构造 report 消费的最小 state（market_data 只含 quote_volume_24h）。"""
    market_data = {}
    for s in tokens:
        market_data[s] = {"quote_volume_24h": {"value": (volumes or {}).get(s)}}
    return {
        "tokens": tokens,
        "market_data": market_data,
    }


def test_build_report_writes_artifacts_snapshot_diff(monkeypatch, tmp_path):
    """验收（04 票）：candidates.json 仅候选列表（reports/<ts> + latest 软链）
    + 信号快照/diff（latest）。"""
    monkeypatch.chdir(tmp_path)
    state = _evidence_state()
    run_dir, artifacts = report.build_report(state, {})
    cand = json.loads((run_dir / "candidates.json").read_text(encoding="utf-8"))
    assert cand == artifacts == {"candidates": ["BTC", "ETH"]}
    assert (Path("reports") / "latest" / "snapshot.json").is_file()
    assert (Path("reports") / "latest" / "signal_diff.json").is_file()
    # 三软链 + 快照/对比两文件（原 overview.md 软链退役）
    for f in ("run.json", "evidence.md", "candidates.json"):
        assert (Path("reports") / "latest" / f).is_symlink(), f


def test_snapshot_disk_roundtrip(monkeypatch, tmp_path):
    """落盘语义（04 票信号快照）：首运行全 new → 二次信号变化得 changed（快照覆盖
    为最新）→ 损坏快照视为无 prev（不抛异常）。轻量走 _write_snapshot_and_diff。"""
    monkeypatch.chdir(tmp_path)
    report._write_snapshot_and_diff(_evidence_state(), "t0", "mock")
    diff = json.loads(
        (Path("reports") / "latest" / "signal_diff.json").read_text(encoding="utf-8")
    )
    assert diff["BTC"]["action"] == "new"
    assert diff["ETH"]["action"] == "new"

    # 第二次运行：BTC momentum 6.25 → 5.0（信号变化）→ changed；ETH 不变 → unchanged
    cur = _evidence_state()
    cur["signals"]["BTC"]["momentum"]["value"] = 5.0
    report._write_snapshot_and_diff(cur, "t2", "mock")
    diff = json.loads(
        (Path("reports") / "latest" / "signal_diff.json").read_text(encoding="utf-8")
    )
    assert diff["BTC"]["action"] == "changed"
    assert diff["BTC"]["prev"]["momentum"] == 6.25
    assert diff["BTC"]["cur"]["momentum"] == 5.0
    assert diff["ETH"]["action"] == "unchanged"
    snap = json.loads(
        (Path("reports") / "latest" / "snapshot.json").read_text(encoding="utf-8")
    )
    assert snap["signals"]["BTC"]["momentum"] == 5.0  # 快照已覆盖为新

    # 损坏快照（非法 UTF-8/坏 JSON）→ 视为无 prev，不抛异常
    latest = Path("reports") / "latest"
    (latest / "snapshot.json").write_bytes(b"\xff\xfe\x00broken")
    assert report._read_prev_snapshot() is None
    (latest / "snapshot.json").write_text("{not json", encoding="utf-8")
    assert report._read_prev_snapshot() is None


def test_artifacts_failure_records_report_error(monkeypatch, tmp_path):
    """失败语义：工件失败仅记 report_error，run.json/evidence.md 不丢，批不中断。"""
    monkeypatch.chdir(tmp_path)

    def _boom(state):
        raise RuntimeError("工件计算失败")

    monkeypatch.setattr(report, "_build_artifacts", _boom)
    meta: dict = {}
    run_dir, artifacts = report.build_report(_mk_state(["BTC"]), meta)
    assert "report_error" in meta
    assert "工件/快照落盘失败" in meta["report_error"]
    assert (run_dir / "run.json").is_file()
    assert (run_dir / "evidence.md").is_file()
    assert artifacts == {"candidates": []}  # 兜底形状与 write_report 异常路径一致
    assert not (run_dir / "candidates.json").exists()  # 失败工件不落盘


# ── 10 票：成本统计 ──────────────────────────────────────


def test_llm_calls_counting(monkeypatch) -> None:
    """成本统计（03 票改造）：mock 汇总假模型计数 + total；live 汇总 callback 计数；handler 按 key 累加。"""
    monkeypatch.setattr(report, "_MOCK_CALL_COUNTS", {"bull": 6, "bear": 6})
    assert report._llm_calls() == {"bull": 6, "bear": 6, "total": 12}
    monkeypatch.setattr(report, "is_mock_mode", lambda: False)
    monkeypatch.setattr(report, "LIVE_CALL_COUNTS", {"bull": 2, "bear": 2})
    assert report._llm_calls() == {"bull": 2, "bear": 2, "total": 4}

    from strategy_research import env

    env.LIVE_CALL_COUNTS["bull"] = 0
    h = env.live_call_counter("bull")
    h.on_llm_start({}, [])
    h.on_llm_start({}, [])
    assert env.LIVE_CALL_COUNTS["bull"] == 2


def test_run_json_meta_llm_calls(monkeypatch, tmp_path):
    """run.json meta 含 llm_calls，且同步进节点 meta（规格 state.meta.llm_calls）。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(report, "_MOCK_CALL_COUNTS", {"bull": 2, "bear": 1})
    meta: dict = {}
    run_dir, _ = report.build_report(_mk_state(["BTC"]), meta)
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run["meta"]["llm_calls"]["total"] == 3
    assert meta["llm_calls"]["total"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-q"])


# ── 04 票：证据 md + 信号快照 + 工件简化 ──────────────────────


#: 与 report._SNAPSHOT_KEYS 同序全量键（09 票：funding_z/beta_7d/alpha_7d 退役，
#: 横截面费率分位 + 交易结构进快照；社交四键随「抓了必须喂」进快照）
#: ——用于「signals 层失败 → 全 None」契约断言
_ALL_SIGNAL_KEYS = [
    "momentum",
    "quadrant",
    "funding_pctile_90d",
    "oi_price_divergence",
    "liq_imbalance",
    "funding_x_pctile",
    "funding_interval_hours",
    "funding_carry_7d_pct",
    "funding_carry_30d_pct",
    "spread_pct",
    "bid_depth_usd_2pct",
    "ask_depth_usd_2pct",
    "depth_band_state",
    "rv_7d",
    "rv_30d",
    "drawdown_1y",
    "vol_adj_ret_7d",
    "vol_adj_ret_30d",
    "beta_30d",
    "alpha_30d",
    "turnover",
    "tvl_trend_30d",
    "fees_trend_30d",
    "stablecoin_change_30d",
    "social_heat_trend",
    "social_heat_window",
    "post_frequency",
    "social_price_divergence",
]


def _evidence_state() -> dict:
    """证据体系 state：evidence（bull/bear_case）+ rejected_evidence + 信号数据。"""
    state = _mk_state(["BTC", "ETH"])
    state["evidence"] = {
        "BTC": {
            "bull_case": [
                {
                    "claim": "动量分 6.25 处于增长区",
                    "basis": {
                        "domain": "signals",
                        "field": "momentum.value",
                        "value": "6.25",
                    },
                    "source": "signals",
                },
                {
                    "claim": "资金费率 0.01% 偏低",
                    "basis": {
                        "domain": "signals",
                        "field": "sentiment.components.funding",
                        "value": "0.0001",
                    },
                    "source": "signals",
                },
                {
                    "claim": "24h 成交额 2 亿美元",
                    "basis": {
                        "domain": "market_data",
                        "field": "quote_volume_24h.value",
                        "value": "200000000",
                    },
                    "source": "market_data",
                },
            ],
            "bear_case": [
                {
                    "claim": "taker 买卖比 1.0 无买盘优势",
                    "basis": {
                        "domain": "signals",
                        "field": "sentiment.components.taker_bs_ratio_1h",
                        "value": "1.0",
                    },
                    "source": "signals",
                }
            ],
        },
        "ETH": {"bull_case": [], "bear_case": []},
    }
    state["rejected_evidence"] = {
        "BTC": [
            {"claim": "TVL 上升", "reason": "字段不存在: tvl.value"},
            {"claim": "OI 下降", "reason": "值不一致: 引用 1 vs 快照 0"},
        ]
    }
    state["signals"] = {
        "BTC": {
            "momentum": {"value": 6.25},
            "divergence": {"value": {"quadrant": "III"}},
        },
        "ETH": {"error": "模拟信号层失败"},
    }
    state["fundamental_data"] = {
        "BTC": {
            "tvl_trend_30d": {"value": "rising"},
            "fees_trend_30d": {"value": "flat"},
            "stablecoin_change_30d": {"value": 5.0},
        },
        "ETH": {
            "tvl_trend_30d": {"value": None},
            "fees_trend_30d": {"value": None},
            "stablecoin_change_30d": {"value": None},
        },
    }
    state["market_data"] = {
        "BTC": {
            "price": {"value": 70000.0},
            "funding_pctile_90d": {"value": 42.0},
            "quote_volume_24h": {"value": 2e8},
            "funding_x_pctile": {"value": 96.0},
            "funding_interval_hours": {"value": 8.0},
            "funding_carry_7d_pct": {"value": 0.21},
            "funding_carry_30d_pct": {"value": 0.9},
            "spread_pct": {"value": 0.03},
            "bid_depth_usd_2pct": {"value": 1_200_000.0},
            "ask_depth_usd_2pct": {"value": 950_000.0},
            "depth_band_state": {"value": "band_complete"},
        },
        "ETH": {"funding_pctile_90d": {"value": None}},
    }
    state["microstructure_data"] = {
        "BTC": {"oi_price_divergence": {"value": {"label": "negative"}}},
        "ETH": {"oi_price_divergence": {"value": {}}},
    }
    state["social_data"] = {
        "BTC": {
            "social_heat_trend": {"value": 217.0},
            "social_heat_window": {"value": "recent5_vs_prior_median"},
            "post_frequency": {"value": 1.0},
            "social_price_divergence": {
                "value": {"label": "confirm_long", "note": "价涨社区热度升"}
            },
        },
        "ETH": {"social_price_divergence": {"value": {}}},
    }
    state[
        "scanner_snapshot"
    ] = {  # 07 票：快照日期透传与证据节标注（防旧快照误读为实时）
        "date": "2026-08-16",
        "market": {"BTC": {"price": 69000.0, "ret_24h": -1.2}},
        "microstructure": {"BTC": {"ls_ratio_all": 0.9}},
    }
    return state


_ALL_NONE = {k: None for k in _ALL_SIGNAL_KEYS}


def _render_evidence(state, meta=None) -> str:
    """纯函数渲染证据 md（替代 build_report 落盘：零 I/O）。"""
    run = {
        "meta": {
            "mode": "mock",
            "tokens": state["tokens"],
            "screening": (meta or {}).get("screening") or {"mode": "manual"},
            "run_ts": "2026-08-21T00:00:00+00:00",
            "node_order": [],
            "llm_calls": (meta or {}).get("llm_calls") or {"total": 12},
        },
    }
    return report._render_evidence_md(state, run)


def test_evidence_md_sections():
    """验收：证据 md——总览表（token/价格/多通过/空通过/剔除/数据域覆盖）
    + 每 token 做多/做空表 + 聚合剔除附录。"""
    md = _render_evidence(_evidence_state())
    assert "运行模式：`mock`" in md
    assert "tokens：BTC, ETH" in md
    assert "LLM 调用：12" in md
    # 总览表（09 票：列名不再隐含方向，新增剔除数列）
    assert "| token | 最新价格 | 做多通过 | 做空通过 | 剔除 | 数据域覆盖 |" in md
    assert "| BTC | 70000 | 3 | 1 | 2 | signals, market_data |" in md
    assert "| ETH | — | 0 | 0 | 0 | — |" in md
    assert "不构成多空结论" in md  # 09 票：计数不是方向强度
    # 每 token 节：做多/做空两张表（# | claim | basis | source）
    assert "## BTC" in md
    assert "- 扫描器快照日期：2026-08-16" in md  # 07 票：快照日期标注
    assert "### 做多证据" in md
    assert "| # | claim | basis | source |" in md
    assert (
        "| 1 | 动量分 6.25 处于增长区 | signals.momentum.value = 6.25 | signals |" in md
    )
    assert (
        "| 3 | 24h 成交额 2 亿美元 | market_data.quote_volume_24h.value = 200000000 | market_data |"
        in md
    )
    assert "### 做空证据" in md
    assert (
        "| 1 | taker 买卖比 1.0 无买盘优势 | signals.sentiment.components.taker_bs_ratio_1h = 1.0 | signals |"
        in md
    )
    # 空证据占位 + 剔除附录（09 票聚合：claim 去空白归一 + 条数列）
    assert "## ETH" in md
    assert "（无做多证据）" in md
    assert "（无做空证据）" in md
    assert "## 剔除记录" in md
    assert "共 2 条剔除，聚合为 2 种" in md
    assert "| token | claim | 原因 | 条数 |" in md
    assert "| BTC | TVL上升 | 字段不存在: tvl.value | 1 |" in md
    assert "| BTC | OI下降 | 值不一致: 引用 1 vs 快照 0 | 1 |" in md


def test_rejected_lines_aggregate_same_failure_mode():
    """09 票聚合：同一 claim × 同一原因的重复剔除收敛为一行，条数即严重度。

    实测一次运行 172 条剔除里 168 条是同一失败模式，逐条罗列把最有诊断价值的
    章节埋成噪音；claim 空白归一让「OI 下降」与「OI下降」算同一种。
    """
    same = {"claim": "OI 下降", "reason": "值不一致: 引用 1 vs 快照 0"}
    rejected = {
        "BTC": [
            same,
            dict(same),
            {"claim": "OI下降", "reason": "值不一致: 引用 1 vs 快照 0"},  # 空白变体
            {"claim": "TVL 上升", "reason": "字段不存在: tvl.value"},
        ]
    }
    lines = report._rejected_lines(rejected, ["BTC"])
    body = "\n".join(lines)
    assert "共 4 条剔除，聚合为 2 种" in body
    assert "| BTC | OI下降 | 值不一致: 引用 1 vs 快照 0 | 3 |" in body
    assert "| BTC | TVL上升 | 字段不存在: tvl.value | 1 |" in body
    assert sum(1 for ln in lines if ln.startswith("| BTC |")) == 2  # 聚合后只剩两行


def test_evidence_md_empty_state():
    """空态：无证据/无剔除 → 占位不报错，报告仍生成。"""
    md = _render_evidence(_mk_state(["BTC"]))
    assert "| BTC | — | 0 | 0 | 0 | — |" in md
    assert "（无做多证据）" in md
    assert "（无做空证据）" in md
    assert "（本批无剔除记录）" in md


def test_signal_snapshot_build():
    """验收：snapshot.json 为信号快照——signals 投影（信号 + 交易结构 + 派生 + 趋势），无 decision 语义。"""
    snap = report._build_snapshot(_evidence_state(), "t0", "mock")
    assert snap["run_ts"] == "t0"
    assert snap["mode"] == "mock"
    assert snap["tokens"] == ["BTC", "ETH"]
    assert "results" not in snap
    assert _ALL_SIGNAL_KEYS == list(report._SNAPSHOT_KEYS)  # 键清单与本测试同源
    btc = snap["signals"]["BTC"]
    assert btc["quadrant"] == "III"
    assert btc["momentum"] == 6.25
    assert btc["funding_pctile_90d"] == 42.0
    assert btc["oi_price_divergence"] == "negative"
    assert btc["tvl_trend_30d"] == "rising"
    assert btc["fees_trend_30d"] == "flat"
    assert btc["stablecoin_change_30d"] == 5.0
    # 09 票：横截面费率分位 + 交易结构（可执行性）进快照；深度档位是字符串档
    assert btc["funding_x_pctile"] == 96.0
    assert btc["funding_carry_30d_pct"] == 0.9
    assert btc["spread_pct"] == 0.03
    assert btc["bid_depth_usd_2pct"] == 1_200_000.0
    assert btc["depth_band_state"] == "band_complete"
    # 社交四键进快照：数值档直取数值，复合值只取 label（同 oi_price_divergence）
    assert btc["social_heat_trend"] == 217.0
    assert btc["social_heat_window"] == "recent5_vs_prior_median"
    assert btc["post_frequency"] == 1.0
    assert btc["social_price_divergence"] == "confirm_long"
    # 退役键不再出现（funding_z 量纲失真、beta_7d/alpha_7d n=7 噪声）
    assert not {"funding_z", "beta_7d", "alpha_7d"} & set(btc)
    # signals 层失败 → signals 派生键全 None；趋势/社交键从各自域直读
    # （本例 ETH 无社交数据，故投影为全 None）
    assert snap["signals"]["ETH"] == _ALL_NONE


def test_signal_diff_actions():
    """验收：diff 信号对比三 action（new/changed/unchanged）+ 物性变化字段清单。"""
    base = {k: 1.0 for k in _ALL_SIGNAL_KEYS}
    base.update({"momentum": 6.25, "quadrant": "III", "tvl_trend_30d": "rising"})
    prev = {
        "signals": {"SAME": dict(base), "CHG": dict(base)}
    }  # NEW 只在 cur（prev 缺失语义）
    cur = {
        "signals": {
            "NEW": dict(base),
            "SAME": dict(base),
            "CHG": {**base, "momentum": 5.0},  # 单字段变化
        }
    }
    diff = report._build_signal_diff(prev, cur)
    assert diff["NEW"]["action"] == "new"  # prev 缺失
    assert diff["NEW"]["changed_fields"] == []
    assert diff["SAME"]["action"] == "unchanged"
    assert diff["SAME"]["changed_fields"] == []
    assert diff["CHG"]["action"] == "changed"
    assert diff["CHG"]["changed_fields"] == ["momentum"]
    assert {d["action"] for d in diff.values()} <= {"new", "changed", "unchanged"}
    assert diff["CHG"]["prev"]["momentum"] == 6.25
    assert diff["CHG"]["cur"]["momentum"] == 5.0
    # 首运行：prev=None → 全 new
    first = report._build_signal_diff(None, cur)
    assert all(d["action"] == "new" for d in first.values())


def test_field_changed_materiality():
    """09 票物性阈值：浮点噪音不算变化，跨零与数据可用性变化算。

    stop_long/stop_short 退役后这条链是系统唯一的失效机制——旧裸相等比较在
    两次相隔 15 分钟的真实运行里把 5/5 标的全判 changed（实测最大相对变化
    <0.1%），阈值饱和等于没有失效机制。
    """
    changed = report._field_changed
    assert not changed(6.25, 6.25)
    assert not changed(70000.0, 70000.0 - 3.0)  # 相对 0.004% → 噪音
    assert not changed(0.00026684, 0.00026684 + 1e-12)  # 近零浮点残差
    assert changed(6.25, 5.0)  # 相对 20% → 物性
    assert changed(-0.5, 0.5)  # 跨零（α 由负转正）不是噪音
    assert changed(1.0, None) and changed(None, 1.0)  # 可用性事件
    assert not changed(None, None)
    assert changed("rising", "flat") and not changed("III", "III")  # 分类字段相等
    assert changed(True, False)  # 分类位翻转算变化（快照实际不产 bool：_scalar 已过滤）


def test_candidates_simplified():
    """验收：candidates.json 仅候选列表（机会分级/流动性分层退役，spec D8）。"""
    a = report._build_artifacts(_mk_state(["BTC", "ETH"]))
    assert a == {"candidates": ["BTC", "ETH"]}


def test_run_json_evidence_and_snapshot(monkeypatch, tmp_path):
    """验收：run.json 含证据清单（evidence/rejected_evidence）+ 数据快照投影 + 信号快照。"""
    monkeypatch.chdir(tmp_path)
    run_dir, _ = report.build_report(_evidence_state(), {})
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run["evidence"]["BTC"]["bull_case"][0]["claim"].startswith("动量分")
    assert run["rejected_evidence"]["BTC"][0]["reason"].startswith("字段不存在")
    assert (
        run["data_snapshot"]["BTC"]["market_data"]["quote_volume_24h"]["value"] == 2e8
    )
    assert (
        run["data_snapshot"]["BTC"]["fundamental_data"]["tvl_trend_30d"]["value"]
        == "rising"
    )
    assert run["data_snapshot"]["ETH"]["signals"]["error"] == "模拟信号层失败"
    assert (
        run["data_snapshot"]["BTC"]["scanner_snapshot"]["date"] == "2026-08-16"
    )  # 07 票：快照日期透传
    assert run["signals"]["BTC"]["momentum"] == 6.25
    assert run["signals"]["BTC"]["quadrant"] == "III"
    assert run["signals"]["BTC"]["tvl_trend_30d"] == "rising"
    assert run["signals"]["ETH"] == _ALL_NONE
