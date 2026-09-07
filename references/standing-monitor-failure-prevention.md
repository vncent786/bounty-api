# Standing-monitor failure prevention

## Incident: 6–7 September 2026

Three separate faults combined:

1. **The implementation run exhausted its orchestration budget.** A bounded dashboard release was created as a goal-mode task and expanded into live collection, classification, UI work, security hardening, full regression, browser QA and release preparation. Two runs each reached the 150-iteration ceiling before the final commit/deployment gate.
2. **Two writers owned the same latest pointer.** The new durable public-parity runner and an already-running legacy scheduled agent could both write `artifacts/dd/ghost-kdp/coverage_latest.json`. Editing the cron prompt did not cancel the old run already in flight, so the old writer finished later and temporarily replaced the new-format pointer.
3. **A failed/incomplete social attempt displaced the last auditable result.** The 7 September attempt had source failures and no complete linked evidence projection, but its newer timestamp still became the dashboard headline. That produced a 55-post/510-response headline with zero reconciled source links, replacing the verified 101-post/847-response observation.

A separate classifier defect was also found during repair: raw HTML script/style payloads and distant page terms could create a false management-attribution match. The accepted connector now removes non-visible page payloads and requires the product identity and economic claim in one local passage.

## Permanent controls

### 1. Hard run-budget reserve

- Checkpoint verified state by 50% of the available iterations.
- Freeze scope and stop exploratory fan-out by 70%.
- Reserve the final 20% for exact-tree tests, packaging, deployment or a complete terminal handoff.
- Bounded implementation/release cards use `goal_mode=false`.
- A timed-out retry must be narrower; it must not repeat the full workflow.
- Large qualitative reviews use one immutable partition manifest. Retry only failed partitions and verify the final ID union deterministically.

### 2. One writer per mutable pointer

- Immutable run artifacts may have many producers. A mutable `*_latest.json`, dashboard snapshot or release receipt has one registered writer.
- The GHOST public-parity pointer owner is `scripts/run_ghost_thesis_monitor.py`, writer version 1.
- The runner takes a cross-process lock, writes source receipts plus an immutable run artifact, then atomically advances the latest pointer with writer metadata.
- Before a method migration, inspect active cron jobs and processes. Update or pause the legacy schedule and wait for any already-running old job to finish.
- Readers allowlist the current writer/schema. A later legacy timestamp cannot beat an accepted pointer.

### 3. Last accepted observation wins

- A current social observation is dashboard-eligible only when all five required canaries are healthy, root and response counts are present, and a persisted linked evidence artifact exists.
- A failed or unlinked attempt remains in audit but cannot replace the latest source-linked headline, chart or sentiment view.
- Current GHOST fallback is therefore the 6 September accepted observation: 101 original posts, 847 captured comments/replies and 948 reconciled links. The failed 7 September attempt remains visible only as source-health context.

### 4. Public-information claim safety

- Remove script, style, template and other non-visible page payloads before matching.
- Require product identity and the economic claim in the same local passage.
- News/RSS titles are discovery links, not directly read articles and not qualifying coverage.
- Public sell-side references are tracked separately. Paywalled/private research is `not_observable_not_checked`.

## Regression coverage

The release must pass tests for:

- a late legacy parity writer losing to the registered writer;
- a failed latest social attempt retaining the last fully linked observation;
- displayed post/response counts matching persisted links;
- sentiment counts matching row-level linked classifications;
- script/style payloads not creating exact-implication matches;
- unsafe/private/redirected source URLs being rejected;
- hover, focus and tap search tooltips, readable percentage axes and explicit missing-window reasons;
- authenticated/private dashboard behavior on desktop and 390px mobile.

## Operational release sequence

1. Confirm no old writer or bounded collection process is still active.
2. Run the durable source runner and verify writer metadata.
3. Build the zero-call tracker snapshot.
4. Run focused tests, then full regression.
5. Build a clean worktree from current `origin/main`; copy only the allowlisted release files.
6. Re-run the focused and full suites on that exact tree.
7. Commit and push the clean tree.
8. Verify deployed bytes, snapshot hash, authentication, desktop/mobile layout and the user-facing counts.
