# Show HN: Bounty API — An API marketplace where AI agents pay per request in USDC

Bounty is an API marketplace built for AI agents. Agents discover data via MCP (Model Context Protocol), call endpoints, and pay automatically using x402 micropayments (USDC on Base). No API keys. No subscriptions. One price per call, known upfront.

Live at https://bountyapi.com | Code: https://github.com/vncent786/bounty-api

## How it works

An agent calls a paid endpoint. The server responds with HTTP 402 + payment instructions. The agent signs a USDC transfer on Base, retries with the payment proof, and gets the data. Settlement happens in ~2 seconds.

Real transaction from our MCP test — an AI agent autonomously paid $0.01 for HDB resale data:
https://basescan.org/tx/0xc42354ea66478958099236920b293629eb1711d235b5bacad6f90d9b82beb6c5

## What's live now

7 endpoints:
- Stamp duty calculator (FREE) — verified against IRAS tax rates
- Postal district mapper (FREE) — 28 SG districts from URA source
- Mortgage/compound/currency calculators (FREE)
- HDB resale transaction data (PAID — $0.01/call, from data.gov.sg)
- Rental yield investment calculator (PAID — $0.005/call)

Every response carries source provenance. No interpolated or fabricated data.

## MCP integration

npm package: `bountyapi-mcp@1.1.0`

```json
{
  "mcpServers": {
    "bounty": {
      "command": "npx",
      "args": ["bountyapi-mcp"],
      "env": {
        "EVM_PRIVATE_KEY": "0x...",
        "MAX_SPEND_USD": "1.00"
      }
    }
  }
}
```

The MCP server handles the full x402 payment flow automatically. When an agent calls a paid endpoint, the server detects the 402, checks the price against MAX_SPEND_USD, signs the payment, and retries. The agent never sees the payment — it just gets data.

Also supports hosted MCP at https://bountyapi.com/mcp (Streamable HTTP transport).

## Tech

- Backend: FastAPI on Railway
- Payments: x402 protocol, USDC on Base, PayAI facilitator
- MCP: TypeScript, @modelcontextprotocol/sdk, @x402/evm
- Data sources: IRAS, URA, data.gov.sg

## Why we built this

API marketplaces today have two problems:

1. **Pricing opacity.** Platforms like Apify charge for compute, proxies, storage, and data transfer separately. You can't know the real cost until you run something. We charge one price per call. That's it.

2. **No quality verification.** Most API marketplaces let anyone publish. There's no quality bar. We're building continuous endpoint monitoring so agents know what actually works before they pay.

## What's next

- More APIs: company registry lookups, compliance screening, financial fundamentals
- Quality monitoring: continuous testing of every endpoint, published publicly
- Provider onboarding: let third-party developers publish APIs on Bounty
- Routing: when multiple providers offer the same capability, automatically route to the best one

Happy to answer questions about the x402 protocol, MCP integration, or the marketplace model.
