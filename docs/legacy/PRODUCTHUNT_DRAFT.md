# Product Hunt submission draft — Bounty API

Submit at: https://www.producthunt.com/posts/new (requires PH account; ideally post on a Tue–Thu for traffic).
Do NOT auto-submit. Review, then post manually and schedule a launch.

---

## Name
Bounty API

## Tagline (60 chars max)
Singapore property data APIs that pay-per-call in USDC — for AI agents

(Note: 69 chars — trim to:)
Pay-per-call data APIs for AI agents — company intelligence, news, jobs, app reviews, and Singapore property. Pay per call in USDC

(Final 58-char option:)
Singapore data APIs for AI agents — pay-per-call in USDC

## URL
https://bountyapi.com

## Topics / Category
Developer Tools, Artificial Intelligence, Fintech, Real Estate

## Description

Bounty API gives AI agents verified Singapore property, tax, affordability, and location data through clean REST endpoints and a 12-tool MCP server. No API keys. No subscriptions. Free utility tools (stamp duty, postal/MRT lookup, calculators) and paid data tools billed per call in USDC on Base via the x402 protocol — the payment itself is the auth.

**Why it's different**
- Built agent-native: discover via MCP, consume via REST, settle via x402. Zero human signup.
- 8 free + 6 paid endpoints. Singapore is live now; the API shape is region-parameterized for HK/UAE/AU/JP.
- Every response carries source provenance (IRAS, URA, data.gov.sg, MAS, LTA) — no fabricated or interpolated data.
- A single provider today (not a marketplace), with continuous endpoint-quality monitoring on the roadmap.

**Connect in seconds**
Remote MCP (no install):
```json
{ "mcpServers": { "bountyapi": { "url": "https://bountyapi.com/mcp" } } }
```
Or local: `npx bountyapi-mcp`

**Maker comment / first comment to post**
Hi PH! 👋 I built Bounty API because most data APIs are shaped for humans — sign up, get a key, pick a plan. That friction breaks agentic workflows. I wanted data an autonomous agent could discover, price, and consume end-to-end with no human step. x402 (stablecoin per-call payments on Base) lets payment replace auth entirely. Singapore property is the first vertical; the region parameter means the same tools extend to HK/UAE/AU/JP later. I'm around all day — ask me anything about the x402 flow, MCP, or the data model.

## Gallery assets needed (prepare before launch)
- Logo (256×256, transparent PNG)
- First gallery image (1270×760): "12 MCP tools · 8 free · 6 paid · pay-per-call in USDC"
- 2–3 product screenshots: Claude Desktop calling sg_stamp_duty; the 402→pay→data flow; property pitch output
- Optional 30s video / GIF of an agent autonomously paying for HDB data

## Makers / Team
Add yourself (Vincent). If you have a hunter with followers, consider asking them to hunt it.

## Links to add
- GitHub: https://github.com/vncent786/bounty-api
- npm: https://www.npmjs.com/package/bountyapi-mcp
- Docs: https://bountyapi.com/docs
- Real x402 tx (social proof): https://basescan.org/tx/0xc42354ea66478958099236920b293629eb1711d235b5bacad6f90d9b82beb6c5
