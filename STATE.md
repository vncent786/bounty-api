# Bounty API — Active Work State

**Last updated:** 2026-08-03 15:08 SGT
**Status:** OWNED CURRENT REDDIT ROUTE LIVE; RAW + NORMALIZED RECORDING VERIFIED, SLA MEASUREMENT PENDING

## Verified this session

- Multi-route source broker with ordered fallback, route timeouts, duplicate-platform suppression and sanitized per-attempt health.
- Useful partial results are retained when later routes fail.
- Every returned item includes connector provenance and fetch timestamp.
- TikTok default routes: authenticated connector, then Playwright fallback.
- SQLite observation warehouse stores immutable collection runs and append-only engagement history.
- Query registry supports atomic leases so concurrent workers cannot collect the same scheduled query.
- Collection completion and schedule advancement are transactional.
- Failed due queries release their lease and do not block later queries.
- Collection administration is protected by `BOUNTY_SOCIAL_ADMIN_TOKEN`.
- Paid social search fails closed unless x402 middleware initializes successfully.
- Proxy URL credentials are removed from health output.
- `POST /social/search` added to x402 payment routing.
- Camoufox Reddit depth endpoints added: allowlisted subreddit feeds and canonical post/comment hydration.
- Camoufox runs through a single bounded, killable subprocess worker with queue/operation timeouts, strict Reddit URL and egress allowlists, cancellation-safe resource gating, challenge detection and explicit comment truncation metadata.
- Hardened live feed probe returned three real `r/Python` posts. Repeated exact-post probes later hit Reddit verification and correctly returned a worker error rather than false empty success.
- Reddit discovery now uses PullPush primary plus an optional Brave Search API fallback when `BOUNTY_BRAVE_SEARCH_API_KEY` is configured. The fallback accepts canonical Reddit post URLs only, deduplicates by post ID, and does not fabricate engagement.
- Fresh scheduled snapshots can be served through `POST /social/search/cached` using an exact query/platform/region key and caller-specified maximum age.
- `scripts/collect_social_due.py` provides a one-shot, lease-safe scheduler entrypoint. A live PullPush scheduled run persisted 20 observations, returned a cache hit for the same run, advanced its schedule, and was skipped on immediate second invocation.
- Brave credential moved from Downloads into the gitignored project `.env`; the Downloads copy was removed. The main application now loads local `.env` values without overriding platform-provided environment variables.
- Live Brave testing: corrected one-week `site:reddit.com python packaging` query returned one canonical post. Restrictive `/r/`, `inurl`, and quoted query variants returned zero. Published pricing is $5 per 1,000 requests with $5 monthly credits and 50 requests/second capacity.
- PullPush rejects relative `after=7d`/`after=180d` parameters with HTTP 400; the connector now sends Unix timestamps. Its recent index returned zero in live tests, so it is classified as archival/backfill rather than dependable real-time discovery.
- Arctic Shift returned five recent records for a scoped `r/Python` query, but requires a subreddit or author for keyword search and therefore cannot replace global discovery.
- Arctic Shift connector added between PullPush and Brave for configured subreddits. It uses cancellable async HTTP, strict total/read/connect timeouts, a process-wide concurrency gate, a 2 MB response cap, controlled rate-limit fallback, canonical URL validation, and explicit scoped-coverage metadata.
- Archived Arctic engagement is never presented as current: each item carries `source_observed_at` from `retrieved_on`; metrics are nulled if that timestamp is absent or invalid. Corrupt timestamps, boolean metrics, deleted authors, unknown media, flair semantics, unsupported filters, and invalid configuration are handled fail-closed.
- Local scope is deliberately limited to `r/Python`. Live end-to-end result: post `1v0sg94`, archive observation timestamp `2026-07-19T14:46:28+00:00`, 1.469-second connector latency, `global_coverage=false`.
- Request-level Reddit scopes now propagate through API validation, broker routing, scheduled queries, collection runs, and exact cache keys. Search time/sort options are also keyed, preventing stale cross-window cache collisions.
- Unscoped connectors are skipped for scoped requests. Complete scoped outages are errors, not partial successes, and scheduler exit status surfaces them.
- Scope-aware SQLite migration handles both fully legacy databases and partially migrated databases with stale unique constraints.
- Named cross-use-case canaries and a seven-day reliability report are built. Reports exclude ad hoc runs, count Reddit-only items, exclude skipped attempts from latency, and remain fail-closed until minimum-run, error, item-coverage, and timestamp gates pass.
- Realistic investing canary: Arctic Shift returned HTTP 500 for all five requested communities. Result contained zero items, no unscoped contamination, and correctly failed the reliability gate. Arctic alone is therefore inadequate for investment-grade scoped current discovery.
- Scheduled/admin collection now uses a separate broker that prefers exact-scope Camoufox `/rising` + `/new` collection with a bounded 210-second worker and 240-second route ceiling. Customer live search remains on the fast broker and never invokes this browser route.
- Camoufox scheduled scope is capped at five requested communities, checked against an operator allowlist, persisted in cache/run identity, and reported in coverage metadata. Unscoped queries skip Camoufox rather than scanning the full allowlist.
- Camoufox is now in the production Python dependency manifest and Docker build fetches its browser runtime. Docker execution could not be verified locally because Docker is not installed on this host.
- Camoufox consumes a dedicated `BOUNTY_REDDIT_PROXY_SERVER` when configured, while retaining shared proxy credentials. Local Reddit uses Geonode rotating port 9000; TikTok remains on its sticky port.
- Live scheduled `r/Python` probes failed both direct and through sticky/rotating residential routes with Reddit's verification challenge. The latest stored run is `e460178b-a6a9-4a13-8f0d-59ccf95b28b0`; Camoufox failed after 39.447 seconds and Arctic Shift also returned unavailable. Zero items were stored and the route remained an explicit error.
- Reddit's first-party anonymous `/new.json` feed also returned HTTP 403 through both direct and rotating residential access. The unresolved problem is source access, not parser or routing logic.
- Verification: 59 focused tests passed; compile checks and `git diff --check` passed. Seven of eight legacy live-server tests passed; the remaining known stale test still expects JSON at `/` even though the route now serves HTML. No browser/server processes were left running.
- GitHub/Reddit research identified three owned routes: Redlib's official-Android installed-client token flow, Reddit Atom feeds, and browser-minted `/svc/shreddit/token` bearer tokens. Anonymous Shreddit probes reached Reddit but did not mint a token; the Redlib-derived mobile flow worked immediately.
- `reddit_mobile_owned` is now scheduled Reddit priority 1. It uses a stable local device ID, a read-only short-lived bearer, browser TLS emulation, sticky proxy identity binding, exact per-subreddit `/new` listings, one 401 refresh, serialized access, bounded request timeouts, and conservative pacing. No developer key or external data provider is used.
- `reddit_atom_scoped` is priority 3 fallback. It makes one combined `/new/.rss` request for up to five communities, enforces the requested age window, validates canonical `t3_` identity, and never supplies engagement fields that RSS does not contain.
- Live owned investing collection succeeded across `stocks`, `investing`, `options`, `SecurityAnalysis`, and `ValueInvesting`: 14 keyword-matching posts with canonical URLs, creation timestamps, scores, and comment counts in 6.890 seconds. All five subreddit listing requests succeeded.
- Scheduled collection now privately archives untouched mobile post JSON and byte-exact base64 Atom payloads in append-only `source_records`, with original-byte/canonical-JSON SHA-256 verification. Raw records never appear in customer/admin collection responses. Latest verified run `ec4b80e6-1a7e-4fb6-ba2f-4f20d78d1625` stored 60 source records with all hashes valid and five distinct per-subreddit fetch timestamps.
- Repeated post observations append rather than overwrite. Post `1ve04mj` now has RSS discovery plus multiple mobile metric observations; the latest verified record preserved 12 score and 34 comments without smoothing or interpolation.
- Scheduled Reddit queries now require explicit subreddit scope. Observation-history access requires the admin token. Collection leases are 30 minutes, and raw records from attempted but unselected fallback routes are retained.
- Verification: 71 focused tests passed; compilation and `git diff --check` passed. No browser/server processes were left running.

## Honest platform status

- Reddit: PullPush provides global archive/backfill, not dependable current coverage. Arctic Shift is active as a free, keyless, scoped archive route for configured communities, with dynamic rate limits and no SLA. Brave is a credentialed tertiary canonical-URL locator with sparse fresh-result recall and metered cost. Camoufox `/new` and `/rising` feeds are the strongest current route for configured communities. No current route provides reliable global real-time keyword coverage.
- YouTube: one route, yt-dlp. Needs official YouTube API primary.
- TikTok: two owned browser routes, still operationally fragile. Needs two independent commercial upstream routes plus session/account operations.
- Douyin: one Playwright route, not reliable from current region.
- Xiaohongshu: one Playwright route, login/region constrained.

Do not claim trend velocity yet. Historical collection infrastructure now exists, but repeated observations must be accumulated before a rising-trend endpoint is defensible.

## Next priorities

1. Run the owned mobile route plus RSS fallback through the named canaries for seven days. Measure availability, freshness, completeness, rate limits, and metric divergence before claiming an SLA.
2. Keep Camoufox only for selected post/comment hydration. Do not use repeated anonymous feed retries while Reddit challenges that route.
3. Add mobile OAuth thread JSON plus bounded `morechildren` expansion after current-post recording has accumulated cleanly.
4. In parallel, build YouTube Data API primary and owned TikTok redundancy. Commercial providers remain emergency fallback only.
5. Trend velocity remains deliberately deferred until the underlying observations are sufficiently accumulated and verified.

See `SOCIAL_RELIABILITY_FOUNDATION.md` for architecture, endpoints and verification commands.
