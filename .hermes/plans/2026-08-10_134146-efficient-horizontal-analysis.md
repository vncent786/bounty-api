# Efficient Horizontal Analysis Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make Bounty self-serve for arbitrary user-defined use cases while ensuring expensive conversation retrieval, LLM extraction, and optional enrichments run only on deliberately shortlisted evidence.

**Architecture:** Bounty will use one immutable horizontal evidence corpus and a progressive-cost funnel. Google Trends ingestion and deterministic filtering run broadly; social root probes, comment hydration, horizontal LLM extraction, and use-case enrichments run on successively smaller, budgeted sets. Horizontal extraction is cached by evidence hash and shared across users; user lenses are cheap, versioned evaluations over that shared result and never mutate the corpus.

**Tech Stack:** Python 3.11, FastAPI, SQLite/WAL, existing `SourceBroker`, Google Trends/trendspy, YouTube/Reddit connectors, existing conversation normalizer and lens engine, pytest.

---

## Product Decision

Bounty does **not** analyze every Google Trends candidate.

The default run follows this funnel:

| Stage | Scope | Cost | Output |
|---|---:|---:|---|
| 0. Trends ingest | Every returned candidate | Very low | Immutable observation and provenance |
| 1. Deterministic screening | Every candidate | Very low | Per-user eligibility and priority, no LLM |
| 2. Social root probe | Budgeted union of lens shortlists | Low/moderate | Root posts/videos, source health, cheap engagement |
| 3. Thread hydration | Smaller promoted set | Moderate | Bounded comments/replies and explicit coverage |
| 4. Horizontal extraction | Only changed hydrated bundles | Expensive | Shared cited signals/entities/limitations |
| 5. Lens evaluation | Any number of users/lenses | Very low | User-specific ranking/filtering over shared features |
| 6. Optional enrichment | Explicitly requested or promoted | Expensive | Investing, product, marketing, or future use-case interpretation |

Initial operational safety limits, configurable rather than treated as analytically optimal:

```python
DEFAULT_SCAN_BUDGET = {
    "root_probe_candidates": 20,
    "deep_read_candidates": 5,
    "horizontal_llm_candidates": 5,
    "threads_per_platform": 2,
    "comments_per_thread": 20,
    "max_thread_depth": 2,
    "optional_enrichments": 0,
}
```

These numbers are load guards. Telemetry will determine later defaults. Users can promote a candidate manually, raise a workspace budget, or schedule a standing read.

---

## Non-Negotiable Invariants

1. Every Trends observation is stored before filtering.
2. Missing, unavailable, disabled, partial, and empty remain distinct states.
3. Raw posts/comments/replies remain separate from model interpretation.
4. A user lens cannot delete or mutate canonical evidence.
5. Creating or editing a lens performs no scrape and no LLM call.
6. Identical evidence plus identical extraction version performs no repeat LLM call.
7. New comments create a new evidence-bundle version; old analyses remain reproducible.
8. Optional use-case enrichments never become universal gates.
9. No interpolated metrics, inferred coverage, or fabricated source success.
10. Every stage records duration, item count, and whether an external/LLM call occurred.

---

## Phase 0: Lock the Current Baseline and Add Cost Telemetry

### Task 1: Define pipeline stage and budget models

**Objective:** Represent scan limits and stage outcomes explicitly instead of hiding limits in function defaults.

**Files:**
- Create: `social_scraper/discovery/budgets.py`
- Test: `tests/discovery/test_budgets.py`

**Steps:**
1. Write failing tests for non-negative budgets, bounded thread depth, and serialization.
2. Add immutable `ScanBudget` and `StageUsage` dataclasses.
3. Reject negative limits and unknown stage names.
4. Run: `python -m pytest -q tests/discovery/test_budgets.py`
5. Expected: all tests pass.

### Task 2: Persist stage usage and external-call counts

**Objective:** Make efficiency measurable per Discovery run.

**Files:**
- Modify: `social_scraper/discovery/storage.py`
- Test: `tests/discovery/test_stage_usage.py`

**Schema:**

```sql
CREATE TABLE discovery_stage_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discovery_run_id TEXT NOT NULL REFERENCES discovery_runs(id),
    stage TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    candidates_considered INTEGER NOT NULL,
    candidates_processed INTEGER NOT NULL,
    records_returned INTEGER NOT NULL,
    external_calls INTEGER NOT NULL,
    llm_calls INTEGER NOT NULL,
    cache_hits INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_category TEXT
);
```

**Steps:**
1. Write a migration test against a pre-migration scratch database.
2. Add the table through an additive schema migration.
3. Add `record_stage_usage()` and `list_stage_usage()`.
4. Verify zero calls remain zero rather than null.
5. Run: `python -m pytest -q tests/discovery/test_stage_usage.py tests/discovery/test_candidate_history.py`

### Task 3: Add one scan-cost summary response

**Objective:** Return stage usage to the dashboard without exposing secrets or raw errors.

**Files:**
- Modify: `apis/dashboard_api.py`
- Test: `tests/discovery/test_stage_usage_api.py`

**Endpoint:**

```text
GET /dashboard/api/discovery/runs/{run_id}/usage
```

**Acceptance:** The response separately reports source calls, LLM calls, cache hits, candidates considered, and candidates processed.

---

## Phase 1: Make Lenses Truly Self-Serve

### Task 4: Persist reusable lens definitions separately from evaluations

**Objective:** Let a user save a lens once and apply versions repeatedly.

**Files:**
- Create: `social_scraper/lenses/storage.py`
- Modify: `social_scraper/lenses/core.py`
- Test: `tests/lenses/test_lens_storage.py`

**Tables:**

```sql
CREATE TABLE research_lenses (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE research_lens_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lens_id TEXT NOT NULL REFERENCES research_lenses(id),
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    UNIQUE(lens_id, version)
);
```

**Rules:**
- Editing creates a new immutable version.
- Archiving does not remove evaluations.
- Lens definitions reference registered horizontal features only.
- Presets are templates copied into a workspace, not privileged global logic.

### Task 5: Add lens CRUD APIs

**Objective:** Support create, list, read, version, duplicate, and archive operations.

**Files:**
- Modify: `apis/dashboard_api.py`
- Test: `tests/lenses/test_lens_crud_api.py`

**Endpoints:**

```text
GET    /dashboard/api/workspaces/{workspace_id}/lenses
POST   /dashboard/api/workspaces/{workspace_id}/lenses
GET    /dashboard/api/workspaces/{workspace_id}/lenses/{lens_id}
POST   /dashboard/api/workspaces/{workspace_id}/lenses/{lens_id}/versions
POST   /dashboard/api/workspaces/{workspace_id}/lenses/{lens_id}/duplicate
DELETE /dashboard/api/workspaces/{workspace_id}/lenses/{lens_id}
```

**Acceptance:** Creating/editing/duplicating a lens increments no source-call or LLM-call counter.

### Task 6: Compile lens rules into cheap pre-analysis and post-analysis rules

**Objective:** Determine which criteria can screen before social/LLM work.

**Files:**
- Create: `social_scraper/lenses/compiler.py`
- Modify: `social_scraper/lenses/core.py`
- Test: `tests/lenses/test_lens_compiler.py`

**Classification:**
- `candidate`: Trends fields such as volume, growth, age, category.
- `root_probe`: root-post counts, source availability, cheap engagement.
- `horizontal_analysis`: cited signal kinds, voices, platforms, coverage.
- `enrichment`: company exposure, market awareness, or future use-case fields.

**Acceptance:** The compiler returns required pipeline depth. A lens using only Trends fields never triggers social collection.

---

## Phase 2: Build the Progressive-Cost Scheduler

### Task 7: Add candidate stage states

**Objective:** Expose exactly how far each candidate has progressed.

**Files:**
- Create: `social_scraper/discovery/stages.py`
- Modify: `social_scraper/discovery/storage.py`
- Test: `tests/discovery/test_candidate_stages.py`

**States:**

```text
observed
screened_out
eligible
root_probed
promoted_for_deep_read
deep_read_partial
deep_read_complete
horizontal_analyzed
enrichment_requested
enriched
```

`unavailable`, `failed`, and `not_checked` are outcomes, not silently collapsed into `screened_out`.

### Task 8: Build deterministic candidate prioritization

**Objective:** Rank candidates for scarce collection budgets without LLM calls.

**Files:**
- Create: `social_scraper/discovery/prioritization.py`
- Test: `tests/discovery/test_prioritization.py`

**Inputs:**
- User lens pre-analysis filters.
- Candidate recency, volume, growth, and category.
- Whether the candidate has already been processed under the same observation.
- Manual promotion.
- Standing-read membership.

**Rules:**
- Hard user filters determine eligibility.
- Priority ordering is deterministic and records every component.
- No hidden universal “investability” or “product opportunity” score.
- Manual promotion outranks automatic priority.
- Ties use stable candidate IDs.

### Task 9: Union multiple user shortlists before collection

**Objective:** Avoid duplicate collection when several users/lenses choose the same candidate.

**Files:**
- Create: `social_scraper/discovery/scheduler.py`
- Test: `tests/discovery/test_scheduler.py`

**Algorithm:**
1. Compile all active workspace lenses.
2. Produce each lens’s eligible ordered candidate IDs.
3. Allocate candidates round-robin within each workspace budget.
4. Union candidate IDs across workspaces.
5. Collect each candidate once.
6. Evaluate each lens independently after shared evidence arrives.

**Acceptance:** Two users selecting one candidate produce one source collection and one horizontal analysis, then two deterministic lens evaluations.

### Task 10: Add manual promotion and per-run budget APIs

**Objective:** Let users spend more only where judgment says it is worthwhile.

**Files:**
- Modify: `apis/dashboard_api.py`
- Test: `tests/discovery/test_scheduler_api.py`

**Endpoints:**

```text
POST /dashboard/api/discovery/runs
POST /dashboard/api/discovery/candidates/{geo}/{keyword}/promote
POST /dashboard/api/discovery/candidates/{geo}/{keyword}/enrich
```

**Acceptance:** The run request shows the exact requested budget before execution.

---

## Phase 3: Finish Bounded Conversation Reads

### Task 11: Persist thread read attempts independently

**Objective:** Preserve route attempts and coverage even when no records return.

**Files:**
- Create: `social_scraper/conversations/thread_storage.py`
- Modify: `social_scraper/conversations/thread_reader.py`
- Test: `tests/conversations/test_thread_storage.py`

**Persist:** root ID, route, requested count/depth, returned count, platform total, truncation, status, error category, and timestamp.

### Task 12: Complete YouTube thread integration

**Objective:** Promote the already live-validated YouTube reader into the scheduled pipeline.

**Files:**
- Modify: `social_scraper/connectors/youtube.py`
- Modify: `social_scraper/broker.py`
- Modify: `social_scraper/monitoring/conversation_gate.py`
- Test: `tests/connectors/test_youtube_threads.py`
- Test: `tests/monitoring/test_conversation_gate.py`

**Acceptance:** Parent and reply IDs survive normalization and storage. Bounded reads remain `partial` when platform totals exceed returned records.

### Task 13: Harden Reddit route recovery

**Objective:** Make Reddit useful without interpreting blocked routes as empty conversation.

**Files:**
- Modify: `social_scraper/connectors/reddit.py`
- Modify: `social_scraper/connectors/reddit_camoufox.py`
- Add immutable fixtures: `tests/fixtures/reddit_threads/`
- Test: `tests/connectors/test_reddit_threads.py`

**Route order:** public JSON, rendered page, then explicit unavailable. Do not add brittle undocumented bypasses.

**Acceptance:** At least one live public thread returns comments/replies, or the route is formally marked unavailable with captured fixture tests and no false completeness claim.

### Task 14: Add adaptive thread selection

**Objective:** Read the roots most likely to add distinct evidence rather than the first results.

**Files:**
- Create: `social_scraper/conversations/selection.py`
- Test: `tests/conversations/test_thread_selection.py`

**Cheap selection inputs:** text relevance, comment count, recency, platform diversity, and whether a root has already been read.

**Stop conditions:** budget exhausted, all routes unavailable, requested platform diversity reached, or no unread eligible root remains. Semantic saturation is deferred until Phase 3+ because it requires measured clustering behavior.

---

## Phase 4: Cache One Shared Horizontal Analysis

### Task 15: Version evidence bundles by content hash

**Objective:** Reanalyze only when evidence changes.

**Files:**
- Create: `social_scraper/conversations/bundles.py`
- Modify: `social_scraper/conversations/storage.py`
- Test: `tests/conversations/test_evidence_bundles.py`

**Hash input:** ordered canonical record IDs, record content hashes, coverage states, and normalizer version. Do not include user lens IDs.

### Task 16: Persist horizontal extraction cache keys

**Objective:** Make model reuse explicit and reproducible.

**Files:**
- Modify: `social_scraper/discovery/storage.py`
- Modify: `social_scraper/discovery/triage.py`
- Test: `tests/discovery/test_analysis_cache.py`

**Cache key:**

```text
sha256(evidence_bundle_hash + prompt_version + model_provider + model_name)
```

**Acceptance:** Repeating a scan with unchanged evidence yields one cache hit and zero LLM calls. Changing only a lens also yields zero LLM calls.

### Task 17: Minimize model input without discarding raw evidence

**Objective:** Reduce tokens while preserving auditability.

**Files:**
- Create: `social_scraper/conversations/model_context.py`
- Modify: `social_scraper/discovery/triage.py`
- Test: `tests/conversations/test_model_context.py`

**Rules:**
- Deduplicate exact repeated text before model input.
- Keep the full raw record in storage.
- Send stable IDs, bounded text, parent/depth, author identity hash, timestamp, and provenance.
- Never summarize evidence with a second LLM before the primary extraction.
- Record input record count and prompt character/token estimate.

### Task 18: Separate horizontal extraction from optional interpretation

**Objective:** Ensure investing, product, and marketing views reuse the same extracted evidence.

**Files:**
- Modify: `social_scraper/discovery/triage.py`
- Modify: `social_scraper/lenses/investing.py`
- Create: `social_scraper/lenses/product_opportunity.py`
- Create: `social_scraper/lenses/marketing_intelligence.py`
- Test: `tests/lenses/test_optional_interpretations.py`

**Rules:**
- Horizontal extraction identifies cited signals/entities/coverage.
- Deterministic lens evaluation is the default.
- A use-case LLM interpretation is opt-in, separately cached, and never required to browse evidence.
- Marketing monitored subjects are user configuration, not hardcoded product names.

---

## Phase 5: Build One User-Facing Vertical Slice

### Task 19: Create the lens and budget control surface

**Objective:** Let a user understand and control what costs money.

**Files:**
- Modify: `apis/dashboard_page.py`
- Modify: `apis/dashboard_api.py`
- Add browser-level tests under: `tests/ui/`

**Representative screen:** Candidate Explorer with:
- Neutral Explorer default.
- Saved-lens selector.
- Create/duplicate/edit lens.
- Candidate stage and coverage badges.
- Per-run root/deep/LLM budget controls.
- “Promote for deeper read” action.
- “Run optional interpretation” action.
- Source records and limitations always visible.
- Usage panel showing calls, cache hits, and candidates processed.

**Approval gate:** Serve locally, populate from the real persisted Phase 1B dataset, and stop for Vincent’s review before expanding the UI.

### Task 20: Exercise three Vincent workflows and one hypothetical external workflow

**Objective:** Prove horizontality rather than merely naming it.

**Fixtures / scenarios:**
1. Investing/social-arbitrage lens.
2. Product-opportunity lens.
3. Marketing lens with a configurable monitored subject.
4. A different user’s custom lens using only a subset of available features.

**Acceptance:** All four reuse the same canonical candidate/evidence records. Changing the fourth lens does not scrape, rehydrate, or rerun horizontal extraction.

---

## Phase 6: Incremental Standing Reads

### Task 21: Add saved monitored subjects

**Objective:** Support ongoing marketing/product/investing monitoring without hardcoding Cairn, Nagi, Clarte, or any other subject.

**Files:**
- Create: `social_scraper/monitoring/subjects.py`
- Modify: `apis/dashboard_api.py`
- Test: `tests/monitoring/test_subjects.py`

**Configuration:** subject name, aliases, excluded meanings, platforms, lens IDs, cadence, and budget.

### Task 22: Fetch only deltas

**Objective:** Avoid rereading unchanged roots and comments.

**Files:**
- Create: `social_scraper/monitoring/incremental.py`
- Modify connector methods as required.
- Test: `tests/monitoring/test_incremental_reads.py`

**Acceptance:** Existing record IDs/content hashes are skipped; new comments create a new bundle version; unchanged bundles hit the analysis cache.

---

## Validation Matrix

Before calling the system done, verify:

```text
[ ] Every Trends candidate persists without an LLM call.
[ ] A Trends-only lens produces no social call.
[ ] A root-probe lens performs no comment hydration.
[ ] A deep-read budget cannot be exceeded by automatic scheduling.
[ ] Manual promotion is explicit in stage history.
[ ] Two users selecting one candidate share collection and extraction.
[ ] Creating/editing/duplicating a lens uses zero source and LLM calls.
[ ] Unchanged evidence uses the horizontal-analysis cache.
[ ] Changed comments invalidate only the affected evidence bundle.
[ ] Empty, unavailable, disabled, failed, and partial remain distinct.
[ ] Raw records survive even if an analysis is later superseded.
[ ] Investing, product, and marketing interpretations are peers.
[ ] A fourth arbitrary user lens can be created without code changes.
[ ] UI displays exact stage usage and cache behavior.
[ ] Full relevant pytest suite passes.
[ ] Local UI vertical slice is reviewed before wider propagation.
```

## Exact Verification Commands

```bash
cd /d/vncen/saas/bounty-api-fresh
python -m pytest -q tests/discovery tests/lenses tests/conversations tests/connectors tests/monitoring
python -m py_compile social_scraper/**/*.py apis/*.py
```

Run a bounded local smoke test with explicit budgets and verify the persisted stage-usage rows match actual calls. Do not run a broad paid scan merely to demonstrate completion.

---

## Risks and Tradeoffs

1. **Early filters can hide useful candidates.** Preserve every observation and show screened-out candidates; allow manual promotion and filter changes without recollection.
2. **Several users can expand the union shortlist.** Enforce workspace and global budgets, then deduplicate the union before collection.
3. **Root engagement can be a poor proxy for useful conversation.** Treat it only as a cheap scheduling input, expose the priority components, and allow user override.
4. **Reddit access remains route-dependent.** Preserve unavailable status and do not build claims from missing comments.
5. **LLM extraction can change between versions.** Pin model/prompt versions and retain old analyses against immutable evidence bundles.
6. **Semantic saturation could save more tokens but adds complexity.** Defer it until real thread data can validate stopping thresholds; do not invent one now.
7. **Optional interpretations can drift into hidden universal logic.** Keep them explicit, separately versioned, and user-triggered.

## Recommended Execution Order

Execute Phases 0–2 first. They deliver the largest efficiency gain: measurable costs, self-serve reusable lenses, and a budgeted scheduler. Then finish thread reliability and shared analysis caching. Build only one UI vertical slice and obtain approval before expanding standing reads or later clustering features.
