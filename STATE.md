# Bounty API — Active Work State

**Last updated:** 2026-07-19 21:20 SGT
**Status:** IN PROGRESS — Social scraper: 3/6 sources live (Reddit, YouTube, Instagram). TikTok blocked free, needs TikHub decision.

## What just shipped (this session)
- `social_scraper/instagram.py` — new IG connector via Playwright tags page (free, no auth). Extracts: top creators + follower counts + hashtag volume + captions. Login-wall limited to ~10 items.
- `social_scraper/probe_tiktok.py` + `probe_instagram.py` — honest probes proving what works free vs blocked
- `social_scraper/audit.py` — wired Instagram into the orchestrator
- **3 sources now return OK:** Reddit (PullPush), YouTube (yt-dlp), Instagram (Playwright). 30 items on test query "dopamine detox", 0 failures.

## What's blocked / needs decision
- **TikTok**: confirmed blocked without X-Bogus signature. Free OSS path dead (Evil0ctal demo API 404, TikTok serves error page to headless browsers). Only working path = **TikHub paid API** ($0.001/req, 50 free on signup, $2 referral bonus). Needs Vincent's call before signing up.
- **XHS / Douyin / RedNote**: same situation — needs TikHub or geo-located proxy. Defer to Phase 2.

## What's NOT built
- Signal classifier (behavior-shift / pain-point / purchase-intent tagging)
- Velocity tracking (first seen / count by day / cross-platform spread)
- Bounty API productization (`/social/trend-search`, `/social/behavior-shifts`, `/social/arb-signal`)

## Next up (priority order)
1. **TikTok decision** — TikHub paid API signup (Vincent approves), or ship v1 with 3 free sources
2. **Run real benchmark queries** — test 5-10 actual trend queries across the 3 working sources, see what's actually useful
3. **Wire as Bounty API endpoint** — `/social/trend-search` multi-source, price at $0.05/call
4. **Add signal classifier** — tag items by intent (pain/shift/intent)
5. **Velocity tracking** — daily snapshots to detect trend acceleration

## How to resume
1. Read this file
2. Run `python -m social_scraper.audit --query "your query" --limit 10 --no-github` to verify the 3 sources still work
3. Check `curl -s https://bountyapi.com/health` for prod status
4. Pick next item from "Next up"
