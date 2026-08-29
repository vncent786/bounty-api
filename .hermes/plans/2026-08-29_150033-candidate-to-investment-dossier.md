# Candidate-to-Investment Dossier Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. Do not begin implementation until Vincent approves this plan. Preserve the current private Radar and last-known-good snapshot throughout.

**Goal:** Extend Bounty from a cited behavioral-candidate Radar into a human-reviewed investment research workflow that can map a signal to economic exposure, assess materiality from primary disclosures, test information parity and price context, and produce a source-grounded trade memo without fabricating precision.

**Architecture:** Build an append-only sequence of versioned research artifacts above the existing private Radar: `Candidate → Exposure → Disclosure → Materiality → Information Parity → Price/Expectations → Investment Memo → Standing Read`. Each stage has its own evidence, source-health receipt, explicit missing-data state, and deterministic gate. Common stock, ADRs, and other listed exposures are valid; options are optional implementation metadata, not an eligibility gate.

**Tech Stack:** Existing Python/FastAPI/SQLite/vanilla-JS stack; official filing and exchange sources; issuer investor-relations documents; provider-neutral transcript and market-data adapters; existing immutable social evidence, citation validation, leases, and source-health patterns.

---

## 1. Decisions frozen before implementation

1. **Bounty generates research dossiers, not automatic trades.** No order execution is in scope.
2. **Options are preferred when attractive, but never required.** Instrument output supports common stock, ADR/GDR, optional options, relevant listed suppliers/competitors, or `no_public_instrument`.
3. **Direction is not assigned from a social observation alone.** Maintain separate conclusions for:
   - consumer value;
   - observed consumer behavior;
   - company economics;
   - market expectations.
4. **A true trend is not necessarily a positive company signal.** A reward can improve retention, merely reclassify existing heavy users, or cost more than the incremental economics it creates.
5. **No universal worldwide-completeness claim.** Coverage is issuer- and jurisdiction-specific. Each source class can be `complete`, `partial`, `unavailable`, `not_disclosed`, `failed`, or `not_applicable`.
6. **No fabricated materiality.** Search interest, post counts, views, membership counts, or segment association cannot be translated into revenue unless a source-supported bridge exists.
7. **No fake sell-side silence.** If licensed analyst research or point-in-time consensus is unavailable, parity remains `unknown_for_analyst_coverage`.
8. **Every factual memo claim resolves to an immutable source record.** LLMs may suggest mappings and draft prose but cannot establish company identity, reported numbers, materiality, parity, or price facts.
9. **The current Radar gates remain intact.** This workflow starts only after a candidate is selected for investigation; it does not weaken behavior, persistence, anomaly, breadth, or citation requirements.
10. **Human review is mandatory before `investment_ready`.** Review decisions are append-only and cannot edit raw evidence.

---

## 2. Costco Executive membership as the calibration case

### 2.1 What the social observation means

“Members say Executive is worth it because the 2% reward covers the upgrade” is not inherently bullish or bearish for Costco.

It could be:

- **Company-positive:** the reward encourages profitable upgrades, retention, or incremental shopping; incremental membership fees and merchandise contribution exceed rewards and benefit costs.
- **Company-neutral:** Costco is mainly reclassifying households that were already heavy shoppers; the incremental fee approximately offsets reward cost, with little behavioral change.
- **Company-negative:** rewards and added benefits subsidize spending that would have happened anyway, or members later downgrade/cancel after poor reward value.

The first dossier therefore must preserve `company_direction = uncertain` until company economics and counterfactual behavior are investigated.

### 2.2 What primary disclosures establish

Costco’s FY2025 Form 10-K establishes that:

- Executive members receive a 2% reward on qualified purchases, generally capped at $1,250 per year.
- The reward reduces reported net sales and is allocated to the merchandise category where it was generated.
- Executive-member sales penetration represented approximately 73.6% of worldwide net sales in FY2025.
- Executive members represented 38.7 million of 81.0 million paid memberships at FY2025 year-end.
- Membership-fee revenue was $5.323 billion in FY2025, up 10%, driven by new sign-ups and membership-fee increases.
- Overall renewal was 92.3% in the US/Canada and 89.8% worldwide.

Costco’s Q3 FY2026 Form 10-Q establishes that:

- Membership-fee revenue was $1.373 billion for the quarter and $4.057 billion for the first 36 weeks.
- Membership-fee revenue increased 11% and 13%, respectively, driven by new sign-ups, fee increases, and upgrades to Executive membership.
- Overall renewal was 92.2% in the US/Canada and 89.7% worldwide.

Primary sources:

- FY2025 10-K: https://www.sec.gov/Archives/edgar/data/909832/000090983225000101/cost-20250831.htm
- Q3 FY2026 10-Q: https://www.sec.gov/Archives/edgar/data/909832/000090983226000051/cost-20260510.htm
- 2024 membership-fee change: https://investor.costco.com/news/news-details/2024/Costco-Wholesale-Corporation-Reports-June-Sales-Results-and-Announces-Quarterly-Cash-Dividend-and-Plans-for-Membership-Fee-Increase/default.aspx
- Official reward terms: https://www.costco.com/f/-/executive-rewards

### 2.3 What is not disclosed

Public disclosure does not provide enough information to calculate:

- Executive-only membership-fee revenue;
- aggregate Executive rewards generated, redeemed, expired, or paid;
- Executive-specific renewal, downgrade, or cancellation rates;
- incremental sales caused by upgrading;
- Executive contribution margin;
- matched Gold Star versus Executive cohort economics;
- customer lifetime value by membership tier.

Therefore the correct initial conclusion is:

```text
behavior relevance: plausible and worth investigating
strategic exposure: high, because Executive members are a major member and sales cohort
standalone Executive economics: not disclosed
incremental company benefit: not estimable from public disclosure
company direction: uncertain
trade conclusion: none
```

This is the canonical acceptance fixture. The system fails if it turns 73.6% sales penetration into “73.6% of revenue is at risk,” assumes correlation is causation, or calls Executive growth automatically bullish.

### 2.4 Evidence that would resolve direction

The dossier should search for, but never assume:

1. Executive-specific renewal, downgrade, cancellation, and re-upgrade data.
2. Management comments on incremental shopping frequency, basket size, or retention after upgrade.
3. Aggregate reward liability, redemption, or cost commentary beyond the balance-sheet total.
4. Evidence that upgrades are incremental rather than simply selected by pre-existing high spenders.
5. Member discussions showing actual upgrade, renewal, downgrade, or cancellation behavior over time.
6. News and analyst coverage that explicitly connects Executive economics to earnings, rather than merely repeating reward arithmetic.
7. Price and estimate reactions around fee increases, membership disclosures, and earnings releases, while preserving confounding events.

If these remain unavailable, the system must end at `economics_not_estimable`, not invent an answer.

---

## 3. End-to-end research model

### Stage A: Candidate

Question: **Is there a specific, fresh, independently supported behavior worth investigating?**

Reuse the current private Radar:

- specificity;
- concrete behavior;
- firsthand evidence quality;
- persistence;
- comparable anomaly;
- independent breadth;
- citations;
- contradiction;
- invalidation.

Output: `CandidateArtifact`.

### Stage B: Exposure

Question: **Which legal entities, brands, products, segments, geographies, channels, and listed instruments could be affected?**

Output can include multiple relationships:

- direct owner;
- operating subsidiary;
- licensee/franchisee;
- supplier;
- sales channel;
- competitor;
- private company;
- ambiguous/unresolved.

A ticker is never inferred directly from a brand string.

### Stage C: Disclosure

Question: **What did the company or regulator actually disclose about the affected exposure?**

Retrieve and persist:

- annual reports;
- quarterly/interim reports;
- material-event filings;
- earnings releases;
- presentations;
- official call transcripts or webcast/audio;
- segment and geographic notes;
- guidance and guidance revisions.

### Stage D: Materiality

Question: **Can the economic exposure be quantified, bounded, proxied, or only described qualitatively?**

Allowed outputs:

1. `exactly_quantified`
2. `source_supported_bound`
3. `segment_proxy_only`
4. `company_total_proxy_only`
5. `qualitative_only`
6. `not_estimable`

No percentage is displayed unless the numerator and denominator match in issuer, period, scope, currency, accounting basis, and metric definition.

### Stage E: Information parity

Question: **Who already knows the specific financial implication?**

Distinguish:

- topic mentioned;
- consumer implication discussed;
- company economics connected;
- financial impact discussed;
- consensus or company expectations changed;
- price explicitly attributed.

Coverage is a matrix, not one Google News count.

### Stage F: Price and expectations

Question: **What does the market appear to have reflected, and what cannot be known?**

Store timestamp-safe:

- exact instrument and venue;
- local-currency price and volume;
- corporate-action adjustment status;
- event window;
- company guidance as of the cutoff;
- point-in-time consensus only when legitimately available;
- confounding company/market events.

### Stage G: Investment memo

Question: **Is there a source-grounded thesis worth human consideration?**

Required sections:

- observation;
- why now;
- verified exposure;
- consumer-positive, company-positive, company-negative, and neutral interpretations;
- materiality status;
- information-parity status and exact checked universe;
- price/expectations context;
- catalyst;
- bear case;
- invalidation;
- implementation alternatives;
- missing evidence.

### Stage H: Standing read

Question: **Did the behavior, economics, parity, or price relationship change after first detection?**

Track:

- behavior persistence/acceleration/reversal;
- new independent cohorts/geographies/platforms;
- management acknowledgment;
- estimate or guidance revisions;
- mainstreaming of the implication;
- price reaction;
- thesis invalidation or edge closure.

---

## 4. Data contracts

### 4.1 Shared artifact envelope

Create: `social_scraper/investing/research_artifacts.py`

```python
@dataclass(frozen=True)
class ArtifactEnvelope:
    schema_version: str
    artifact_id: str
    candidate_id: str
    created_at: str
    as_of: str
    status: str
    evidence_refs: tuple[str, ...]
    source_receipt_ids: tuple[str, ...]
    missing_reason_codes: tuple[str, ...]
```

Every artifact is versioned and append-only. Interpretations create a new artifact rather than mutating the old one.

### 4.2 Entity and instrument graph

```python
@dataclass(frozen=True)
class EntityRelationship:
    subject_entity_id: str
    relationship: str
    object_entity_id: str
    effective_from: str | None
    effective_to: str | None
    evidence_refs: tuple[str, ...]
    confidence_state: str

@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    issuer_entity_id: str
    instrument_type: str  # common_stock, ADR, GDR, ETF_proxy, option
    exchange_mic: str
    ticker: str
    isin: str | None
    currency: str
    primary_listing: bool
    evidence_refs: tuple[str, ...]
```

Relationship types include `owns`, `operates`, `licenses`, `franchises`, `supplies`, `distributes`, `competes_with`, and `reported_in_segment`.

### 4.3 Disclosure facts

```python
@dataclass(frozen=True)
class DisclosureFact:
    fact_id: str
    issuer_entity_id: str
    document_id: str
    metric_as_reported: str
    value: Decimal | None
    units: str | None
    currency: str | None
    period_start: str | None
    period_end: str | None
    scope_type: str
    scope_name_as_reported: str | None
    accounting_basis: str | None
    source_locator: str
    exact_source_text: str
    extraction_method: str
    verification_status: str
```

`source_locator` must be a filing section, page/table, XBRL concept/context, paragraph anchor, or transcript timestamp.

### 4.4 Materiality assessment

```python
@dataclass(frozen=True)
class MaterialityAssessment:
    status: str
    economic_mechanism: str
    affected_metric: str | None
    numerator_fact_ids: tuple[str, ...]
    denominator_fact_ids: tuple[str, ...]
    formula: str | None
    computed_value: Decimal | None
    unit: str | None
    proxy_scope: str | None
    assumptions: tuple[str, ...]
    offsetting_exposures: tuple[str, ...]
    missing_reason_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
```

The automatic path permits exact arithmetic only from verified facts. Scenario assumptions require an explicit human-authored assumption record.

### 4.5 Coverage and parity

```python
@dataclass(frozen=True)
class SourceClassReceipt:
    source_class: str
    jurisdiction: str
    status: str
    queries: tuple[str, ...]
    checked_at: str
    document_ids: tuple[str, ...]
    limitations: tuple[str, ...]

@dataclass(frozen=True)
class ParityAssessment:
    implication_tested: str
    parity_level: str
    source_receipts: tuple[str, ...]
    matched_claim_ids: tuple[str, ...]
    untested_source_classes: tuple[str, ...]
    status: str
```

The `implication_tested` must be explicit. “Costco Executive membership” is too broad. A valid implication is “Executive upgrades increase Costco’s incremental contribution after reward costs.”

### 4.6 Transcript record

```python
@dataclass(frozen=True)
class TranscriptRecord:
    transcript_id: str
    issuer_entity_id: str
    event_date: str
    source_type: str  # official_text, filing_exhibit, licensed, official_audio_asr, public_video_asr
    source_url: str
    original_language: str
    text_language: str
    raw_hash: str
    asr_model: str | None
    translation_model: str | None
    truncation_status: str
    verification_status: str
```

Translated or ASR text never replaces the original. Critical numeric quotes require verification against official text or timestamped audio.

### 4.7 Market context

```python
@dataclass(frozen=True)
class MarketObservation:
    instrument_id: str
    observed_at: str
    available_at: str
    venue_timezone: str
    currency: str
    price: Decimal | None
    volume: Decimal | None
    adjustment_state: str
    provider: str
    source_ref: str
```

Point-in-time `available_at` is mandatory to prevent hindsight leakage.

---

## 5. Materiality methodology

### 5.1 Exact percentage

Calculate only when both facts are verified and comparable:

```text
affected reported product/segment revenue
-----------------------------------------
matching consolidated or segment revenue
```

Valid examples:

- issuer reports a named product’s revenue and consolidated revenue for the same period;
- issuer explicitly states an affected business is less than 1% of group revenue;
- issuer reports segment revenue, allowing a segment share of group revenue.

Invalid examples:

- social-post volume divided by anything financial;
- brand mentioned in a segment description, then assumed to be a percentage of that segment;
- separately normalized Google Trends levels used as sales shares;
- Executive sales penetration treated as incremental Executive economics;
- weekly observations annualized without a disclosed conversion relationship.

### 5.2 Source-supported bound

Use only when the issuer or regulator provides the bound. Preserve the exact language and inequality. Never create “small” or “immaterial” bounds from intuition.

### 5.3 Segment proxy

Show:

- the affected brand/product is officially associated with Segment A;
- Segment A represents a verified share of company revenue/profit;
- the brand/product share within Segment A is not disclosed.

The UI must not relabel segment exposure as brand exposure.

### 5.4 Qualitative materiality

A candidate may be important without a revenue percentage because it affects:

- renewal/churn;
- pricing power;
- brand trust;
- a key launch;
- a regulated license;
- a major customer;
- liquidity/covenants;
- a strategic growth segment.

The output remains `qualitative_only`, with the reason and missing facts shown.

### 5.5 Not estimable

If numerator, denominator, period, scope, currency, accounting basis, or ownership is unresolved, return `not_estimable`. `null + reason_code` is mandatory; never zero-fill.

### 5.6 The 2% portfolio rule

The existing “revenue at risk below 2% means no trade” rule applies only when exposure is defensibly quantified. If exposure cannot be measured, the system must not assume it is above or below 2%. The dossier remains research-only until a human can support a bound or alternative materiality mechanism.

---

## 6. Worldwide disclosure strategy

### 6.1 Source hierarchy

1. Regulator filing or designated official disclosure system.
2. Official exchange disclosure.
3. Issuer investor-relations document or webcast.
4. Licensed transcript/consensus/market-data source.
5. Secondary aggregator or media, used for discovery and narrative mapping only.

### 6.2 Jurisdiction profiles

Create: `social_scraper/investing/jurisdictions.py`

Each profile records:

- jurisdiction;
- regulator/disclosure repositories;
- exchange repositories;
- filing language;
- accounting basis;
- expected document types;
- fiscal-period conventions;
- structured-data availability;
- transcript norms;
- price-source options;
- retention/licensing constraints;
- current adapter health.

Initial source map:

| Market | Primary disclosure | Timely/exchange disclosure | Notes |
|---|---|---|---|
| US | SEC EDGAR, data.sec.gov | 8-K/6-K, issuer IR | Best structured starting point |
| Canada | SEDAR+ | TMX and issuer IR | API/access is less convenient |
| UK | FCA NSM, Companies House | RNS/LSE, issuer IR | Registry and market disclosure are different |
| EU | National OAM, ESEF | Home exchange, issuer IR | Federated, not one EDGAR |
| Japan | EDINET | TDnet/JPX, issuer IR | Original Japanese retained |
| Hong Kong | HKEXnews | HKEX, issuer IR | English/Chinese and superseded docs |
| Singapore | SGXNet | SGX, issuer IR | Often strong announcement coverage |
| China | CNINFO plus relevant exchange | SSE/SZSE/BSE, issuer IR | Listed vehicle and affiliate mapping critical |

### 6.3 Rollout rule

Do not implement eight jurisdictions at once. Prove the architecture on:

1. **US:** Costco, a well-disclosed but causally ambiguous case.
2. **One difficult non-US issuer:** local-language filings and no official text transcript.
3. **One conglomerate/brand case:** the brand belongs to a reported segment but brand revenue is not disclosed.

Only then add jurisdiction adapters one at a time. The UI advertises only verified coverage profiles.

### 6.4 Document retrieval interface

Create: `social_scraper/investing/disclosure_sources.py`

```python
class DisclosureSource(Protocol):
    def discover(self, issuer, *, as_of, document_types): ...
    def fetch(self, document_ref): ...
    def health(self): ...
```

Initial adapters:

- `social_scraper/investing/sources/sec_edgar.py`
- `social_scraper/investing/sources/issuer_ir.py`
- `social_scraper/investing/sources/manual_document.py`

`manual_document.py` is intentional: if an official filing is inaccessible programmatically but Vincent supplies it, the workflow should ingest and hash that exact document rather than use an inferior substitute.

---

## 7. Transcript acquisition strategy

There is no universal official transcript database. Use this order:

1. Issuer-hosted official transcript.
2. Transcript or prepared remarks filed as an exhibit.
3. Official investor-relations webcast/audio/video.
4. Licensed transcript provider with retention rights.
5. Public audio/video ASR only where access and retention are lawful.
6. Aggregator transcript for discovery only, with critical passages reverified against official material.

Create: `social_scraper/investing/transcript_sources.py`

Required statuses:

- `official_transcript`
- `filing_exhibit`
- `licensed_transcript`
- `official_audio_machine_transcribed`
- `public_video_machine_transcribed`
- `translation_only`
- `partial`
- `unavailable`

Rules:

- Prepared remarks and Q&A are separate sections.
- Analyst questions are not company claims.
- “We expect” is not a reported fact.
- Every quote has speaker, event date, source URL, and page or timestamp.
- ASR confidence never turns a number into a verified fact automatically.
- Local-language originals are immutable; translations are derivative artifacts.
- If no transcript exists, use releases, slides, filings, and official audio if available. Otherwise show `transcript_unavailable`.

Before adding any transcript provider, verify:

- coverage by jurisdiction;
- historical depth;
- licensing and retention rights;
- rate limits and cost;
- speaker/timestamp quality;
- correction policy.

---

## 8. Information-parity methodology

### 8.1 Test the implication, not the keyword

For each candidate, define one or more explicit implications:

```text
Observed behavior: members say the Executive reward covers the upgrade.
Implication A: this is increasing upgrade conversion.
Implication B: this is improving renewal or retention.
Implication C: incremental fee and merchandise contribution exceed reward cost.
Implication D: the market underestimates these effects.
```

Search and classify each implication separately.

### 8.2 Checked source classes

- social/consumer conversation;
- specialist/industry media;
- mainstream consumer media;
- financial media;
- issuer filings/releases;
- earnings calls/transcripts;
- analyst research or consensus where legitimately available;
- price/volume reaction.

### 8.3 Parity ladder

- `L0`: social-only in the checked healthy sources.
- `L1`: niche/specialist awareness.
- `L2`: mainstream consumer coverage; investment implication not connected.
- `L3`: company/IR acknowledgment or broad financial coverage.
- `L3.5`: financial source explicitly connects the implication.
- `L4`: repeated analyst/consensus incorporation or management/price attribution.
- `unknown`: critical source coverage unavailable or failed.

A syndicated article counts as one root, not multiple independent sources.

### 8.4 Existing code correction

`social_scraper/investing/owned_radar.py::check_news_parity()` currently uses Google News RSS and a financial-publication source-name heuristic. Keep it as a cheap first pass, but do not call it a completed parity assessment. The new parity artifact must show every checked source class and every untested source class.

---

## 9. Price, expectations, and implementation

### 9.1 Price context

Create: `social_scraper/investing/market_data.py`

Provider interface:

```python
class MarketDataSource(Protocol):
    def resolve_instrument(self, issuer, venue=None): ...
    def history(self, instrument, start, end): ...
    def snapshot(self, instrument, as_of): ...
    def corporate_actions(self, instrument, start, end): ...
```

Store venue, currency, timezone, adjusted/unadjusted status, corporate actions, and `available_at`.

Never claim the signal caused a price move. Use:

- `reaction_observed`
- `no_clear_reaction_observed`
- `confounded`
- `price_unavailable`

### 9.2 Expectations

Source order:

1. Company guidance and guidance revisions.
2. Point-in-time licensed consensus when available.
3. Public analyst commentary, labeled as sampled coverage.
4. `historical_consensus_unavailable` when no legitimate as-of dataset exists.

Do not compare a historical event with today’s revised consensus.

### 9.3 Common stock and optional options

Every memo can propose:

- no position;
- common stock long/short;
- ADR/GDR;
- listed supplier/competitor exposure;
- basket or pair;
- options only when available and attractive.

Options data is an optional appendix:

- expiry;
- strike;
- bid/ask;
- open interest;
- volume;
- implied volatility;
- event timing.

No candidate fails solely because it lacks options.

### 9.4 Action-day verification

Before any position:

- verify the exact instrument and venue;
- get current executable price/liquidity from IBKR or another approved broker source;
- check earnings/corporate-event timing;
- check borrow if shorting;
- check options only when used;
- record the quote time and data source.

---

## 10. Persistence and workflow

### 10.1 Storage

Create: `social_scraper/investing/research_store.py`

Add namespaced tables without modifying current private Radar tables:

```text
investment_research_runs
investment_research_stage_history
investment_entities
investment_entity_relationships
investment_instruments
investment_documents
investment_document_facts
investment_transcripts
investment_transcript_segments
investment_exposure_assessments
investment_materiality_assessments
investment_parity_assessments
investment_market_observations
investment_expectation_observations
investment_memos
investment_reviews
investment_source_receipts
investment_evidence_refs
```

Raw document/transcript/evidence records are immutable. New assessments and reviews create new versions.

### 10.2 Runner

Create: `social_scraper/investing/research_runner.py`

Stages:

```text
candidate_handoff
entity_resolution
disclosure_collection
fact_extraction
materiality
parity
market_context
memo_draft
human_review
standing_read
```

Reuse the existing leased/resumable staged-run pattern. Each stage records:

- start/end;
- input artifact versions;
- source attempts;
- status;
- missingness;
- output artifact ID;
- model/tool usage;
- error category.

### 10.3 Human review

Create: `social_scraper/investing/review.py`

Allowed decisions:

- approve mapping;
- reject mapping;
- select among ambiguous entities;
- add a labeled assumption;
- request another source;
- verify a transcript quote;
- approve/reject materiality treatment;
- approve/reject final memo;
- expire/supersede a thesis.

Review cannot:

- edit raw evidence;
- delete failed source attempts;
- turn unknown into pass without evidence or an explicit assumption artifact;
- overwrite previous decisions.

---

## 11. Implementation tasks

### Task 0: Protect the repository and freeze fixtures

**Objective:** Establish a safe baseline before any code change.

**Files:**
- Create later: `tests/fixtures/investing/dossiers/costco_executive/`
- Do not modify production code.

**Steps:**

1. Re-run `git status --short --branch` and `git diff --cached --stat`.
2. If the staged-deletion/untracked-duplicate pattern persists, halt and repair the Git index separately with Vincent’s approval. Never run `git clean`.
3. Hash and archive the current production snapshot and latest private-Radar database fixture under ignored artifacts.
4. Create a source manifest for the Costco 10-K, Q3 10-Q, fee announcement, and reward terms.
5. Freeze expected Costco conclusions:
   - strategic exposure present;
   - company direction uncertain;
   - standalone economics not estimable;
   - no trade conclusion.
6. Run the existing full suite before changes.

**Verification:** No production file changes; baseline tests green; fixture source hashes recorded.

### Task 1: Add artifact contracts and pure gates

**Files:**
- Create: `social_scraper/investing/research_artifacts.py`
- Create: `social_scraper/investing/research_gates.py`
- Test: `tests/investing/test_research_artifacts.py`
- Test: `tests/investing/test_research_gates.py`

**Steps:**

1. Write failing serialization tests for every artifact and missing-data enum.
2. Add frozen dataclasses/Pydantic models.
3. Write failing tests proving `unknown` cannot pass.
4. Implement pure deterministic gates with no network/storage/model access.
5. Test that options absence does not fail an otherwise valid instrument.
6. Test that missing numerator prevents a materiality percentage.

**Verification:** Focused tests pass; no existing qualification behavior changes.

### Task 2: Add append-only workflow storage

**Files:**
- Create: `social_scraper/investing/research_store.py`
- Test: `tests/investing/test_research_store.py`

**Steps:**

1. Write migration tests from the current database.
2. Add namespaced tables and immutable raw-document/transcript triggers.
3. Add versioned assessment and review writes.
4. Add cross-reference validation to existing candidate/evidence IDs.
5. Add lease/stale-run recovery tests.

**Verification:** Existing databases open without destructive migration; updates/deletes to raw evidence fail.

### Task 3: Create the stable Candidate handoff

**Files:**
- Modify: `social_scraper/investing/private_radar.py`
- Create: `social_scraper/investing/candidate_handoff.py`
- Test: `tests/investing/test_candidate_handoff.py`

**Steps:**

1. Convert a qualified or manually selected reviewed subject into `CandidateArtifact` without changing its original decision.
2. Preserve all social evidence, movement bundles, coverage, gates, contradiction, and invalidation.
3. Reject cross-panel or missing evidence IDs.
4. Allow human-selected near-misses to enter research as `research_only`, never `qualified`.

**Verification:** The Costco near-miss can be researched without being mislabeled trade-ready.

### Task 4: Build issuer/entity/instrument resolution

**Files:**
- Create: `social_scraper/investing/issuer_registry.py`
- Create: `social_scraper/investing/entity_resolution.py`
- Test: `tests/investing/test_entity_resolution.py`
- Fixtures: `tests/fixtures/investing/entities/`

**Steps:**

1. Add canonical entity, alias, brand, subsidiary, segment, and instrument models.
2. Require official evidence for ownership/operator relationships.
3. Support multiple listings and ADR relationships.
4. Keep licensee/franchisee/supplier relationships distinct.
5. Add ambiguous-brand and private-company tests.
6. Add manual-review selection without mutating resolver output.

**Verification:** Costco maps to the issuer and NASDAQ common stock; a private brand does not acquire a fake ticker.

### Task 5: Add jurisdiction coverage profiles

**Files:**
- Create: `social_scraper/investing/jurisdictions.py`
- Test: `tests/investing/test_jurisdictions.py`

**Steps:**

1. Encode source classes, languages, expected filings, and current adapter support.
2. Add US plus one difficult non-US profile first.
3. Add `unsupported` and `source_unavailable` states.
4. Prevent “no disclosure” when the source was not checked.

**Verification:** Coverage reports exact repositories checked and never claims worldwide completeness.

### Task 6: Implement official disclosure retrieval

**Files:**
- Create: `social_scraper/investing/disclosure_sources.py`
- Create: `social_scraper/investing/sources/sec_edgar.py`
- Create: `social_scraper/investing/sources/issuer_ir.py`
- Create: `social_scraper/investing/sources/manual_document.py`
- Test: `tests/investing/test_disclosure_sources.py`

**Steps:**

1. Write deterministic fixture tests before live calls.
2. Implement SEC submission/filing discovery using CIK/accession IDs.
3. Fetch and hash official documents with respectful rate limits and identifiable user agent.
4. Add issuer-IR discovery as a separate, lower-tier source.
5. Add manual supplied-document ingestion.
6. Record failed access as failure, not absence.

**Verification:** Costco filings resolve from SEC; a simulated SEC outage does not become `not_disclosed`.

### Task 7: Extract disclosure facts with exact locators

**Files:**
- Create: `social_scraper/investing/disclosure_facts.py`
- Test: `tests/investing/test_disclosure_facts.py`
- Fixtures: `tests/fixtures/investing/disclosures/`

**Steps:**

1. Parse structured XBRL facts where available.
2. Extract document passages/tables with exact locators.
3. Let the LLM suggest candidate facts only from supplied text.
4. Deterministically verify number, units, period, scope, and source text before storing a fact.
5. Store conflicting facts separately.
6. Preserve restatements and segment redefinitions.

**Verification:** Costco figures are extracted with source text; “73.6% sales penetration” cannot be stored as “73.6% incremental revenue.”

### Task 8: Implement materiality states and arithmetic

**Files:**
- Create: `social_scraper/investing/materiality.py`
- Test: `tests/investing/test_materiality.py`
- Fixtures: `tests/fixtures/investing/materiality/`

**Steps:**

1. Implement exact ratio, issuer-supported bound, segment proxy, qualitative-only, and not-estimable outputs.
2. Require matching scope/period/currency/accounting basis.
3. Add explicit offsetting-exposure search requirements.
4. Add human-authored assumption artifacts for scenarios.
5. Reject model-generated or unlabeled assumptions.
6. Add the Costco causal-ambiguity fixture.
7. Add a conglomerate fixture where a strong product signal is offset by another segment.

**Verification:** Costco returns `not_estimable` for Executive standalone economics and no fabricated percentage.

### Task 9: Add transcript acquisition and verification

**Files:**
- Create: `social_scraper/investing/transcript_sources.py`
- Create: `social_scraper/investing/sources/official_ir_transcript.py`
- Create later, after rights review: `social_scraper/investing/sources/official_audio_asr.py`
- Test: `tests/investing/test_transcript_sources.py`

**Steps:**

1. Implement official transcript/prepared-remarks discovery.
2. Store prepared remarks and Q&A separately.
3. Add official audio metadata without transcribing initially.
4. Complete provider rights/cost review before ASR implementation.
5. If approved, add ASR with timestamps, model version, truncation, and confidence.
6. Require human verification for critical numeric quotes.
7. Preserve original language and derivative translation separately.

**Verification:** No-transcript issuers return a truthful gap; YouTube comments/metadata are never treated as transcript text.

### Task 10: Replace the parity shortcut with a source matrix

**Files:**
- Create: `social_scraper/investing/information_parity.py`
- Modify: `social_scraper/investing/owned_radar.py` only to label the current check as preliminary
- Test: `tests/investing/test_information_parity.py`

**Steps:**

1. Require explicit implication strings.
2. Search each source class independently.
3. Deduplicate syndicated coverage.
4. Extract whether coverage mentions the topic, behavior, economics, or investment implication.
5. Record untested analyst/consensus coverage as unknown.
6. Calibrate L0-L4 on labeled examples.
7. Require human review before an “off-radar” conclusion.

**Verification:** An article saying “Executive membership is worth it” does not automatically prove that incremental contribution is already understood.

### Task 11: Add timestamp-safe market context

**Files:**
- Create: `social_scraper/investing/market_data.py`
- Create: `social_scraper/investing/expectations.py`
- Test: `tests/investing/test_market_data.py`
- Test: `tests/investing/test_expectations.py`

**Steps:**

1. Define provider-neutral price/guidance/consensus contracts.
2. Implement one approved historical EOD adapter with provenance.
3. Add corporate-action and venue-timezone handling.
4. Add company-guidance extraction from official sources.
5. Add licensed point-in-time consensus only if access is available.
6. Add look-ahead leakage tests.
7. Add optional IBKR action-day verification adapter separately.

**Verification:** Current consensus cannot be used for a past event; missing consensus remains missing; common stock works without options.

### Task 12: Assemble the investment memo and review queue

**Files:**
- Create: `social_scraper/investing/investment_memo.py`
- Create: `social_scraper/investing/review.py`
- Modify later: `apis/dashboard_api.py`
- Modify later: `apis/investing_dashboard_page.py`
- Modify later: `public/investing-dashboard.js`
- Modify later: `public/investing-dashboard.css`
- Test: `tests/investing/test_investment_memo.py`
- Test: `tests/investing/test_investment_review.py`

**Steps:**

1. Build structured memo JSON before any UI.
2. Require evidence IDs for factual claims.
3. Require explicit unknowns and alternatives.
4. Add optional LLM prose rendering over validated artifacts only.
5. Add append-only human review.
6. Render one Costco dossier internally.
7. Stop for Vincent’s approval before general UI rollout.

**Verification:** The memo clearly says why Costco Executive can be positive, neutral, or negative and why public data cannot settle the incremental economics.

### Task 13: Add candidate-specific standing reads

**Files:**
- Create: `social_scraper/investing/standing_read.py`
- Modify: `social_scraper/investing/research_runner.py`
- Test: `tests/investing/test_standing_read.py`

**Steps:**

1. Freeze exact candidate queries/scopes after approval.
2. Run comparable forward collection.
3. Detect persistence, spread, reversal, company acknowledgment, mainstreaming, and edge closure.
4. Preserve changing query versions as new scopes.
5. Never interpolate missing cycles.

**Verification:** A missing collection cycle appears as a gap; the thesis state changes only from observed evidence.

### Task 14: Calibrate on known failures and a blind holdout

**Fixtures:**
- Costco Executive: ambiguous economics.
- Coach queue example: striking observation rejected after causal investigation.
- Restaurant Brands: incomplete company exposure/offsetting segment.
- Shiseido d Program: specific product failure with potentially measurable exposure.
- Estée Lauder boycott: real social signal but immaterial exposure.
- One blind holdout set frozen before thresholds are tuned.

**Steps:**

1. Run every fixture without special-case code.
2. Record false positives, false negatives, unknowns, and source gaps.
3. Tune only versioned generic gates.
4. Freeze the method.
5. Run blind holdout.
6. Require Vincent to approve whether the dossiers are genuinely worth investigating.

**Verification:** No handpicked case-specific rule; unknowns remain unknown; blind results are archived.

### Task 15: Controlled release

**Steps:**

1. Keep the existing private Radar unchanged until the full dossier path passes.
2. Release the first dossier only internally.
3. Run focused tests, full regression, source canaries, and one real bounded workflow.
4. Inspect desktop/mobile with real source-backed data.
5. Verify every displayed link and numeric fact.
6. Verify production asset markers and last-known-good fallback.
7. Do not publish if any required source fails or a memo claim lacks evidence.

---

## 12. Required tests and adversarial cases

Create:

```text
tests/investing/test_research_artifacts.py
tests/investing/test_research_gates.py
tests/investing/test_research_store.py
tests/investing/test_candidate_handoff.py
tests/investing/test_entity_resolution.py
tests/investing/test_jurisdictions.py
tests/investing/test_disclosure_sources.py
tests/investing/test_disclosure_facts.py
tests/investing/test_materiality.py
tests/investing/test_transcript_sources.py
tests/investing/test_information_parity.py
tests/investing/test_market_data.py
tests/investing/test_expectations.py
tests/investing/test_investment_memo.py
tests/investing/test_investment_review.py
tests/investing/test_standing_read.py
```

Mandatory adversarial cases:

- brand maps to several legal entities;
- brand is licensed rather than owned;
- affected entity is private;
- parent has multiple listed share classes/ADRs;
- segment is redefined between periods;
- product revenue is not disclosed;
- denominator is consolidated but numerator is subsidiary-only;
- currency/period/accounting basis mismatches;
- official sources conflict;
- filing source fails and must not become “no disclosure”;
- transcript is absent;
- ASR mistranscribes a number;
- local-language translation changes a product name;
- news is syndicated across many outlets;
- keyword is covered but investment implication is not;
- analyst coverage is unavailable;
- price moved because of a confounding earnings release;
- current consensus is accidentally used for a historical event;
- no options exist but common stock is liquid;
- human review attempts to bypass an unknown gate;
- failed workflow tries to overwrite the last successful dossier;
- raw social counts are passed into revenue calculations;
- a correlated high-spender cohort is mistaken for causal uplift.

Existing regressions that must remain green include:

```text
tests/investing/test_qualification.py
tests/investing/test_private_radar.py
tests/investing/test_api.py
tests/investing/test_snapshot_builder.py
tests/discovery/test_evidence_boundaries.py
tests/discovery/test_execution.py
tests/discovery/test_candidate_history.py
tests/test_investing_dashboard_product.py
```

---

## 13. Release gates

The infrastructure is not “investment-ready” until:

1. A candidate can traverse every stage with immutable evidence and explicit source receipts.
2. Costco correctly ends at uncertain direction/not-estimable incremental economics.
3. A non-US local-language issuer can be processed without pretending missing disclosure is zero.
4. A conglomerate’s offsetting exposures are displayed.
5. A news keyword mention is separated from coverage of the actual financial implication.
6. Historical price/consensus data passes look-ahead tests.
7. Common stock works when options do not exist.
8. Every factual memo claim has a resolvable primary or clearly labeled secondary source.
9. Human review is append-only and cannot mutate evidence.
10. A blind holdout produces dossiers Vincent independently considers useful, including honest “no thesis” outcomes.
11. Forward standing reads demonstrate persistence/reversal without interpolation.
12. Source canaries, full regression, real workflow QA, and desktop/mobile review pass.

---

## 14. Risks and tradeoffs

### Disclosure heterogeneity

Some companies disclose product revenue; others disclose only broad segments. The architecture solves this with explicit evidence levels, not a universal percentage. The tradeoff is fewer numeric answers but substantially higher integrity.

### Transcript access and licensing

Official transcripts are inconsistent globally. Audio ASR may be feasible technically but still requires rights, retention, and quality review. The workflow must work with `transcript_unavailable` rather than depend on a brittle vendor.

### Consensus access

Reliable historical consensus is usually licensed. Without it, Bounty can assess company guidance, financial-media narrative, and public analyst commentary but cannot claim full consensus parity.

### Entity mapping

Brand ownership, operating control, licensing, and listed exposure change over time. Every relationship must be dated and cited. Human review remains necessary for ambiguous corporate structures.

### False precision

Materiality scenarios can look authoritative even when assumptions dominate. Automatic output therefore uses only verified facts; human assumptions remain separately colored/labeled and never become reported facts.

### Scope

Building all jurisdictions at once would create a facade. The disciplined sequence is US + one difficult non-US case + one conglomerate, then expand only after each adapter passes fixtures and live canaries.

---

## 15. Explicitly out of scope for the first implementation

- automated order execution;
- mandatory options;
- universal sell-side coverage;
- every global exchange on day one;
- scraping paywalled research without rights;
- automatic financial forecasts from social volume;
- inferred brand revenue;
- customer-facing release before calibration;
- redesigning the existing Radar UI before the Costco dossier works as structured JSON.

---

## 16. Approval gates

### Gate 1: Method approval

Approve this plan, including the rule that many dossiers will end at `not_estimable` or `unknown`.

### Gate 2: Costco structured dossier

After Tasks 0-8, review the raw structured Costco dossier before adding transcripts, parity, or market context.

### Gate 3: Full Costco memo

After Tasks 9-12, review whether the memo genuinely clarifies direction, materiality, news coverage, and unknowns.

### Gate 4: Non-US hard case

Prove local-language/missing-transcript handling before adding more jurisdictions.

### Gate 5: Blind holdout

Approve the methodology only if unhandpicked outputs are useful and weak cases are rejected.

### Gate 6: Internal release

Only then expose the workflow through the private dashboard.
