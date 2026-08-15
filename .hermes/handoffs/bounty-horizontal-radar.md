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
Approval boundary reached through Phase 5 Task 5.3. Await Vincent's product review before extending the roadmap.

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

## 2026-08-15 Phase 2-4.1 checkpoint
- `318e544`: Phase 2 Tasks 2.1-2.3 complete; integrated suite was 359 passed.
- Phase 3 Tasks 3.1-3.2 implemented: additive topic-family/member/edge persistence, strict immutable evidence, deterministic relationship builders, correlational-only no-merge rule, and conservative family planning.
- Phase 3 Explore projection implemented: cited explanations only, corroboration separated from propagation, source/period-gated volume, versioned lifecycle rules, and pure zero-collection Perspective transforms using existing lens evaluation.
- Task 3.3 measured spike: `fastembed` with `BAAI/bge-small-en-v1.5` reached P=0.917/R=1.000 at 0.60 and P=1.000/R=0.909 at 0.65 on 22 labeled pairs; canonical x402/agentic-payments context similarity 0.667; warm load 0.54s, roughly 23ms/text, peak working set 265MB, model cache 64.1MB. Lexical fallback was rejected. Do not ship a fixed threshold before expanding to at least 100 representative pairs.
- Task 4.1 implemented: versioned universal eligibility plus nine inspectable promotion routes, explicit limitations, manual attribution guard, and deterministic capped/stratified exploration allocation.
- Focused integration gate: 55 passed, JS syntax clean, diff check clean. Runtime DBs/images/logs/tmp remain unstaged.

## 2026-08-15 Phase 4-5.3 closeout
- Durable shadow promotion evaluation, funnel and workspace-scoped action labels are implemented. Shadow mode performs zero collection, zero LLM calls and zero downstream actions.
- Shadow evidence now fails closed when its source observation timestamp is missing, future-dated or more than 90 days old. Invalid action/outcome combinations are rejected before insertion.
- Global Explore is backed only by persisted family/evaluation/history records. Trajectory uses complete, comparable, timestamped runs and withholds stale series. Explanations retain only parseable HTTP(S) citations; malformed URL input is discarded without failing the family feed.
- Monitor is truthfully presented as a monitoring request rather than an active schedule. Dismissal is derived from workspace labels and does not mutate the shared family record.
- UI request epochs reject out-of-order Perspective responses; stale family detail is cleared during loading and on failures. Structured trajectory periods render as explicit start/end timestamps.
- Controlled browser QA used visibly synthetic `QA` families and `.invalid` citations. Populated, loading, empty and forced-error states passed at 1440x1000 and 390x844 with 0px horizontal overflow. Monitor/Dismiss interactions settled correctly. The isolated port-8101 server was stopped; the pre-existing port-8100 process was untouched.
- Final focused gate: 26 passed. Final authoritative suite: 413 passed, 11 existing deprecation warnings. `node --check public/dashboard.js` and `git diff --check` passed.
- Independent review initially blocked on evidence provenance/freshness, trajectory comparability, citation validation and action truthfulness. Fixes were regression-tested; exact-tree narrow re-review returned PASS, including malformed citation and out-of-order response probes.
- Runtime databases, WAL/SHM files, screenshots, logs, browser harnesses, seed scripts and unrelated scratch remain uncommitted.

## Next action
Vincent reviews the Phase 5 Task 5.3 product boundary. Do not add embedding-based grouping until the labeled calibration set materially exceeds the 22-pair spike; do not convert monitoring requests into schedules without an explicit project/subject ownership design.
