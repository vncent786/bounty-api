# asia-data-mcp

MCP server for Asia Data API — Singapore property, financial, and geographic data for AI agents.

## Quick Start

Install and use with Claude Desktop or any MCP-compatible client:

```json
{
  "mcpServers": {
    "asia-data": {
      "command": "npx",
      "args": ["asia-data-mcp"]
    }
  }
}
```

Then ask Claude:

> "What's the stamp duty for a $1.5M property in Singapore for a Singapore Citizen buying their first home?"

> "What's the rental yield on a $1.2M condo renting at $4,200/month?"

> "What's the median HDB resale price in Bishan?"

## Available Tools

### sg_stamp_duty
Calculate Singapore property stamp duty (BSD + ABSD).
- Buyer profiles: SC, SPR, FR, entity, developer, trustee
- Returns total duty, effective rate, and tier-by-tier breakdown
- Source: IRAS (iras.gov.sg)

### sg_postal_lookup
Look up Singapore postal code to district.
- All 28 postal districts with area names
- Returns district number, name, and areas

### sg_rental_yield
Calculate rental investment metrics.
- Gross yield, net yield, cap rate
- Price-to-rent ratio, monthly cashflow, years to break even

### hdb_resale_median
Get HDB resale price data by town.
- All 26 HDB towns
- Median prices by flat type (2 ROOM through EXECUTIVE)
- 234K+ transactions from data.gov.sg

### hdb_resale_search
Search HDB resale transactions with filters.
- Filter by town, flat type, price range
- Returns individual transaction records

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIA_DATA_API_URL` | `http://localhost:8000` | API base URL |

## Development

```bash
npm install
npm run build
node dist/index.js
```

## License

MIT
