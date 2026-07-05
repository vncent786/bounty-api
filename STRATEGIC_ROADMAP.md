# Asia Data API Marketplace — Strategic Roadmap
## PM & Marketing Plan | Goal: $50K/month | Timeline: 24-36 months

---

## Executive Summary

**The business:** A marketplace where AI agents (and human developers) discover and pay for specialist Asian data APIs — property, corporate, financial, e-commerce. Powered by x402 micropayments for agents and traditional subscriptions for humans.

**The honest reality:** $50K/month from pure agent micropayments requires 10M+ calls/month. The entire x402 ecosystem does ~16M/month today. So we need a dual-engine strategy.

**The dual engine:**
1. **Agent channel (x402)** — passive, grows with AI adoption, zero support. Long-term compounding.
2. **Human/enterprise channel** — immediate revenue, proven market, higher value. What pays the bills until agents scale.

Build the data layer once. Sell it through two payment channels.

---

## Phase 0: Foundation (Now — Week 2)
**Goal:** Deploy the stamp duty API, make it discoverable, validate the first real call.

| Task | Details | Status |
|------|---------|--------|
| Deploy API to Fly.io | Free tier, public URL | Pending |
| Create llms.txt | Structured description for LLMs | Pending |
| Create llms-full.txt | Complete API documentation in markdown | Pending |
| Build MCP server | Expose APIs as MCP tools for Claude/Cursor | Pending |
| Register on Smithery.ai | First MCP directory listing | Pending |
| Register on Glama.ai | Second MCP directory | Pending |
| GitHub repo | Public repo for the MCP server + marketplace | Pending |

**Deliverable:** A live, discoverable API that any agent can find and call.

**Success metric:** First external API call (not from us) within 30 days of deployment.

---

## Phase 1: Catalog Depth (Week 2 — Month 2)
**Goal:** Build 5-7 APIs so the marketplace has enough surface area for agents to find something useful.

### API build priority (ranked by margin x demand x ease)

| # | API | Price | Source | Cost | Margin | Build time |
|---|-----|-------|--------|------|--------|-----------|
| 1 | **SG Stamp Duty (BSD+ABSD)** | $0.002 | Pure math | $0 | 100% | ✅ DONE |
| 2 | **SG Postal Code → District** | $0.001 | Static table | $0 | 100% | 2 hours |
| 3 | **HDB Resale Median by Town** | $0.002 | HDB open data (cached) | $0 | 99% | 4 hours |
| 4 | **SG Property Price Index** | $0.003 | URA quarterly (cached) | $0 | 99% | 4 hours |
| 5 | **SGX Company Basics** | $0.003 | SGX website (cached) | $0 | 99% | 6 hours |
| 6 | **SG Rental Yield Calculator** | $0.002 | Pure math | $0 | 100% | 3 hours |
| 7 | **SG Property Transaction Lookup** | $0.005 | URA REALIS (cached) | $0 | 99% | 8 hours |

All 7 are free data sources, zero anti-bot, low maintenance. Total build time: ~30 hours of my time.

### Parallel: Build the marketplace website

A simple, fast, LLM-optimized site:
- Single page listing all APIs with pricing
- `/llms.txt` and `/llms-full.txt` for AI discovery
- `/mcp` endpoint for MCP server connection
- JSON-LD structured data on every API page
- Minimal JS, server-rendered, fast load (agents don't need fancy UI)

**Design principle:** The "user" is an AI agent reading markdown, not a human admiring gradients. Optimize for machines first, humans second.

---

## Phase 2: Distribution & Discovery (Month 2 — Month 4)
**Goal:** Make sure every AI agent and LLM in the world knows we exist.

### Agent discovery channels (AI SEO)

| Channel | Action | Impact |
|---------|--------|--------|
| **Smithery.ai** | List MCP server with all tool manifests | High — primary MCP directory |
| **Glama.ai** | List MCP server | High — second MCP directory |
| **OpenTools.ai** | List MCP server | Medium |
| **llms.txt** | Deploy on marketplace site | Critical — LLMs read this first |
| **llms-full.txt** | Complete crawlable documentation | Critical |
| **GitHub** | Open-source MCP server, well-documented README | High — training data + credibility |
| **npm package** | `npx asia-data-mcp` for one-line install | High — matches PayAPI's distribution model |

### Human/developer discovery channels (Traditional SEO + community)

| Channel | Action | Impact |
|---------|--------|--------|
| **RapidAPI listing** | List 2-3 APIs on RapidAPI (human subscription channel) | Medium — proven developer audience |
| **Reddit** | Educational posts in r/SideProject, r/webscraping, r/SingaporeFinance | Medium — training data + awareness |
| **Hacker News** | "Show HN: Asia data marketplace for AI agents" | High if it lands |
| **Dev.to / Medium** | Technical articles on x402 + Asia data gaps | Medium — SEO + training data |
| **SG developer communities** | Telegram/WhatsApp groups for SG fintech devs | Low-medium |

### Content marketing (the long game)

Write articles that answer the questions agents and developers actually ask:
- "How to calculate Singapore stamp duty programmatically"
- "Singapore property data API — what's available"
- "x402 protocol explained for Asian markets"
- "Building AI agents for Asian financial data"
- "Singapore property investment analysis with AI"

These become both SEO content AND training data for future LLMs.

---

## Phase 3: Revenue Activation (Month 3 — Month 6)
**Goal:** First dollars earned. Dual channel: agent micropayments + human subscriptions.

### Channel A: Agent micropayments (x402)

Add x402 payment middleware to all API endpoints. Pricing:
- Calculators (stamp duty, rental yield): $0.002/call
- Cached data (property index, HDB medians): $0.003/call
- Transaction lookups: $0.005/call
- Company data: $0.003/call

**Revenue target Month 6:** $200-1,000/month from agent calls.

### Channel B: RapidAPI / human subscriptions

List the same APIs on RapidAPI with subscription pricing:
- Free tier: 100 calls/month (real data, not mock)
- Developer: $19/month (5,000 calls)
- Pro: $49/month (25,000 calls)
- Business: $199/month (100,000 calls + priority support)

**Revenue target Month 6:** $500-2,000/month from human subscriptions.

### Channel C: Enterprise (the real money)

Direct outreach to companies that need Asian data:
- **PropertyGuru / 99.co** (SG property portals) — property data APIs
- **Fintech startups** (StashAway, Endowus, Syfe) — financial data
- **Real estate agencies** (ERA, PropNex, OrangeTee) — stamp duty + transaction data for client tools
- **Investment research firms** — SGX data, corporate registry

Enterprise pricing: $500-5,000/month for API access with SLA.

**Revenue target Month 6:** $0 (long sales cycle, but pipeline building starts now).

---

## Phase 4: Marketplace Open (Month 4 — Month 8)
**Goal:** Open the platform to third-party providers. Start the network effect.

### Provider recruitment

Target developers who already have Asian data but no monetization channel:

| Provider type | Example | What they have | Why they'd join |
|--------------|---------|----------------|----------------|
| **Open-source scraper devs** | GitHub repos for HDB, SGX, URA | Working data pipelines | Zero-effort monetization |
| **Indonesian devs** | Tokopedia scrapers, ID gov data | Indonesia-specific data | No monetization channel exists |
| **HK/TW developers** | HK Land Registry, TW property | Regional data | Regional demand from agents |
| **Fintech developers** | Stock screeners, financial APIs | Market data | x402 is cheaper than Stripe for micropayments |

### Marketplace features needed

- Provider onboarding flow (register wallet, list API, set pricing)
- MCP server auto-generation (every listed API becomes an MCP tool)
- Revenue dashboard (USDC earnings, call volume, uptime)
- Provider verification (basic vetting to prevent garbage APIs)

### Revenue model

| Source | Rate |
|--------|------|
| Own APIs | 97% of per-call revenue |
| Third-party provider APIs | 3% platform fee on all calls |
| Featured listing | $49/month per provider (priority placement) |
| Enterprise contracts | 100% (direct sales, marketplace fee doesn't apply) |

---

## Phase 5: Scale (Month 8 — Month 18)
**Goal:** $5K-15K/month. Agent adoption growing, marketplace compounding.

### Expand API coverage

| Category | APIs to add | Target price |
|----------|-------------|-------------|
| **Hong Kong** | Property transactions, HKEX data, corporate registry | $0.005-0.01 |
| **Japan** | Real Estate Information Network (REIN), company data | $0.005-0.01 |
| **Malaysia** | SSM corporate registry, property data | $0.003-0.005 |
| **Thailand** | Corporate registry, property data | $0.003-0.005 |
| **Indonesia** | Tokopedia data, AHU corporate registry | $0.003-0.005 |
| **Cross-border** | SEA property comparison, multi-country KYC | $0.01-0.05 |

### Expand into higher-value APIs

The $0.001-0.005 calculators are table stakes. Real revenue comes from:
- **Property valuation estimates** (comparable sales + ML model): $0.05-0.20/call
- **Investment analysis** (rental yield + capital growth + tax): $0.10/call
- **KYC verification** (multi-country corporate registry): $0.05-0.10/call
- **Market intelligence reports** (aggregated, analyzed): $0.50-2.00/call

At $0.10 avg/call, $50K/month = 500,000 calls/month. Much more achievable than 10M calls at $0.005.

---

## Phase 6: $50K/month (Month 18 — Month 36)
**Goal:** The milestone. Multiple revenue engines compounding.

### What $50K/month looks like (realistic mix)

| Revenue source | Monthly | How |
|----------------|---------|-----|
| Agent micropayments (own APIs) | $15K | 5M calls at $0.003 avg |
| Agent micropayments (marketplace) | $2K | 3% of 15M calls across 30 providers |
| RapidAPI subscriptions | $8K | 200 subscribers at $49 avg |
| Enterprise contracts | $20K | 8-10 clients at $2K avg |
| Featured listings | $1K | 20 providers at $49/month |
| Premium API analytics | $4K | 50 subscribers at $80/month |
| **TOTAL** | **$50K** | |

### What needs to be true for this to happen

1. **x402 becomes a real standard** (Coinbase, Visa, Stripe pushing it — good signs)
2. **AI agent adoption grows** (AgentKit, MCP adoption, autonomous agents become common)
3. **We're the dominant Asia data marketplace** (first-mover + geographic moat)
4. **Enterprise sales pipeline matures** (this is where guaranteed revenue lives)
5. **Provider network compounds** (30+ providers = gravity that's hard to leave)

---

## Immediate Next Steps (This Week)

I recommend executing these in order. Each one is a prerequisite for the next.

### Step 1: Deploy the stamp duty API (1 day)
Get it live on a public URL. Can't test agent discovery if the API isn't reachable.

### Step 2: Build the marketplace landing page + llms.txt (2 days)
Single page, all APIs listed, llms.txt deployed, MCP config published. This is the discovery foundation.

### Step 3: Build 3 more APIs (3 days)
- SG Postal Code → District (2 hours — trivial)
- SG Rental Yield Calculator (3 hours — pure math)
- HDB Resale Median by Town (4 hours — cached open data)

This gives us 4 APIs — enough catalog depth for agents to find multiple useful tools.

### Step 4: Build the MCP server (2 days)
Expose all APIs as MCP tools. One config entry connects agents to all our APIs.

### Step 5: Register on MCP directories (1 day)
Smithery, Glama, OpenTools. This is the "App Store" launch.

### Step 6: GitHub repo + npm package (1 day)
Public repo, documented README, npm package for one-line MCP install. Training data for LLMs + credibility.

### Step 7: List on RapidAPI (1 day)
Same APIs, human subscription channel. This is where immediate revenue lives.

**Total: ~10 working days. After this, we have a live, discoverable, multi-channel API marketplace with 4 APIs.**

---

## The bet we're making

This is a **platform bet on the agent economy**. If x402 and autonomous agents become mainstream (which Coinbase, Visa, Stripe, AWS, and Google are all betting on), being the first Asia-specific data marketplace puts us in a position to capture a disproportionate share of agent data spend.

If the agent economy DOESN'T materialize, we still have:
- APIs generating revenue on RapidAPI (human channel)
- Enterprise API contracts (direct sales)
- Technical infrastructure and data pipelines with real value

The downside is protected because the data layer has value regardless of which payment channel succeeds.
