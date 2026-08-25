# Bounty Investing Social-Arbitrage Pivot Implementation Plan

> **For Hermes:** Use the software-engineering lifecycle and subagent-driven development to implement this plan task by task. Stop at every release gate. Do not start UI work until the relevant data gate passes.

**Goal:** Turn Bounty into a global investing social-arbitrage radar that discovers unusual search and social behaviour, explains why each lead surfaced, and lets the user run a cited investigation, while retaining the horizontal evidence engine for future verticals.

**Architecture:** Keep the canonical horizontal corpus, research-run, connector, workspace, and lens layers unchanged. Add an investing-specific candidate projection and Radar service above them. Every discovery source emits the same provenance-bearing `DiscoveryObservation` contract while retaining its native metric and unit. Candidate acquisition is centralized and scheduled; customers read persisted results and never trigger Google, social, or external discovery sources directly. Global breadth comes from sweeping global feeds, then spending expensive historical calls only on promoted subjects and their relevant geographies.

**Tech Stack:** Python 3.11, FastAPI, SQLite/Postgres-compatible repositories, trendspy 0.1.6 behind a provider interface, Google Trends BigQuery public datasets where available, existing SourceBroker/StagedRunner, vanilla JS/CSS dashboard, pytest, Playwright QA.

---

## 1. Corrected decisions

This plan replaces the earlier brute-force proposal in this file.

1. **No arbitrary “1,500 terms × 8 categories × 3 countries” job.** There is no fixed term count or fixed eight-category ontology. The candidate universe grows from actual global feeds and transparent taxonomies.
2. **Seeding does not use LLM tokens.** Importing terms from a CSV/taxonomy is ordinary data processing. LLM calls are reserved for a small number of promoted candidates during cited investigation.
3. **Global means global candidate coverage.** All 125 currently verified `trending_now` markets can contribute candidates. Expensive five-year history is fetched sparsely after promotion rather than exhaustively for every term-country pair.
4. **Trendspy does not eliminate every Google limit.** The audited `trending_now()` endpoint has been reliable and fast; `interest_over_time()`, `interest_by_region()`, and `related_queries()` use different Google Explore endpoints and can throttle. Treat them as separate providers with separate health.
5. **No direct upstream calls from user requests.** Customers read Bounty’s persisted observations. One scheduled fetch can serve one user or ten thousand users.
6. **Do not append separately normalized Google website windows as one continuous series.** With unofficial Explore data, refetch the complete analytical window and compute signals within that request. Only the official consistently scaled API may support safe incremental appends across requests.
7. **Do not batch unrelated terms merely to save calls.** Shared normalization can crush lower-volume terms and distort the shape. Multi-term calls are permitted only for explicitly comparable terms and tested scaling behaviour.
8. **Company mapping and materiality are deferred, not abandoned.** The initial Radar proves discovery and behavioural evidence first. A later gated phase maps promoted trends to global securities and quantifies exposure from filings and explicit assumptions.
9. **No stock recommendation or “high conviction” claim.** Bounty produces auditable research leads, information-coverage evidence, exposure scenarios, counterevidence, and uncertainty. Conviction remains the investor’s decision.

## 2. Product contract

### Primary user

An active global public-markets investor looking for underfollowed changes in demand, behaviour, adoption, rejection, or operational pain before they are fully reflected in market narratives.

### Primary loop

```text
Open Global Radar
→ inspect unexplained change from any supported market
→ see why it surfaced and the raw historical/social evidence
→ choose Investigate
→ receive a citation-backed investing brief
→ Monitor, Dismiss, or save as a research lead
```

### Full end-state after the core Radar works

```text
Discover the change
→ verify observed behaviour
→ measure how widely the information is already covered
→ map the economic mechanism to relevant public companies
→ quantify revenue/earnings exposure where filings support it
→ monitor the thesis, counterevidence, awareness, and materiality
```

The final two stages are intentionally later. They are valuable only after Bounty reliably finds and validates a signal.

### Two investing lanes

1. **Breaking now**
   - Global `trending_now` and public-conversation candidates.
   - Event demand, sudden behaviour changes, products, personalities, regulation, and news.
   - Uses existing candidate-generation code and can ship first.

2. **Building quietly**
   - One-year/five-year historical anomaly candidates.
   - Seasonal anomalies, persistent growth, cross-country agreement, and first-detectable date.
   - Ships only after historical-provider reliability and blind-scan gates pass.

### Investing dossier

For each investigated lead, show only supported sections:

- What changed
- Where it changed
- Why the signal was promoted
- Historical/seasonal context
- Observed adoption or purchase behaviour
- Pain points, rejection, and counterevidence
- Source breadth and recency
- Market-awareness evidence if checked
- Limitations and missing data
- Openable citations

Explicitly out of scope for the initial Radar and investigation releases:

- Company/ticker mapping and quantified materiality (planned as later gated phases)
- Portfolio construction
- Price targets or return forecasts
- Buy/sell recommendations
- Demographic inference
- Marketing/product lens UI

## 3. Preserve horizontal optionality

### Keep unchanged

- `social_scraper/conversations/*`: canonical evidence and provenance
- `social_scraper/discovery/staged_runner.py`: durable execution
- `social_scraper/discovery/storage.py`: research runs and findings
- `social_scraper/discovery/triage.py`: citation-gated conversation interpretation
- `social_scraper/broker.py` and connectors
- `social_scraper/lenses/*`: generic lens contracts
- Workspace, auth, usage, leases, and TackSense coordinator/export boundaries

### Add vertically

Create `social_scraper/investing/` as a projection/service layer. It may rank and interpret canonical data but cannot mutate or silently suppress it.

### Park, do not delete

- Product/marketing lens navigation
- Horizontal Explorer navigation
- Zones UI
- Legacy API marketplace/x402 surfaces

Use `BOUNTY_PRODUCT_EDITION=investing` to make the investing experience the default. Existing APIs remain callable and contract-tested.

## 4. Global candidate acquisition without brute force

### Layer A: global current feeds

- Sweep all 125 verified `TRENDING_NOW_COUNTRIES` centrally on a schedule.
- Persist every returned candidate with source-native category, country, timestamps, search volume when supplied, and exact provenance.
- Deduplicate the same term across countries without losing country observations.
- Ingest Google Trends BigQuery Top/Rising data for approximately 50 countries if the legal/credential gate passes.
- Retain candidates permanently so a seven-day feed becomes a multi-year candidate memory.

This is global breadth. A user does not select three countries first.

### Layer B: transparent vocabulary expansion

- Add related terms only from recorded source responses.
- Add terms extracted from canonical social evidence with evidence IDs.
- Import selected open taxonomies as vocabulary, not as automatically important signals.
- Record `candidate_origin` for every term: `trending_now`, `bigquery_top`, `bigquery_rising`, `related_query`, `corpus_entity`, or `taxonomy`.
- Do not invoke an LLM during import or deterministic normalization.

### Layer C: sparse historical depth

For each promoted distinct term:

1. Fetch a Worldwide 12-month and five-year series.
2. Fetch `interest_by_region()` to identify where the term has measurable interest.
3. Add countries where the term was independently discovered through the global current feed.
4. Fetch full one-year/five-year histories only for those relevant country pairs.
5. Cache the full raw/normalized response and provider metadata.

This provides **global breadth with sparse depth**. Any supported country can originate a lead, but Bounty does not waste calls on every term-country combination.

## 5. Multi-channel discovery, not Google-only

Google search is one sensor. It is useful for explicit intent, but it can miss behaviour that first appears in communities, video, software adoption, encyclopedic attention, or physical events. Bounty therefore promotes subjects from multiple independent channel adapters.

### Shared observation contract

Every channel emits a `DiscoveryObservation` with:

- canonical subject plus source-native title/identifier
- `channel_type`: `search_attention`, `knowledge_attention`, `video_attention`, `social_conversation`, `developer_adoption`, `event_catalyst`, or later `commercial_activity`
- geography/language where supplied
- observed period and collection timestamp
- native metric name, value, and unit
- source URL/API identity and retrieval status
- raw-record hash/internal provenance
- explicit partial/unavailable state

Do **not** convert unlike metrics into one synthetic global index. A Google 0–100 index, Wikipedia pageviews, YouTube views, GitHub events, and article counts remain separate. Cross-channel confirmation changes priority and explanation, not the underlying evidence.

### Channel priority

#### Tier 1: build and validate first

1. **Google Trends**: global current-search candidates plus long-horizon history where the provider gate passes.
2. **Wikimedia pageview movers**: the official Analytics API supports most-viewed pages, country views, and per-page history. Use multiple language editions; remove recurring administrative/calendar pages deterministically but preserve the excluded list.
3. **YouTube popular-video discovery**: validate the official Data API `videos.list(chart=mostPopular)` by region/category. The official method has low per-call quota cost, but current chart composition/coverage must be measured before calling it broad YouTube discovery.
4. **GDELT event/news discovery**: global multilingual event and entity candidates. Treat this as catalyst/media awareness, never as proof of consumer adoption.
5. **Existing Reddit and YouTube evidence collection**: primarily confirms or contradicts promoted subjects. Unseeded discovery is added only where a reliable feed/scope exists; keyword search alone is not unknown-unknown discovery.

#### Tier 2: add after the first Radar is useful

- **GitHub public activity** for developer/tool adoption. The official Events API supports ETag polling and an `X-Poll-Interval`; its public timeline is limited and not real-time, so use it as a tech-specific sensor rather than a market-wide feed.
- **Hacker News** for early technical discussion.
- **Apple App Store charts/review changes** after API and commercial-use validation.
- **Official weather/climate alerts** as catalyst inputs for event-to-demand detection.
- **Regulatory and government feeds** for approvals, restrictions, subsidies, recalls, and standards changes.

#### Tier 3: licensed or paid only after demand is proven

- Retail bestseller/rank changes
- App-download estimates
- Job-posting/skill demand
- Card spending or transaction data
- Shipping, inventory, and web-traffic datasets

Do not build brittle retail/app scrapers and call them production data. Each Tier 3 source requires a reliability, cost, and data-rights gate.

### Promotion rules

- A candidate may surface from one strong channel; cross-channel presence is helpful but not mandatory.
- Show exactly which channel discovered it and which channels later confirmed, contradicted, or lacked coverage.
- Rank using versioned, channel-specific features and transparent reason codes.
- Do not suppress a valid single-channel lead merely because another platform has no data.
- Deduplicate a subject across channels while preserving every immutable observation.
- LLMs may summarize only promoted evidence; they do not manufacture candidates or native metrics.

### Why the horizontal architecture remains valuable

The same candidate identity, provenance, evidence, lease, storage, and citation machinery works for every channel. The vertical change is the investing-specific promotion and presentation logic, not a new scraper stack per source.

## 6. Provider and rate-limit architecture

### Provider interface

Create adapters with explicit capability and health contracts:

- `TrendingNowProvider`: trendspy `trending_now`; global candidate enumeration.
- `ExploreHistoryProvider`: trendspy `interest_over_time`, `interest_by_region`, `related_queries`; scheduler-only, unofficial, can throttle.
- `BigQueryTrendsProvider`: public Top/Rising datasets; quota/cost controlled.
- `OfficialGoogleTrendsProvider`: future consistently scaled API when access is approved.

### Hard scaling rule

No dashboard/API request may call any Google endpoint. Endpoints query persisted Bounty data only.

### Scheduler controls

- Durable queue keyed by provider, term, geography, horizon, and collection method.
- One active worker per rate-sensitive provider initially.
- Token-bucket rate budget, jitter, exponential backoff, retry-after support, and circuit breaker.
- Idempotent leases and explicit `complete`, `partial`, `unavailable`, and `failed` outcomes.
- Request/response hashes and provider timestamps stored internally.
- Cache freshness classes rather than per-user refresh:
  - Trending Now: centrally refreshed several times daily.
  - Building Quietly five-year series: full-window periodic refresh for promoted terms.
  - Related queries/region breakdown: refreshed only on promotion or stale expiry.

### Reliability gates

Before `ExploreHistoryProvider` becomes a sellable dependency:

1. Seven-day canary across varied terms and geographies.
2. At least 95% of scheduled units reach a truthful persisted outcome within 24 hours; upstream failures remain explicit.
3. Warm-cache load test: 100 concurrent customer reads cause zero Google calls.
4. Cold worker restart resumes queues without duplicate authoritative observations.
5. A provider outage leaves the last successful observation visibly stale and does not erase it.
6. If the gate fails, Building Quietly stays internal and Bounty switches to BigQuery, official API access, or another licensed source rather than pretending trendspy is production-safe.

### Commercial data-rights gate

Trendspy’s MIT license covers the library, not automatically the commercial redistribution of Google data. Before selling:

- Review Google Trends/BigQuery/API terms for storing, deriving, displaying, and reselling insights.
- Prefer derived signals and citations over redistributing raw series.
- Apply for the official Google Trends API alpha/current program.
- Record the approved source and permitted use in provider metadata.

## 7. Deterministic signal methodology

All signal math is code, not LLM judgment.

### Building Quietly features

Calculated only when enough comparable observations exist:

- Current-window median vs prior same-season medians
- Current seasonal peak vs prior seasonal peaks
- Recent-window median vs preceding window
- Persistence: consecutive comparable periods above a historical percentile
- New-high flag excluding the partial current point
- Cross-country agreement based on within-country anomalies, never raw index comparison across independently normalized countries
- First-detectable date using only information available as of each historical date
- Missing/partial coverage counts

### Ranking

Do not collapse everything into an unexplained score. Store individual features and produce a deterministic ordering with visible reasons, for example:

```text
Portable air conditioners
Surfaced because:
- current seasonal level exceeds prior comparable summers
- the change persisted for four complete observations
- matching within-country anomalies appeared in Germany and France
Missing:
- two requested markets lacked sufficient history
```

### Backtest integrity

- Known cases (air conditioners, seaweed, Sora) validate calculations only.
- A blind holdout universe validates discovery.
- Freeze the candidate universe and scoring version before running the holdout.
- No UI until the blind scan produces useful non-handpicked leads and Vincent approves the raw output.

## 8. Implementation phases

### Phase 0: Freeze contracts and baseline

**Objective:** Protect the shipped horizontal engine before vertical changes.

**Files:**
- Create: `tests/investing/test_horizontal_contracts.py`
- Modify later: none during the baseline step

**Tasks:**
1. Capture current research-run create/execute/findings fixtures.
2. Capture current TackSense-compatible export fixture.
3. Add a feature-flag contract asserting the default edition can change without route deletion.
4. Run focused research-run and dashboard tests.
5. Commit the baseline separately, excluding databases/screenshots/tmp files.

**Gate:** Existing research and authenticated API contracts remain green.

### Phase 1A: Ship useful global Breaking Now internally

**Objective:** Give Vincent a global investing radar immediately using the reliable current feed.

**Files:**
- Create: `social_scraper/investing/__init__.py`
- Create: `social_scraper/investing/models.py`
- Create: `social_scraper/investing/storage.py`
- Create: `social_scraper/investing/global_sweep.py`
- Create: `social_scraper/investing/service.py`
- Create: `scripts/collect_investing_radar.py`
- Test: `tests/investing/test_global_sweep.py`
- Test: `tests/investing/test_investing_storage.py`

**Tasks:**
1. Define additive `InvestingCandidate`, `CandidateObservation`, and `RadarReason` models.
2. Add tables for sweep runs, candidate identities, country observations, and source outcomes.
3. Reuse `TRENDING_NOW_COUNTRIES`; do not duplicate the allowlist.
4. Implement a resumable sweep across every supported country.
5. Deduplicate global candidate identity while preserving all country observations.
6. Build a persisted Radar read model with optional country/category filters.
7. Verify one failed country does not fail or erase the rest of the sweep.
8. Run one controlled all-market sweep and report actual coverage, duration, and failures.

**Gate:** All supported markets receive a persisted outcome and the Radar is readable with zero live upstream calls.

### Phase 1B: Prove historical-provider capacity

**Objective:** Determine whether unofficial long-horizon collection can support the product before depending on it.

**Files:**
- Create: `social_scraper/investing/providers.py`
- Create: `social_scraper/investing/history_scheduler.py`
- Create: `scripts/probe_investing_history_provider.py`
- Test: `tests/investing/test_trend_providers.py`
- Test: `tests/investing/test_history_scheduler.py`

**Tasks:**
1. Wrap each trendspy endpoint separately and expose structured health.
2. Preserve full-window observations and partial markers.
3. Enforce single-term historical requests unless an explicit comparison test permits otherwise.
4. Implement queue leases, jitter, backoff, and circuit breaker.
5. Verify independently normalized windows cannot be appended.
6. Run a diverse canary: short/long terms, low/high volume, multiple scripts and geographies.
7. Start the seven-day production-equivalent canary.
8. Apply for/verify current official Google Trends API access separately.

**Gate:** The provider must meet the reliability gate above. Failure triggers source substitution, not more UI work.

### Phase 2: Build candidate memory and sparse global depth

**Objective:** Grow a global historical candidate universe without term-country Cartesian explosion.

**Files:**
- Create: `social_scraper/investing/candidate_universe.py`
- Create: `social_scraper/investing/bigquery_provider.py`
- Create: `scripts/import_investing_taxonomy.py`
- Test: `tests/investing/test_candidate_universe.py`

**Tasks:**
1. Promote distinct terms from global current sweeps into persistent candidate memory.
2. Add BigQuery Top/Rising ingestion behind configuration.
3. Add taxonomy imports as provenance-bearing vocabulary only.
4. Deduplicate case, Unicode, aliases, and source identifiers without merging genuinely different topics.
5. For promoted terms, fetch Worldwide history and region distribution.
6. Schedule country histories only for discovered/relevant geographies.
7. Record why each term-country history was requested.
8. Produce a coverage report by language, region, origin, and source health.

**Gate:** Every promoted term is traceable to a non-LLM source; global coverage gaps are explicit.

### Phase 2B: Add independent non-Google discovery channels

**Status (2026-08-25):** Social Pulse first slice implemented with centrally scheduled Reddit, YouTube, TikTok, Instagram, and X discovery probes; immutable evidence, citation-gated candidate extraction, explicit source gaps, and a peer Social conversations UI lane. Broader Wikimedia/GDELT/GitHub adapters remain future gates.

**Objective:** Broaden unknown-unknown discovery without turning the first release into a connector zoo.

**Files:**
- Create: `social_scraper/investing/channels/__init__.py`
- Create: `social_scraper/investing/channels/base.py`
- Create: `social_scraper/investing/channels/wikimedia.py`
- Create: `social_scraper/investing/channels/youtube_popular.py`
- Create: `social_scraper/investing/channels/gdelt.py`
- Test: `tests/investing/test_discovery_channels.py`
- Test: `tests/investing/test_cross_channel_identity.py`

**Tasks:**
1. Define a `DiscoveryChannel` protocol returning provenance-bearing `DiscoveryObservation` records.
2. Implement a Wikimedia spike using official most-viewed and page-history endpoints across a small representative set of language editions; measure recurring-noise exclusions and preserve the exclusion report.
3. Probe YouTube’s official `mostPopular` endpoint across representative regions/categories; record actual coverage, quota use, and whether it still provides useful broad discovery before productionizing it.
4. Implement a GDELT spike for global event/entity candidates; keep media activity separate from demand/adoption evidence.
5. Deduplicate cross-channel subject identity conservatively while preserving every source-native observation.
6. Build channel-specific change features; never compare native metric magnitudes across channels.
7. Add transparent `discovered_by`, `confirmed_by`, `contradicted_by`, and `coverage_missing` reason fields.
8. Run a blind multi-channel scan and compare unique useful leads contributed by each channel.
9. Keep each new channel behind its own feature/config flag and provider canary.

**Gate:** At least two independent channels contribute useful non-duplicate candidates, source health is explicit, and cached Radar reads cause zero channel-provider calls. Channels that fail the gate remain experimental and do not block the investing release.

### Phase 3: Implement Building Quietly math and blind scan

**Objective:** Prove the investing signal before building its UI.

**Files:**
- Create: `social_scraper/investing/signals.py`
- Create: `social_scraper/investing/ranking.py`
- Create: `scripts/run_investing_blind_scan.py`
- Test: `tests/investing/test_signals.py`
- Test fixtures: `tests/fixtures/investing_trends/`

**Tasks:**
1. Write tests for seasonal windows across northern/southern hemispheres.
2. Write tests for partial latest points and missing weeks.
3. Write tests proving separately normalized geographies are never compared by raw magnitude.
4. Implement seasonal baseline, persistence, cross-country agreement, and first-detectable date.
5. Version every signal-method configuration.
6. Validate calculations on known cases without using them for threshold tuning after the holdout freeze.
7. Freeze a blind candidate universe and run the scan.
8. Export a human-readable table with raw charts, reasons, provenance, gaps, and first-detectable dates.

**Gate:** Vincent approves at least several non-handpicked leads as worth investigating. Otherwise revise candidate coverage/methodology and rerun a newly frozen holdout.

### Phase 4: Vertical investing dashboard

**Objective:** Make investing the only visible product while retaining hidden horizontal routes.

**Files:**
- Modify: `apis/dashboard_page.py`
- Modify: `apis/dashboard_api.py`
- Modify: `public/dashboard.js`
- Modify: `public/dashboard.css`
- Modify: `tests/test_dashboard_product.py`
- Create: `tests/investing/test_investing_api.py`

**Tasks:**
1. Add `BOUNTY_PRODUCT_EDITION=investing` with investing as the production default after approval.
2. Replace the default navigation with `Radar`, `Research`, `Monitors`, and `Usage`.
3. Keep old views addressable behind a disabled-by-default horizontal flag.
4. Add persisted `/investing/radar` list/detail endpoints; no provider calls.
5. Render Breaking Now and Building Quietly as separate lanes.
6. Show why-surfaced reasons, geography, history, source timestamps, and gaps.
7. Add Investigate action using the existing research-run API.
8. Verify desktop/mobile, loading/empty/stale/provider-outage states.

**Gate:** Vincent completes global lead → investigate → cited findings → reload on desktop and mobile.

### Phase 5: Investing-specific cited projection

**Objective:** Reuse the horizontal corpus but make the brief answer investing questions.

**Files:**
- Modify: `social_scraper/lenses/investing.py`
- Modify: `social_scraper/lenses/presets.py`
- Modify: `social_scraper/discovery/handlers.py`
- Modify: `public/dashboard.js`
- Test: `tests/discovery/test_execution.py`
- Test: `tests/investing/test_investing_projection.py`

**Tasks:**
1. Make the investing preset affect extraction/projection, not only metadata.
2. Prioritize cited adoption, purchase intent, rejection, substitution, operational impact, and counterevidence.
3. Add market-awareness checks only when actually executed.
4. Preserve the full canonical evidence list regardless of ranking.
5. Render unsupported sections as omitted or explicitly unavailable.
6. Keep company mapping and materiality outputs disabled in the initial release. Treat the existing qualitative `social_scraper/lenses/investing.py` output only as a hypothesis generator until filing-backed exposure work is implemented.

**Gate:** A real candidate produces a useful cited brief without stock advice or unsupported causal claims.

### Phase 6: Information-coverage and market-awareness analysis (later)

**Objective:** Determine how widely the investment-relevant information has already been observed, while never claiming universal silence from incomplete source checks.

**Reuse:** Extend `social_scraper/discovery/market_awareness.py`, which already preserves cited headlines, source tiers, source health, company acknowledgement, and price context.

**Files:**
- Modify: `social_scraper/discovery/market_awareness.py`
- Create: `social_scraper/investing/coverage.py`
- Modify: investing storage/read model
- Test: `tests/investing/test_information_coverage.py`
- Test: existing market-awareness tests

**Tasks:**
1. Define the checked information universe for each run: niche media, mainstream media, company filings/releases, earnings calls, and sell-side research only where licensed or explicitly supplied.
2. Store first-observed and first-published timestamps, unique outlets, source tiers, article/mention counts, geography/language, and complete source-health receipts.
3. Separate `event_covered` from `investment_implication_covered`. A heatwave may be headline news while the downstream HVAC demand implication remains weakly discussed.
4. Add truthful classifications such as `unchecked`, `niche_only`, `mainstream_covered`, `company_acknowledged`, `sell_side_observed`, and `unknown_incomplete_coverage`.
5. Never infer sell-side silence from public web search. Licensed research feeds or user-imported research are required for that claim.
6. Add price/volume context as market-reaction evidence, labeled `context_not_causation`.
7. Track coverage over time so an initially obscure lead can visibly migrate to mainstream/company/sell-side awareness.
8. Expose the exact checked sources and limitations next to the result.

**Gate:** The system can distinguish “little coverage found in the checked universe” from “not checked” and “coverage incomplete”; every coverage claim resolves to dated evidence and source health.

### Phase 7: Security relevance and filing-backed materiality (later)

**Objective:** Map a validated trend to potentially affected listed companies and quantify plausible revenue, margin, earnings, or cash-flow exposure without relying on entity mentions or LLM confidence alone.

**Reuse:** The existing `social_scraper/lenses/investing.py` already rejects entity-only materiality. Retain it as a hypothesis generator, then replace its qualitative `plausible/weak` output with a filing-backed exposure model.

**Files:**
- Create: `social_scraper/investing/security_resolution.py`
- Create: `social_scraper/investing/filings.py`
- Create: `social_scraper/investing/exposure.py`
- Create: `social_scraper/investing/materiality.py`
- Modify: `social_scraper/lenses/investing.py`
- Test: `tests/investing/test_security_resolution.py`
- Test: `tests/investing/test_materiality.py`
- Fixtures: dated annual reports/filing extracts with source URLs and hashes

**Tasks:**
1. Resolve the trend to an economic mechanism first: demand volume, price/mix, input cost, capacity, inventory, retention, substitution, regulation, or reputation.
2. Map the mechanism to products, services, inputs, customers, and value-chain positions before mapping companies.
3. Resolve public companies globally using stable company/security identifiers, exchange, ticker, and effective dates; preserve aliases and corporate actions.
4. Source product, segment, and geographical exposure from primary filings, annual reports, investor presentations, and earnings-call transcripts.
5. Separate every input into `reported`, `calculated`, `assumed`, or `unavailable`.
6. Calculate disclosed exposure first: affected segment revenue as a percentage of consolidated revenue, relevant geography share where disclosed, segment margins, and cost/input exposure.
7. Where the trend magnitude cannot be translated directly into sales, build explicit low/base/high scenarios instead of treating search/social growth as revenue growth.
8. Bridge scenarios through revenue, gross/operating profit, EPS, or free cash flow only when the required reported inputs exist.
9. Show direct beneficiaries, suppliers, substitutes, customers, and negatively exposed companies separately.
10. Preserve contradictory evidence, diversification offsets, capacity constraints, inventory timing, pricing response, and what the market may already discount.
11. If product/geography exposure is not disclosed, return `materiality_not_quantifiable`; do not manufacture an allocation.
12. Version assumptions and make every calculation reproducible.

**Materiality output, not an opaque score:**

- relevance mechanism and value-chain position
- reported revenue/earnings exposure range
- modeled scenario impact with explicit assumptions
- directness and confidence by evidence type
- timing to financial statements
- key offsets and invalidation conditions
- source filing/date/page or stable URL
- `not_quantifiable` where data is missing

**Gate:** On a dated real-company fixture, every hardcoded financial input traces to a primary filing, formulas reproduce the scenario bridge, entity matching alone cannot create materiality, and missing segment/geography disclosure remains unknown.

### Phase 8: Multi-user sellability

**Objective:** Prove user growth does not multiply upstream risk.

**Files:**
- Modify: scheduler/provider modules based on canary findings
- Create: `tests/investing/test_cached_read_load.py`
- Create: `tests/investing/test_provider_outage.py`
- Update: deployment/env documentation

**Tasks:**
1. Load-test cached Radar endpoints with 100 concurrent reads.
2. Assert zero upstream provider calls during reads.
3. Verify worker leases under multiple replicas.
4. Add stale-data labeling and provider canary alerts.
5. Complete the commercial data-rights review.
6. Run authenticated production Radar → investigate → findings → reload.
7. Keep external beta closed until the seven-day canary and legal gate pass.

## 9. Validation commands

Use focused commands during implementation; exact tests may grow additively:

```bash
python -m pytest tests/investing -q
python -m pytest tests/test_dashboard_product.py tests/discovery/test_execution.py tests/discovery/test_triage.py -q
node --check public/dashboard.js
python -m py_compile apis/dashboard_api.py apis/dashboard_page.py social_scraper/investing/*.py
git diff --check
```

Before each deploy:

```bash
git status --short --branch
git diff --cached --name-status
git diff --cached --check
```

Then run one controlled desktop/mobile investing journey and one real bounded global candidate investigation. Stage only intended source/tests; exclude databases, WAL/SHM files, logs, screenshots, browser profiles, credentials, and `tmp/` scripts.

## 10. Delivery sequence and realistic timing

### Internal usefulness first

- **1–2 focused days:** Global Breaking Now persisted sweep and read model, using the already-audited endpoint.
- **2–4 additional focused days:** Historical-provider spike, candidate memory, and first blind Building Quietly output if the provider cooperates.
- **1–2 focused days per non-Google channel spike:** Wikimedia first, then YouTube/GDELT; only channels that add useful non-duplicate leads graduate. These spikes can proceed independently after the shared observation contract exists.
- **2–3 additional focused days:** Investing dashboard and existing-research integration after signal approval.

### Sellable beta

No earlier than:

- seven-day provider canary completed,
- cached multi-user load test passed,
- authenticated production journey passed,
- commercial data-rights gate resolved.

A fast internal product and a sellable multi-user product are different gates. Do not claim the latter from a few successful local trendspy calls.

## 11. Immediate approval decision

The recommended default is:

1. Product name/edition: **Bounty Investing** internally; branding can be revisited later.
2. Visible product: global `Radar → Research → Monitors → Usage`.
3. Geography: global by default; country/region are filters, not scope limits.
4. Data strategy: centralized persisted collection; no per-user upstream calls.
5. First delivery: global Breaking Now plus a provider-capacity report, followed by Building Quietly only after its gate.
6. Discovery breadth: Google is one channel; Wikimedia, YouTube, and GDELT enter through independent gated adapters rather than a blended black-box score.
7. Company mapping: deferred until Phase 7, after discovery, behavioural evidence, and information-coverage stages are useful.

Once approved, implementation starts at Phase 0 and Phase 1A. Do not begin by seeding a large arbitrary term list or redesigning the full dashboard.
