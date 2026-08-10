# Bounty Buzzabout-Parity Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build Bounty into a horizontal, evidence-backed social research SaaS whose first optimized workflow is investment social arbitrage: discover emerging behavior through Google Trends and social conversation, determine whether it may still be informationally early, and monitor it through bounded and standing reads.

**Architecture:** Treat Discovery and Zones as co-primary workflows over one horizontal evidence engine. Preserve immutable normalized posts, comments, trends observations, market-awareness observations, and collection provenance. Discovery generates and ranks unknown candidates; bounded and standing reads investigate and monitor them. Investing-specific novelty, materiality, public-information parity, and long/short triage live in an optional lens above generic conversation analysis.

**Tech stack:** Python 3.11, FastAPI, SQLite/WAL for local validation, existing source broker/connectors, GPT-5.4 through the shared LLM client for local analysis, local BGE-small embeddings plus scikit-learn/HDBSCAN-equivalent clustering, vanilla HTML/CSS/JavaScript dashboard, pytest.

**Primary benchmark:** [Buzzabout's Google Trends alternatives methodology](https://buzzabout.ai/blog/google-trends-alternatives)

---

## Product benchmark and non-goals

### Outcomes to reproduce

1. A user defines one zone with four or five seed phrases.
2. Bounty reads a historical corpus across available platforms without pretending uneven coverage is even.
3. Bounty retrieves comments and nested replies, not only parent posts.
4. Bounty groups the corpus into meaningful themes.
5. Each theme explains beliefs, questions, dissent, entities, and representative evidence.
6. A recurring weekly read detects rising negativity, entity changes, belief shifts, and themes starting or ending.
7. Every answer or alert links back to the supporting source material.
8. Google Trends Discovery identifies unknown candidates and records how fresh and persistent each signal is.
9. An investing lens distinguishes behavior from sentiment, maps possible company exposure, checks headline/price awareness, and rejects stale or immaterial signals.

### First customer workflow: investment social arbitrage

The first optimized workflow is not generic stock sentiment. It looks for an observed change in consumer behavior or belief that may transmit into a company's revenue, margins, retention, inventory, or reputation before the market fully absorbs it.

The investment lens must:

- Scan positive and negative behavior symmetrically.
- Separate actions (`bought`, `switched`, `cancelled`, `returned`, `sold out`) from opinions or generic complaints.
- Distinguish fresh change from perennial content, political noise, fandom rivalry, and already-mainstream stories.
- Identify the possible company/ticker and economic transmission mechanism without treating entity matching as proof of materiality.
- Check contemporaneous headlines and price behavior before calling a signal early.
- Preserve an explicit `unknown/not established` state for materiality and market awareness.
- Produce research leads, not automatic trade recommendations.

### Explicit non-goals until the core read is validated

- New x402 endpoints
- Railway migration of the full scraper stack
- Subscription billing
- Enterprise team features
- LinkedIn collection
- Perfect parity across every platform
- Generic Discovery cosmetics that do not improve signal discovery, evidence, novelty, or market-awareness assessment
- Implementing marketing-specific tags such as hook and CTA before core research dimensions prove useful

---

## Phase 0: Establish a trustworthy baseline

### Task 0.1: Move monitoring tests out of scratch

**Objective:** Make the current monitoring behavior reproducibly testable.

**Files:**
- Move/adapt: `tmp/test_monitoring.py`
- Create: `tests/monitoring/test_monitor.py`
- Create: `tests/monitoring/test_zones.py`
- Create: `tests/monitoring/conftest.py`

**Steps:**
1. Convert all current scratch assertions into isolated pytest fixtures using temporary SQLite databases.
2. Add tests for zone CRUD, due scheduling, snapshot persistence, and first-run/no-previous-snapshot behavior.
3. Run `python -m pytest tests/monitoring/test_monitor.py tests/monitoring/test_zones.py -v` and verify all tests pass before feature changes.
4. Commit as `test: establish monitoring baseline`.

### Task 0.2: Add source-health acceptance fixtures

**Objective:** Ensure partial platform failures stay visible and do not discard successful data.

**Files:**
- Create: `tests/fixtures/social/`
- Create: `tests/monitoring/test_source_health.py`
- Modify if necessary: `social_scraper/broker.py`

**Acceptance criteria:**
- One failed connector does not erase successful posts from other connectors.
- Reports identify attempted, successful, empty, and failed sources separately.
- No test fixture invents unavailable engagement metrics.

---

## Phase 1: Canonical conversation corpus

**Status:** Completed 2026-08-10. The canonical layer is additive to the existing public broker/API contract. `ObservationStore` writes normalized records and route attempts in the same transaction as legacy observations; zone runs then link those records to the exact zone and seed keyword before monitor-only tags are added.

### Task 1.1: Define canonical post, comment, and reply records

**Objective:** Create one schema capable of representing complete conversation trees across platforms.

**Files:**
- Create: `social_scraper/conversations/models.py`
- Create: `social_scraper/conversations/__init__.py`
- Create: `tests/conversations/test_models.py`

**Required fields:**
- `platform`
- `source_route`
- `external_id`
- `parent_external_id`
- `root_post_external_id`
- `record_type` (`post`, `comment`, `reply`)
- `depth`
- `author_external_id` when available
- `author_display_name` when available
- `text`
- `title`
- `url`
- `published_at`
- `collected_at`
- `engagement` with nullable source-native fields
- `language`
- `is_repost` nullable
- `repost_of_external_id` nullable
- `raw_payload_hash`

**Acceptance criteria:**
- Missing values remain `null`, not zero.
- Parent-child relationships reconstruct deterministically.
- The same external item collected twice produces the same identity key.

### Task 1.2: Persist immutable conversation records

**Objective:** Store raw normalized records independently from derived clusters and reports.

**Files:**
- Create: `social_scraper/conversations/storage.py`
- Create: `tests/conversations/test_storage.py`
- Modify: `social_scraper/monitoring/zones.py`

**Tables:**
- `conversation_records`
- `collection_runs`
- `collection_run_sources`
- `zone_record_membership`

**Acceptance criteria:**
- Re-collecting an item updates observation metadata only through an explicit observation table; raw historical records are not silently overwritten.
- Every record traces to a run, zone, keyword, platform, and route.
- SQLite WAL and busy timeout remain enabled.

### Task 1.3: Normalize existing connector output

**Objective:** Pass all five current connectors through one canonical normalizer.

**Files:**
- Create: `social_scraper/conversations/normalize.py`
- Modify: `social_scraper/broker.py`
- Create: `tests/conversations/test_normalize.py`

**Verification:** Run fixture-based tests for YouTube, Reddit, TikTok, X, and Instagram. Explicitly assert each platform's unsupported fields remain null.

---

## Phase 1B: Investing-first Google Trends Discovery

### Task 1B.1: Persist Google Trends candidate observations

**Objective:** Distinguish a genuinely new or accelerating candidate from a keyword that repeatedly appears in the feed.

**Files:**
- Create: `social_scraper/discovery/models.py`
- Create: `social_scraper/discovery/storage.py`
- Modify: `social_scraper/monitoring/topdown.py`
- Create: `tests/discovery/test_candidate_history.py`

**Persist per observation:**
- Keyword and related terms
- Geography and category
- Observed search volume and growth, preserving missing values
- Trend start timestamp supplied by the source
- Bounty first-seen and last-seen timestamps
- Consecutive observations and observation gaps
- Conversation-gate result and source health

Do not infer a smooth history between observations. A candidate that disappears and later returns must retain the gap.

### Task 1B.2: Add horizontal behavior and novelty classification

**Objective:** Rank candidates by whether they reflect observable action and fresh change rather than generic attention.

**Files:**
- Create: `social_scraper/discovery/triage.py`
- Modify: `social_scraper/monitoring/conversation_reader.py`
- Create: `tests/discovery/test_triage.py`

**Generic output fields:**
- `behavior_type`: observed action, intended action, sentiment only, informational discussion, unknown
- `direction`: positive, negative, mixed, neutral, unknown
- `novelty`: new change, accelerating recurrence, perennial, event spike, unknown
- `durability_evidence`
- `independent_voice_count`
- `entities`
- `products`
- `representative_record_ids`
- `limitations`

The schema remains useful to non-investing customers. Investing-specific interpretation consumes these fields rather than changing them.

### Task 1B.3: Add an optional investment social-arbitrage lens

**Objective:** Determine whether a candidate deserves investment research without pretending it is already a trade.

**Files:**
- Create: `social_scraper/lenses/investing.py`
- Create: `social_scraper/lenses/__init__.py`
- Create: `tests/lenses/test_investing.py`

**Output fields:**
- Candidate public companies/tickers with mapping confidence
- Possible economic transmission mechanism: revenue, price/mix, margin, retention, inventory, reputation, or unknown
- Preliminary materiality: plausible, weak, not established
- Signal direction: potential long, potential short, mixed, or no directional inference
- Signal freshness
- Invalidating evidence
- Required next diligence

Hard rule: entity/ticker matching does not establish economic exposure or materiality.

### Task 1B.4: Check public-information parity

**Objective:** Identify whether headlines or price behavior suggest that the candidate is already broadly known.

**Files:**
- Create: `social_scraper/discovery/market_awareness.py`
- Create: `tests/discovery/test_market_awareness.py`
- Reuse current Google News RSS and market-data utilities where appropriate

**Evidence:**
- Earliest observed social/search timestamp
- Relevant headline timestamps and URLs
- Company price movement over explicitly stated windows
- Earnings/announcement proximity when available
- Data retrieval timestamps and source health

**Classification:**
- `social_only`
- `niche_coverage`
- `mainstream_coverage`
- `company_acknowledged`
- `unknown`

A public signal is not automatically absorbed, but Bounty must never call it early without showing this comparison. Price movement is context, not proof of causality.

### Task 1B.5: Rank Discovery for edge, not popularity

**Objective:** Make the Discovery page surface fresh, durable, potentially material behavior changes before generic high-volume trends.

**Files:**
- Modify: `apis/dashboard_api.py`
- Modify: `apis/dashboard_page.py`
- Create: `tests/discovery/test_ranking.py`

**Ranking gates:**
1. Collection/source health
2. Social conversation exists
3. Behavior evidence exists
4. Novelty/durability survives obvious-noise filters
5. Possible company exposure exists
6. Materiality is at least plausible
7. Public-information parity is not already mainstream/company-acknowledged

Show long and short candidates symmetrically. Rejected candidates remain inspectable with a reason rather than disappearing silently.

**Approval gate:** Review at least twenty real candidates. The desired result is allowed to be “no actionable early signals.” False scarcity is better than manufactured conviction.

---

## Phase 2: Comment thread and reply analysis

### Task 2.1: Define a connector thread-reader contract

**Objective:** Add an optional capability without breaking connectors that cannot retrieve comments.

**Files:**
- Modify: `social_scraper/base.py`
- Create: `social_scraper/conversations/thread_reader.py`
- Create: `tests/conversations/test_thread_reader.py`

**Contract:**
```python
async def fetch_thread(
    post: ConversationRecord,
    max_comments: int,
    max_depth: int,
) -> ThreadFetchResult:
    ...
```

`ThreadFetchResult` must include records, truncation status, attempted route, error category, and platform-reported total comments when available.

### Task 2.2: Integrate YouTube comment threads

**Objective:** Retrieve top-level comments and replies for collected YouTube videos.

**Files:**
- Modify the active YouTube connector under `social_scraper/connectors/`
- Create: `tests/connectors/test_youtube_threads.py`
- Add immutable real-response fixture under `tests/fixtures/social/youtube/`

**Acceptance criteria:**
- Parent-child relationships are retained.
- A disabled-comments video returns an explicit status, not an empty-success ambiguity.
- Pagination and truncation are reported.
- URLs point back to the source video/comment when possible.

### Task 2.3: Integrate Reddit comment trees

**Objective:** Retrieve Reddit comments and nested replies using the most reliable existing local route.

**Files:**
- Modify the active Reddit connector(s) under `social_scraper/connectors/`
- Reuse/adapt: `social_scraper/connectors/reddit_camoufox.py`
- Create: `tests/connectors/test_reddit_threads.py`
- Add immutable fixtures under `tests/fixtures/social/reddit/`

**Acceptance criteria:**
- Nested depth is preserved.
- Deleted authors/text remain explicitly missing.
- More-comments placeholders are either resolved or marked truncated.
- Route provenance identifies Camoufox, JSON, OAuth, or fallback behavior.

### Task 2.4: Add thread-aware conversation analysis

**Objective:** Make GPT-5.4 distinguish the initiating claim from agreement, dissent, questions, and side discussions in replies.

**Files:**
- Modify: `social_scraper/monitoring/conversation_reader.py`
- Create: `social_scraper/conversations/analysis.py`
- Create: `tests/conversations/test_analysis.py`

**Output schema:**
- `summary`
- `dominant_beliefs`
- `dissenting_beliefs`
- `unanswered_questions`
- `pain_points`
- `desired_outcomes`
- `entities`
- `sentiment_distribution`
- `representative_quotes` with record IDs
- `coverage` and `limitations`

**Verification:** Use hand-labeled fixtures containing sarcasm, disagreement, nested correction, deleted comments, and mixed sentiment. Require strict JSON validation and citation IDs that exist in the input corpus.

**Approval gate:** Demonstrate one real YouTube thread and one real Reddit thread in a local report. Confirm that the report captures arguments found only in replies before proceeding.

---

## Phase 3: Bounded historical zone read

### Task 3.1: Extend the zone definition

**Objective:** Support an explicit baseline read instead of a shallow fixed-count search.

**Files:**
- Modify: `social_scraper/monitoring/zones.py`
- Modify: `apis/dashboard_api.py`
- Modify: `apis/dashboard_page.py`
- Create: `tests/monitoring/test_zone_scope.py`

**Add:**
- `lookback_days`
- `target_posts`
- `max_comments_per_post`
- `max_reply_depth`
- `baseline_status`

**Rules:** Recommend four or five seed phrases but do not hardcode an industry. Show an estimated scope before running. Treat a corpus smaller than the target as the size of the available niche, not as failure.

### Task 3.2: Build a durable collection-job state machine

**Objective:** Replace in-memory staged jobs with persistent, restart-safe run state and real source-level progress.

**Files:**
- Create: `social_scraper/jobs/models.py`
- Create: `social_scraper/jobs/storage.py`
- Modify: `apis/dashboard_api.py`
- Modify: `apis/dashboard_page.py`
- Create: `tests/jobs/test_collection_jobs.py`

**States:** `queued`, `collecting_posts`, `collecting_threads`, `normalizing`, `clustering`, `analyzing`, `complete`, `partial`, `failed`.

**UI acceptance criteria:** Display each platform as waiting, running, complete, empty, partial, or failed with real record counts. Do not convert stages into fake percentages if total work is unknown.

### Task 3.3: Execute the bounded read

**Objective:** Collect the zone across all selected platforms and persist the complete baseline corpus.

**Files:**
- Create: `social_scraper/monitoring/bounded_read.py`
- Modify: `social_scraper/monitoring/monitor.py`
- Create: `tests/monitoring/test_bounded_read.py`

**Acceptance criteria:**
- Search every seed phrase on every selected platform unless a source explicitly fails.
- Deduplicate cross-keyword results.
- Fetch comments only after parent-post deduplication.
- Persist source failures and truncation.
- Produce a run manifest before analysis begins.

**Approval gate:** Run one neutral horizontal test zone. Review corpus relevance, source mix, missing coverage, and elapsed time before implementing semantic clustering.

---

## Phase 4: Semantic clustering and cluster narratives

### Task 4.1: Generate local semantic embeddings

**Objective:** Replace lexical overlap with token-cheap semantic representations.

**Files:**
- Create: `social_scraper/analysis/embeddings.py`
- Create: `tests/analysis/test_embeddings.py`
- Modify: dependency manifest used by the repository

**Approach:** Start with `BAAI/bge-small-en-v1.5` locally. Cache vectors by normalized-text hash. Do not send every full post and reply to GPT.

### Task 4.2: Cluster posts and thread summaries

**Objective:** Group semantically equivalent conversations even when vocabulary differs.

**Files:**
- Create: `social_scraper/analysis/clustering.py`
- Modify: `social_scraper/monitoring/monitor.py`
- Create: `tests/analysis/test_clustering.py`

**Acceptance criteria:**
- Noise/outlier records may remain unclustered.
- Clusters retain all source record IDs.
- Cluster evaluation fixture measures pairwise precision/recall against hand labels.
- Thresholds live in configuration and are recorded per run.

### Task 4.3: Produce one structured narrative per meaningful cluster

**Objective:** Turn clusters into evidence-backed research findings.

**Files:**
- Create: `social_scraper/analysis/cluster_reader.py`
- Modify: `social_scraper/llm_client.py` only if structured-output support is required
- Create: `tests/analysis/test_cluster_reader.py`

**Core dimensions:**
- Topic and concise label
- Dominant narrative
- Dissent and counterarguments
- Intent
- Emotion and sentiment distribution
- Entities/brands/products
- Questions and unmet needs
- Representative citations
- Source/platform distribution
- Coverage limitations

Defer marketing-only `hook type` and `CTA` unless validation users ask for them.

### Task 4.4: Maintain stable cluster identity

**Objective:** Match current clusters to prior clusters semantically rather than hashing current members.

**Files:**
- Create: `social_scraper/analysis/cluster_identity.py`
- Modify: `social_scraper/monitoring/zones.py`
- Create: `tests/analysis/test_cluster_identity.py`

**Acceptance criteria:** Similar themes keep an ID as membership changes. Merges and splits are represented explicitly. Uncertain matches remain new rather than forced.

**Approval gate:** Compare old lexical output and new semantic output for three real corpora. Approve only if clusters are materially more coherent and cited narratives match source records.

---

## Phase 5: Weekly standing read

### Task 5.1: Store comparable period metrics

**Objective:** Calculate change from observed data without using an LLM for arithmetic.

**Files:**
- Create: `social_scraper/analysis/metrics.py`
- Modify: `social_scraper/conversations/storage.py`
- Create: `tests/analysis/test_metrics.py`

**Metrics:**
- New unique posts/comments per period
- Cluster share of corpus
- Sentiment distribution by cluster
- Entity mention count and share
- Platform mix
- Engagement per age-normalized observation where timestamps support it

Missing periods remain missing. No interpolation.

### Task 5.2: Implement the four change detectors

**Objective:** Detect events that can change a user's decision.

**Files:**
- Create: `social_scraper/monitoring/change_detection.py`
- Modify: `social_scraper/monitoring/monitor.py`
- Create: `tests/monitoring/test_change_detection.py`

**Events:**
1. Rising negative sentiment
2. Brand or competitor mention change
3. Belief or narrative shift
4. Theme starting or ending

Each event must include current evidence, prior comparison, affected cluster, magnitude, source coverage, and confidence. Thresholds must require minimum comparable sample sizes.

### Task 5.3: Build the weekly report

**Objective:** Show what changed, why it matters, and the evidence without forcing users to reread the baseline.

**Files:**
- Create: `social_scraper/monitoring/standing_read.py`
- Modify: `apis/dashboard_api.py`
- Modify: `apis/dashboard_page.py`
- Create: `tests/monitoring/test_standing_read.py`

**Report order:**
1. Material changes
2. New or ending themes
3. Belief and sentiment shifts
4. Entity movement
5. Stable background themes
6. Source-health and comparability caveats

**Approval gate:** Let three zones run weekly for at least three comparable observations before asserting useful velocity behavior.

---

## Phase 6: Reposts and propagation

### Task 6.1: Normalize native repost metadata

**Objective:** Preserve explicit repost/share relationships where platforms expose them.

**Files:**
- Modify connector normalizers under `social_scraper/connectors/`
- Modify: `social_scraper/conversations/models.py`
- Create: `tests/conversations/test_reposts.py`

### Task 6.2: Detect probable copied or cross-posted content

**Objective:** Separate repeated propagation from independent corroboration.

**Files:**
- Create: `social_scraper/analysis/propagation.py`
- Create: `tests/analysis/test_propagation.py`

**Approach:** Use normalized URL matching, explicit native IDs, text hashes, and semantic similarity. Label uncertain relationships as probable; never state identity as fact without a stable source relation.

### Task 6.3: Add propagation context to cluster reports

**Objective:** Explain whether a theme reflects many independent voices or a small number of amplified originals.

**Acceptance criteria:** Reports expose original count, repost/copy count, platform spread, earliest observed source, and uncertainty.

---

## Phase 7: Grounded zone Q&A

### Task 7.1: Build citation-first retrieval

**Objective:** Retrieve relevant posts, comments, and cluster narratives for a user question.

**Files:**
- Create: `social_scraper/analysis/retrieval.py`
- Create: `tests/analysis/test_retrieval.py`

### Task 7.2: Generate answers that cannot cite nonexistent records

**Objective:** Answer natural-language questions strictly from the stored zone corpus.

**Files:**
- Create: `social_scraper/analysis/qa.py`
- Modify: `apis/dashboard_api.py`
- Modify: `apis/dashboard_page.py`
- Create: `tests/analysis/test_qa.py`

**Acceptance criteria:** Every factual statement has at least one valid record citation. If evidence is insufficient or contradictory, the answer says so.

---

## Phase 8: Validation before commercialization

### Task 8.1: Choose three validation zones

Use one zone each for:
- Investing/consumer behavior as the first and deepest validation workflow
- Brand or product research
- A non-commercial cultural/technology topic

The implementation must remain identical across all three.

### Task 8.2: Run a four-week validation protocol

Track:
- Relevant-record precision from a hand-labeled sample
- Thread retrieval success and truncation by platform
- Cluster coherence from manual review
- Citation validity
- Change-alert precision
- Source availability
- Cost and elapsed time per zone
- Whether the report changes a user's understanding or decision

Store results under `docs/validation/` with source URLs and no interpolated weeks.

### Task 8.3: Make the production decision

Only after validation, decide:
- Production job queue and database
- LLM API provider and model split
- Authentication and tenant isolation
- Pricing basis
- Billing
- Deployment platform
- API access tier

---

## Recommended execution order

1. Phase 0 baseline tests
2. Phase 1 canonical corpus
3. Phase 1B Google Trends history, behavior triage, and investing lens
4. Review twenty real Discovery candidates and calibrate false positives
5. Phase 2 YouTube and Reddit replies, then feed reply evidence back into Discovery durability
6. Phase 3 bounded read
7. Stop for corpus-quality approval
8. Phase 4 semantic clustering and narratives
9. Stop for analysis-quality approval
10. Phase 5 standing read
11. Run investing-first multi-week validation while building Phase 6 propagation
12. Validate the unchanged horizontal engine on two non-investing zones
13. Add Q&A only after citations and retrieval are dependable
14. Commercial infrastructure last

## Principal risks

- **Connector fragility:** Mitigate with immutable fixtures, route provenance, and explicit partial status.
- **Retrospective selection bias:** Platform search over-represents popular winners. Label the baseline as a relevance-sampled historical read and rely on forward weekly collection for cleaner change measurement.
- **LLM hallucination:** Require record IDs in structured outputs and reject unknown citations.
- **Fake velocity:** Require comparable observations and minimum samples; leave gaps visible.
- **Token cost:** Embed locally, summarize threads once, analyze meaningful clusters only, and cache by content hash.
- **Overcopying Buzzabout:** Match the user outcome, not every implementation detail. Bounty should differentiate through transparent coverage, stronger provenance, citation discipline, and eventual agent/API access.
- **Mistaking public information for edge:** Every investing candidate must show contemporaneous headline and price-awareness evidence. If absorption cannot be assessed, label it unknown rather than early.
- **Turning Bounty into a stock screener:** Keep collection, behavior, novelty, and conversation analysis horizontal. Company mapping and materiality belong in the optional investing lens.

## Definition of core-product success

An investing user can discover an emerging Google Trends candidate, verify that it reflects fresh social behavior rather than noise, see whether headlines or price already appear aware of it, promote it into a bounded zone, inspect coherent themes with real reply evidence and source limitations, and receive weekly reports identifying what materially changed. The same evidence engine must work without investing-specific assumptions for non-investing zones.
