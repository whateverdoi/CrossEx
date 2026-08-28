# Scout Report — Prediction-Quality Architectural Friction

> **本文件已过期（2026-08 证据分支重构前的审计快照），勿作现状依据。**
> 它引用的 `strategy_research/review.py`、`DECIDE_PROMPT`、`TokenAnalysis`、
> `tests/test_risk_check.py` 均已退役删除；第 1 条与文末「Start here」建议补的
> 决策/置信度校准回路，是 `docs/adr/0001` 刻意退役的对象——信号 vs 价格的历史
> 相关性改由图外只读工具 `strategy_research/lookback.py` 度量。
> 领域词汇表是**大写** `CONTEXT.md`（两者文件名只差大小写，勿混读）。
> 审计时点后的整改结果见 `.scratch/` 票面与 git log。

## 1. Decision-review feedback loop is NOT a feedback loop (BLOCKER)
- `review_past_decisions` (`review.py:167`) and its `_calibrate` stats are consumed ONLY by report rendering: `report.py:61`, `report.py:271-285`. Stats land in `run.json["decision_review"]` (`report.py:85`).
- Nothing feeds `hit_rate`/`by_confidence`/`by_decision` back into `DECIDE_PROMPT` (`schemas.py:355`) or any pipeline state. Grep for `stats|hit_rate|calibrat` shows no LLM-prompt or state consumption outside `review.py`/`report.py`. So LLM never sees its own calibration; hit-rate cannot influence future prompts.
- Docstring even claims "供 overview「决策复盘」节与后续 prompt 锚定" (`review.py:4-6`) — the "后续 prompt 锚定" is unrealized.

## 2. Duplication
- `_QUOTES` tuple duplicated verbatim: `nodes.py:34` and `review.py:25` (comment admits intentional copy to avoid coupling, `review.py:23-24`).
- Daily-return math duplicated two ways with **inconsistent semantics**:
  - `nodes.py:_ret(klines, days)` (`nodes.py:68-76`): `close[-1] vs close[-1-days]` (trailing window, uses latest bar).
  - `review.py:_returns(klines, run_ts_ms)` (`review.py:33-66`): picks a *closed* base bar by `open_time + 1d <= run_ts_ms`, then locates +1d/+7d closes by absolute time key. This is deliberately no-lookahead, but the two functions can disagree on the same klines (different base, different horizon alignment). Same domain (N-day return from daily closes) with divergent logic.
- `_funding_stats` mean/±10% trend logic (`nodes.py:113-139`) vs mock `datasources/mock.py:434` comment claims "同口径" — a manual-sync comment, not code reuse.
- `_SENTIMENT_NOTE` duplicated: `signals.py:15-18` and `datasources/mock.py:381-382` (verbatim string).

## 3. Cross-file manual-sync couplings (medium)
- `signals.py:15-18 _SENTIMENT_NOTE` hardcodes "解读规则见 DECIDE_PROMPT" — prose coupling; `test_signals.py:315` asserts the literal `"DECIDE_PROMPT"` string appears in the note. If `DECIDE_PROMPT`'s interpretation rules change, nothing enforces sync (no shared constant).
- `schemas.py:353-355` documents "DECIDE_PROMPT = ANALYZE 基线两处修改" — two hand-authored prompt strings must stay consistent; only a weak test asserts role markers (`test_nodes_llm.py:102-104`), not content parity.
- `nodes.py` and `mock.py` share field/naming conventions by convention only.

## 4. Summary builders test coverage (medium)
- `_build_challenge_summary` directly unit-tested (`test_nodes_llm.py:137,152`).
- `_build_facts_summary`/`_build_decide_summary`/`_build_finalize_summary` are NOT directly tested (grep `test_nodes_llm.py` finds no matches). `_facts_summary_lines` is tested (`test_nodes_llm.py:311,328`). The three wrappers only add header lines — low risk but unverified, and they must stay in sync with each LLM prompt's expected input shape.
- No test asserts prompt/rendering consistency (e.g. that `_build_decide_summary` emits a shape matching what `DECIDE_PROMPT` expects).

## 5. Deterministic signal layer never validated against outcomes (BLOCKER)
- `signals.py` functions (`valuation_ratios`/`momentum_score`/`divergence`/`sentiment_raw`) tested only for unit correctness (`test_signals.py:118-485`).
- Nothing tests that e.g. quadrant III predicts positive forward returns. `_calibrate` (`review.py:99`) buckets by `confidence` and `decision`, but **not by signal state** (quadrant/momentum/funding). So the deterministic layer is a black box with zero empirical validation — the exact loop the "research agent" spec wants remains unclosed.

## 6. Time horizon
- Horizon lives implicitly: `review.py:28 _REVIEW_HORIZON_DAYS = 7` and T+1d/T+7d in `_returns`. But `DECIDE_PROMPT` (`schemas.py:345`) says "不设价格锚点与有效期，失效由周期性重跑信号对比管理".
- `TokenAnalysis` (`schemas.py:145-188`) has NO horizon/validity/invalidation field (explicitly rejected in docstring, `schemas.py:146`). So the decision carries no expiry; the 7d review window is an evaluation-side assumption the LLM is told to ignore. Mismatch: the LLM may set expectations that the 7d evaluation can't match.

## 7. Test coverage gaps
- Thin: `test_main.py`=1, `test_graph.py`=3, `test_base.py`=5, `test_collect.py`=6. Nodes are tested only via LLM-mocked `test_nodes_llm.py` (14) + `test_risk_check.py` (17) + `test_e2e_smoke.py` (11); no direct deterministic node-function tests for decide/finalize nodes.
- Confidence calibration IS tested: `review.py:_calibrate` covered by `test_review.py:87-107` (bins/groups, empty), plus `_returns` `test_review.py:42-73` and `_hit`. Good.
- No test validates `_QUOTES` duplication consistency, prompt/rendering parity, or signal-outcome predictive power.

## Key files
- `strategy_research/signals.py` (deterministic layer), `strategy_research/review.py` (calibration, currently dead-ended), `strategy_research/schemas.py` (prompts + TokenAnalysis, no horizon), `strategy_research/nodes.py` (summary builders + duplicated `_ret`), `strategy_research/report.py:61,271` (only review consumer).

## Start here
`strategy_research/review.py` `_calibrate` + `report.py:271` — close the loop: surface `stats` (and per-signal breakdown) into `DECIDE_PROMPT`/state so LLM and downstream can use hit-rate, and add signal-state bucketing to `_calibrate` to validate quadrant/momentum against forward returns.
