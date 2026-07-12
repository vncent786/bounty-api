# Bounty API — MCP / API Directory Submissions

**Last updated:** 2026-07-09 (SGT) by risk-mitigation pass
**Live:** https://bountyapi.com · **Repo:** https://github.com/vncent786/bounty-api · **npm:** bountyapi-mcp@1.4.0 · **MCP:** https://bountyapi.com/mcp

---

## ⚠️ RISK-CHECK RESULTS (gate before listing)

All 7 pre-listing checks were run against the live site. Result: **PASS with one mitigated item.**

| # | Check | Result |
|---|-------|--------|
| 1 | `npm install -g bountyapi-mcp` + MCP connects | ✅ PASS — installs (112 pkgs), `initialize` returns `bountyapi-mcp v1.4.0`, `tools/list` returns **12 tools** over stdio |
| 2 | `curl https://bountyapi.com/health` → 200 | ✅ PASS — `{"status":"ok",...}` |
| 3 | `curl /llms.txt` content + accurate counts | ✅ PASS — 15 APIs, 12 MCP tools, 8 free, 6 paid |
| 4 | Free endpoints return valid JSON | ✅ PASS — `/bsd?price=1000000` → 200 (BSD breakdown), `/postal/238582` → 200 (district 9). **Note:** `/hdb/towns` is a *paid* endpoint ($0.01, protected in `payment.py`); its 402 is correct, not a failure |
| 5 | Paid endpoints return 402 (not 500) | ✅ PASS — `POST /property/pitch` → 402, `POST /property/analyze` → 402 |
| 6 | Landing page loads | ✅ PASS — 200, 14.6 KB |
| 7 | No overclaim ("data APIs" not "marketplace"; "Singapore" not "global") | ⚠️ FOUND & MITIGATED in source — see below |

### Check #7 — overclaim mitigation (recoverable risk, fixed in source)
The live `/llms.txt` + landing-page meta said **"Data API Marketplace" / "marketplace" / focus "Global"**. Bounty is a single provider with Singapore data live. Fixed in commit `cd98e59` (+ follow-up):
- `/llms.txt` generator (`app.py`): category → "Specialist Data APIs", focus → "Singapore", "marketplace of specialist data APIs" reworded to "single provider (marketplace is a roadmap item)".
- Landing page: meta description, og:description, and the "Built for agent economics" panel no longer say "marketplace".
- Landing JSON stats aligned: live_apis 14→**15**, mcp_tools 11→**12**, paid_endpoints 5→**6**.
- `public/llms.txt` was stale ("Asia Data API" / asiadataapi.com, dead file not served) — rewritten to match live copy.

**🔴 ACTION NEEDED (Vincent): redeploy.** The fixes are in source + committed locally but **not yet live**. Crawl-based directories (Glama, PulseMCP, official registry) read the live site/repo, so **redeploy to Railway before/after those submissions** so they index corrected copy. Author-controlled submissions (mcp.so issue, Reddit, HN, PH) already use accurate copy in this file.

---

## ❗ GLOBAL BLOCKER: GitHub login

`gh` CLI is **not installed** (winget silent install did not place a binary) and there is no stored GitHub token. Any directory that requires GitHub/Google OAuth **cannot be submitted from this environment** and needs Vincent to complete login in a browser. Flagged per-directory below.

---

## DIRECTORY STATUS

| Directory | Status | Submission path | Needs |
|-----------|--------|-----------------|-------|
| **Smithery** | ✅ Already listed | `vncent786/bounty-api` | — (verify the listing still resolves) |
| **Official MCP Registry** | 🟡 Payload ready, validated | `mcp-publisher` CLI | GitHub OAuth + npm republish |
| **mcp.so** | 🟡 Payload ready | https://mcp.so/submit *or* GitHub issue | GitHub/Google login |
| **Glama** | 🟡 Auto-crawl expected | "Add Server" button (GitHub OAuth) | GitHub login; or wait for auto-crawl of repo |
| **PulseMCP** | 🟡 Payload ready | https://pulsemcp.com/servers/new | Browser (Cloudflare 403 from CLI) |
| **awesome-mcp-servers** | 🟡 PR payload ready | GitHub PR to punkpeye/awesome-mcp-servers | GitHub login |
| **mcp.run** | ⚪ Auto-discovers npm | https://mcp.run (Dylibso) | None (auto-pulls from npm/github) |
| **mcphub.io** | ⚪ Discover | https://mcphub.io | TBD |
| **Reddit** (r/MCP, r/ClaudeAI) | 🟡 Draft saved | `REDDIT_POSTS.md` | Manual post |
| **Hacker News** | 🟡 Draft saved | `SHOW_HN_POST.md` | Manual post |
| **Product Hunt** | 🟡 Draft saved | `PRODUCTHUNT_DRAFT.md` | Manual post + assets |

Legend: ✅ done · 🟡 payload/draft ready, needs Vincent's manual action · ⚪ auto / TBD

---

## 1) Official MCP Registry — `mcp-publisher`

Registry only hosts metadata; ownership is verified via the npm package + GitHub auth.

**Already done in this repo:**
- `mcp-server/server.json` created and **validated** (`mcp-publisher validate` → ✅ valid). Includes BOTH a remote `streamable-http` transport (`https://bountyapi.com/mcp`) and the npm stdio package, with `EVM_PRIVATE_KEY` documented as optional.
- `mcpName` field added to `mcp-server/package.json` (`io.github.vncent786/bounty-api`) — required for ownership verification.

**Steps for Vincent (in `C:\Users\vncen\saas\asia-data-api\mcp-server`):**
```bash
# 1. Republish npm with the mcpName field (registry verifies ownership from the published package)
npm version patch            # bumps 1.4.0 -> 1.4.1
npm publish                  # requires npm login (npm whoami)

# 2. Install the publisher (Windows)
curl -L "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_windows_amd64.tar.gz" | tar xz mcp-publisher.exe
# (or: brew install mcp-publisher  on macOS)

# 3. Authenticate (GitHub OAuth — opens browser) and publish
mcp-publisher login github
mcp-publisher validate       # confirm server.json still valid
mcp-publisher publish
```
Verification URL after publish: `https://registry.modelcontextprotocol.io/v0/servers/io.github.vncent786/bounty-api`

---

## 2) mcp.so

Primary: https://mcp.so/submit (form, GitHub/Google OAuth).
Fallback: GitHub issue in https://github.com/chatmcp/mcpso/issues (needs `gh` or browser login).

### GitHub issue title
`Submit MCP Server: Bounty API — Pay-per-call data APIs for AI agents`

### GitHub issue body (accurate, non-overclaim)
```markdown
## Server Name
Bounty API

## Repository
https://github.com/vncent786/bounty-api

## MCP Server URL
https://bountyapi.com/mcp

## npm Package
https://www.npmjs.com/package/bountyapi-mcp

## Website
https://bountyapi.com

## Description
Bounty API provides verified Singapore property, tax, affordability, transaction, and location data for AI agents via MCP and x402 micropayments. Singapore is live now; the API shape includes a region parameter for future market expansion (HK, UAE, AU, JP). Bounty is a single provider, not a marketplace.

## Current MCP Tools (12)
- sg_stamp_duty — Singapore BSD + ABSD calculator (FREE)
- sg_postal_lookup — postal code to district (FREE)
- sg_address_intel — district, planning area, market region, nearest MRT (FREE)
- sg_mrt_near — nearest MRT stations to a postal code (FREE)
- sg_mrt_search — MRT station search (FREE)
- sg_affordability — MAS TDSR/MSR mortgage affordability (PAID)
- sg_rental_yield — rental yield calculator (PAID)
- hdb_resale_median — HDB resale medians by town/flat type (PAID)
- hdb_resale_search — HDB resale transaction search (PAID)
- sg_property_analyze — complete property investment analysis (PAID)
- sg_property_rank — rank candidate properties by value/yield/affordability/location (PAID)
- sg_property_pitch — client-ready property investment thesis (PAID)

## Categories
Finance, Real Estate, Data, Singapore, x402, Payments, Government Data, MCP

## Install
```bash
npx bountyapi-mcp
```

## Remote MCP
```text
https://bountyapi.com/mcp
```

## Pricing
Freemium. 8 free utility endpoints; 6 paid data/workflow endpoints via x402 USDC on Base ($0.005–$0.10/call).

## Notes
Every response carries source provenance (IRAS, URA, data.gov.sg, MAS, LTA). No interpolated or fabricated data.
```

---

## 3) Glama — https://glama.ai/mcp/servers

Glama indexes by GitHub author and has an **"Add Server"** button (GitHub OAuth). Expected page once indexed: `https://glama.ai/mcp/servers/vncent786/bounty-api`. Action: sign in with GitHub, click "Add Server", point at `https://github.com/vncent786/bounty-api` + `https://bountyapi.com/mcp`. Otherwise it auto-crawls npm/GitHub on its own schedule.

---

## 4) PulseMCP — https://pulsemcp.com/servers/new

Cloudflare returns 403 to CLI tools; submit from a browser. Use the mcp.so description block above. Provide: name `Bounty API`, repo, remote MCP URL `https://bountyapi.com/mcp`, npm `bountyapi-mcp`.

---

## 5) awesome-mcp-servers — https://github.com/punkpeye/awesome-mcp-servers

Curated list (a README). Submission = a PR adding one line under the Data / Real Estate section:
```markdown
- [bountyapi-mcp](https://github.com/vncent786/bounty-api) 📇 ☁️ 🏠 - Verified Singapore property, tax, affordability, and location data for AI agents via MCP and x402 micropayments.
```
(Legend: 📇 = reference/lookup data, ☁️ = hosted/remote, 🏠 = real estate.) Needs GitHub login.

---

## 6) mcp.run — https://mcp.run (Dylibso)

Auto-discovers from npm/GitHub. No explicit submit form required. Once the npm package is stable (it is) the package should be discoverable. Verify at `https://mcp.run/bountyapi-mcp` after a crawl cycle.

---

## Community posts (drafts saved, do NOT auto-post)
- `REDDIT_POSTS.md` — r/MCP + r/ClaudeAI drafts
- `SHOW_HN_POST.md` — Show HN draft (refreshed to 15 APIs / 12 tools / v1.4.0, marketplace overclaim removed)
- `PRODUCTHUNT_DRAFT.md` — Product Hunt submission + maker comment + asset checklist

---

## Checklist for Vincent (manual, in priority order)
1. **Redeploy** to Railway (so live `/llms.txt` + landing reflect the non-overclaim copy) — unblocks crawl-based directories.
2. **Official MCP Registry** — npm republish (1.4.1 with `mcpName`) → `mcp-publisher login github` → `publish`. (`server.json` ready & validated in `mcp-server/`.)
3. **mcp.so** — submit via https://mcp.so/submit (GitHub login) or the chatmcp/mcpso issue above.
4. **Glama** — "Add Server" with GitHub, or let auto-crawl pick up the repo.
5. **PulseMCP** — submit via browser at https://pulsemcp.com/servers/new.
6. **awesome-mcp-servers** — open PR with the one-liner above.
7. **Reddit / HN / Product Hunt** — post from the saved drafts when ready to launch.
