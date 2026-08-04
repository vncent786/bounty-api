# Social Reliability Foundation

Updated: 2026-08-03

## Status

The reliability control plane is implemented locally. This is infrastructure, not a claim that every social platform is now reliable.

Verified test result:

```text
59 passed, 1 third-party deprecation warning
```

The legacy root-level `test_api.py` suite still expects a live server on `localhost:8000`; it fails with connection refused when no server is running and is not part of the isolated unit/integration result above.

## What is built

### Multi-route broker

`social_scraper/broker.py`

- Multiple ordered connectors per platform
- Automatic fallback
- Per-route timeout
- Retention of useful partial data when later fallbacks fail
- Duplicate platform suppression
- Per-attempt health output
- Sanitized public errors
- Field-level connector provenance

Current default route inventory:

- Reddit discovery: PullPush global archive/backfill, Arctic Shift scoped archive search, then Brave Search API canonical-URL discovery. None provides reliable global real-time search.
- Reddit current: owned official-Android installed-client OAuth listings, then combined Atom `/new` feeds, then Camoufox. Customer live search remains browser-free and scheduled collection serves the cache.
- Reddit depth: Camoufox allowlisted post/comment endpoints pending owned OAuth thread JSON and `morechildren` expansion.
- YouTube: yt-dlp
- TikTok: authenticated Chrome, then Playwright fallback
- Douyin: Playwright
- Xiaohongshu: Playwright

TikTok currently has two broker routes. Reddit has ordered discovery fallback plus a separate Camoufox depth route. Live August testing showed PullPush's recent index can be empty, while Brave's one-week search returned only one canonical Reddit post for a query where restrictive query variants returned zero. Arctic Shift is now inserted between them for explicitly configured subreddits. Its public service is free and keyless, dynamically rate-limited, and explicitly provides no uptime or performance guarantee. Responses disclose the searched subreddit scope and `global_coverage=false`; archived engagement is timestamped with Arctic's `retrieved_on` rather than the API request time. Camoufox `/new` and `/rising` feeds remain the stronger current-data route for known communities. YouTube still needs an official connector. TikTok still needs independent commercial upstream routes and production account/session operations before it can be described as reliable.

### Immutable historical storage

`social_scraper/storage.py`

SQLite tables store:

- complete collection runs and raw normalized responses
- append-only engagement observations
- scheduled query definitions
- collection leases

Repeated observations are appended rather than overwritten. SQLite runs in WAL mode with a busy timeout. Invalid provenance timestamps fall back to the verified collection timestamp rather than being stored as arbitrary sortable text.

Reddit subreddit scope is now a first-class request option rather than a server-wide assumption. Live search, scheduled query identity, collection runs, and cache keys include normalized subreddit scope plus search time/sort options. Existing SQLite databases are migrated to the scope-aware unique key, including partially migrated schemas that already had the column but retained the stale unique constraint. Connectors that cannot enforce an exact requested scope are skipped rather than contaminating the result with global matches.

Each response includes explicit requested/successful subreddit coverage and factual timestamp-completeness counts. Named investing, marketing, creator, consumer, and technical canaries live in `social_scraper/config/reddit_canaries.json`; `scripts/seed_reddit_canaries.py` validates/seeds them and `scripts/reddit_reliability_report.py` applies a seven-day, minimum-run, error, item-coverage, and freshness-timestamp gate.

The first realistic investing canary (`earnings` across `stocks`, `investing`, `options`, `SecurityAnalysis`, and `ValueInvesting`) returned HTTP 500 from Arctic Shift for all five scoped requests. The broker correctly returned an error, did not leak into unscoped PullPush/Brave results, and the scheduler now treats such a persisted run as an error. This is evidence that scoped Reddit is not yet SLA-ready and still needs a second exact-scope current source.

The scheduled Camoufox current-feed route is now built with exact request scope, `/rising` + `/new` coverage, an operator allowlist, proxy support, production package/browser installation, and longer collection-only timeouts. Live probes on 2026-08-03 were challenged by Reddit through direct access, a sticky residential route, and a rotating residential route. Reddit's anonymous first-party `/new.json` feed returned HTTP 403 both direct and proxied. These are explicit source-access failures: no current items were accepted, and repeated anonymous browser retries should not be scheduled until authenticated access or a commercial current-data route is available.

Subsequent GitHub research found and locally reproduced Redlib's self-operated official-Android installed-client flow. `reddit_mobile_owned` now mints a read-only short-lived token using browser TLS emulation and a stable device identity, then reads exact-scope `/new` listings from `oauth.reddit.com`. A live five-subreddit investing run returned 14 normalized posts with canonical identity, creation timestamps, current scores, and comment counts. `reddit_atom_scoped` independently returned eight current investing matches and remains the no-engagement discovery fallback.

Scheduled collection stores two immutable layers: normalized observations and private source records. Mobile JSON is archived canonically; Atom payloads are preserved byte-exact as base64. Each source record carries connector, source ID, actual per-request fetch time, payload format, and a verified SHA-256 digest. Raw records are stripped from API responses. No trend calculation is enabled.

Default runtime database:

```text
data/social_observations.db
```

Override with:

```text
BOUNTY_SOCIAL_DB
```

Runtime databases and WAL files are gitignored.

### Atomic scheduled collection

`social_scraper/collection.py`

- Due queries are atomically claimed with leases
- A second worker cannot collect an already claimed query
- Run persistence and schedule advancement occur in one transaction
- Failed queries release their lease
- One failed query does not prevent later due queries from running

### API endpoints

Search and collection routes are exposed in `apis/social_search_api.py`:

- `POST /social/search`
- `POST /social/search/cached`
- `POST /social/tiktok/search`
- `GET /social/reddit/feed`
- `GET /social/reddit/post`
- `GET /social/platforms`
- `GET /social/sources/health`
- `POST /social/queries`
- `GET /social/queries`
- `GET /social/queries/{id}`
- `POST /social/queries/{id}/collect`
- `POST /social/collect-due`
- `GET /social/history/{platform}/{post_id}`

Administrative collection routes require:

```text
BOUNTY_SOCIAL_ADMIN_TOKEN
X-Social-Admin-Token: <token>
```

Paid search routes fail closed with HTTP 503 unless x402 middleware initializes successfully. Application startup records payment activation in `BOUNTY_X402_ACTIVE`; merely setting `X402_PAY_TO` is not sufficient. `POST /social/search`, `POST /social/search/cached`, and both Reddit depth routes are in the x402 route table. The cached route serves only an exact query/platform/region match within the caller's maximum age and never silently launches a browser.

Reddit depth access requires a comma-separated subreddit allowlist in `BOUNTY_REDDIT_SUBREDDITS`. A hardened live feed probe returned three real `r/Python` posts with canonical IDs, scores and comment counts. Repeated exact-post probes later triggered Reddit's verification page; the connector detected this and returned a controlled worker error instead of reporting empty content as success. Camoufox therefore improves depth access but is not treated as infallible.

### Security and data-integrity behavior

- Search cannot be amplified by repeating a platform name
- Public connector errors do not expose raw exception strings
- Proxy URL credentials are removed from health output
- Payment and collection administration fail closed when unconfigured
- No interpolation of missing engagement fields
- Every returned item identifies the connector and fetch timestamp used

## What remains before production reliability

1. Run seven days of canaries against the owned mobile route and RSS fallback. Keep commercial access as emergency backup only. Do not resume continuous anonymous Camoufox feed retries.
2. Add YouTube Data API as primary with yt-dlp fallback.
3. Integrate two independent TikTok commercial upstreams behind the broker.
4. Build the session/account/proxy control plane for the owned TikTok fallback.
5. Add persistent production storage; Railway-local SQLite is not sufficient for multi-instance deployment.
6. Deploy `scripts/collect_social_due.py` on a recurring platform schedule. The one-shot runner is built and lease-safe; deployment is not configured yet.
7. Seed query registries and collect repeated snapshots.
8. Measure route success, field completeness, latency and cost for at least one operating period before publishing an SLA.
9. Build trend velocity only after sufficient repeated observations exist.

## Verification commands

```bash
"C:/Users/vncen/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe" -m pytest tests test_payment.py test_proxy_config.py -q
"C:/Users/vncen/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe" -m compileall -q social_scraper apis/social_search_api.py payment.py
```
