# Bounty API

**Agent-native API marketplace where AI agents pay per request in USDC on Base.**

Live at [bountyapi.com](https://bountyapi.com)

## What it does

AI agents discover data APIs via MCP, call them, and pay automatically using x402 micropayments. No API keys. No subscriptions. No scraping. One price per call, known upfront, settled on-chain.

```
Agent calls GET /company-lookup?name=ACME
  → Server returns 402 Payment Required ($0.05 USDC)
  → Agent signs payment on Base
  → Server returns data + settlement receipt
  → Transaction verifiable on Basescan
```

## Verified working

Real on-chain transaction — an AI agent autonomously paid for data on Base mainnet:
- Tx: [0xc42354ea...](https://basescan.org/tx/0xc42354ea66478958099236920b293629eb1711d235b5bacad6f90d9b82beb6c5)
- Amount: $0.01 USDC
- Network: Base (eip155:8453)
- Facilitator: PayAI

## Architecture

```
Layer 1: Raw API endpoints (FastAPI)
  → bountyapi.com/bsd, /hdb/towns, /postal/{code}, etc.

Layer 2: x402 payment middleware
  → Paid endpoints return 402 + payment instructions
  → Agent pays USDC on Base → facilitator verifies → data returned

Layer 3: MCP discovery (npm: bountyapi-mcp)
  → Agents discover APIs as tools via Model Context Protocol
  → Supports both stdio (npx) and HTTP transport (/mcp endpoint)
  → Wallet-aware: auto-pays for paid endpoints when configured
```

## Live APIs (7 endpoints)

| API | Type | Price | Description |
|-----|------|-------|-------------|
| Stamp Duty (BSD + ABSD) | FREE | $0 | Singapore property stamp duty, verified against IRAS |
| Postal District Lookup | FREE | $0 | 28 postal districts, mapped from URA source |
| Mortgage Calculator | FREE | $0 | Standard amortization |
| Compound Interest | FREE | $0 | Growth projections |
| Currency Converter | FREE | $0 | 30+ currencies, ECB rates |
| HDB Resale Data | PAID | $0.01/call | Government transaction data from data.gov.sg |
| Rental Yield Calculator | PAID | $0.005/call | Investment metrics (gross yield, cap rate, cashflow) |

Every response carries source provenance. No interpolated or fabricated values.

## Quick start

### Install the MCP server

```bash
npx bountyapi-mcp
```

### With payment support (for paid endpoints)

```bash
EVM_PRIVATE_KEY=0x... MAX_SPEND_USD=1.00 npx bountyapi-mcp
```

### Claude Desktop config

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

Then ask Claude: *"What's the stamp duty on a $1.5M Singapore property for a first-time buyer?"*

## Direct API calls (no MCP needed)

```bash
# Free endpoint
curl https://bountyapi.com/bsd?price=1500000
# → {"bsd": 44600, "source": "iras.gov.sg"}

# Paid endpoint (returns 402 without payment)
curl https://bountyapi.com/hdb/towns
# → 402 Payment Required + x402 challenge
```

## Tech stack

- **Backend:** FastAPI (Python), deployed on Railway
- **Payments:** x402 protocol, USDC on Base, PayAI facilitator
- **MCP:** TypeScript, @modelcontextprotocol/sdk, @x402/evm
- **Data sources:** IRAS, URA, data.gov.sg, ECB
- **Domain:** bountyapi.com (SSL via Railway/Let's Encrypt)

## Project structure

```
├── app.py              # Main FastAPI app, landing page, routes
├── pages.py            # Marketplace pages (pricing, providers, setup)
├── payment.py          # x402 payment middleware config
├── apis/               # API modules (postal, rental, hdb, mortgage, etc.)
├── mcp-server/         # TypeScript MCP server (published to npm)
├── public/             # Favicons, OG images, webmanifest
└── Dockerfile          # Railway deployment
```

## Development

```bash
# Backend
pip install -r requirements.txt
uvicorn app:app --reload

# MCP server
cd mcp-server
npm install
npm run build
node dist/index.js
```

## License

MIT
