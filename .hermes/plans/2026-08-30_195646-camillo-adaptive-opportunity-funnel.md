# Camillo-Style Adaptive Opportunity Funnel Implementation Plan

> **For Hermes:** Implement task-by-task with TDD and independent data-integrity review. Preserve strict trade gates. Never turn search/social activity directly into a financial conclusion.

**Goal:** Replace Bounty's broad fixed-panel one-shot screener with a Camillo-style observation → adaptive replication → historical anomaly → parity → company exposure funnel that continuously yields bounded investigation opportunities while allowing zero trade-ready ideas.

**Architecture:** Keep the 16 panels as a stable coverage scaffold, but split the pipeline into two products: a permissive, cited **Opportunity Queue** for specific observations worth investigating and a strict **Trade-Ready Gate** for verified behavior, persistence, materiality, awareness and implementation. Roots create seeds; deterministic anchor extraction and source-native follow-up collect independent roots/comments/replies before semantic triage. Historical baselines and information-parity checks happen before final qualification, not only after a candidate already passes current-evidence gates.

**Tech stack:** Python, FastAPI, SQLite, existing owned social connectors, existing discovery deep-read handlers, deterministic conversation deduplication/propagation utilities, bounded LLM triage with citation validation, vanilla JavaScript dashboard.

---

## Current diagnosis

Latest verified scan `5326c39d668e4f508deb822657f5814c`:

- 16 requested panels and 128 discovery scopes.
- 2,242 source-reported root records; 2,583 persisted records after expansion/corroboration.
- 1,896 X records; 61/64 X scopes capped.
- 292 TikTok records, of which 256 predated the stated 30-day cutoff.
- No persisted root/comment/reply distinction; operational evidence was effectively root-post evidence.
- 256 records entered the panel-balanced model input, capped at 16 per panel.
- 8 model proposals.
- 84 requested citations, only 17 exact subject-supporting citations retained.
- 0/8 passed behavior, evidence-quality or persistence gates.
- 0 historical-window receipts because window collection was skipped when preflight gates failed.
- Anomaly and information parity were unknown for all 8.
- 0 trade-ready leads.

This is not a market-with-no-opportunities result. It is a funnel problem:

1. Broad fixed queries collect large quantities of roots but little exact repeated behavior.
2. The LLM proposes candidates before adaptive investigation and comment collection.
3. Strict current-evidence gates fire before historical anomaly work.
4. Failed preflight prevents the follow-up that could resolve uncertainty.
5. Google Trends is overused as a seed selector despite weak product/behavior specificity.
6. The system has duplicate promotion/deep-read capabilities that the private Radar does not reuse.

## Camillo fidelity target

The rebuilt loop must behave like:

```text
specific observation seed
  → replicate across independent people/communities/geographies
  → read comments and reasons
  → seek competitors, substitutes and counter-explanations
  → compare speech with source-native historical norms
  → test whether the economic implication is already known
  → map full company exposure and offsets
  → trade research or explicit rejection
```

It must not behave like:

```text
broad panel query
  → collect top roots
  → ask model for one idea per panel
  → reject almost everything before deeper investigation
```

## Product contract

### Opportunity Queue

A cycle should normally produce a small ranked investigation queue, but each item must be labelled honestly:

- `seed_observation`
- `replication_underway`
- `retrospective_anomaly`
- `forward_confirming`
- `rejected`

An Opportunity Queue item is not a trade. It requires:

- a specific object/product/service/problem;
- a concrete behavior or operational observation;
- at least one cited source;
- explicit reason to investigate;
- explicit missing evidence and next action;
- no financial direction inferred from search/social volume.

### Trade-Ready Gate

Trade-ready may remain empty. It requires all existing hard gates plus:

- independently replicated behavior;
- comparable history;
- information parity below threshold;
- verified company/instrument exposure;
- primary-source economic support;
- materiality quantified or explicitly scenario-bounded;
- implementation constraints checked;
- contradiction and invalidation defined.

Never force a trade because the Opportunity Queue is populated.

---

## Phase 1: Make evidence semantics reliable

### Task 1: Preserve root/comment/reply provenance

**Files:**
- Modify: `social_scraper/investing/private_radar.py`
- Modify: `social_scraper/investing/owned_radar.py`
- Modify: `social_scraper/investing/storage.py`
- Test: `tests/investing/test_private_radar.py`
- Test: `tests/investing/test_owned_radar.py`
- Create: `tests/investing/test_adaptive_depth.py`

**Steps:**

1. Write failing fixtures containing roots, comments and replies.
2. Add additive evidence fields: `record_type`, `parent_external_id`, `root_post_external_id`, `thread_depth`, `query_lineage_id`, `community_id`, `creator_id`, `is_repost`, `copy_cluster_id`, `truncated`.
3. Update `_thread_evidence()` to preserve all available connector provenance.
4. Ensure a comment cannot silently become an independent root.
5. Ensure copied/reposted roots count as propagation, not independent voices.
6. Verify old database rows remain readable with null/default additive fields.

**Acceptance:** Stored evidence can deterministically distinguish roots, comments, replies, propagation and independent discussion roots.

### Task 2: Deduplicate before semantic triage

**Files:**
- Reuse/modify: `social_scraper/conversations/deduplication.py`
- Reuse/modify: `social_scraper/conversations/propagation.py`
- Modify: `social_scraper/investing/private_radar.py`
- Test: `tests/conversations/test_deduplication.py`
- Test: `tests/conversations/test_propagation.py`

**Steps:**

1. Freeze fixtures for URL duplicates, normalized-text duplicates, quoted reposts and genuinely independent paraphrases.
2. Apply deduplication before panel balancing or candidate proposal.
3. Preserve duplicate counts and propagation clusters as evidence metadata.
4. Prevent duplicate roots from inflating behavior, breadth or persistence.

**Acceptance:** The latest-scan fixture's 406 excess URL duplicates and 411 excess normalized-text duplicates cannot inflate candidate gates.

---

## Phase 2: Replace universal panel queries with source-native seed recipes

### Task 3: Version per-source query recipes

**Files:**
- Modify: `social_scraper/investing/private_radar.py`
- Modify: `social_scraper/investing/owned_radar.py`
- Create: `tests/investing/test_source_native_queries.py`

**Steps:**

1. Define `QueryRecipe` with panel, source, exact query, version, geography, language, sort/time filter and intended observation type.
2. Retain the four X behavior slices.
3. Replace natural-language non-X panel phrases with source-native forms:
   - Reddit: short noun/entity query plus discovered subreddit scope.
   - TikTok: short phrase/hashtag-native query.
   - Instagram: short keyword/phrase query.
   - YouTube: short phrase with explicit sort/time filter.
4. Persist recipe/version and source receipt for every collection.
5. Reject broad panel-only terms as candidate anchors.

**Acceptance:** Every stored root resolves to an exact versioned source-native query and comparable scope.

### Task 4: Keep Google Trends as seed metadata only

**Files:**
- Modify: `social_scraper/investing/google_discovery.py`
- Modify: `social_scraper/investing/private_radar.py`
- Test: `tests/investing/test_google_discovery.py`

**Steps:**

1. Preserve Google candidates, related terms, country and timing as seed metadata.
2. Require candidate-specific semantic anchors before social investigation.
3. Reject single generic country/person/event terms and query drift.
4. Do not let Google classification satisfy behavior, breadth or anomaly gates.

**Acceptance:** `usa` cannot validate `miss usa pageant contestants`; generic `japan` cannot become a behavior candidate without a specific economic observation.

---

## Phase 3: Add adaptive observation discovery before LLM triage

### Task 5: Deterministically extract observation anchors

**Files:**
- Create: `social_scraper/investing/adaptive_investigation.py`
- Modify: `social_scraper/investing/private_radar.py`
- Create: `tests/investing/test_adaptive_investigation.py`

**Steps:**

1. Build deterministic extraction for product/service/entity phrases, behavior phrases, problem/workaround phrases, hashtags, brands and communities.
2. Require an anchor to contain a specific object plus a behavior/problem, not only a panel term.
3. Select a bounded diverse anchor set per panel:
   - up to 4 high-support exact anchors;
   - up to 2 distinctive low-engagement exploration anchors;
   - no more than one near-duplicate anchor.
4. Persist lineage: `seed_query → source_root → extracted_anchor`.
5. Do not call an LLM during this seed-to-anchor stage.

**Acceptance:** Frozen fixtures recover specific behavior anchors missed by fixed panel vocabulary without promoting celebrity/news/perennial controls.

### Task 6: Select follow-up searches by information gain

**Files:**
- Modify: `social_scraper/investing/adaptive_investigation.py`
- Modify: `social_scraper/investing/owned_radar.py`
- Test: `tests/investing/test_adaptive_investigation.py`
- Test: `tests/discovery/test_budgets.py`

**Steps:**

1. Generate bounded source-native follow-ups for:
   - exact product/behavior;
   - reason/problem;
   - competitor/substitute;
   - rejection/cancellation/return language;
   - opposing explanation;
   - new geography/community.
2. Rank follow-ups by expected new authors, communities, platforms, behaviors and low copy overlap.
3. Cap calls by explicit per-candidate and per-source budgets.
4. Preserve every attempted query and receipt, including empty/partial/failure.

**Acceptance:** Collection budget shifts toward uncertainties that can confirm or reject a seed rather than scanning all categories equally.

### Task 7: Read bounded comments/replies before triage

**Files:**
- Modify: `social_scraper/investing/owned_radar.py`
- Reuse: `social_scraper/discovery/handlers.py`
- Test: `tests/investing/test_adaptive_depth.py`
- Test: `tests/connectors/test_owned_social_depth.py`
- Test: `tests/conversations/test_thread_reader.py`

**Steps:**

1. For accepted adaptive anchors, hydrate up to 2 roots per platform.
2. Read up to 20 comments/replies per root, depth 2.
3. Record truncation and platform-reported totals.
4. Preserve comments as conversational evidence, not roots.
5. Keep incomplete thread reads partial.
6. Include X replies where the owned connector can reconstruct conversation IDs.

**Acceptance:** Candidate triage sees reasons, objections, switching intent and counter-explanations from actual conversations.

---

## Phase 4: Cluster and triage the investigated corpus

### Task 8: Conservative observation clustering

**Files:**
- Modify: `social_scraper/investing/adaptive_investigation.py`
- Reuse/modify: `social_scraper/discovery/topic_families.py`
- Test: `tests/investing/test_adaptive_investigation.py`
- Test: `tests/discovery/test_topic_family_edges.py`

**Steps:**

1. Group by explicit alias, shared entity/product identity, URL/product identity and behavior/problem anchor.
2. Keep alternative/substitute/enabler relationships as edges, not forced merges.
3. Never merge solely on temporal or geographic co-movement.
4. Version cluster splits/merges and preserve source lineage.

**Acceptance:** Cluster purity beats current panel-level LLM proposal purity on a frozen labelled fixture.

### Task 9: Run one cited triage call per retained cluster

**Files:**
- Modify/reuse: `social_scraper/discovery/triage.py`
- Modify: `social_scraper/investing/private_radar.py`
- Test: `tests/discovery/test_triage.py`
- Test: `tests/investing/test_adaptive_investigation.py`

**Steps:**

1. Supply deduplicated roots/comments/replies, source metadata, query lineage and limitations.
2. Require evidence IDs for every observation, reason, contradiction and mechanism.
3. Reject unknown/invalid citations.
4. Keep failed/malformed model output internal.
5. Remove the one-candidate-per-panel restriction; cap globally by evidence-backed cluster quality and investigation budget.

**Acceptance:** The LLM interprets an already-investigated corpus instead of inventing the candidate before collection.

---

## Phase 5: Build comparable history before hard qualification

### Task 10: Persist source-native historical baselines

**Files:**
- Modify: `social_scraper/investing/storage.py`
- Modify: `social_scraper/investing/owned_radar.py`
- Modify: `social_scraper/investing/sweep.py`
- Create: `tests/investing/test_historical_baselines.py`

**Steps:**

1. Persist query/scope/version/geography/language for every historical window.
2. Track roots, independent authors, creators, communities, comments and source-native engagement distributions.
3. Backfill comparable windows where provider timestamps support it.
4. Separate stale search results from current-window observations; enforce a common cutoff per source receipt.
5. Add lifecycle states: `building_baseline`, `retrospective_anomaly`, `forward_confirming`, `fading`.
6. Do not skip historical collection merely because a current-evidence preflight gate failed; use history to resolve uncertainty.

**Acceptance:** Anomaly is no longer universally unknown, and TikTok rows outside the stated cutoff cannot count as current evidence.

### Task 11: Preserve strict qualification as the trade gate

**Files:**
- Modify minimally: `social_scraper/investing/qualification.py`
- Test: `tests/investing/test_qualification.py`
- Create: `tests/investing/test_trade_gate.py`

**Steps:**

1. Keep independent-author, firsthand, persistence, anomaly, breadth, parity and investigability requirements.
2. Add explicit evidence namespace checks: search attention cannot count as conversation behavior.
3. Distinguish Opportunity Queue status from trade qualification.
4. Require verified exposure/materiality/parity before `trade_research_eligible`.

**Acceptance:** Opportunity recall may rise while unsupported-trade-pass rate remains zero.

---

## Phase 6: Real parity and company exposure

### Task 12: Implement implication-level information parity

**Files:**
- Reuse/modify: `social_scraper/discovery/market_awareness.py`
- Modify: `social_scraper/investing/owned_radar.py`
- Modify: `social_scraper/investing/private_radar.py`
- Test: `tests/discovery/test_market_awareness.py`
- Test: `tests/investing/test_trade_gate.py`

**Steps:**

1. Search the exact behavior plus economic implication, not only the keyword.
2. Record checked universes: consumer, specialist, financial, company disclosure, transcript and price reaction where defensible.
3. Implement L0-L5 as a coverage object with limitations.
4. Never claim analyst silence from sampled public news.

**Acceptance:** Parity is no longer universally unknown and no keyword-only article falsely closes the edge.

### Task 13: Map complete listed exposure downstream

**Files:**
- Modify: `social_scraper/investing/generic_dossier.py`
- Modify: `social_scraper/investing/research_runner.py`
- Create: `social_scraper/investing/exposure_mapping.py`
- Test: `tests/investing/test_trade_gate.py`
- Test: `tests/investing/test_generic_dossier.py`

**Steps:**

1. Preserve human confirmation of ambiguous company/ticker mappings.
2. Map product/service → brand → parent/operator/licensee/supplier → segment/geography → instrument.
3. Include competitors, substitutes and offsetting segments.
4. Keep primary facts, calculations, assumptions and unavailable inputs separate.
5. Keep common stock, ADR, pair, supplier, options or no-position as valid outputs.

**Acceptance:** A candidate cannot become trade-research eligible from a brand mention alone.

---

## Phase 7: Product and scheduling

### Task 14: Add the Opportunity Queue UI

**Files:**
- Modify: `apis/investing_dashboard_page.py`
- Modify: `public/investing-dashboard.js`
- Modify: `public/investing-dashboard.css`
- Test: `tests/test_investing_dashboard_product.py`

**Steps:**

1. Add a separate lane above the strict audit trail for specific observations under investigation.
2. Show status, exact evidence, why now, missing evidence, next adaptive action and rejection condition.
3. Never label these as trade-ready.
4. Keep the strict qualified lane visually distinct.
5. Preserve mobile per-card 3M/1Y/5Y controls and query relevance gates.

**Acceptance:** A zero-trade cycle is still useful because users can see cited opportunity investigations without filler.

### Task 15: Schedule seed → investigation continuation

**Files:**
- Modify: `apis/scheduler.py`
- Modify: `social_scraper/investing/sweep.py`
- Modify: `social_scraper/investing/storage.py`
- Test: `tests/investing/test_scheduler.py`
- Test: `tests/discovery/test_radar_scheduler.py`

**Steps:**

1. Run cheap seed collection daily.
2. Enqueue bounded adaptive investigations for new/changed anchors.
3. Run deeper investigations weekly or when information-gain thresholds trigger.
4. Use durable leases, idempotency and stale-run recovery.
5. Customer reads remain cached and make zero upstream calls.

**Acceptance:** Scheduled seeds no longer terminate without entering a durable investigation queue.

---

## Phase 8: Blind validation and release

### Task 16: Build frozen blind holdouts

**Files:**
- Create: `tests/fixtures/investing/adaptive_holdout.json`
- Create: `tests/fixtures/investing/adaptive_negative_controls.json`
- Create: `tests/investing/test_blind_evaluation.py`

**Holdout requirements:**

- frozen time windows;
- geography/platform stratification;
- no hand-picked winners;
- celebrity/news/sport/perennial/promotional/search-only negative controls;
- expert labels hidden from ranking code;
- separate development and final holdout.

### Task 17: Compare old versus adaptive funnel

**Metrics:**

- novel-anchor rate;
- unknown-unknown recall@K;
- seed-to-anchor yield;
- behavior attachment accuracy;
- independent-author/community precision;
- comment/reply evidence yield;
- cluster purity and weekly continuity;
- perennial/noise rejection;
- blinded promotion precision@K;
- comparable-window completeness;
- next-two-cycle persistence and reversal detection;
- unsupported-trade-pass rate, target zero;
- search-only-to-lead rate, target zero;
- calls and wall-clock per retained investigation.

**Release criterion:** Non-inferior blinded promotion precision and zero safety regressions while materially increasing novel-anchor/unknown-unknown recall at the same bounded collection budget.

### Task 18: Run a blind fresh production scan

1. Freeze recipe, panel and model versions.
2. Run mandatory source canaries.
3. Run one fresh seed cycle and bounded adaptive investigations.
4. Do not tune thresholds after seeing the output.
5. Review Opportunity Queue and trade-ready outputs separately.
6. Run full tests, desktop/mobile QA, secret scan and independent data-integrity review.
7. Deploy only after the blind results pass.

---

## Immediate implementation slice

The first release should be deliberately smaller than the full architecture:

1. Add provenance fields for roots/comments/replies.
2. Add source-native non-X recipes.
3. Add deterministic anchor extraction.
4. Re-query up to 4 anchors per panel.
5. Hydrate up to 2 roots per platform with 20 comments/replies.
6. Run citation-backed triage after depth collection.
7. Persist an Opportunity Queue item even when strict qualification fails, provided it has a specific cited observation and explicit next action.
8. Keep trade-ready gates unchanged.
9. Evaluate on frozen negative controls and one blind fresh scan.

This slice directly attacks the observed 2,583 → 8 → 0 failure without weakening evidence integrity.

## Risks

- Owned platform sessions can rate-limit or expire; adaptive budgets must remain bounded.
- Comment retrieval may be partial or unavailable; missingness cannot become negative evidence.
- Deterministic anchors can fragment aliases; conservative family edges are preferred over aggressive merging.
- Opportunity Queue growth can become noise if specificity and citation requirements are weak.
- Historical data from ranked search is not a firehose; label coverage honestly.
- More ideas do not guarantee more trades. The system must optimize worthwhile investigations, not dashboard population.
