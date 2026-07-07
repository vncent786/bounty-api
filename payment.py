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
    }

    app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)

    print(f"[x402] Payment middleware active")
    print(f"[x402] Network: Base mainnet ({EVM_NETWORK})")
    print(f"[x402] Facilitator: {FACILITATOR_URL}")
    print(f"[x402] Pay-to: {PAY_TO_ADDRESS}")
    print(f"[x402] Protected routes: {list(routes.keys())}")
    return True
