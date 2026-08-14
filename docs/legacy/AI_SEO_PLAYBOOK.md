# AI SEO Playbook for the Asia Data API Marketplace

## The core difference

**Traditional SEO:** Optimize for Google's crawler → rank on search results → human clicks your link.

**AI SEO:** Optimize for LLMs (ChatGPT, Claude, Gemini, Perplexity) → when a user asks "what's the best API for SG property data?" the LLM recommends your marketplace, or an autonomous agent calls your API directly.

Traditional SEO: You want humans to visit your website.
AI SEO: You want AI agents to USE your service without a human ever visiting your website.

---

## The 5 layers of AI SEO

### Layer 1: llms.txt (CRITICAL — do this first)

**What it is:** A markdown file at `yoursite.com/llms.txt` that LLMs read at inference time. Proposed by Jeremy Howard (llmstxt.org, Sept 2024). Already adopted by Stripe, Anthropic, Apify, and PayAPI.

**Why it matters:** When an LLM encounters your site, it checks for `/llms.txt` first. If found, it reads a structured summary instead of trying to parse your HTML. This is the single highest-leverage AI SEO action.

> Verification note (Jul 2026): `anthropic.com/llms.txt` returns 404 — Anthropic does NOT serve llms.txt at root. `modelcontextprotocol.io` (Mintlify-hosted docs) DOES serve one. Always verify adoption claims by `curl`ing the domain yourself before repeating them. llms.txt remains a *proposal* (not a W3C standard).

**What PayAPI does (per their published llms.txt — re-verify before copying):**
- Clear one-paragraph description of the service
- JSON metadata block (name, category, protocol, pricing)
- "What it IS" and "What it is NOT" sections
- Full API catalog with URLs and per-request pricing
- FAQ section (matches natural language questions)
- Blog post URLs (for training data ingestion)
- MCP connection config (copy-paste JSON for Claude Desktop)
- Link to `/llms-full.txt` for complete crawlable content

**What we build:** `/llms.txt` on our marketplace with the same structure, plus `/llms-full.txt` containing all API documentation in markdown.

### Layer 2: MCP Directory Listings (CRITICAL — the "App Store" for agents)

**What it is:** MCP (Model Context Protocol) directories are where agents browse for tools. Think of them as the App Store, but for AI agents.

**Active directories I verified today:**
| Directory | Status | Size |
|-----------|--------|------|
| **Smithery.ai** | ✅ Active | 315K+ chars of listings |
| **Glama.ai/mcp/servers** | ✅ Active | 160K+ chars |
| **OpenTools.ai** | ✅ Active | 114K+ chars |

**Why it matters:** When someone configures Claude Desktop or Cursor, they connect to MCP servers from these directories. If your marketplace's MCP server is listed there, agents auto-discover every API you offer.

**What we build:** Register our marketplace's MCP endpoint on all three directories. Each listing includes tool manifests for every API (stamp duty, property lookup, etc.).

### Layer 3: Structured Data / Schema.org (HIGH — do this second)

**What it is:** JSON-LD structured data embedded in HTML pages. Tells AI crawlers exactly what each page is about, in a machine-readable format.

**Why it matters:** Google's AI Overviews, Perplexity, and ChatGPT search all read structured data. A page with proper schema.org markup gets cited more often than a page without it.

**What we build:** Each API page gets:
- `SoftwareApplication` schema (name, description, pricing)
- `APIReference` schema (endpoints, parameters, responses)
- `Offer` schema (pricing per request)
- `FAQPage` schema (common questions about the API)

### Layer 4: Training Data Presence (MEDIUM — long-term play)

**What it is:** Being present in the sources that LLMs are trained on. If your marketplace appears in GitHub, Reddit, blog posts, and documentation, LLMs "know" about you even without real-time lookup.

**Why it matters:** When a user asks ChatGPT "what API can I use for Singapore property data?" ChatGPT draws from its training data. If your marketplace was mentioned in training sources, it recommends you.

**What we build:**
- GitHub repo with the MCP server (star-worthy, documented)
- Reddit posts in r/webscraping, r/SideProject, r/Singapore (educational, not spammy)
- Blog posts on the marketplace site about "how x402 works" and "Asia data for AI agents"
- Answer questions on StackOverflow, Hacker News about SG property data APIs

### Layer 5: Traditional SEO (MEDIUM — still matters)

**What it is:** Standard Google SEO. Keywords, backlinks, page speed, mobile-friendly.

**Why it matters:** AI agents still use web search. When an agent searches "Singapore stamp duty API," Google's results feed into the LLM's context. If your site ranks #1, the agent is more likely to recommend you.

**What we build:**
- Target keywords: "Singapore property API," "SG stamp duty calculator API," "Asia data API marketplace"
- Fast, structured content pages (not marketing fluff)
- Backlinks from GitHub, directories, developer communities

---

## How AI agent discovery actually works (the flow)

```
User asks Claude: "I'm buying a property in Singapore. Calculate stamp duty for $1.5M."

Step 1: Claude checks its connected MCP servers
  → If our marketplace MCP is connected → Claude finds "sg_stamp_duty" tool
  → Calls it, pays $0.002, gets exact breakdown
  → Done.

Step 2: If not connected, Claude searches the web
  → Searches "Singapore stamp duty API"
  → Finds our marketplace (if traditional SEO is good)
  → Reads our /llms.txt (if we have one)
  → Understands our API exists and recommends it to the user
  → OR: Claude computes it itself (burning tokens, but free for user)

Step 3: If the user asks "what's the best data marketplace for Asia?"
  → Claude checks training data for mentions of our marketplace
  → If we're in training data (GitHub, Reddit, blog posts) → recommends us
  → If not → recommends whatever it knows (probably nothing for Asia)
```

**The insight:** MCP directory listing (Layer 2) is the highest-leverage for direct agent calls. llms.txt (Layer 1) is the highest-leverage for web-search-based discovery. Training data (Layer 4) is the long-term brand play.

---

## Priority action list for our marketplace

| Priority | Action | Effort | Impact |
|----------|--------|--------|--------|
| 1 | Create `/llms.txt` file | 1 hour | Immediate |
| 2 | Create `/llms-full.txt` with all API docs | 2 hours | Immediate |
| 3 | Build MCP server and list on Smithery.ai | 4 hours | High |
| 4 | List on Glama.ai and OpenTools.ai | 1 hour each | High |
| 5 | Add JSON-LD structured data to marketplace pages | 2 hours | Medium |
| 6 | Create GitHub repo for the MCP server | 2 hours | Medium-term |
| 7 | Write educational blog posts (x402, Asia data) | Ongoing | Long-term |
| 8 | Reddit/HN community presence | Ongoing | Long-term |
