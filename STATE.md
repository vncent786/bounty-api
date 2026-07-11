# Bounty API — Active Work State

**Last updated:** 2026-07-10 15:00 SGT
**Status:** IDLE — URA private property data is LIVE. 31 APIs, 27 MCP tools, npm@1.8.0. URA moat unlocked.

## What just shipped
- `POST /property/pitch` — live on bountyapi.com, $0.05/call
- `bountyapi-mcp@1.4.0` on npm — 12 MCP tools
- HDB town-specific fetch (accurate medians per town)
- Docs updated (llms.txt, llms-full.txt)

## In progress
Nothing blocked right now. URA AccessKey registration is pending Vincent.

## Next up (priority order)
1. **URA private property transactions** — BLOCKED on Vincent registering for URA API AccessKey at https://www.ura.gov.sg/maps/ (free). Unlocks condo/private property price fairness, comps, and pitch analysis.
2. **Property comparables endpoint** — "find 5 similar units that transacted recently" (needs URA data)
3. **Condo/project database** — facilities (pool, tennis, gym), tenure, TOP year, unit count
4. **Market trend data** — price movements by district/town over 1/3/5 years
5. **Buy-vs-rent analysis** — break-even horizon
6. **Investments/trading vertical** — options strategy analyzer, earnings impact, portfolio risk

## How to resume
If you're a cron job or fresh session picking this up:
1. Read this file to understand current state
2. Check `git log --oneline -5` in `C:\Users\vncen\saas\asia-data-api` for latest commits
3. Check `curl -s https://bountyapi.com/health` to confirm production is up
4. Pick the next item from "Next up" that isn't blocked
5. Update this file when you start and when you finish
