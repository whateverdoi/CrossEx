# AGENTS.md

This project is a crypto asset strategy research agent built from the implementation spec in `AgentArchitecture_Combined.md` — a 6-node parallel-branch LangGraph pipeline (deterministic signal layer + bull/bear evidence branches + deterministic evidence verification) that produces verifiable artifacts (evidence listing + signal snapshots). The system produces no directional conclusions — the user decides from the evidence.

## Agent skills

### Issue tracker

Issues live as local markdown files under `.scratch/<feature>/issues/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles mapped to default strings (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `CONTEXT.md` + `docs/adr/`. See `docs/agents/domain.md`.

## Conventions

- Domain vocabulary is defined in `CONTEXT.md` — use it, don't drift to synonyms
- Follow the global discipline in section 十 of `AgentArchitecture_Combined.md` (parallel-branch graph, batch never interrupted, UNKNOWN discipline, mock only in `SR_MOCK=1` mode)
- Every field you put into a per-token snapshot must have a consumer — rendered in the branch summary or listed in `report._SNAPSHOT_KEYS` (纪律 十-11「抓了必须喂」). `tests/test_data_coverage.py` turns red otherwise; hide a field deliberately by adding it to that test's `_HIDDEN` allowlist with a reason.
- Python env: `/home/lhh/pythonprojects/.venv`, package management via `uv pip`
