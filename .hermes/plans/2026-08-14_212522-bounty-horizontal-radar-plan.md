# Bounty Horizontal Radar and Conversation Intelligence Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. Stop at every approval gate. Do not proceed to the next product phase without Vincent's explicit approval.

**Goal:** Turn Bounty's current Google Trends list and separate monitoring pipeline into a horizontal trend-discovery and conversation-research product that surfaces understandable early topic families, spends LLM tokens only on promoted evidence, and supports editable user perspectives without imposing one investing or marketing method.

**Architecture:** Bounty will operate two co-primary workflows over one evidence engine: a global/personal radar for unknown-unknown discovery, and Projects/Zones for bounded and standing reads of known niches. Daily Google Trends snapshots and weekly root-social scans are token-free. Deterministic route-based promotion, topic-family grouping and a small exploration allocation decide which candidates receive thread hydration and one cached horizontal synthesis. Editable perspectives reuse the same evidence and synthesis.

**Tech Stack:** Python 3.11, FastAPI, SQLite/WAL, existing `trendspy`, `SourceBroker`, platform connectors, local semantic embeddings after a measured feasibility spike, vanilla HTML/CSS/JavaScript dashboard, pytest and browser QA.

**Canonical reference:** [Buzzabout, “Google Trends Alternatives: They All Count the Wrong Thing”](https://buzzabout.ai/blog/google-trends-alternatives)

---

## Confirmed Product Decisions

1. Bounty is horizontal. Investing and Cairn marketing are validation workflows, not product-wide gates.
2. There are two co-primary discovery surfaces:
   - **Global radar:** broad cross-domain unknown-unknown discovery.
   - **Personal radars / Projects:** saved regions, domains, seed concepts, questions and cadence.
3. Google Trends Explore is important, particularly for unknown-unknown investing discovery, but a raw Trend is never a finding.
4. Cadence:
   - Daily Google Trends metadata snapshots, zero LLM calls.
   - Weekly broad root-social collection, zero LLM calls.
   - Triggered deep reads and LLM synthesis only for promoted candidates/topic families.
   - Projects/Zones use weekly standing reads by default; users may configure another cadence.
5. Promotion uses universal evidence gates plus multiple explicit OR routes. It is not one opaque score.
6. Daily repeated presence remains a valid promotion route. It does not establish durability by itself.
7. Promotion routes launch in **shadow mode** before automatic deep analysis.
8. Raw Trend candidates remain immutable and separately measured.
9. Related raw candidates such as `x402` and `agentic payments` may be grouped into an evidence-backed **topic family**, while retaining term-level trajectories and provenance.
10. Topic-family grouping must never sum overlapping Google volumes or double-count posts.
11. Engagement, independent corroboration, repost propagation and reply depth are separate dimensions.
12. One viral post is propagation/reach, not independent consensus.
13. The LLM receives engagement and thread metadata; it must not analyze decontextualized titles/text alone.
14. One cached horizontal synthesis is shared across all user perspectives.
15. User interpretation uses editable starter perspectives plus fully custom perspectives. There is no canonical universal “Investing lens.”
16. First approval-gated product slice: make **Global Explore** understandable and actionable.
17. Second validation slice: a Cairn personal radar / bounded read that extracts engaged pain points, replies, objections, workarounds and audience language.
18. No interpolation, silent source failure, fabricated completeness or guessed missing engagement.

## Explicit Non-Goals for This Plan

- Automatic trade recommendations.
- Hardcoding Vincent's social-arbitrage method as the only investing method.
- Treating high engagement as truth or materiality.
- Running LLM analysis on every Trend or every post/comment.
- Replacing raw evidence with summaries.
- Perfect platform parity where connectors cannot provide comments/reposts.
- Shipping every screen before Vincent approves the first populated Explore slice.
- Reviving x402 marketplace, Singapore property, MCP or other deferred legacy work.

---

## Current Baseline and Defects to Preserve in Tests

Current revision at planning time: `50da97c` plus this plan file only.

1. `social_scraper/monitoring/topdown.py::scan_all()` selects up to 20 candidates by freshness/growth.
2. `apply_conversation_gate()` currently searches YouTube, Reddit and TikTok, hydrates up to two threads per platform and calls `analyze_conversation()` for every checked candidate with records.
3. Therefore a user-triggered Explore scan can consume close to one LLM call per checked candidate before manual selection.
4. The gate's absolute engagement sum combines likes, comments and `views // 1000`, ignores reposts/shares and does not normalize by platform, content age or creator size.
5. Final result order remains primarily freshness/growth; gate evidence does not create a transparent promotion explanation.
6. `triage._prepare_evidence()` caps model context to five records per platform and sends text/platform/object type, but excludes engagement, author-size, repost, parent/depth and age context.
7. Research-run defaults use YouTube and Reddit; the Trend gate uses YouTube, Reddit and TikTok. Deep five-platform coverage is not established.
8. `optional_enrichment` is a no-op.
9. Lens selection does not meaningfully change discovery ranking or extraction.
10. Raw candidate history exists, but the dashboard does not surface it as an interpretable trajectory.
11. Zones schedule repeated collection but use a shallower pipeline, weak lexical clusters, no post-level deduplication and no horizontal findings.
12. Existing evidence cache, budgets, stage states, lens persistence, research-run execution and findings persistence should be extended rather than rewritten.

Before changing behavior, lock these facts in characterization tests so changes are deliberate.

---

# Phase 0: Freeze Baseline and Make Cost Observable

## Task 0.1: Add characterization tests for the current accidental Explore cost path

**Objective:** Prove the current Trend scan hydrates threads and calls the LLM automatically, so the later removal is covered by regression tests.

**Files:**
- Create: `tests/discovery/test_explore_cost_path.py`
- Test helpers: reuse deterministic connectors and `llm_call_fn` patterns from `tests/discovery/test_execution.py` and `tests/discovery/test_triage.py`

**Steps:**
1. Build a deterministic broker fixture with three candidate keywords and root/thread records.
2. Inject a counting LLM function.
3. Call `TopDownDiscovery.scan_all(apply_gate=True)`.
4. Assert the current baseline records thread calls and one LLM call per readable candidate.
5. Mark the assertions as characterization behavior that Phase 1 intentionally replaces.
6. Run:
   ```bash
   python -m pytest tests/discovery/test_explore_cost_path.py -v
   ```
7. Expected: tests pass against the pre-change behavior.
8. Commit only the characterization test.

## Task 0.2: Extend usage receipts to include prompt input size and family reuse

**Objective:** Measure projected and actual source/LLM cost instead of guessing.

**Files:**
- Modify: `social_scraper/discovery/budgets.py`
- Modify: `social_scraper/discovery/storage.py`
- Modify: `social_scraper/discovery/staged_runner.py`
- Modify: `apis/dashboard_api.py`
- Test: `tests/discovery/test_stage_usage.py`
- Test: `tests/discovery/test_stage_usage_api.py`

**Add to stage usage:**
```python
{
    "input_records": int,
    "input_characters": int,
    "input_tokens_reported": int | None,
    "output_tokens_reported": int | None,
    "topic_family_id": str | None,
    "shared_evidence_reuse": bool,
}
```

**Rules:**
- Provider-reported token counts are stored when available.
- Missing token counts remain `null`; do not invent estimates.
- Character count is always recorded as a reproducible fallback.
- Projected usage and actual usage are separate fields.

**Verification:** Run both focused tests, then `python -m pytest tests/discovery -q`.

---

# Phase 1: Separate Daily Observation, Weekly Roots and Promoted Analysis

## Task 1.1: Define scan modes explicitly

**Objective:** Stop one endpoint from silently doing Trends ingestion, root search, thread hydration and LLM analysis.

**Files:**
- Create: `social_scraper/discovery/scan_modes.py`
- Modify: `social_scraper/discovery/stages.py`
- Test: `tests/discovery/test_scan_modes.py`

**Modes:**
```python
class ScanMode(str, Enum):
    TRENDS_SNAPSHOT = "trends_snapshot"       # Trends metadata only
    ROOT_SWEEP = "root_sweep"                 # root social evidence, no threads/LLM
    DEEP_READ = "deep_read"                   # selected families only
    HORIZONTAL_SYNTHESIS = "horizontal_synthesis"
    OPTIONAL_INTERPRETATION = "optional_interpretation"
```

**Hard invariant tests:**
- `TRENDS_SNAPSHOT`: zero source-broker calls and zero LLM calls.
- `ROOT_SWEEP`: root search calls allowed; zero thread and LLM calls.
- `DEEP_READ`: thread calls allowed; zero LLM calls.
- `HORIZONTAL_SYNTHESIS`: at most one LLM call per changed family evidence bundle.

## Task 1.2: Remove automatic LLM and thread hydration from the Trend feed path

**Objective:** Make Explore ingestion token-free and cheap.

**Files:**
- Modify: `social_scraper/monitoring/topdown.py:285-392`
- Modify: `social_scraper/monitoring/conversation_gate.py`
- Modify: `apis/dashboard_api.py:495-545`
- Modify: `tests/discovery/test_explore_cost_path.py`
- Modify: relevant gate tests under `tests/monitoring/`

**Implementation rules:**
- Split gate root probing from thread hydration.
- Trend snapshot persists raw candidates without calling the broker.
- Root sweep may attach root counts, engagement and source health but must pass `max_threads=0` and never call `analyze_conversation()`.
- Existing heavy behavior remains available only through explicit research-run stages.
- Compatibility `/discover` may remain, but dashboard code must call the explicit lightweight mode.

**Acceptance:** The characterization test changes from “LLM called” to “zero LLM/thread calls,” while all source-health states remain explicit.

## Task 1.3: Add durable daily and weekly scheduler entries

**Objective:** Implement the approved hybrid cadence without recursive agent jobs or per-request auto-runs.

**Files:**
- Modify: `apis/scheduler.py`
- Modify: `app.py` startup scheduler wiring
- Modify: `social_scraper/discovery/storage.py`
- Create: `tests/discovery/test_radar_schedule.py`

**Default schedules:**
- Trends snapshot: daily per configured geography.
- Root sweep: weekly for all current eligible candidates and active personal radars.
- Deep reads: never scheduled globally until promotion exits shadow mode.

**Requirements:**
- Lease/claim rows prevent duplicate execution across replicas.
- Each schedule stores last attempt, last successful comparable run and source health.
- Failure does not create a false missing-candidate gap.
- Scheduling configuration is environment/workspace controlled; no hardcoded Vincent-only regions.

**Approval condition:** Do not enable production automatic deep reads in this task.

---

# Phase 2: Preserve Rich Root Evidence and Normalize Engagement

## Task 2.1: Extend canonical engagement without zero-filling

**Objective:** Preserve source-native resonance and propagation metrics for root posts and replies.

**Files:**
- Modify: `social_scraper/base.py`
- Modify: `social_scraper/conversations/models.py`
- Modify: `social_scraper/conversations/normalize.py`
- Modify connector adapters only where source fields already exist
- Test: `tests/conversations/test_models_and_normalize.py`

**Canonical nullable fields:**
```python
likes: int | None
upvotes: int | None
comments: int | None
replies: int | None
views: int | None
shares: int | None
reposts: int | None
bookmarks: int | None
creator_followers: int | None
```

**Rules:**
- Do not map unsupported metrics to zero.
- Preserve raw source field names in provenance.
- Record publication and collection timestamps.
- Comment/reply records retain their own engagement, parent and depth.

## Task 2.2: Deduplicate roots and model repost propagation separately

**Objective:** Prevent multi-query/topic-family scans from counting one item multiple times while retaining propagation as evidence.

**Files:**
- Create: `social_scraper/conversations/deduplication.py`
- Create: `social_scraper/conversations/propagation.py`
- Modify: `social_scraper/discovery/handlers.py`
- Test: `tests/conversations/test_deduplication.py`
- Test: `tests/conversations/test_propagation.py`

**Identity order:**
1. Platform + canonical external ID.
2. Canonical URL.
3. Exact normalized content hash.
4. Near-duplicate grouping only after a measured threshold test.

**Output distinction:**
```python
{
    "unique_root_count": 8,
    "independent_author_count": 6,
    "repost_cluster_count": 2,
    "largest_repost_cluster_size": 4,
    "propagation_reach": {...},
}
```

Never describe repost count as independent corroboration.

## Task 2.3: Build platform-, age- and creator-aware engagement baselines

**Objective:** Replace one absolute cross-platform engagement sum with comparable percentile features.

**Files:**
- Create: `social_scraper/analysis/engagement.py`
- Modify: `social_scraper/discovery/storage.py`
- Test: `tests/analysis/test_engagement.py`

**Baseline dimensions:**
- Platform.
- Content-age bucket.
- Creator-size bucket when available.
- Metric availability.
- Trailing observed period and sample count.

**Output per root:**
```python
{
    "like_percentile": float | None,
    "comment_percentile": float | None,
    "repost_percentile": float | None,
    "view_percentile": float | None,
    "creator_adjusted_percentile": float | None,
    "baseline_sample_size": int,
    "baseline_status": "supported" | "weak" | "unavailable",
}
```

**Rules:**
- Percentiles are calculated only from observed comparable records.
- Weak/unavailable baselines cannot satisfy an automatic outlier route.
- Retain raw counts alongside derived percentiles.

---

# Phase 3: Link Raw Candidates into Topic Families

## Task 3.1: Persist raw candidates, family nodes and evidence-backed edges

**Objective:** Group `x402` and `agentic payments` when evidence supports a relationship without destroying their separate Trends histories.

**Files:**
- Create: `social_scraper/discovery/topic_families.py`
- Modify: `social_scraper/discovery/storage.py`
- Test: `tests/discovery/test_topic_families.py`

**Additive tables:**
```sql
CREATE TABLE topic_families (
    id TEXT PRIMARY KEY,
    canonical_label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE topic_family_memberships (
    family_id TEXT NOT NULL,
    geo TEXT NOT NULL,
    normalized_keyword TEXT NOT NULL,
    relationship TEXT NOT NULL,
    confidence TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    first_linked_at TEXT NOT NULL,
    PRIMARY KEY (family_id, geo, normalized_keyword)
);

CREATE TABLE topic_relationship_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    left_candidate_key TEXT NOT NULL,
    right_candidate_key TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    strength REAL,
    evidence_json TEXT NOT NULL,
    observed_at TEXT NOT NULL
);
```

**Allowed relationships:** alias, broader, narrower, enabling_technology, alternative, associated_event, related_distinct, uncertain.

## Task 3.2: Build deterministic relationship evidence

**Objective:** Generate explainable family edges without LLM use.

**Files:**
- Modify: `social_scraper/discovery/topic_families.py`
- Test: `tests/discovery/test_topic_family_edges.py`

**Edge evidence:**
- Google `trend_keywords` / related queries.
- Root-post and reply co-occurrence.
- Shared entities and URLs.
- Shared repost/content clusters.
- Temporal and geographic co-movement.

**Hard rule:** Temporal co-movement alone cannot merge candidates.

## Task 3.3: Spike local semantic linkage

**Objective:** Determine whether a small local embedding model can link lexically dissimilar but related topics without unacceptable latency/RAM.

**Files:**
- Create: `tmp/spikes/topic_family_embeddings.py`
- Create: `tests/fixtures/discovery/topic_relationship_labels.json`
- Planning output only if spike invalid: document measured alternative; do not ship spike code into production paths.

**Given/When/Then:**
- Given labeled related and unrelated candidate pairs, including `x402` ↔ `agentic payments`.
- When embedding bounded related-query and root-text context locally.
- Then report precision/recall, elapsed time, peak memory and cache size.

**Candidates:** start with a small sentence-transformer/BGE-small class model already contemplated in the prior roadmap. Do not add the dependency if the spike is not viable on the 16GB Windows host.

**Result:** `VALIDATED`, `PARTIAL` or `INVALIDATED` with measured constraints.

## Task 3.4: Add optional ambiguous-pair adjudication

**Objective:** Spend model tokens only when high-priority candidates have unresolved deterministic/semantic relationships.

**Files:**
- Create: `social_scraper/discovery/topic_adjudication.py`
- Modify: `social_scraper/discovery/evidence_cache.py`
- Test: `tests/discovery/test_topic_adjudication.py`

**Rules:**
- No call for strong deterministic links or clearly unrelated pairs.
- One cached call per ambiguous pair/context version.
- Strict output enum; unsupported relationships remain `uncertain`.
- Raw candidates are never deleted or volume-summed.

## Task 3.5: Collect and synthesize per family without double counting

**Objective:** Search member terms broadly but build one deduplicated evidence bundle and one horizontal synthesis.

**Files:**
- Modify: `social_scraper/discovery/handlers.py`
- Modify: `social_scraper/discovery/evidence_cache.py`
- Test: `tests/discovery/test_family_execution.py`

**Acceptance:**
- Three member queries matching the same post yield one canonical root.
- Member-level source/trajectory provenance remains inspectable.
- Unchanged family evidence yields zero new LLM calls.

---

# Phase 4: Route-Based Promotion in Shadow Mode

## Task 4.1: Define configurable eligibility and promotion contracts

**Objective:** Replace opaque prioritization with explicit pass/fail routes and inspectable reasons.

**Files:**
- Create: `social_scraper/discovery/promotion.py`
- Modify: `social_scraper/discovery/prioritization.py`
- Test: `tests/discovery/test_promotion.py`

**Initial universal eligibility defaults, explicitly calibration candidates:**
```yaml
minimum_unique_roots: 3
minimum_independent_authors: 2
require_usable_text: true
require_source_health: true
reject_duplicate_only_support: true
```

**Initial routes:**

1. `daily_search_persistence`
   - Present in at least two of the last three comparable daily snapshots.
   - Explicit gaps remain gaps.
   - Minimal root-social evidence exists after the weekly sweep.

2. `search_trajectory_expansion`
   - Persistence plus observed increase in own-series reported volume/growth, related-query breadth or regional breadth.
   - Missing values cannot pass that component.

3. `cross_platform_breadth`
   - At least two healthy platforms with hits.
   - At least three independent authors.
   - At least three distinct roots/threads.

4. `age_adjusted_engagement_breakout`
   - Two independent roots above a configured supported percentile, or one extreme supported outlier plus an independent corroborating root.
   - Thresholds live in configuration and are not called optimal before calibration.

5. `creator_breadth_expansion`
   - At least three current independent creators.
   - Material increase against the candidate/family's own previous comparable observation.
   - Duplicate repost clusters excluded from independent breadth.

6. `conversation_depth_trigger`
   - Comment/reply activity exceeds a supported platform/age baseline, or several independent roots have active discussions.

7. `personal_radar_recurrence`
   - Candidate matches a saved radar.
   - Lower configured evidence floor allowed, while source limitations remain visible.

8. `manual_promotion`
   - Always planned within the user's explicit budget.

9. `exploration_allocation`
   - Samples from eligible, non-promoted candidates using a fixed per-run cap and category/region stratification.

**Boolean model:**
```python
eligible = all(universal_gates)
automatically_promoted = eligible and any(route.passed for route in automatic_routes)
```

Passing fragments from different routes does not create an implicit pass.

## Task 4.2: Persist route evaluations and reasons

**Objective:** Make every promotion reproducible and auditable.

**Files:**
- Modify: `social_scraper/discovery/storage.py`
- Test: `tests/discovery/test_promotion_storage.py`

**Persist:** policy version, configuration snapshot, input observation IDs, eligibility gates, each route's components/outcome, promotion mode, projected stage usage and limitations.

## Task 4.3: Run shadow mode with no automatic deep reads

**Objective:** Learn whether criteria produce too many, too few or wrong candidates before spending tokens.

**Files:**
- Modify: `social_scraper/discovery/scheduler.py`
- Modify: `apis/dashboard_api.py`
- Test: `tests/discovery/test_shadow_mode.py`

**Shadow output per weekly run:**
- Raw candidate count.
- Unique topic-family count.
- Eligible count.
- Pass count per route.
- Route overlap matrix.
- Exploration sample.
- Projected deep reads.
- Projected LLM calls.
- Projected prompt characters.
- No automatic deep execution.

## Task 4.4: Add human calibration labels

**Objective:** Measure precision and false negatives rather than tuning by intuition.

**Files:**
- Modify: `apis/dashboard_api.py`
- Modify: `social_scraper/discovery/storage.py`
- Test: `tests/discovery/test_calibration_labels.py`

**Labels:** useful, noise, unclear, duplicate_family, already_obvious, potentially_early, wrong_context.

**Rules:**
- Labels are user judgments, not evidence facts.
- Exploration samples estimate missed useful candidates.
- Policy changes create a new immutable version.

**Approval gate:** After several comparable scans, Vincent reviews route yield, candidate quality and projected tokens. No automatic deep-read rollout until explicitly approved.

---

# Phase 5: Build the Global Explore Prototype

## Task 5.1: Create an Explore read model per topic family

**Objective:** Answer “what is this, why now, is it durable, and why might it matter?” without exposing internal plan jargon.

**Files:**
- Create: `social_scraper/discovery/explore_read_model.py`
- Modify: `apis/dashboard_api.py`
- Test: `tests/discovery/test_explore_read_model.py`

**Response shape:**
```json
{
  "family_id": "...",
  "label": "Agentic payments",
  "member_terms": [
    {"term": "agentic payments", "relationship": "broader"},
    {"term": "x402", "relationship": "enabling_technology"}
  ],
  "what_it_is": {"text": "...", "status": "supported|unclear"},
  "why_surfaced": [{"route": "daily_search_persistence", "evidence": {}}],
  "stage": "observed|emerging|confirming|established|cooling|event_spike|unclear",
  "trajectory": {},
  "resonance": {},
  "corroboration": {},
  "propagation": {},
  "conversation_depth": {},
  "coverage": {},
  "limitations": [],
  "available_actions": ["investigate", "monitor", "dismiss"]
}
```

**Rules:**
- `what_it_is` must remain unclear until supported by related/root context.
- Stage criteria are versioned and inspectable.
- Search volume is not displayed without explaining its source and period.
- No generic “investability” field.

## Task 5.2: Add radar and perspective APIs

**Objective:** Support global radar, saved personal radars and editable perspectives without recollection.

**Files:**
- Modify: `apis/dashboard_api.py`
- Modify: existing workspace/lens storage and service files under `social_scraper/workspaces/` and `social_scraper/lenses/`
- Test: `tests/discovery/test_radar_api.py`
- Test: `tests/discovery/test_lens_crud_api.py`

**API capabilities:**
- List global radar families by date/region/category/stage.
- Create/update/archive personal radar.
- Copy an editable starter perspective.
- Create a fully custom perspective.
- Apply perspective to existing read models with zero source calls.
- Return projected cost before deep investigation.

**User-facing terminology:** “Perspective,” not “lens,” unless later user testing prefers Lens. Internal `lens_*` code may remain to avoid unnecessary migration.

## Task 5.3: Build one populated Explore screen

**Objective:** Prove the user experience before expanding the dashboard.

**Files:**
- Modify: `apis/dashboard_page.py`
- Modify: `public/dashboard.js`
- Modify: `public/dashboard.css`
- Modify: `tests/test_dashboard_product.py`
- Modify/add: `tests/browser_dashboard_qa.py`

**Screen requirements:**
- Global radar / Personal radars switch.
- Region multi-select.
- Perspective selector with editable starter/custom options.
- Family cards showing:
  - plain-language topic explanation
  - member terms and relationships
  - why surfaced
  - stage and trajectory
  - separate resonance/corroboration/propagation/depth
  - coverage and missing sources
  - Investigate, Monitor, Dismiss
- Rejected/unclear candidates remain inspectable.
- No “Create bounded research plan,” candidate UUIDs or persisted-findings jargon.
- Mobile at 375px and desktop at 1440px.

**Fixture:** Use a deterministic, clearly labeled test dataset that includes:
- related member terms (`x402`, `agentic payments`)
- a one-off event spike
- a cross-platform emerging topic
- an eligible candidate promoted only through exploration
- explicit missing source coverage

Do not put fixture data into production routes.

**Approval gate:** Serve locally, capture desktop/mobile screenshots and stop. Ask Vincent:
1. Can you understand each candidate within five seconds?
2. Is it clear why Bounty surfaced it?
3. Is it clear what is observed versus inferred?
4. Would you know which action to take?

Do not implement automatic deep analysis before approval.

---

# Phase 6: Engagement-Aware Deep Reads and Shared Synthesis

## Task 6.1: Select roots for deep reading without using only popularity

**Objective:** Read high-resonance, representative, independent and dissenting conversations.

**Files:**
- Create: `social_scraper/conversations/selection.py`
- Modify: `social_scraper/discovery/handlers.py`
- Test: `tests/conversations/test_selection.py`

**Selection slots:**
- strongest supported age-adjusted engagement
- independent cross-platform roots
- largest repost/propagation origin
- representative recurring root
- dissent/negative/contrarian root when detectable
- unread roots before repeated roots

**Hard cap:** Respect `threads_per_platform`, `comments_per_thread` and `max_thread_depth` exactly.

## Task 6.2: Preserve reply support and ranking metadata

**Objective:** Read what replies say and which replies receive support.

**Files:**
- Modify: `social_scraper/discovery/handlers.py`
- Modify active thread-capable connectors under `social_scraper/connectors/`
- Test: `tests/conversations/test_thread_reader.py`
- Test: connector-specific thread fixtures

**Requirements:**
- Parent/depth retained.
- Reply likes/upvotes retained where source supports them.
- Platform-reported totals, truncation and route limitations persisted.
- Unsupported TikTok/Instagram/X thread depth remains explicit.

## Task 6.3: Include engagement/thread context in model evidence

**Objective:** Prevent the model from treating an isolated complaint and an engaged conversation as equivalent.

**Files:**
- Modify: `social_scraper/discovery/triage.py`
- Create: `social_scraper/conversations/model_context.py`
- Test: `tests/discovery/test_triage.py`
- Test: `tests/conversations/test_model_context.py`

**Model record:**
```json
{
  "id": "...",
  "platform": "reddit",
  "object_type": "reply",
  "root_id": "...",
  "parent_id": "...",
  "depth": 2,
  "published_at": "...",
  "engagement": {
    "upvotes": 120,
    "comments": null,
    "reposts": null,
    "age_adjusted_percentiles": {}
  },
  "propagation": {"cluster_id": "...", "is_origin": false},
  "text": "..."
}
```

**Rules:**
- Exact duplicates removed before prompt construction.
- Full raw evidence retained in storage.
- Stable IDs and citations mandatory.
- Input selection and truncation recorded.
- No second LLM summarizes evidence before horizontal synthesis.

## Task 6.4: Cache one synthesis per changed family bundle

**Objective:** Make tokens scale with promoted changed families, not raw Trends or perspectives.

**Files:**
- Modify: `social_scraper/discovery/evidence_cache.py`
- Modify: `social_scraper/discovery/handlers.py`
- Test: `tests/discovery/test_evidence_cache.py`
- Test: `tests/discovery/test_family_execution.py`

**Cache key:** family evidence-bundle hash + extraction schema/prompt version + provider/model.

**Acceptance:**
- Unchanged weekly family: zero LLM calls.
- Perspective switch: zero LLM calls.
- New reply: only affected family cache invalidated.
- Member-term rename with identical evidence: no reanalysis.

---

# Phase 7: Editable Perspectives

## Task 7.1: Replace privileged presets with editable starters

**Objective:** Offer useful onboarding without declaring one correct investing or marketing methodology.

**Files:**
- Modify: `social_scraper/lenses/presets.py`
- Modify: `social_scraper/lenses/core.py`
- Modify: `social_scraper/lenses/storage.py`
- Test: `tests/lenses/test_presets.py`
- Test: `tests/lenses/test_configurable_lenses.py`

**Starter examples:**
- Emerging consumer behavior.
- Customer pain and marketing language.
- Product opportunities.
- One transparent social-arbitrage example based on Vincent's method, clearly editable and non-canonical.

**Rules:**
- Starter copy creates a normal workspace-owned perspective.
- Users can change questions, required evidence fields, hard filters and ordering.
- No starter changes canonical collection or evidence.
- Custom perspectives can be created without code changes.

## Task 7.2: Separate deterministic perspective evaluation from optional custom interpretation

**Objective:** Avoid new model calls for common perspective changes.

**Files:**
- Modify: `social_scraper/discovery/ranking.py`
- Modify: `social_scraper/lenses/compiler.py`
- Implement: `social_scraper/discovery/handlers.py::make_optional_enrichment_handler`
- Test: `tests/lenses/test_optional_interpretations.py`

**Rules:**
- Structured horizontal fields are filtered/ranked deterministically.
- A genuinely new custom question may request one separately cached interpretation.
- The user sees projected token use before running it.
- Optional interpretation never becomes a universal gate.

---

# Phase 8: Generic Awareness-Stage Enrichment

## Task 8.1: Collect dated awareness evidence for promoted families

**Objective:** Determine whether a topic remains social/community-only or has reached specialist, mainstream or official coverage without claiming that it is unpriced.

**Files:**
- Modify: `social_scraper/discovery/market_awareness.py`
- Create: `social_scraper/discovery/awareness_sources.py`
- Modify: `social_scraper/discovery/storage.py`
- Reuse/adapt collection from: `apis/news_search.py`
- Test: `tests/discovery/test_market_awareness.py`
- Create: `tests/discovery/test_awareness_sources.py`

**Run conditions:**
- Promoted topic families only.
- Explicit user request or configured perspective requirement.
- No awareness scan for every raw Trend.
- Default collection/classification is deterministic and uses zero LLM calls.

**Evidence to persist:**
- Family/member query used.
- URL, title, source/publisher and publication timestamp.
- Source tier and the rule/evidence supporting that tier.
- Official/company-domain match where verifiable.
- Collection timestamp, route and source health.
- Earliest observed social timestamp and earliest coverage timestamp without inferring causality.

**Classification:**
```text
social_only
community_or_niche_coverage
specialist_media_coverage
mainstream_coverage
official_or_company_acknowledged
unknown
```

**Hard rules:**
- Failed or incomplete web/news collection yields `unknown`, not `social_only`.
- Absence from collected sources is not proof of absence from the internet.
- Publisher tiers live in inspectable configuration and may be customized.
- No stock-price, valuation, materiality or “unpriced” conclusion is produced here.
- Raw awareness evidence and links remain visible.

## Task 8.2: Add optional cached adjudication for ambiguous coverage

**Objective:** Resolve only ambiguous high-priority source classification or relevance cases without spending tokens on every headline.

**Files:**
- Modify: `social_scraper/discovery/market_awareness.py`
- Modify: `social_scraper/discovery/evidence_cache.py`
- Test: `tests/discovery/test_awareness_adjudication.py`

**Rules:**
- Deterministic domain/source/date checks run first.
- An LLM call is allowed only for ambiguous relevance among already collected records.
- One cached call per awareness evidence-bundle/version.
- The model cannot upgrade missing coverage to `social_only`.
- Every included headline keeps its URL and timestamp.

## Task 8.3: Surface awareness stage without implying market absorption

**Objective:** Let any perspective use information maturity while preserving the product boundary.

**Files:**
- Modify: `social_scraper/discovery/explore_read_model.py`
- Modify: `apis/dashboard_api.py`
- Modify: `public/dashboard.js`
- Test: `tests/discovery/test_explore_read_model.py`
- Modify: `tests/test_dashboard_product.py`

**UI:**
```text
Awareness stage: Specialist media coverage

Observed evidence:
• Social conversation first observed [timestamp]
• Earliest collected specialist coverage [timestamp + source]
• Mainstream/official status unknown or not observed in checked sources

This is an information-maturity indicator, not evidence that a security is mispriced.
```

**Acceptance:** Investing, marketing, journalism and product perspectives can all request this enrichment. It remains optional and never becomes a universal gate.

---

# Phase 9: Cairn Personal Radar and Bounded Read Validation

## Task 9.1: Configure Cairn as data, not code

**Objective:** Prove marketing usefulness without hardcoding Cairn into product logic.

**Files:**
- Use normal workspace/project/radar APIs.
- Add test fixture only: `tests/fixtures/workflows/cairn_marketing.json`
- Test: `tests/workflows/test_cairn_marketing.py`

**Illustrative configurable questions:**
- What pain points recur across independent conversations?
- What desired outcomes do people state?
- What workarounds have they tried?
- What objections or trust/privacy concerns appear?
- What exact phrases receive agreement or high reply support?
- Which products or alternatives are mentioned?

Do not treat illustrative questions as product-wide marketing fields.

## Task 9.2: Produce an engagement-aware pain-point brief

**Objective:** Return actionable evidence rather than titles or generic summaries.

**Output:**
- Pain-point cluster.
- Independent voices and threads.
- Platforms and source health.
- Root/reply evidence with links.
- Upvote/like/repost context where supported.
- Propagation versus independent corroboration.
- Desired outcomes, workarounds, objections and exact audience language.
- Limitations and missing source coverage.

**Approval gate:** Run one bounded Cairn research fixture/local live test only after Vincent approves the Explore slice. Stop for review before generalizing the dashboard.

---

# Phase 10: Merge Projects/Zones with Research-Run Execution

## Task 10.1: Make Zone a durable scope, not a separate shallow engine

**Objective:** Resolve the two-pipeline divergence.

**Files:**
- Modify: `social_scraper/monitoring/zones.py`
- Modify: `social_scraper/monitoring/monitor.py`
- Modify: `social_scraper/discovery/staged_runner.py`
- Modify: `apis/scheduler.py`
- Test: `tests/monitoring/test_zones.py`
- Test: `tests/monitoring/test_monitor.py`
- Test: `tests/discovery/test_execution.py`

**Architecture:**
- Zone/Project stores scope, seeds, regions, platforms, perspective IDs, cadence and budget.
- Initial bounded read invokes the research-run engine.
- Standing reads invoke the same engine on deltas.
- Root, thread and horizontal evidence are shared with Explore families.
- No duplicated zone-specific search/analyze path.

## Task 10.2: Detect standing-read changes deterministically

**Objective:** Compare like-for-like periods before asking an LLM to interpret changes.

**Files:**
- Create: `social_scraper/analysis/change_detection.py`
- Modify: canonical conversation/discovery storage
- Test: `tests/analysis/test_change_detection.py`

**Metrics:**
- new unique roots/replies
- independent creator breadth
- topic-family/cluster share
- engagement distribution
- propagation/repost change
- entity mention share
- pain/behavior signal recurrence
- source coverage comparability

Missing periods and failed sources remain missing and suppress unsupported change claims.

---

# Phase 11: Rollout and Calibration

## Task 11.1: Run shadow radar and review funnel metrics

**Objective:** Decide real thresholds and budgets from observed data.

**Procedure:**
1. Enable daily Trends snapshots.
2. Enable weekly root sweeps.
3. Keep deep-read/LLM auto-execution disabled.
4. Review several comparable weekly runs.
5. Label promoted, exploration and rejected samples.
6. Compare candidate-family counts, route yield, overlaps, false positives and exploration discoveries.
7. Review projected source calls, prompt characters and LLM calls.
8. Version the promotion policy if thresholds change.

**No arbitrary success claim:** The desired result may be that no useful early trend appears in a given period.

## Task 11.2: Enable automatic deep reads behind hard caps

**Objective:** Turn on expensive stages only after shadow-mode approval.

**Requirements:**
- Per-run family cap.
- Per-workspace budget.
- Projected use shown before manual execution.
- Cache reuse first.
- Source-health fail closed.
- Kill switch for automatic deep reads.
- No optional custom interpretations by default.

## Task 11.3: Full verification and independent review

**Commands:**
```bash
python -m pytest tests/ -x -q
python -m py_compile apis/dashboard_api.py social_scraper/discovery/*.py
```

**UI QA:**
- Desktop 1440px.
- Mobile 375px.
- Empty/loading/error/partial/populated states.
- Console and network errors checked.
- Trend-family explanation and promotion reasons readable within five seconds.

**Independent review:**
- Data integrity and no interpolation.
- Token/source budget invariants.
- Topic-family double-count prevention.
- Repost versus independent voice separation.
- Cache invalidation correctness.
- Perspective isolation from canonical evidence.
- No unrelated runtime DBs, screenshots, WAL files or scratch scripts committed.

---

# Validation Matrix

```text
[ ] Daily Trends snapshots use zero LLM calls.
[ ] Weekly root sweeps use zero LLM and zero thread calls.
[ ] Raw candidates persist separately from topic families.
[ ] x402 and agentic payments can be linked with inspectable evidence.
[ ] Related terms can remain related-distinct rather than forced merged.
[ ] Google volumes are never summed across family members.
[ ] Duplicate roots are collected/stored/analyzed once per canonical identity.
[ ] Reposts count as propagation, not independent voices.
[ ] Daily repeated presence remains an explicit route.
[ ] Every automatic promotion passes universal gates plus one complete route.
[ ] Route fragments cannot accidentally combine into an implicit pass.
[ ] Exploration samples eligible non-promoted candidates.
[ ] Shadow mode projects cost but executes no automatic deep reads.
[ ] The LLM receives engagement/thread context for selected evidence.
[ ] One changed family bundle produces at most one horizontal synthesis call.
[ ] Unchanged evidence and perspective switches use zero LLM calls.
[ ] Users can copy/edit starter perspectives and create custom ones.
[ ] No perspective mutates or recollects canonical evidence.
[ ] Explore explains what, why now, evidence quality and limitations.
[ ] Cairn workflow reads posts, replies, upvotes/likes and reposts where available.
[ ] Missing platform metrics and failed routes remain explicit.
[ ] Research-runs become the common engine for Explore and Projects/Zones.
```

# Plan Relationship to Existing Documents

This plan supersedes the unimplemented product-order and UX portions of:
- `.hermes/plans/2026-08-10_101218-bounty-buzzabout-roadmap.md`
- `.hermes/plans/2026-08-10_134146-efficient-horizontal-analysis.md`

It preserves completed infrastructure from those plans: budgets, usage receipts, candidate history, staged scheduling, evidence cache, lens storage, research-run execution, findings persistence, projects and monitored subjects.

# First Execution Boundary

If Vincent approves execution, implement only through **Phase 5, Task 5.3**, then stop with a working local Global Explore prototype and shadow-mode evidence. Do not continue into automatic deep reads, perspectives expansion, Cairn validation or Zone migration without review and approval.
