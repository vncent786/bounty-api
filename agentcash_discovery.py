"""
AgentCash Discovery Integration for Bounty API.

Enriches the FastAPI OpenAPI spec with x402 payment metadata so that
AgentCash's `discover_api_endpoints` tool can find and present our
paid endpoints to any AI agent running `npx agentcash install`.

What this does:
1. Overrides /openapi.json with a custom version that includes:
   - info.x-guidance: high-level agent instructions
   - info.contact.email: ownership verification
   - x-payment-info on every paid route (price + protocol)
   - responses.402 on every paid route
2. Serves /well-known/agentcash.json for direct registration

Spec: https://agentcash.dev/merchants.md
"""

import os
import json
from typing import dict as Dict, Any

# ============================================================
# Pricing map — must match payment.py RouteConfig entries
# Key format: "METHOD /path" using OpenAPI {param} syntax
# ============================================================

PAID_ROUTES: Dict[str, Dict[str, Any]] = {
    "GET /hdb/towns": {
        "price": "0.010000",
        "description": "Singapore HDB resale median prices by town",
    },
    "GET /hdb/median/{town}": {
        "price": "0.010000",
        "description": "HDB resale median prices for a specific town",
    },
    "GET /hdb/search": {
        "price": "0.010000",
        "description": "Search HDB resale transactions with filters",
    },
    "POST /rental-yield/calculate": {
        "price": "0.005000",
        "description": "Rental yield investment calculator",
    },
    "POST /property/analyze": {
        "price": "0.050000",
        "description": "Complete property investment analysis",
    },
    "POST /affordability/calculate": {
        "price": "0.010000",
        "description": "Singapore TDSR/MSR mortgage affordability calculator",
    },
    "POST /property/rank": {
        "price": "0.100000",
        "description": "Rank candidate properties by investment value",
    },
    "POST /property/pitch": {
        "price": "0.050000",
        "description": "Generate a property investment pitch with verdict",
    },
    "GET /ura/transactions": {
        "price": "0.050000",
        "description": "URA private residential property transactions",
    },
    "GET /ura/rental-median": {
        "price": "0.050000",
        "description": "URA median rentals by private residential project",
    },
    "GET /ura/developer-sales": {
        "price": "0.050000",
        "description": "URA private residential developer sales data",
    },
    "GET /ura/pipeline": {
        "price": "0.050000",
        "description": "URA private residential future supply pipeline",
    },
    "GET /ura/rental-contracts": {
        "price": "0.050000",
        "description": "URA private residential rental contract statistics",
    },
}

# Agent guidance — injected as info.x-guidance
# This is what agents read to understand how to use our API
AGENT_GUIDANCE = """\
Bounty provides specialist data APIs for Asian property and financial markets, starting with Singapore.

FREE endpoints (no payment needed):
- /postal/{code} — postal code to district mapping
- /address/{code} — full address intelligence (district, MRT, coordinates)
- /mrt/near/{code} — nearest MRT stations to a postal code
- /schools/near/{code} — schools within 1km/2km (SG school admission priority)
- /stamp-duty — buyer stamp duty + ABSD calculation
- /mortgage/calculate — mortgage payment calculator
- /buy-vs-rent — buy-vs-rent total cost comparison
- /salary/search — salary benchmark from live job postings
- /tax/income — Singapore income tax calculator
- /cpf/housing — CPF OA accumulation for housing
- /currency/convert — currency conversion
- /hdb/lease-decay — HDB lease decay analysis

PAID endpoints (x402 micropayment, USDC on Base):
- /hdb/towns, /hdb/search — HDB resale transaction data ($0.01/call)
- /rental-yield/calculate — rental yield investment metrics ($0.005/call)
- /affordability/calculate — TDSR/MSR affordability ($0.01/call)
- /property/analyze — full property investment analysis ($0.05/call)
- /property/pitch — investment thesis one-pager ($0.05/call)
- /property/rank — rank properties by investment value ($0.10/call)
- /ura/transactions, /ura/rental-median, /ura/developer-sales, /ura/pipeline, /ura/rental-contracts — URA private property data ($0.05/call)

Property Investment Research Workflow:
1. Identify the property (address, postal code, or listing URL)
2. Calculate stamp duty with /stamp-duty (FREE)
3. Compare buy vs rent with /buy-vs-rent (FREE)
4. Calculate mortgage with /mortgage/calculate (FREE)
5. Get transaction comparables with /ura/transactions ($0.05)
6. Check rental yield with /rental-yield/calculate ($0.005)
7. Benchmark salary affordability with /salary/search (FREE)
8. Get a complete investment pitch with /property/pitch ($0.05)

Steps 2-4 and 7 are FREE. Start there before paying for data-heavy endpoints.
If no endpoint matches the task, this API only covers Singapore. More Asian markets coming.
"""

# Contact email for ownership verification
CONTACT_EMAIL = os.environ.get("AGENTCASH_CONTACT_EMAIL", "vincent@bountyapi.com")


def enrich_openapi(schema: dict) -> dict:
    """
    Inject AgentCash discovery fields into a FastAPI OpenAPI schema.

    Mutates and returns the schema dict with:
    - info.x-guidance
    - info.contact.email
    - x-payment-info + responses.402 on every paid route
    """
    # Add guidance and contact
    schema.setdefault("info", {})
    schema["info"]["x-guidance"] = AGENT_GUIDANCE
    schema["info"]["contact"] = {"email": CONTACT_EMAIL}

    # Add x-discovery
    schema["x-discovery"] = {
        "ownershipProofs": []
    }

    # Enrich paid routes
    paths = schema.get("paths", {})
    for route_key, route_info in PAID_ROUTES.items():
        method, path = route_key.split(" ", 1)
        method_lower = method.lower()

        # Try exact match first
        path_item = paths.get(path)

        # If not found, try with trailing slash variants
        if not path_item:
            for alt_path in [path, path + "/", path.rstrip("/")]:
                if alt_path in paths:
                    path_item = paths[alt_path]
                    path = alt_path
                    break

        if not path_item or method_lower not in path_item:
            print(f"[agentcash] Warning: route {route_key} not found in OpenAPI paths, skipping")
            continue

        operation = path_item[method_lower]

        # Add x-payment-info
        operation["x-payment-info"] = {
            "price": {
                "mode": "fixed",
                "currency": "USD",
                "amount": route_info["price"],
            },
            "protocols": [
                {"x402": {}}
            ],
        }

        # Add 402 response
        operation.setdefault("responses", {})
        operation["responses"]["402"] = {
            "description": "Payment Required"
        }

    return schema


def mount_agentcash_discovery(app):
    """
    Override the default /openapi.json endpoint with an enriched version
    that includes AgentCash discovery metadata.
    """
    from fastapi.openapi.utils import get_openapi
    from fastapi.responses import JSONResponse

    # Store original openapi generation
    _original_app = app.openapi

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema

        # Generate base schema from FastAPI
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )

        # Enrich with AgentCash fields
        schema = enrich_openapi(schema)

        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi

    # Re-register the /openapi.json route to use our custom version
    # FastAPI registers /openapi.json during setup, but since we're
    # overriding app.openapi(), the existing route will call our version

    # Also add a well-known endpoint for direct AgentCash registration
    @app.get("/.well-known/agentcash.json")
    async def agentcash_well_known():
        return JSONResponse({
            "name": "Bounty API",
            "description": "Specialist data APIs for Asian property and financial markets. Pay-per-call, agent-native.",
            "url": "https://bountyapi.com",
            "openapi_url": "https://bountyapi.com/openapi.json",
            "llms_txt_url": "https://bountyapi.com/llms.txt",
            "protocols": ["x402"],
            "network": "eip155:8453",
            "pricing": {
                "min": "$0.005",
                "max": "$0.10",
                "currency": "USDC on Base"
            },
            "categories": ["property", "finance", "geography"],
            "regions": ["Singapore"],
            "contact": CONTACT_EMAIL,
        })

    print(f"[agentcash] Discovery integration mounted")
    print(f"[agentcash] {len(PAID_ROUTES)} paid routes enriched with x-payment-info")
    print(f"[agentcash] /openapi.json and /.well-known/agentcash.json ready")
