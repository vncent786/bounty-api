# Bounty

**A research system that turns online conversations into cited findings.** Bounty collects posts, comments, and replies from YouTube, Reddit, TikTok, Instagram, and X; discovers rising topics via Google Trends; and uses LLM analysis to extract signals — pain points, adoption patterns, objections, belief shifts — each backed by quotes and source links. If evidence is thin, it says so.

Users: investors (unknown-unknown discovery, pain-point research around companies), marketers (creative angles, competitor mentions), product teams (feature gaps, user complaints). The engine is horizontal; investing is the first lens, not a hardwired filter.

Live at [bountyapi.com/dashboard](https://bountyapi.com/dashboard) (token-gated).

## Read this first

1. **`AGENTS.md`** — operating manual: architecture, the two-pipeline warning, commands, deploy flow, credentials map, hard rules, known-broken list. **Any agent (or human) working in this repo must read this before making changes.**
2. **`STATE.md`** — product philosophy, what's built, gaps, priority order.

## Quickstart

```bash
python -m pytest tests/ -x -q          # 189+ tests, must be green before every push
python -m uvicorn app:app --port 8000  # local dev; BOUNTY_ENV=development bypasses token gate
# open http://localhost:8000/dashboard
```

Deploy: push to `main` → Railway auto-builds → bountyapi.com. Nothing else.

## What this repo is NOT

- **Not the x402/USDC data-API marketplace** — that code exists but is deferred (see `docs/legacy/`)
- **Not Singapore property/real-estate tooling** — legacy, deferred
- **Not an MCP directory play** — legacy, deferred

Old strategy and marketing documents live in [`docs/legacy/`](docs/legacy/) and describe that earlier direction. They are kept for history only.

## Layout

| Path | What |
|---|---|
| `apis/` | FastAPI routers (dashboard API, dashboard page, social search) |
| `public/` | Dashboard frontend (vanilla JS/CSS) |
| `social_scraper/` | Connectors, broker, discovery pipeline, monitoring, storage, LLM client |
| `tests/` | Full suite |
| `docs/legacy/` | Superseded strategy docs — do not implement from these |
