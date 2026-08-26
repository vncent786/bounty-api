# Camillo-Style Information Arbitrage Radar Rebuild

> **For Hermes:** The public Investor Radar has been rolled back. Do not re-expose it until every promotion gate below passes on real holdout data. Raw posts, source-native fallback titles, and Google Trending Now rows are internal evidence, never customer-facing leads.

**Goal:** Make Bounty feel like a disciplined information-arbitrage desk: a very small number of specific, fresh behaviour shifts that are accelerating across comparable observations, remain poorly connected to the financial narrative, and are worth a bounded investigation.

**Method sources:** Vincent's prior Social Arbitrage Scanner v2 skill; Social Arbitrage Process SOP v2/v3; Social Arbitrage Velocity Tracking thread; information-parity L0-L5 ladder; perennial-noise and materiality post-mortems; Chris Camillo's user-supplied *Laughing at Wall Street* PDF; and the 2025 My First Million interview at https://youtu.be/rdChG9FPs80. Canonical source notes: `references/camillo-information-arbitrage-methodology-2026-08.md`.

## Product promise

The first screen must answer:

1. What specific behaviour is changing?
2. Why is it new now, versus perennial discussion?
3. How quickly is it spreading versus its own baseline?
4. Is breadth expanding across independent people, communities, creators or platforms?
5. Has consumer or financial media already connected the implication?
6. What evidence supports the lead and what would invalidate it?

If no topic passes, show **No qualified leads this cycle**. Silence is better than filler.

## What is never a lead

- A raw Reddit/YouTube/TikTok/Instagram/X post title
- A raw Google Trending Now query
- A broad perennial theme such as `AI`, `inflation`, `weather`, `fitness` or `news`
- A viral entertainment item with no behaviour mechanism
- A topic found only because the company/stock price already moved
- A perennial complaint that was equally discoverable six months earlier
- A fully financialized narrative already attributed by Bloomberg/Reuters/CNBC or sell-side
- A company mention without verified economic exposure

## Two internal candidate loops

### A. Open category discovery

Use versioned Camillo-style consumer panels, not random generic probes:

1. Automobiles
2. Airlines
3. Hotels/travel
4. Restaurants/QSR
5. Food/beverage
6. Beauty/skincare
7. Fashion/apparel
8. Luxury
9. Retail
10. Consumer technology
11. Streaming
12. Telecom
13. Fintech/payments
14. Fitness/wearables
15. Pets
16. Household/cleaning

Each panel defines stable platform-native scopes: subreddits, YouTube query families, TikTok/Instagram hashtags, X queries, Google Trends subjects, languages and geographies. These are collection definitions, not LLM-generated terms and do not consume model tokens.

### B. Reversed tradeable-universe monitoring

Maintain a global versioned product/brand/security relationship graph as a second loop. It includes listed equities, ADRs and other relevant tradeable exposures regardless of whether options exist. It solves `interesting behaviour, no tradeable exposure`, but does not replace open discovery. Security mapping stays hidden until the behaviour and freshness gates pass. Options, liquidity, borrow and implementation are downstream attributes, never discovery gates.

## Comparable collection

- Run the same versioned scopes every 5-7 days.
- Preserve immutable records and source-native metrics.
- Store exact query/scope/version, platform, geography, language, retrieved time and source health.
- Never compare snapshots when the query/scope changed.
- Never sum views, likes, comments or search indexes across platforms.
- Keep repost propagation separate from independent voices.
- Collect centrally. Customer reads make zero upstream calls.

### Sustainable source execution

- Reddit: owned current mobile connector plus explicit community universe and canary.
- YouTube: official Data API for canonical metadata/comments where feasible; yt-dlp only behind a measured fallback canary.
- X: Bounty's owned authenticated web-GraphQL collector is the primary low-cost route. Scweet handles SearchTimeline, client-transaction IDs, cookies, account cooldowns, date-bounded search, public engagement metrics, and `conversation_id` reply reconstruction. Run it only on the owned worker with conservative daily/page limits. The official X API is an optional paid fallback/audit route, not an economics-critical dependency. Grok X Search remains discovery/corroboration only.
- TikTok: Bounty's owned authenticated Chrome collector runs from the Windows residential worker using the persistent `tiktok_real` profile, sticky Geonode egress, browser-generated signatures/tokens, API-response interception, and DOM fallback. It supports keyword search plus bounded root comments and replies. Serialize profile use, monitor session health, and never run it from Railway datacenter IPs.
- Instagram: Bounty's owned collector uses stored browser cookies on the session's originating residential IP. The authenticated keyword-search page yields GraphQL media records; the web comment and child-comment endpoints provide bounded thread depth. Hashtag `web_info` remains a fallback, not the definition of keyword search.
- SaaS execution: one or more owned residential workers collect centrally and push immutable observations to Bounty. Customer reads hit only persisted data. Add profile backup, manual re-auth alerts, low-rate queues, account isolation, daily canaries, and a second owned worker before promising an external SLA. Apify and Bright Data are not dependencies.
- Grok synthesis: paid xAI Responses API over a fixed persisted evidence snapshot. Do not use Z.AI and do not let Grok live-search mutate the canonical evidence used for the main synthesis.
- A failed model call leaves the cluster internal. Raw titles never become fallback leads.

## Day-one retrospective detection

Bounty does not require a blank three-week onboarding period when timestamped provider backfill is available.

On day one, backfill comparable historical posts/comments, bucket them into fixed windows and compute the current anomaly against the topic's own source/query/geography baseline. A candidate may surface as **Retrospective anomaly** when the backfill supports onset, velocity, independent breadth, propagation concentration and information-parity checks.

Forward collection is still required to establish post-detection persistence, acceleration/reversal, cohort spread, production reliability and prospective validity. When historical coverage is too shallow or incomparable, the UI must say **Building baseline** rather than infer velocity.

## Persistent topic clustering

Raw records are clustered into stable topic families before promotion.

- Candidate identity is a specific product/service/material/problem/behaviour, not a mega-theme.
- Use deterministic entity/phrase extraction plus embeddings for candidate grouping.
- LLM labels are optional projections after clustering and must cite cluster records.
- Topic merges/splits are versioned and auditable.
- A failed model call leaves the cluster internal; it never falls back to displaying raw titles.

## Velocity features

No public velocity claim until at least three comparable windows exist. These windows may come from verified historical backfill or forward collection; the source and coverage must be disclosed.

For each source/topic/scope:

- new record count versus baseline
- unique independent voices/creators versus baseline
- engagement distribution versus baseline within that source
- number of new communities/channels entering
- small-creator outperformance relative to that creator's own baseline
- persistence across consecutive cycles
- first-detected date
- cross-platform corroboration without metric blending
- Google search acceleration/geography where available

Cold-start truth:

- With sufficient comparable backfill: day-one retrospective anomaly flags are allowed, explicitly labelled retrospective.
- Without sufficient backfill: cycles 1-2 build the baseline; cycle 3 permits a provisional flag.
- Prospective confirmation always requires observations collected after the first alert.

## Promotion gates

A customer-facing lead must pass all required gates.

### Gate 1: Specificity

Reject generic umbrella topics. `AI` fails. `Power-transformer demand for datacentres entering new utility procurement cycles` may proceed.

### Gate 2: Behaviour

Require concrete observed behaviour: purchased, adopted, switched, cancelled, returned, sold out, shortage, workaround, rejection, quality decline, new use case or operational constraint. Sentiment alone does not pass.

### Gate 3: Freshness/perennial test

Ask: could substantially the same evidence have been found six months ago? If yes, reject unless the velocity/breadth regime has newly changed.

### Gate 4: Comparable velocity

Require a statistically/deterministically explainable change versus the topic's own prior comparable snapshots. One viral post is not velocity.

### Gate 5: Breadth

Prefer independent creators/voices and new communities. Reposts and copied headlines count as propagation, not independent confirmation.

### Gate 6: Corroboration

A lead may originate from one platform, but ranking improves when search, another social platform, knowledge attention or physical/event data independently corroborates it.

### Gate 7: Information parity

Use the recovered Camillo-style ladder:

- L0: raw niche communities only
- L1: several niche social/creator signals, no mainstream coverage
- L2: specialist/consumer awareness, investment implication still unconnected
- L3: mainstream consumer coverage
- L3.5: first financial outlet/analyst mention
- L4: repeated sell-side/earnings-call/price attribution
- L5: consensus/company response and fully priced narrative

New-entry candidates target L0-L2. L3 may survive only when the consumer story is covered but the investment implication remains demonstrably unconnected. L3.5+ is not a fresh-entry discovery lead.

Never claim sell-side silence without licensed/user-supplied research coverage.

### Gate 8: Investigability

The lead must support a clear next question and invalidation condition. If the user cannot tell what to investigate, it does not ship.

## Future materiality gate

After discovery quality works:

1. Map behaviour to economic mechanism.
2. Map mechanism to product/value-chain exposure.
3. Resolve global securities.
4. Use primary filings for segment/geography exposure.
5. Quantify reported/calculated/assumed/unavailable inputs separately.
6. Revenue at risk below 2% remains watch-only under the recovered v3 process.

Search/social growth is never translated directly into revenue growth.

## Customer-facing lead contract

Each Radar lead shows:

- specific emerging shift
- why now, with baseline/velocity evidence
- behaviour type and independent voice breadth
- platforms/geographies/languages
- freshness/perennial assessment
- information-parity level and checked universe
- corroboration and contradictions
- source links
- what would invalidate the lead
- `Investigate` action

No raw post feed. No raw Google trend feed. No recommendation score.

## Build sequence

### Phase 0: Protect the user experience

- DONE: `/dashboard` restored to stable Classic Bounty.
- DONE: experimental Investor Radar moved to `/dashboard/investing-preview`.
- DONE: Railway Social Pulse collection disabled by default.

### Phase 1: Source reliability and owned residential collection

- Build source canaries for all five social platforms.
- Fortify owned X web GraphQL with account health, session refresh, fixed query slices, reply-depth canaries, account budgets, and a second owned account before an external SLA. Retain the official X adapter only as an optional fallback/audit route.
- Run TikTok and Instagram from the owned Windows residential worker, never Railway egress.
- Persist and monitor search, root-comment, reply, session-expiry, challenge, latency and truncation outcomes independently.
- Add a signed ingestion handoff so the residential worker writes immutable observations while Railway remains the read/API plane.
- Add a second owned profile/worker only after the first passes the canary; no third-party scraping API dependency.
- Persist source health and comparable scope receipts.
- Seven-day canary gate before any source can contribute to promotion.

### Phase 2: Historical backfill and baseline

- Lock category panels and query versions.
- Backfill historical windows where the provider supports them and record measured coverage.
- Run scheduled comparable snapshots for all panels.
- Keep all outputs internal.
- Build topic clustering and inspect cluster purity manually.

### Phase 3: Shadow qualification

- Run promotion gates in shadow mode after cycle 3.
- Review false positives, perennial content and generic mega-themes.
- Freeze a holdout and verify non-handpicked leads.

### Phase 4: Investor Radar release

- Expose only qualified leads.
- Production journey review from discovery to cited investigation.
- Empty cycle is accepted.
- Re-enable customer-facing Radar only after Vincent approves real shadow output.

## Release gate

Do not re-expose the Investor Radar until:

- all contributing sources pass canaries,
- at least three comparable windows exist from verified backfill and/or forward collection,
- generic mega-themes and perennial content are rejected,
- raw-title fallback is impossible on the public path,
- information parity is explicit,
- a blind holdout produces several leads Vincent would independently choose to investigate,
- desktop/mobile and authenticated production journey pass.
