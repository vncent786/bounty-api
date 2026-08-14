# Asia Data API — Build Log

## What we built (July 5, 2026)

### SG Stamp Duty API (v1.0.0)

**Status:** ✅ Working, tested, verified against IRAS examples.

**What it does:**
Calculates Singapore property stamp duty (BSD + ABSD) for any price and buyer profile.

**Endpoints:**
- `POST /stamp-duty` — Full calculation (BSD + ABSD + breakdown)
- `GET /bsd?price=X&property_type=residential` — BSD only
- `GET /absd?price=X&buyer_profile=SC&property_count=1` — ABSD only
- `GET /health` — Health check
- `GET /docs` — Swagger UI (interactive API docs)

**Rates verified against:**
- Source: iras.gov.sg/taxes/stamp-duty/for-property
- BSD residential: 6-tier marginal (1%-6%), effective 15 Feb 2023
- BSD non-residential: 4-tier marginal (1%-4%)
- ABSD: 0%-65% depending on buyer profile, effective 27 Apr 2023
- Verification: IRAS example ($4,500,100 → $209,606 BSD) matches exactly.

**Test results:** 8/8 pass

**Files:**
- `app.py` — FastAPI application (230 lines)
- `test_api.py` — Test suite (8 tests, all pass)
- `pyproject.toml` — Dependencies

---

## Pricing design

| Endpoint | Price | Rationale |
|----------|-------|-----------|
| `/stamp-duty` (full) | $0.002 | Complete answer an agent needs |
| `/bsd` (BSD only) | $0.001 | Simpler calculation |
| `/absd` (ABSD only) | $0.001 | Simpler calculation |

Cost to serve: $0 (pure math, no data source)
Margin: ~100% (minus negligible compute)

---

## Deployment

### Option 1: Free hosting (Fly.io)
```bash
# Install flyctl
curl -L https://fly.io/install.sh | sh

# Deploy from the asia-data-api directory
cd saas/asia-data-api
fly launch
fly deploy
```

### Option 2: Railway ($5/month)
```bash
# Create Railway account, connect GitHub repo
# Railway auto-detects FastAPI and deploys
```

### Option 3: Vercel (free)
```bash
# Requires wrapping in a serverless function
npm i -g vercel
vercel
```

---

## Next APIs to build (Phase 2)

1. **SG Property Transaction Lookup** — URA data (cached quarterly)
2. **HDB Resale Median by Town** — HDB open data (cached monthly)
3. **SG Postal Code → District** — Static mapping table
4. **SGX Company Info** — Basic listed company data
5. **SG Property Price Index by District** — URA quarterly data

---

## x402 Payment Integration (Phase 3)

Once deployed, wrap endpoints with x402 middleware:

```python
# Using x402 Python SDK (github.com/x402-foundation/x402)
from x402.fastapi import payment_middleware

app.add_middleware(payment_middleware, {
    "/stamp-duty": {"price": "$0.002", "network": "base"},
    "/bsd": {"price": "$0.001", "network": "base"},
    "/absd": {"price": "$0.001", "network": "base"},
})
```

Payments settle in USDC on Base mainnet.
Provider wallet: your Base wallet address.
