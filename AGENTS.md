# AGENTS.md — Bounty Operating Manual

Read this before touching anything. `STATE.md` has product philosophy and roadmap; this file has operational reality. When they conflict on operations, this file wins.

## Identity guard (read before anything else)

This repository is **Bounty**: a research system that turns online conversations into cited findings. Dashboard at `/dashboard`, deployed at bountyapi.com.

It is **NOT**:
- the x402/USDC agent-payments data API marketplace (code exists, deferred)
- Singapore property/real-estate tooling (legacy, deferred)
- an MCP directory product (legacy, deferred)

Every marketing/strategy doc in `docs/legacy/` describes that older direction and is kept for history only. If a document tells you to build payment flows, Singapore data endpoints, or MCP submissions — it is wrong about current priorities. When unsure what to build, `STATE.md` "Current priority order" is the only source of truth.

## Credentials & auth — do not ask the owner for these until you've checked here

Nothing below requires you to request OAuth from anyone. Auth state already exists; know where it lives before assuming something is broken:

| Connector | Auth mechanism | Where it lives |
|---|---|---|
| Reddit (mobile OAuth) | Device-ID token mimicking Reddit's Android app. **No user OAuth, no developer API keys.** | Self-acquired at runtime; optional `BOUNTY_REDDIT_DEVICE_STATE` for persistence. Works without proxy; `BOUNTY_PROXY_USERNAME/PASSWORD/SERVER` if needed |
| YouTube | Free (yt-dlp based). No auth. | — |
| TikTok | Owned authenticated Chrome worker | Persistent `.browser_profiles/tiktok_real`, proxy extension, sticky `BOUNTY_PROXY_*` residential egress. Search and bounded root/reply collection work locally; serialize profile use and do not run from Railway IPs |
| Instagram | Owned web-auth worker | Stored browser cookies at `BOUNTY_IG_COOKIE_PATH` or `data/ig_cookies.json`, direct session-origin IP. Keyword GraphQL search plus bounded root/child comments; `BOUNTY_IG_PROXY` only when it matches the session origin |
| X / Twitter | Owned authenticated web GraphQL primary; official API optional | Owned worker: `BOUNTY_X_AUTH_TOKEN`, `BOUNTY_X_SCWEET_DB`, conservative `BOUNTY_X_DAILY_*` budgets. Optional official fallback: `BOUNTY_X_BEARER_TOKEN` and `BOUNTY_X_ENABLE_FULL_ARCHIVE=1` |
| LLM | xAI Responses API or explicitly configured OpenAI-compatible endpoint | Production Grok: `BOUNTY_LLM_PROVIDER=xai`, `XAI_API_KEY`, optional `XAI_BASE_URL/XAI_MODEL`. Generic adapter: `BOUNTY_LLM_BASE_URL/API_KEY/MODEL`. There is no Z.AI fallback. Local dev can route through a Hermes adapter via `BOUNTY_HERMES_AGENT_PATH` |
| Brave (optional fallback) | API key | `BOUNTY_BRAVE_SEARCH_API_KEY` — optional, unset is fine |

Secrets live in `.env` locally (gitignored — **never commit, never paste contents anywhere**) and in the Railway project's env settings. If an auth flow breaks, first check `.env` exists and Railway vars are set, then check the connector's health endpoint — do not default to "ask owner for OAuth keys." The Reddit developer API specifically **does not work** and is not needed; the mobile OAuth connector replaced it.

## Filesystem map

- Canonical repo: `D:\vncen\saas\bounty-api-fresh` (Windows). All paths in scripts/docs use this.
- `monitoring.db`, `data/monitoring.db` — zone/monitor state (SQLite)
- `data/social_observations.db` — canonical observation store (`BOUNTY_SOCIAL_DB` overrides)
- `scweet_state.db` — X connector session state
- `tmp/` — throwaway scripts and one-off artifacts. Safe to ignore; never import from here
- `artifacts/` — QA screenshots and audit outputs
- `logs/` — run logs
- `.hermes/plans/` — in-repo planning docs (tracked in git, visible to all agents)

## What this is

Bounty's public Investor Radar is withdrawn while its signal methodology is rebuilt. `/dashboard` serves stable Classic Bounty; `/dashboard/investing-preview` is the internal private Radar. The private Radar has an owned-worker scan that persists progress and shows only candidates passing deterministic specificity, behavior, comparable-history anomaly, breadth, citation, parity, and investigability gates. Its authenticated Research view now turns an exact persisted Radar candidate into a saved, hash-verified company dossier: human-confirmed company/ticker, free GLEIF/OpenFIGI resolution, automatic SEC filing and CompanyFacts research for SEC registrants, candidate-specific filing passages, optional transcript sources, sampled public-news checks, and explicit low/base/high analyst assumptions. Common stock is valid and options are optional. Non-US filings without SEC coverage use user-supplied primary URLs and remain explicit source candidates rather than auto-verified numeric facts. `No qualified leads` and `materiality not estimable` are valid results. Public Social Pulse scheduling stays off. Source notes: `references/camillo-information-arbitrage-methodology-2026-08.md`; dossier contract: `.hermes/plans/2026-08-29_170000-generic-candidate-dossier-release.md`.

## Canonical methodology: Buzzabout, not a Google Trends dashboard

Primary product-method reference: [Buzzabout, “Google Trends Alternatives: They All Count the Wrong Thing”](https://buzzabout.ai/blog/google-trends-alternatives). Vincent has provided this repeatedly. Treat it as the design anchor, not background reading.

Its central thesis: **search volume is the receipt for a trend; the trend starts as a conversation.** Bounty adopts the bounded/standing-read method but does not clone Buzzabout's product hierarchy. Bounty has two co-primary user workflows:

1. **Explore / Google Trends discovery:** a broad unknown-unknown radar, particularly important for investing. It surfaces topics the user did not know to place inside a zone. Trends metadata alone is never a finding; every promising candidate must gain plain-language context and pass through conversation and durability checks.
2. **Projects / bounded and standing reads:** deep research and recurring monitoring of a known niche, useful across investing, marketing and product research. Cairn marketing, for example, should reveal pain points, desired outcomes, workarounds, objections, competitor mentions and verbatim audience language.

Both workflows converge into the same horizontal conversation corpus and research engine. Investing, marketing and product lenses change interpretation and actions, not collection or the underlying evidence.

The intended method:

1. **Declare a bounded zone:** four or five seed keywords, narrow enough to read completely and broad enough to expose a trend crossing terms.
2. **Bounded read:** once, read a historical window across every available network at the same time, including comment threads and replies. Cluster the corpus to learn the niche, its questions, dissent, beliefs, products and competitive map.
3. **Candidate feed:** use Google/search trends cheaply to surface unfamiliar names, then run every candidate through a social-conversation gate.
4. **Durability check:** read actual posts and threads. News, sport, celebrity and short hype can resemble durable topics in a growth score but not in conversation texture.
5. **Standing read:** rerun the same bounded zone weekly, because breakout windows are short. Detect four events: rising negative sentiment; brand/competitor mentions; shifts in what the market believes; trends starting and ending.
6. **Downstream validation:** use Google Trends to confirm a suspicion in a legible unit after it was formed upstream from conversation.

For investing, add an optional lens after the horizontal read: map observed behavior to verified company exposure, economic materiality, information parity and price context. Do not distort the underlying collection around tickers.

Product implication: Explore and Projects/Zones are co-primary entry points. Explore is a broad discovery funnel; Projects/Zones provide bounded depth and standing-read continuity. The same research-runs pipeline should execute candidate investigation, initial bounded reads and subsequent standing reads. No raw Google Trends label should be shown without enough context for a user to understand what it is, why it is moving, whether the underlying conversation looks durable and what the active lens can do with it.

## Critical architecture fact: there are TWO pipelines

This is the single most important thing to know. The same codebase has two collection paths with different capabilities:

```
ZONE PATH (older, shallower)          RESEARCH-RUNS PATH (newer, richer)
monitor.py: TrendMonitor              discovery/handlers.py: StagedRunner
  broker.search(keyword, platforms)     reddit_discover.py → auto-discovers subreddits
  → store → lexical cluster             → broker.search WITH subreddit scope
  → EnrichmentEngine (shallow)          → deep_read → fetch_thread (comments/replies)
  → snapshot diff → alerts              → triage.analyze_conversation (LLM findings)
                                        → save_findings (research_findings table)
```

Consequences of the gap (verified 2026-08-11):
- Zones pass NO subreddit options to the broker, so `requires_options` connectors (reddit_mobile_owned, reddit_atom_scoped, camoufox_depth) are all SKIPPED. Zones fall back to pullpush.io, which is usually dead → Reddit yield ~0 from zones.
- Zones never call `fetch_thread`, so no comment/reply depth regardless of platform.
- Zones have no item-level dedup; 5 keywords per zone → same post collected 2-3×.
- Zones use EnrichmentEngine, NOT triage.analyze_conversation. No structured findings from zones.

**PENDING DECISION (owner: Vincent, undecided as of 2026-08-11):** upgrade zones to use the full research-runs pipeline, or deprecate zone collection and route everything through research-runs. Do not start this without his call.

The Explore/dashboard flow (what users actually see) goes through research-runs and works.

## Repo map

| Path | What |
|---|---|
| `app.py` | FastAPI app, mounts everything |
| `apis/dashboard_api.py` | Dashboard JSON API: `/discover`, `/discover/trend-detail`, `/discovery/research-runs`, zones CRUD |
| `apis/dashboard_page.py` | HTML shell served at `/dashboard` |
| `apis/social_search_api.py` | `build_default_broker()` / `build_collection_broker()` — connector registration |
| `public/dashboard.js` | All frontend logic (vanilla JS, single IIFE) |
| `public/dashboard.css` | Styles, responsive at 900px/560px |
| `social_scraper/broker.py` | SourceBroker: search across connectors, fetch_thread |
| `social_scraper/connectors/` | youtube, reddit (+reddit_mobile, reddit_rss, reddit_camoufox, reddit_arctic), tiktok, instagram, x |
| `social_scraper/discovery/handlers.py` | StagedRunner handlers: root_probe, deep_read, horizontal_extraction, optional_enrichment |
| `social_scraper/discovery/reddit_discover.py` | Subreddit auto-discovery via Reddit mobile OAuth `/subreddits/search` |
| `social_scraper/discovery/triage.py` | `analyze_conversation()` — LLM signal extraction with citations |
| `social_scraper/discovery/storage.py` | DiscoveryStore: discovery_runs, research_runs, research_findings |
| `social_scraper/monitoring/topdown.py` | Google Trends discovery (trendspy), conversation gate, EmergingKeyword |
| `social_scraper/monitoring/monitor.py` | TrendMonitor — the zone runner |
| `social_scraper/monitoring/zones.py` | Zone model/registry (no subreddit field — see gap above) |
| `social_scraper/llm_client.py` | All LLM calls centralized here |
| `STATE.md` | Product philosophy, roadmap, priorities |
| `tmp/` | Throwaway scripts. Never import from here. |

## Commands

```bash
# Tests (508 passing as of 2026-08-16) — run before every commit
python -m pytest tests/ -x -q

# Local server (dashboard at localhost:8000/dashboard)
# BOUNTY_ENV=development bypasses token gating locally
python -m uvicorn app:app --port 8000
```

Python is Windows-native. Use `python`, not `python3`. Tests take ~25s.

## Deployment

GitHub `vncent786/bounty-api` branch `main` → Railway auto-deploy → `bountyapi.com`. No CLI deploys; `git push origin main` is the whole flow. Rebuild takes 1-2 min. Verify after push by loading `bountyapi.com/dashboard`.

Dashboard auth fails CLOSED (503) in production unless `BOUNTY_DASHBOARD_TOKEN` is set in Railway env. Client enters it via "Set API token" in the sidebar; it lives in sessionStorage.

## Environment variables (names only — values live in Railway env / local .env, NEVER in the repo)

| Var | Purpose |
|---|---|
| `BOUNTY_DASHBOARD_TOKEN` | Bearer token gating the dashboard API (production) |
| `BOUNTY_X_BEARER_TOKEN` | Official X API bearer token for Recent/Full-Archive Search |
| `BOUNTY_X_ENABLE_FULL_ARCHIVE` | Set to `1` only when the X project has paid full-archive access |
| `BOUNTY_OWNED_SOCIAL_WORKER` | Set `1` only on the residential collection worker; production API/Railway defaults to no browser/session connectors |
| `BOUNTY_PRIVATE_RADAR_DB` | Private Radar SQLite path; local default uses the Discovery database unless explicitly isolated |
| `BOUNTY_X_AUTH_TOKEN` | Owned X account auth cookie used by Scweet web GraphQL |
| `BOUNTY_X_SCWEET_DB` | Owned X account/cooldown state path; use an ignored runtime database |
| `BOUNTY_X_DAILY_REQUEST_LIMIT` / `BOUNTY_X_DAILY_TWEETS_LIMIT` | Hard per-account budgets; defaults 100 pages and 3,000 posts/day |
| `XAI_API_KEY` | Paid xAI API key used when `BOUNTY_LLM_PROVIDER=xai` |
| `XAI_BASE_URL` / `XAI_MODEL` | Optional xAI endpoint/model overrides; defaults are `https://api.x.ai/v1` and `grok-4.6` |
| `BOUNTY_ENV` | `development` bypasses token gating locally |
| `BOUNTY_PROXY_USERNAME` / `BOUNTY_PROXY_PASSWORD` | Proxy creds for Reddit connectors. NOTE: it's `BOUNTY_PROXY_*`, not `BOUNTY_REDDIT_PROXY_*` |
| `BOUNTY_REDDIT_SUBREDDITS` | Optional default subreddit scope for arctic connector |
| `BOUNTY_BRAVE_SEARCH_API_KEY` | Optional Brave fallback for Reddit discovery |
| `BOUNTY_SOCIAL_DB` | Override path for observation store |
| `BOUNTY_X402_ACTIVE` | Paid x402 routes gate — unset/503 by design, don't "fix" |

## Reddit: how it actually works

The developer OAuth API does NOT work (Reddit locked free tier). PullPush.io is dead (502s). The working technique is `reddit_mobile_owned`: device-ID-based OAuth that mimics Reddit's Android app (same technique Redlib uses). It REQUIRES subreddit scope — it can't do global keyword search. `reddit_discover.py` bridges that: for any keyword, it calls `/subreddits/search` via mobile OAuth, gets relevant subs, then searches within them. Cached 1hr/keyword. Works with or without proxy. Tested live: "nofap" → nofapchristians, muslimnofap, nofap1week; "budgeting apps" → personalfinance, budget, moneyapp.

## Hard rules (violating these ruins the product)

1. **Never fabricate or interpolate data.** Missing = missing. A chart with holes beats a smoothed line. "Insufficient evidence" is a valid finding, not a failure.
2. **Every LLM claim needs a citation** back to a stored post with URL. triage.analyze_conversation enforces this — keep it that way.
3. **No sample/demo data in production paths.** No fake findings, no seeded trends.
4. **Explore never auto-runs on page load.** Collection costs real money and time. Explicit user action only.
5. **Preserve provenance:** post URL, platform, timestamp, collection route. Partial results stay partial and are disclosed.
6. **Plain English in the UI.** Users said "what the hell does 'Plan created with 4 candidate(s)' mean" — that class of jargon was deliberately exterminated (commit d8d3a9c). Don't reintroduce it. "Volume 500" → "500 searches". "Persisted findings" → "results".
7. **Tests green before push.** Railway auto-deploys from main — a red build goes straight to production.
8. **Don't touch x402/MCP/legacy API code.** Deferred by design (see STATE.md).

## Known broken / missing (honest list)

- **Owned X worker:** web GraphQL keyword/date search, engagement, raw payloads, and `conversation_id` reply reconstruction are live-verified. It remains ranked/bounded rather than a guaranteed firehose; session refresh, second-account failover, multi-day canaries, and the worker-to-Railway ingestion handoff are not complete.
- **X official API:** retained as an optional paid fallback/audit adapter; `BOUNTY_X_BEARER_TOKEN` is not provisioned locally.
- **Owned TikTok/Instagram worker:** live search, comments, and replies work on the Windows residential host. The signed ingestion handoff, automatic/manual re-auth alerting, second-worker failover, and seven-day canary are not built yet; do not imply an external SLA.
- **x402 payment routes:** 503 by design, `BOUNTY_X402_ACTIVE` unset. Deferred.
- **YouTube transcripts:** only metadata + comments collected, not spoken word. Biggest content gap per marketer feedback.
- **Zone path gaps:** see "TWO pipelines" above. Reddit ~dead from zones, no thread depth, no dedup, no triage findings.
- **Google interest_over_time rate limits (429):** trendspy `trending_now` is reliable; `interest_over_time` gets rate-limited under load. `/discover/trend-detail` degrades gracefully — chart shows error, related queries still load.
- **Usage receipts:** FK on `discovery_stage_usage` references discovery_runs, not research_runs. Research-run usage returns in response body but doesn't persist to the stage-usage table.
- **LLM in production:** the code supports paid xAI/Grok via `BOUNTY_LLM_PROVIDER=xai`, but `XAI_API_KEY` is not provisioned locally. Z.AI is not a fallback.

## TackSense marketer audit (2026-08-11) — verified verdicts

Independent agent tested the nofap zone. Verdicts after code inspection:
1. "Reddit nearly dead from zones" — TRUE (root cause: zone path passes no subreddit scope)
2. "No comment depth" — PARTIAL at audit time. Reddit/YouTube already worked; owned TikTok/Instagram and official X depth were added later. Zones still never call `fetch_thread`.
3. "Duplicates" — TRUE (no dedup in zone path)
4. "No structured analysis" — PARTIAL (research-runs path HAS it; zones don't)

Their P0-P2 ranking is sound IF zones remain a user-facing flow. If zones are deprecated for research, most of it evaporates.

## Recent history (context for "why is X like this")

- `0377119`→`0dd5b33`: backend foundation — budgets, lenses, scheduler, evidence cache, projects
- `8857b5f`: editorial research desk UI
- `e55c10a`: research-run execution + findings persistence (the Execute path)
- `ed842c5`: Reddit mobile OAuth + subreddit auto-discovery (fixed Reddit for research-runs)
- `d8d3a9c`: direct topic research bar + full jargon purge (after user feedback screenshots)
- `d827fce`: trend enrichment — sparkline, related queries, category filter (`/discover/trend-detail`)
- `9c04c93`: mobile responsive fixes

## Before you finish any session

1. `python -m pytest tests/ -x -q` → green
2. Commit with a descriptive message
3. `git push origin main` → Railway deploys
4. Verify `bountyapi.com/dashboard` loads and the feature works
5. Update this file if you learned something the next agent needs
