# bountyapi-mcp

MCP server for BountyAPI — specialist data APIs for AI agents with x402 micropayment support.

Connect AI agents (Claude Desktop, Cursor, etc.) to BountyAPI endpoints at `https://bountyapi.com`. Free endpoints work out of the box. Paid endpoints require a funded wallet — the agent pays automatically per request.

## Quick Start

### Free tools only (no wallet needed)

```json
{
  "mcpServers": {
    "bounty": {
      "command": "npx",
      "args": ["bountyapi-mcp"]
    }
  }
}
```

### With payment support (paid endpoints auto-pay)

1. Create a burner wallet on Base with $2-5 of USDC
2. Export the private key
3. Configure:

```json
{
  "mcpServers": {
    "bounty": {
      "command": "npx",
      "args": ["bountyapi-mcp"],
      "env": {
        "EVM_PRIVATE_KEY": "0xyour_private_key_here",
        "MAX_SPEND_USD": "1.00"
      }
    }
  }
}
```

Or via CLI:
```bash
EVM_PRIVATE_KEY=0x... MAX_SPEND_USD=1.00 npx bountyapi-mcp
```

Then ask your AI:

> "What's the stamp duty for a $1.5M property in Singapore for a Singapore Citizen buying their first home?"

> "Search for recent HDB resale transactions in Tampines, 4 ROOM only"

> "What's the rental yield on a $1.2M condo renting at $4,200/month?"

## How Payments Work

When an agent calls a paid endpoint:

1. Server returns `402 Payment Required` with payment instructions
2. MCP server reads the price and checks against `MAX_SPEND_USD`
3. If within limit, it signs a USDC transfer on Base and retries
4. Server verifies payment via facilitator and returns data
5. Settlement is logged with a Basescan transaction link

The agent never sees the payment — it just gets the data. Cost: ~$0.01 per paid call.

## Available Tools

### sg_stamp_duty (FREE)
Calculate Singapore property stamp duty (BSD + ABSD).
- Buyer profiles: SC, SPR, FR, entity, developer, trustee
- Returns total duty, effective rate, and tier-by-tier breakdown
- Source: IRAS (iras.gov.sg)

### sg_postal_lookup (FREE)
Look up Singapore postal code to district.
- All 28 postal districts with area names
- Returns district number, name, and areas

### sg_rental_yield (PAID)
Calculate rental investment metrics.
- Gross yield, net yield, cap rate
- Price-to-rent ratio, monthly cashflow, years to break even

### hdb_resale_median (PAID)
Get HDB resale price data by town.
- All 26 HDB towns
- Median prices by flat type (2 ROOM through EXECUTIVE)
- Sampled transaction aggregates from data.gov.sg

### hdb_resale_search (PAID)
Search HDB resale transactions with filters.
- Filter by town, flat type, price range
- Returns individual transaction records

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `EVM_PRIVATE_KEY` | _(none)_ | Base wallet private key for automatic payments |
| `MAX_SPEND_USD` | `1.00` | Max USDC to spend per single request |
| `BOUNTY_API_URL` | `https://bountyapi.com` | API base URL |

## Safety

- **Never use your main wallet.** Create a dedicated burner wallet with only a few dollars of USDC on Base.
- The `MAX_SPEND_USD` limit prevents unexpected charges — each request is checked before payment.
- Private keys are never logged or transmitted to the API server. They only sign local payment authorizations.

## Development

```bash
npm install
npm run build
node dist/index.js
```

## License

MIT
