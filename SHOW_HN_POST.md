# Show HN: Bounty API — Singapore property data APIs where AI agents pay per request in USDC

Bounty API is a set of specialist data APIs built for AI agents. Agents discover data via MCP (Model Context Protocol), call endpoints, and (for paid ones) pay automatically using x402 micropayments (USDC on Base). No API keys. No subscriptions. One price per call, known upfront.

Live at https://bountyapi.com | Code: https://github.com/vncent786/bounty-api | npm: bountyapi-mcp@1.4.0

## How it works

An agent calls a paid endpoint. The server responds with HTTP 402 + payment instructions. The agent signs a USDC transfer on Base, retries with the payment proof, and gets the data. Settlement happens in ~2 seconds. Free endpoints need no payment at all.

Real transaction from our MCP test — an AI agent autonomously paid $0.01 for HDB resale data:
https://basescan.org/tx/0xc42354ea66478958099236920b293629eb1711d235b5bacad6f90d9b82beb6c5

## What's live now

15 APIs across 12 MCP tools (8 free, 6 paid). Singapore is the live region; the API shape carries a region parameter for future expansion (HK, UAE, AU, JP planned).

Free:
- Stamp duty calculator (BSD + ABSD) — verified against IRAS tax rates
- Postal district mapper — 28 SG districts
- Address intelligence — postal code to district, planning area (URA), CCR/RCR/OCR, 5 nearest MRT stations (142 stations, all 6 lines)
- MRT search / nearest-MRT lookup
- Mortgage, compound-growth, and currency calculators

Paid (x402):
- TDSR/MSR affordability calculator — $0.01/call (MAS framework)
- HDB resale transaction data — $0.01/call (234K+ transactions, data.gov.sg)
- Rental yield calculator — $0.005/call
- Property investment analysis — $0.05/call (composite)
- Property pitch (client-ready investment thesis) — $0.05/call
- Property ranking (score N candidates 0–100) — $0.10/call

Every response carries source provenance. No interpolated or fabricated data.

## MCP integration

Two ways to connect — remote (HTTP) or local (stdio):

Remote:
```json
{
  "mcpServers": {
    "bountyapi": { "url": "https://bountyapi.com/mcp" }
  }
}
```

Local:
```json
{
  "mcpServers": {
    "bountyapi": {
      "command": "npx",
      "args": ["bountyapi-mcp"],
      "env": { "EVM_PRIVATE_KEY": "0x...", "MAX_SPEND_USD": "1.00" }
    }
  }
}
```

The stdio MCP server handles the full x402 payment flow automatically. When an agent calls a paid endpoint, the server detects the 402, checks the price against MAX_SPEND_USD, signs the payment, and retries. Without EVM_PRIVATE_KEY set, the 8 free endpoints work and paid ones return a 402 challenge (so it's safe to try).

## Tech

- Backend: FastAPI on Railway
- Payments: x402 protocol, USDC on Base, PayAI facilitator
- MCP: TypeScript, @modelcontextprotocol/sdk, @x402/evm
- Data sources: IRAS, URA, data.gov.sg, MAS, LTA

## Why we built this

Most data APIs are built for humans: you sign up, get an API key, manage billing, and commit to a plan. That friction kills agentic workflows. We wanted data that an autonomous agent could discover, price, and consume end-to-end with zero human setup — pay-per-call in stablecoin, no account.

We're a single provider today (not a marketplace). The bet is that per-call, key-less, protocol-native data access is the right shape for agents. Singapore property is the first vertical because the government data is high quality and well structured.

## What's next

- More Singapore APIs: URA private-property transactions (pending an AccessKey), condo/project database, market trend data, buy-vs-rent analysis
- Quality monitoring: continuous testing of every endpoint, published publicly
- More regions using the same region-parameterized shape (HK, UAE, AU, JP)

Happy to answer questions about the x402 protocol, MCP integration, or the data model.
