# Bounty Current State

**Last updated:** 2026-08-16
**Canonical repository:** `D:\vncen\saas\bounty-api-fresh`
**Current product phase:** Investing Release A implemented: `/dashboard` is the global persisted Breaking Now Radar; `/dashboard/classic` preserves the horizontal workbench and receives prefilled investigation topics. Central scheduler sweeps all supported markets; customer reads never call upstream sources. Building Quietly, multi-channel discovery, information coverage, and filing-backed materiality remain gated later phases in `.hermes/plans/2026-08-16-vertical-pivot-building-quietly.md`.

## What Bounty is

Bounty is a horizontal social-intelligence SaaS inspired by Buzzabout's bounded-read and standing-read methodology.

Its job is to read conversations across social platforms, including replies, cluster them into themes, explain what people believe and disagree about, and monitor how those themes change over time.

Investing and social arbitrage are the first workflow and quality benchmark. Social arbitrage means identifying a fresh change in consumer behavior, demand, product reception, availability, or belief before that information is fully reflected in headlines, consensus expectations, or market price. The underlying collection and research engine remains horizontal; investing-specific evaluation is an optional lens above it rather than hardcoded platform, subreddit, sector, or ticker assumptions. Other intended users include marketers, product teams, founders, journalists, researchers, agencies, and analysts.

Despite the legacy name `Bounty API`, the dashboard and monitoring engine are the current product. The x402 API marketplace, Singapore data APIs, MCP distribution, and Railway public API are deferred. They remain in the repository but should not drive current development.

## Product thesis

Search volume can be both an early candidate signal and a later confirmation signal, depending on the topic. Google Trends Discovery is therefore a co-primary workflow for finding unknown-unknowns. Social posts, comment threads, replies, questions, dissent, and repost behavior determine whether a search spike reflects a durable behavior change or merely news, seasonality, celebrity attention, or perennial noise.

Bounty should answer:

- What are people discussing?
- What themes dominate the conversation?
- What do they believe, want, dislike, or disagree about?
- Which products, brands, and people are being mentioned?
- What is new, growing, weakening, or changing?
- What evidence supports each conclusion?
- Which platforms or collection routes were incomplete?

The product is not merely a five-platform search box. Its value is the accumulated, evidence-backed interpretation of conversations over time.

## Buzzabout reference method

Primary reference: https://buzzabout.ai/blog/google-trends-alternatives

Buzzabout's method has two main reads:

1. **Bounded read:** Define a zone using four or five seed keywords. Read a historical window across all available networks, including comment threads. Cluster the resulting corpus and produce a baseline understanding of the niche.
2. **Standing read:** Read the same bounded zone weekly. Detect rising negative sentiment, brand or competitor mentions, belief shifts, and trends starting or ending.

Their four-step discovery procedure is:

1. Define a zone with four or five seed keywords.
2. Use a search-trend feed only as a cheap candidate generator.
3. Put candidates through a social-conversation gate.
4. Read actual posts and comment threads to assess durability.

For Bounty's investing-first workflow, Google Trends Discovery and bounded zone monitoring are co-primary. Discovery searches for unknown emerging candidates; zones deeply read and continuously monitor known areas. Neither is sufficient alone.

## Current architecture

### User surfaces

- FastAPI dashboard at `/dashboard` (deployed at `bountyapi.com/dashboard`, token-gated)
- Dashboard API at `/dashboard/api/*`
- Views: Projects, Explore, Findings, Lenses, Monitors, Usage
- Explore: direct topic research bar (skip trends entirely) + trending-topic browsing with per-trend enrichment (7-day sparkline, rising/top related queries, category filter)
- Guided product tour (11 steps, auto-starts once, replayable)
- Research-runs: create plan → execute (30-90s live collection) → persisted findings with citations

### Collection

Current primary connectors:

- YouTube
- Reddit
- TikTok
- X
- Instagram

Experimental/secondary connector work also exists for Douyin and Xiaohongshu.

Collection is more reliable locally than on Railway. Platform coverage is uneven and must be disclosed in reports. Partial results are retained rather than disguised as comprehensive coverage.

### Discovery

`social_scraper/monitoring/topdown.py` currently:

1. Pulls Google Trends `trending_now` candidates through Trendspy.
2. Preserves volume, growth, freshness, related terms, and category metadata.
3. Runs the highest-priority candidates through a multi-platform conversation gate.
4. Uses the conversation reader to classify verified candidates.

This is a primary workflow for unknown-unknown discovery. It must evolve from a generic trend list into a social-arbitrage funnel that evaluates novelty, behavior versus sentiment, durability, company exposure, likely materiality, headline saturation, and whether price has already reacted.

### Zones and monitoring

`social_scraper/monitoring/zones.py` provides a SQLite-backed zone registry. A zone contains a name, description, four or five seed keywords, platforms, region, frequency, and status.

`social_scraper/monitoring/monitor.py` currently:

1. Searches every zone keyword across configured platforms.
2. Collects normalized posts and platform summaries.
3. Clusters posts using basic keyword overlap.
4. Stores cluster snapshots.
5. Diffs the current and previous snapshots.
6. Produces new/growing/shrinking/stable alerts.

`apis/scheduler.py` checks due active zones automatically. Manual runs execute as background tasks and expose staged progress through `/zones/{id}/status`.

### LLM

All active LLM calls are centralized in `social_scraper/llm_client.py`.

Local development currently uses GPT-5.4 through a temporary Hermes Codex OAuth adapter. This is acceptable for local validation only. Production requires a normal production API credential.

### Data integrity rules

- Never interpolate or fabricate missing observations.
- Preserve post URLs, platform provenance, timestamps, and collection route.
- Keep partial collection results and report source failures.
- Separate hard metrics from LLM interpretations.
- Do not present staged progress as measured connector-level completion.
- Do not claim trend velocity without comparable time observations.

## What is built

- Five-platform normalized collection infrastructure
- Connector fallback and provenance infrastructure
- SQLite observation and monitoring storage
- Canonical immutable post/comment/reply corpus with object-type-safe identities
- Explicit observation history, collection-route attempts, normalization diagnostics, and zone/keyword provenance
- Five-platform canonical normalization with unsupported metrics preserved as null
- Zone creation and scheduling
- Automatic and manual zone runs
- Staged run progress that survives dashboard navigation
- Google Trends candidate generation (trendspy trending_now)
- Trend enrichment on demand: interest-over-time sparkline + rising/top related queries (`/discover/trend-detail`)
- Social conversation gate
- LLM conversation summary, sentiment, entities, type, rationale, and quotes
- Research-run execution path: staged budgets, subreddit auto-discovery, deep thread reads, horizontal LLM extraction, persisted findings with citations
- Reddit mobile OAuth connector (mimics Android client) + keyword→subreddit auto-discovery — works without developer API
- Editorial research desk dashboard (Projects/Explore/Findings/Lenses/Monitors/Usage), responsive, guided tour
- Basic lexical clustering
- Snapshot diffing and alerts
- Legacy x402/API/MCP infrastructure (deferred)

## Known pipeline divergence (2026-08-11)

The zone path (TrendMonitor) and research-runs path (StagedRunner + handlers) have diverged. Research-runs is strictly richer: subreddit discovery, thread depth, triage findings, deduped evidence. Zones: none of those. See AGENTS.md "TWO pipelines" for detail and the pending upgrade-vs-deprecate decision.

## Important gaps

### 1. Replies and comment threads

The canonical nested conversation model, immutable observation storage, thread reconstruction, and zone provenance are integrated. The active connectors still collect primarily top-level posts, however. Phase 2 must retrieve real YouTube and Reddit comments/replies, preserve route completeness and truncation, and feed those records through the canonical corpus.

### 2. Bounded historical read

Creating a zone does not yet trigger a proper historical baseline read over a user-selected window and corpus target. Current zone runs collect a shallow fixed count per keyword and platform.

### 3. Semantic clustering

The monitor currently falls back to lexical overlap. Equivalent themes using different vocabulary can split, and posts sharing words can merge incorrectly. Cluster identity between runs is based on a basic hash rather than semantic continuity.

### 4. Cluster-level analysis

Current enrichment samples posts but does not yet produce a structured narrative for every meaningful cluster with claims, dissent, representative quotes, entities, sentiment distribution, and confidence/coverage.

### 5. Standing-read event detection

Snapshot diffing exists, but the four user-relevant events are not fully modeled:

- Rising negative sentiment
- Brand or competitor mention changes
- Belief shifts
- Themes starting or ending

### 6. Reposts and propagation

The system does not yet distinguish original discussion from reposts, copied content, cross-platform propagation, or independent corroboration.

### 7. Grounded Q&A

Users cannot yet ask a question of a zone corpus and receive a citation-backed answer grounded only in stored posts and comments.

### 8. Production SaaS infrastructure

Customer accounts, tenant isolation, billing, durable workers, production connector dependencies, production LLM credentials, and deployment of the complete engine are not built.

## Current priority order

1. Upgrade Google Trends Discovery into an investing-first social-arbitrage funnel while keeping its underlying fields horizontal.
2. Integrate nested reply collection for YouTube and Reddit and use replies to judge candidate durability.
3. Build a real bounded historical zone read with transparent source progress.
4. Build semantic clustering, stable cluster identity, and cluster-level narratives with evidence and dissent.
5. Build the weekly standing read and four change-event detectors.
6. Add repost/reshare and propagation analysis.
7. Add citation-grounded Q&A over the zone corpus.
8. Validate the first workflow on investing/consumer-behavior zones, then verify the same engine on two non-investing zones.
9. Only then add authentication, billing, production workers, and API distribution.

Generic Discovery-page cosmetics, more legacy API endpoints, x402, and Railway migration are not current priorities. Discovery intelligence and ranking are priorities.

## Active execution phases

- **Completed:** Phase 0 monitoring/source-health baseline and Phase 1 canonical conversation corpus
- **Next:** Phase 1B investing-first Google Trends Discovery
- **Then:** Phase 2 comment thread and reply analysis
- **Later:** Bounded reads, semantic clusters, standing reads, propagation, and grounded Q&A
- **After validation:** Production SaaS infrastructure

## Current implementation plan

See `.hermes/plans/2026-08-10_101218-bounty-buzzabout-roadmap.md`.
