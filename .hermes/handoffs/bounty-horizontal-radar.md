# Bounty Horizontal Radar Implementation Handoff

## Objective
Implement the approved plan at `.hermes/plans/2026-08-14_212522-bounty-horizontal-radar-plan.md` through the first approval boundary only: Phase 5 Task 5.3, a working local Global Explore prototype plus shadow-mode funnel.

## Durable decisions
- GPT-5.6 is orchestrator/final QA.
- GLM-4.7 handles narrow mechanical tasks when verified; GLM-5.2 handles complex migrations/logic.
- Daily Trends metadata, weekly root-social sweep, triggered deep reads.
- No automatic LLM per raw Trend.
- Raw candidates remain separate; related candidates may form evidence-backed topic families.
- Promotion is route-based and starts in shadow mode.
- Awareness enrichment is optional and later than the first approval boundary.
- Stop after Phase 5 Task 5.3 for Vincent review.

## Verified baseline
- Plan committed: `8f444ae`.
- Full suite: 189 passed on 2026-08-15.
- GLM-4.7 provider smoke test: passed.
- GLM-5.2 provider smoke test: passed.
- Worktree contains pre-existing runtime DB/image/log/tmp changes; never stage them.

## Current task
Phase 2 Task 2.1: preserve source-native nullable engagement metrics without zero-filling unsupported data.

## Completed milestones
- `21eeb19`: concise passing characterization test for the current accidental Explore thread/LLM cost path.
- `4b0ddd5`: additive exact prompt-usage telemetry, migration and API receipts.
- GPT full-suite rerun: 205 passed.
- Independent GLM-5.2 review of Phase 0 Task 0.2: PASS, no blockers.
- GLM-4.7 trial rejected for reasoning-heavy test-harness work; reserve it for fully specified mechanical edits.
- Phase 1 Tasks 1.1-1.2 implemented on top of `4b0ddd5` (uncommitted working tree, nothing staged):
  - New `social_scraper/discovery/scan_modes.py`: `ScanMode` enum (TRENDS_SNAPSHOT/ROOT_SWEEP/DEEP_READ/HORIZONTAL_SYNTHESIS/OPTIONAL_INTERPRETATION), immutable `ScanModePolicy` capability table, `coerce_scan_mode`/`policy_for`/`resolve_scan_mode`; `FEED_MODES` vs `RESEARCH_RUN_MODES` partition.
  - `stages.py`: `SCAN_MODE_STAGES` + `stages_for_scan_mode` mapping modes to collection stages (consistent with `DEPTH_STAGES`).
  - `conversation_gate.py`: `max_threads` threaded through `run_conversation_gate`/`gate_check_keyword` (0 = root-only).
  - `topdown.py`: `scan_all(mode=...)`; legacy `apply_gate=True`→ROOT_SWEEP, `False`/None→TRENDS_SNAPSHOT; deep modes raise ValueError; gate_only+snapshot rejected; `apply_conversation_gate` no longer runs the LLM reader (removed; research-runs own analysis); gate checks persist with analysis=None.
  - `apis/dashboard_api.py` `/discover`: explicit `mode` param (default trends_snapshot), legacy `gate` maps to root_sweep, 422 for unknown/deep modes and gate_only-on-snapshot.
  - `public/dashboard.js`: Explore feed explicitly requests `mode=trends_snapshot`; "confirmed checks only" checkbox requests `root_sweep` (still zero threads/LLM).
  - `handlers.py` untouched: explicit research-runs still deep-read threads and analyze (guarded by a test).
  - Tests: `tests/discovery/test_scan_modes.py` new (15), `test_explore_cost_path.py` flipped to zero-cost expectations (13, incl. distinct complete/partial/empty/failed persisted gate records + research-run still-analyzes guard). Full suite 232 passed. Task 1.3 (scheduler) NOT started.

- `0130279`: explicit scan-mode policies; Explore defaults to zero-cost Trends metadata; weekly root mode has zero thread/LLM calls; legacy Python/API positional and keyword contracts preserved.
- GPT independent full-suite rerun after compatibility hardening: 235 passed.
- Final independent GLM-5.2 review of Phase 1 Tasks 1.1-1.2: PASS, no blockers.

- `96fb5cf`: additive radar schedule/run ledger, replica-safe leases, active-subject enumeration and realistic pre-radar migration coverage.
- Task 1.3a independent gates: 261 full tests; simultaneous claim [0,1]; independent GLM-5.2 review PASS with 30 claim and 30 duplicate-completion races.

- `0108f7f`: environment/workspace-controlled radar reconciliation, one-at-a-time claimed execution, token-checked cancellation release and heartbeat renewal, exclusive discovery provenance, explicit partial coverage, and awaited app shutdown.
- Task 1.3b final gates: 40 scheduler tests; 181 relevant tests; 301 full tests; compile/diff/security checks clean. Independent review found 3 blockers, all fixed with regressions; final GLM code re-review verified all 3 closed.

## Next action
Implement Phase 2 Task 2.1 as a narrow test-first increment: extend canonical root/comment engagement with nullable source-native metrics, preserve raw provenance and timestamps, never zero-fill unsupported fields, update only connectors that already expose the data, run focused normalization tests and the full suite, then independent review and commit.

## 2026-08-15 rapid execution checkpoint
- HEAD remains `0108f7f`; unrelated runtime DB/image/log/tmp artifacts remain unstaged.
- Task 2.1 RED baseline reverified: 35 failed, 23 passed across the four focused test files; failures are the expected missing production behavior.
- Three non-overlapping GLM-5.2 workstreams authorized: Task 2.1 engagement/provenance, Task 2.2 root deduplication/propagation, and Task 2.3 engagement baselines.
- Workers must not stage, commit, push, or touch unrelated artifacts. Orchestrator will inspect diffs, integrate, run tests, and make narrow commits.
- If interrupted, inspect `git status`, preserve useful worker edits, and resume from the exact failing focused suite rather than restarting broad agents.
