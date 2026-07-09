# Reddit post drafts — Bounty API

Do NOT post these automatically. Read through, pick a sub, and post manually.
Recommended order: r/MCP first (most on-topic), then r/ClaudeAI, then r/ClaudeAIDev if allowed.

---

## r/MCP — title

Bounty API: 12 MCP tools for Singapore property/financial data, with per-call USDC payments via x402

## r/MCP — body

Built an MCP server that exposes Singapore property, tax, and location data as tools an agent can call directly. Singapore is live now; the shape is region-parameterized for later expansion (HK/UAE/AU/JP).

**What's in it (12 tools, 15 APIs)**

Free (no payment, no key):
- `sg_stamp_duty` — BSD + ABSD, verified against IRAS
- `sg_postal_lookup` / `sg_address_intel` — postal code to district, URA planning area, CCR/RCR/OCR, 5 nearest MRT
- `sg_mrt_near` / `sg_mrt_search` — 142 stations, all 6 lines
- Mortgage / compound / currency calculators

Paid (x402, USDC on Base):
- `sg_affordability` — MAS TDSR/MSR ($0.01)
- `hdb_resale_median` / `hdb_resale_search` — 234K+ HDB transactions ($0.01)
- `sg_rental_yield` ($0.005)
- `sg_property_analyze` — full investment analysis ($0.05)
- `sg_property_pitch` — client-ready thesis ($0.05)
- `sg_property_rank` — score N candidates 0–100 ($0.10)

**Two ways to connect**

Remote (HTTP), no install:
```json
{ "mcpServers": { "bountyapi": { "url": "https://bountyapi.com/mcp" } } }
```

Local (stdio):
```json
{ "mcpServers": { "bountyapi": { "command": "npx", "args": ["bountyapi-mcp"], "env": { "EVM_PRIVATE_KEY": "0x...", "MAX_SPEND_USD": "1.00" } } } }
```

The interesting bit: paid tools use the x402 protocol. The server returns HTTP 402 + a payment challenge, the agent pays USDC on Base, retries, gets data. The stdio MCP server does the 402 → pay → retry loop automatically and gates spend behind `MAX_SPEND_USD`. Without a wallet key, the free tools work and paid ones just 402 (safe to try in any client).

Here's a real on-chain tx where an agent paid $0.01 for HDB data:
https://basescan.org/tx/0xc42354ea66478958099236920b293629eb1711d235b5bacad6f90d9b82beb6c5

We're a single provider (not a marketplace) — one team, verified sources (IRAS, URA, data.gov.sg, MAS, LTA), every response carries provenance.

- Site: https://bountyapi.com
- Code: https://github.com/vncent786/bounty-api
- npm: bountyapi-mcp@1.4.0
- Docs: https://bountyapi.com/docs

Curious what people think about per-call stablecoin payments as the auth/billing layer for MCP tools vs API keys + subscriptions. Fire away.

---

## r/ClaudeAI — title

Made an MCP server with Singapore property data tools — agents can pay per call in USDC, no API key

## r/ClaudeAI — body

I wanted Claude (and other MCP clients) to be able to pull verified Singapore property data — stamp duty, HDB resale prices, MRT proximity, mortgage affordability — without signing up for anything. So I packaged it as an MCP server.

12 tools, 15 APIs. The free ones (stamp duty, postal/MRT lookup, calculators) just work. The data-heavy ones (HDB resale transactions, full property analysis, ranking candidates) cost a few cents per call, paid in USDC on Base via the x402 protocol — no API key, the payment IS the auth.

You can add it to Claude Desktop / Cursor as a remote server (zero install):
```json
{ "mcpServers": { "bountyapi": { "url": "https://bountyapi.com/mcp" } } }
```

Or locally:
```json
{ "mcpServers": { "bountyapi": { "command": "npx", "args": ["bountyapi-mcp"] } } }
```

Local mode auto-handles the per-call payment if you set `EVM_PRIVATE_KEY` and a `MAX_SPEND_USD` cap. Without a wallet, free tools work and paid ones return a payment challenge (safe to poke around).

Example: ask it "what's the stamp duty on a $1.5M condo for a Singapore citizen?" (free) or "rank these 5 HDB listings by investment value" ($0.10).

- Site: https://bountyapi.com
- Code: https://github.com/vncent786/bounty-api

Not posting affiliate links or anything — just built this and wanted to share. Happy to take feature requests, especially other Singapore data you'd want as agent-callable tools.
