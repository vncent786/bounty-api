# AGENTS.md — Bounty Operating Manual

Read this before touching anything. `STATE.md` has product philosophy and roadmap; this file has operational reality. When they conflict on operations, this file wins.

## What this is

Bounty is a research system that turns online conversations into cited findings. Five-platform collection (YouTube, Reddit, TikTok, Instagram, X), Google Trends discovery for unknown-unknowns, LLM extraction of signals with citations. Users: investors, marketers, product teams. First workflow is investing; horizontality is architecture, not branding — never hardcode an investability filter.

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
# Tests (189 passing as of 2026-08-11) — run before every commit
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

- **X connector (scweet):** returns 0 results. Not investigated yet.
- **x402 payment routes:** 503 by design, `BOUNTY_X402_ACTIVE` unset. Deferred.
- **YouTube transcripts:** only metadata + comments collected, not spoken word. Biggest content gap per marketer feedback.
- **Zone path gaps:** see "TWO pipelines" above. Reddit ~dead from zones, no thread depth, no dedup, no triage findings.
- **Google interest_over_time rate limits (429):** trendspy `trending_now` is reliable; `interest_over_time` gets rate-limited under load. `/discover/trend-detail` degrades gracefully — chart shows error, related queries still load.
- **Usage receipts:** FK on `discovery_stage_usage` references discovery_runs, not research_runs. Research-run usage returns in response body but doesn't persist to the stage-usage table.
- **LLM in production:** local dev uses a temporary adapter. Production needs a real credential decision.

## TackSense marketer audit (2026-08-11) — verified verdicts

Independent agent tested the nofap zone. Verdicts after code inspection:
1. "Reddit nearly dead from zones" — TRUE (root cause: zone path passes no subreddit scope)
2. "No comment depth" — PARTIAL (Reddit/YouTube implement fetch_thread correctly and research-runs uses it; TikTok/IG/X don't implement it; zones never call it)
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
