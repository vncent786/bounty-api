"""
x402 Payment Middleware for Bounty API.

Implements the x402 protocol for agent-native micropayments:
1. Agent requests data → server responds 402 + payment instructions
2. Agent pays USDC on Base → retries with payment proof
3. Server verifies via facilitator → returns data

Freemium model:
- FREE: Stamp duty, mortgage calc, investment growth, currency, postal lookup
  (cheap computed endpoints — drive discovery)
- PAID: HDB resale data, rental yield (data-heavy, real value)
"""

import os
from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.schemas import Network
from x402.server import x402ResourceServer

# Network: Base mainnet for production
EVM_NETWORK: Network = "eip155:8453"  # Base mainnet

# Facilitator: PayAI public facilitator (production, no API keys required)
# Coinbase CDP facilitator requires Coinbase API auth and returns 401 without it.
FACILITATOR_URL = os.environ.get(
    "X402_FACILITATOR_URL",
    "https://facilitator.payai.network"
)

# Receiving wallet — set via env var on Railway
PAY_TO_ADDRESS = os.environ.get("X402_PAY_TO", "")

# Pricing (per request)
PRICE_HDB = "$0.01"        # HDB resale data — real government data, costs us to fetch
PRICE_YIELD = "$0.005"     # Rental yield — computed but valuable
PRICE_ANALYSIS = "$0.05"   # Full property analysis — composite, high value
PRICE_AFFORDABILITY = "$0.01"  # TDSR/MSR — regulatory computation
PRICE_RANK = "$0.10"       # Property ranking — highest-value workflow endpoint
PRICE_PITCH = "$0.05"     # Property pitch — investment thesis one-pager
PRICE_URA = "$0.05"       # URA private property data — exclusive government API data
PRICE_COMPANY_INTEL = "$0.05"  # Company website intelligence — replaces BuiltWith ($295/mo)
PRICE_NEWS = "$0.01"      # News search — replaces NewsAPI ($449/mo)
PRICE_JOBS = "$0.02"      # Job/hiring signal aggregation — replaces job-board scraping workflows
PRICE_REVIEWS = "$0.02"   # App Store review intelligence — replaces manual competitor-review scraping
PRICE_SOCIAL = "$0.05"   # Cross-platform social trend search (Reddit + YouTube + Instagram)
PRICE_TIKTOK = "$0.03"   # TikTok search — real video data with views/likes/comments


def create_payment_middleware(app):
    """Add x402 payment middleware to FastAPI app.

    Only protects premium endpoints. Free endpoints (stamp duty, mortgage,
    investment, currency, postal) remain open for discovery.
    """
    if not PAY_TO_ADDRESS:
        print("[x402] WARNING: X402_PAY_TO not set. Payment middleware disabled.")
        print("[x402] Set X402_PAY_TO to your Base wallet address to enable payments.")
        return False

    facilitator = HTTPFacilitatorClient(
        FacilitatorConfig(url=FACILITATOR_URL)
    )

    server = x402ResourceServer(facilitator)
    server.register(EVM_NETWORK, ExactEvmServerScheme())

    routes: dict[str, RouteConfig] = {
        "GET /hdb/towns": RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="exact",
                    pay_to=PAY_TO_ADDRESS,
                    price=PRICE_HDB,
                    network=EVM_NETWORK,
                ),
            ],
            mime_type="application/json",
            description="Singapore HDB resale median prices by town",
        ),
        # NOTE: x402 middleware supports :param and [param] syntax, NOT {param}
        "GET /hdb/median/:town": RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="exact",
                    pay_to=PAY_TO_ADDRESS,
                    price=PRICE_HDB,
                    network=EVM_NETWORK,
                ),
            ],
            mime_type="application/json",
            description="HDB resale median prices for a specific town",
        ),
        "GET /hdb/search": RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="exact",
                    pay_to=PAY_TO_ADDRESS,
                    price=PRICE_HDB,
                    network=EVM_NETWORK,
                ),
            ],
            mime_type="application/json",
            description="Search HDB resale transactions with filters",
        ),
        "POST /rental-yield/calculate": RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="exact",
                    pay_to=PAY_TO_ADDRESS,
                    price=PRICE_YIELD,
                    network=EVM_NETWORK,
                ),
            ],
            mime_type="application/json",
            description="Rental yield investment calculator",
        ),
        "POST /property/analyze": RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="exact",
                    pay_to=PAY_TO_ADDRESS,
                    price=PRICE_ANALYSIS,
                    network=EVM_NETWORK,
                ),
            ],
            mime_type="application/json",
            description="Complete property investment analysis — stamp duty, comparables, yield, affordability, location",
        ),
        "POST /affordability/calculate": RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="exact",
                    pay_to=PAY_TO_ADDRESS,
                    price=PRICE_AFFORDABILITY,
                    network=EVM_NETWORK,
                ),
            ],
            mime_type="application/json",
            description="Singapore TDSR/MSR mortgage affordability calculator",
        ),
        "POST /property/rank": RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="exact",
                    pay_to=PAY_TO_ADDRESS,
                    price=PRICE_RANK,
                    network=EVM_NETWORK,
                ),
            ],
            mime_type="application/json",
            description="Rank candidate properties by investment value — stamp duty, comps, yield, affordability, location",
        ),
        "POST /property/pitch": RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="exact",
                    pay_to=PAY_TO_ADDRESS,
                    price=PRICE_PITCH,
                    network=EVM_NETWORK,
                ),
            ],
            mime_type="application/json",
            description="Generate a complete property investment pitch — price fairness, stamp duty, affordability, yield, location, tenure risk, and plain-English verdict",
        ),
        "GET /ura/transactions": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_URA, network=EVM_NETWORK)],
            mime_type="application/json",
            description="URA private residential property transactions (caveat data)",
        ),
        "GET /ura/rental-median": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_URA, network=EVM_NETWORK)],
            mime_type="application/json",
            description="URA median rentals by private residential project",
        ),
        "GET /ura/developer-sales": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_URA, network=EVM_NETWORK)],
            mime_type="application/json",
            description="URA private residential developer sales data",
        ),
        "GET /ura/pipeline": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_URA, network=EVM_NETWORK)],
            mime_type="application/json",
            description="URA private residential future supply pipeline",
        ),
        "GET /ura/rental-contracts": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_URA, network=EVM_NETWORK)],
            mime_type="application/json",
            description="URA private residential rental contract statistics",
        ),
        "GET /company/:domain": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_COMPANY_INTEL, network=EVM_NETWORK)],
            mime_type="application/json",
            description="Company website intelligence — tech stack, contacts, security, metadata for any domain. Replaces BuiltWith.",
        ),
        "GET /news/search": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_NEWS, network=EVM_NETWORK)],
            mime_type="application/json",
            description="Search news articles by keyword — aggregated from Google News and other free sources. Replaces NewsAPI.",
        ),
        "GET /jobs/search": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_JOBS, network=EVM_NETWORK)],
            mime_type="application/json",
            description="Search job postings and hiring signals across public sources for market mapping, recruiting, and lead generation.",
        ),
        "GET /reviews/app/:country/:app_id": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_REVIEWS, network=EVM_NETWORK)],
            mime_type="application/json",
            description="Fetch recent App Store reviews with rating distribution and deterministic complaint topic flags.",
        ),
        "GET /social/trend-search": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_SOCIAL, network=EVM_NETWORK)],
            mime_type="application/json",
            description="Cross-platform social trend search across Reddit, YouTube, and Instagram with per-source health reporting.",
        ),
        "POST /social/search": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_SOCIAL, network=EVM_NETWORK)],
            mime_type="application/json",
            description="Resilient cross-platform social search with connector failover, provenance, and per-route health.",
        ),
        "POST /social/search/cached": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_SOCIAL, network=EVM_NETWORK)],
            mime_type="application/json",
            description="Serve the newest fresh scheduled social snapshot without launching live browser collection.",
        ),
        "GET /social/reddit/feed": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_SOCIAL, network=EVM_NETWORK)],
            mime_type="application/json",
            description="Camoufox-backed Reddit subreddit feed with canonical post metadata.",
        ),
        "GET /social/reddit/post": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_SOCIAL, network=EVM_NETWORK)],
            mime_type="application/json",
            description="Camoufox hydration of a canonical Reddit post and its comments.",
        ),
        "POST /social/tiktok/search": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_TIKTOK, network=EVM_NETWORK)],
            mime_type="application/json",
            description="TikTok video search with real engagement metrics (views, likes, comments). Authenticated residential proxy. Replaces TikHub/ScrapeCreators.",
        ),
        "GET /social/youtube/search": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_SOCIAL, network=EVM_NETWORK)],
            mime_type="application/json",
            description="YouTube search with full engagement metrics: views, likes, channel subscriber counts, upload dates. Sort by views or recency. Time-window filtering.",
        ),
        "GET /social/youtube/video/{video_id}": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_SOCIAL, network=EVM_NETWORK)],
            mime_type="application/json",
            description="Deep video intelligence for a single YouTube video: description, tags, categories, likes, comments, channel subscribers, duration.",
        ),
        "GET /social/youtube/channel/{handle}": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_SOCIAL, network=EVM_NETWORK)],
            mime_type="application/json",
            description="YouTube channel overview: subscriber count, recent top videos ranked by views. Creator analysis and competitive benchmarking.",
        ),
        "POST /social/tiktok/trending": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_SOCIAL, network=EVM_NETWORK)],
            mime_type="application/json",
            description="Trending TikTok content by region and category. Ranked by engagement velocity. Views, likes, comments, shares, hashtags.",
        ),
        "POST /social/trends": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=PAY_TO_ADDRESS, price=PRICE_SOCIAL, network=EVM_NETWORK)],
            mime_type="application/json",
            description="Cross-platform trend analysis: searches YouTube, TikTok, and Reddit in one call. Returns top content, creators, viral outliers, trending hashtags across platforms.",
        ),
    }

    app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)

    print(f"[x402] Payment middleware active")
    print(f"[x402] Network: Base mainnet ({EVM_NETWORK})")
    print(f"[x402] Facilitator: {FACILITATOR_URL}")
    print(f"[x402] Pay-to: {PAY_TO_ADDRESS}")
    print(f"[x402] Protected routes: {list(routes.keys())}")
    return True
