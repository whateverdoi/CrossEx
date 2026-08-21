"""11 票验收：全链路联调与冒烟。

mock 回归 + 异常注入 + 筛选器端到端走常规 pytest（conftest SR_MOCK=1 全离线）；
真实 API 冒烟（BTC/UNI 全链含 search_web、真实筛选器产候选）需显式
SR_SMOKE=1 开启——走真实网络，不进常规回归。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from strategy_research import env
from strategy_research.graph import build_graph
from strategy_research.main import main as cli_main
from strategy_research.screener import (
    ScreeningError,
    ScreenRule,
    select_tokens,
)

MOCK_TOKENS = ["BTC", "ETH", "SOL", "UNI", "DOGE", "XRP"]
_ACTIVE = {"BTC", "ETH", "SOL"}  # mock decide 映射：TRADE/long、TRADE/short、WATCH/long
requires_smoke = pytest.mark.skipif(
    os.environ.get("SR_SMOKE") != "1",
    reason="真实冒烟需显式 SR_SMOKE=1（走真实网络，不进常规回归）",
)


def _reset_counts() -> None:
    for k in env._MOCK_CALL_COUNTS:
        env._MOCK_CALL_COUNTS[k] = 0


def _latest() -> Path:
    return Path("reports") / "latest"


# ── 1. mock 端到端回归（SR_MOCK=1 全离线）────────────────


def test_mock_full_chain_report_and_cost_model(monkeypatch, tmp_path):
    """验收 1+7：mock 全链 6 token → 报告/工件生成 + 成本模型符合规格七节。

    规格七节：PASS 2 次（③+④）/ TRADE-WATCH 4 次（③④⑤⑥）；
    ≈2.6/token 是 20% 非 PASS 的估算，mock 50% 活跃 → 3.0，同一线性模型。
    """
    monkeypatch.chdir(tmp_path)
    _reset_counts()
    result = build_graph().invoke({"tokens": MOCK_TOKENS, "meta": {}})
    meta = result["meta"]
    assert meta["report_path"]  # 报告目录落盘
    run_dir = Path(meta["report_path"])
    for f in ("run.json", "overview.md", "candidates.json"):
        assert (run_dir / f).is_file()
    assert (_latest() / "snapshot.json").is_file()
    assert (_latest() / "signal_diff.json").is_file()
    # 成本模型：3 活跃 × 4 + 3 PASS × 2 = 18
    assert dict(env._MOCK_CALL_COUNTS) == {
        "facts": 6,
        "decide": 6,
        "challenge": 3,
        "rebuttals": 3,
    }
    assert meta["llm_calls"]["total"] == 18
    avg = meta["llm_calls"]["total"] / len(MOCK_TOKENS)
    assert avg == (2 * 3 + 4 * 3) / 6  # PASS 2 / 活跃 4 线性模型
    assert 2.0 <= avg <= 4.0  # 规格七节区间：PASS 下限 2，活跃上限 4


def test_mock_pass_tokens_zero_adversarial_calls(monkeypatch, tmp_path):
    """验收 1：PASS 透传路径零对抗调用（⑤⑥ 跳过，challenge/rebuttals 仅活跃 token）。"""
    monkeypatch.chdir(tmp_path)
    _reset_counts()
    result = build_graph().invoke({"tokens": MOCK_TOKENS, "meta": {}})
    for s in MOCK_TOKENS:
        row = result["results"][result["tokens"].index(s)]
        if s in _ACTIVE:
            assert row["decision"] in ("TRADE", "WATCH")
            assert row["rebuttals"]  # 活跃 token 有对抗
        else:
            assert row["decision"] == "PASS"
            assert row["rebuttals"] == []  # 透传零对抗
            assert (result["challenges"].get(s) or []) == []
    # 计数佐证：challenge/rebuttals 只来自 3 个活跃 token
    assert env._MOCK_CALL_COUNTS["challenge"] == 3
    assert env._MOCK_CALL_COUNTS["rebuttals"] == 3


# ── 2/3. 异常注入（断网）全链级 ──────────────────────────


def _boom(*args, **kwargs):
    raise RuntimeError("模拟断网：模型不可用")


def test_offline_challenge_batch_continues_report_generated(monkeypatch, tmp_path):
    """验收 3：断网跑 challenge 不中断批——该 token 挑战置空/降级，报告仍生成。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env, "get_llm", _boom)
    _reset_counts()
    result = build_graph().invoke({"tokens": MOCK_TOKENS, "meta": {}})
    # 批完成：全部 token 有 results 行 + 报告可生成
    assert len(result["results"]) == len(MOCK_TOKENS)
    assert result["meta"]["report_path"]
    assert (Path(result["meta"]["report_path"]) / "overview.md").is_file()
    assert (_latest() / "run.json").is_file()
    # 无报告级错误：兜底路径接管（fallback 已记录在行内）
    assert "report_error" not in result["meta"]


def test_offline_decide_fallback_pass_reported(monkeypatch, tmp_path):
    """验收 3 延伸：decide 断网 → PASS 兜底 + fallback 落盘（报告审计可见）。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env, "get_llm", _boom)
    build_graph().invoke({"tokens": ["BTC", "UNI"], "meta": {}})
    run = json.loads((_latest() / "run.json").read_text(encoding="utf-8"))
    for row in run["results"]:
        assert row["decision"] == "PASS"  # json_mode 兜底（规格 ④-3）
        assert row.get("fallback") == "json_mode"


# ── 4/5. 筛选器端到端（mock 部分 + 断网批终止）────────────


def test_cli_manual_tokens_skip_screening(monkeypatch, tmp_path):
    """验收 6：--tokens 手动模式与筛选互斥（select_tokens 不被调用），
    meta.screening.mode="manual" 落盘 run.json。"""
    monkeypatch.chdir(tmp_path)

    def _not_called(*a, **k):
        raise AssertionError("手动模式不应调用筛选器")

    monkeypatch.setattr("strategy_research.main.select_tokens", _not_called)
    meta = cli_main(["--tokens", "btc, ETH"])
    assert meta["screening"]["mode"] == "manual"
    assert meta["report_path"]
    run = json.loads((Path(meta["report_path"]) / "run.json").read_text(encoding="utf-8"))
    assert run["meta"]["tokens"] == ["BTC", "ETH"]
    assert run["meta"]["screening"]["mode"] == "manual"


def _not_called(*a, **k):
    raise AssertionError("手动模式不应调用筛选器")


def test_cli_sr_tokens_env_manual_mode(monkeypatch, tmp_path):
    """验收 6：SR_TOKENS 环境变量与 --tokens 同语义（手动优先，跳过筛选）。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SR_TOKENS", "SOL,XRP")
    monkeypatch.setattr("strategy_research.main.select_tokens", _not_called)
    meta = cli_main([])
    assert meta["screening"]["mode"] == "manual"
    run = json.loads((Path(meta["report_path"]) / "run.json").read_text(encoding="utf-8"))
    assert run["meta"]["tokens"] == ["SOL", "XRP"]


def test_cli_auto_screening_mock_lands_meta(monkeypatch, tmp_path):
    """验收 4（mock 等价）：自动筛选端到端——候选即 tokens、规则落盘 run.json。"""
    monkeypatch.chdir(tmp_path)
    meta = cli_main([])
    assert meta["screening"]["mode"] == "mock"  # mock 分支 mode（真实模式为 auto）
    assert meta["screening"]["rules"]  # 规则描述落盘
    assert meta["screening"]["candidates"]  # 候选带 reason
    run = json.loads((Path(meta["report_path"]) / "run.json").read_text(encoding="utf-8"))
    assert run["meta"]["tokens"] == [c["symbol"] for c in meta["screening"]["candidates"]]
    assert run["meta"]["screening"]["mode"] == "mock"
    assert run["meta"]["screening"]["rules"] == meta["screening"]["rules"]


def test_screening_failure_terminates_batch_at_entry(monkeypatch, tmp_path):
    """验收 5：筛选快照失败 → ScreeningError 批终止（全架构唯一允许终止的节点），
    报错信息明确；main 入口不吞异常。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SR_MOCK", "0")
    from strategy_research.datasources import binance, binance_futures

    monkeypatch.setattr(binance, "fetch_ticker_24h_all", lambda: None)
    monkeypatch.setattr(binance_futures, "fetch_listing_days", lambda: None)
    with pytest.raises(ScreeningError, match="批终止"):
        cli_main([])


def test_graph_internal_failures_never_escape(monkeypatch, tmp_path):
    """验收 5 对照：图内节点异常不冒泡出 invoke（ScreeningError 是唯一终止点）。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env, "get_llm", _boom)
    _reset_counts()
    result = build_graph().invoke({"tokens": MOCK_TOKENS, "meta": {}})
    assert result["meta"]["report_path"]  # 全链路断网仍完成批 + 报告
    assert "report_error" not in result["meta"]


# ── 真实 API 冒烟（SR_SMOKE=1 显式开启，走真实网络）──────


@requires_smoke
def test_real_api_smoke_btc_uni(monkeypatch, tmp_path):
    """验收 2：真实 API 全链 BTC/UNI（含 search_web 实测，六维查询模板出结果）。"""
    monkeypatch.setenv("SR_MOCK", "0")
    monkeypatch.chdir(tmp_path)
    result = build_graph().invoke({"tokens": ["BTC", "UNI"], "meta": {}})
    meta = result["meta"]
    assert meta["report_path"]
    run = json.loads((Path(meta["report_path"]) / "run.json").read_text(encoding="utf-8"))
    assert run["meta"]["mode"] == "live"
    assert len(run["results"]) == 2
    # search_web 实测：六维查询模板（project/team/social/adoption/unlock/catalyst）出结果
    from strategy_research.tools import search_web

    text = search_web.invoke({"query": "UNI adoption"})
    assert "不可用" not in text
    assert "无搜索结果" not in text


@requires_smoke
def test_real_screener_smoke_produces_candidates(monkeypatch, tmp_path):
    """验收 4：真实筛选 listing_days_lt(100)+volatility_24h(10) 产候选，
    候选带 reason 与指标。"""
    monkeypatch.setenv("SR_MOCK", "0")
    rules = [
        ScreenRule("filter", "listing_days_lt", {"max_days": 100}),
        ScreenRule("rank", "volatility_24h", {"top_n": 10}),
    ]
    result = select_tokens(rules, top_n=5)
    assert result.mode == "auto"
    assert result.candidates, "真实市场快照应产出候选"
    for c in result.candidates:
        assert "listing_days_lt" in c["reason"]
        assert "volatility_24h" in c["reason"]
        assert c["metrics"], "候选带指标"
    assert len(result.rules) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
