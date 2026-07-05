"""
Bounty API — Specialist data APIs for AI agents.
Singapore property, financial, and geographic data.
Designed for x402 micropayments and MCP discovery.

APIs:
- SG Stamp Duty (BSD + ABSD) — verified against IRAS
- SG Postal Code to District mapper
- SG Rental Yield Calculator
- HDB Resale Price data (live from data.gov.sg)
"""

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, HTMLResponse
from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum
from datetime import datetime
import math
import os

app = FastAPI(
    title="Bounty API",
    description="Specialist data APIs for AI agents. Pay-per-call, agent-native, globally scalable.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Startup: pre-warm HDB cache so first user request doesn't block
# ============================================================

@app.on_event("startup")
async def _prewarm_cache():
    """Trigger HDB data download in background on app start.
    Without this, the first /hdb/* request after deploy hangs 30-60s."""
    try:
        from apis.hdb_resale import _maybe_refresh_background
        _maybe_refresh_background()
    except Exception:
        pass  # non-fatal — endpoints will warm on first request


# ============================================================
# Rate tables — sourced from IRAS (verified Jan 2026)
# Source: iras.gov.sg/taxes/stamp-duty/for-property
# ============================================================

BSD_RESIDENTIAL_TIERS = [
    (180_000, 0.01),       # First $180,000 at 1%
    (180_000, 0.02),       # Next $180,000 at 2%
    (640_000, 0.03),       # Next $640,000 at 3%
    (500_000, 0.04),       # Next $500,000 at 4%  (effective 15 Feb 2023)
    (1_500_000, 0.05),     # Next $1,500,000 at 5%
    (float('inf'), 0.06),  # Remaining at 6%
]

BSD_NON_RESIDENTIAL_TIERS = [
    (180_000, 0.01),
    (180_000, 0.02),
    (640_000, 0.03),
    (float('inf'), 0.04),  # Remaining at 4%
]

# ABSD rates effective on or after 27 Apr 2023
ABSD_RATES = {
    'SC':       {1: 0.00, 2: 0.20, 3: 0.30},
    'SPR':      {1: 0.05, 2: 0.30, 3: 0.35},
    'FR':       {1: 0.60},
    'entity':   {1: 0.65},
    'developer': {1: 0.40},  # 35% + 5% non-remittable
    'trustee':  {1: 0.65},
}


class BuyerProfile(str, Enum):
    SC = "SC"           # Singapore Citizen
    SPR = "SPR"         # Singapore Permanent Resident
    FR = "FR"           # Foreigner
    ENTITY = "entity"   # Entity (company, partnership, etc.)
    DEVELOPER = "developer"  # Housing Developer
    TRUSTEE = "trustee"      # Trustee


class PropertyType(str, Enum):
    RESIDENTIAL = "residential"
    NON_RESIDENTIAL = "non-residential"


# ============================================================
# Core calculation logic
# ============================================================

def calculate_marginal_duty(price: float, tiers: list) -> tuple:
    """Calculate tiered/marginal stamp duty. Returns (total_duty, breakdown)."""
    remaining = price
    total = 0.0
    breakdown = []
    cumulative = 0

    for tier_size, rate in tiers:
        if remaining <= 0:
            break
        taxable = min(remaining, tier_size)
        duty = taxable * rate
        total += duty

        lower = cumulative
        upper = cumulative + taxable

        breakdown.append({
            "range": f"${lower:,.0f} – ${upper:,.0f}" if upper != float('inf') and taxable == tier_size
                     else (f"First ${upper:,.0f}" if cumulative == 0
                           else f"${lower:,.0f} – ${upper:,.0f}"),
            "rate_percent": rate * 100,
            "taxable_amount": round(taxable, 2),
            "duty": round(duty, 2),
        })
        remaining -= taxable
        cumulative += taxable

    # BSD rounded down to nearest dollar, minimum $1
    total = max(1, math.floor(total)) if total > 0 else 0
    return total, breakdown


def get_absd_rate(profile: BuyerProfile, property_count: int) -> float:
    """Get ABSD rate based on buyer profile and property count."""
    rates = ABSD_RATES.get(profile.value, {})

    if profile.value in ('FR', 'entity', 'developer', 'trustee'):
        return rates.get(1, 0.0)

    # SC and SPR: rate depends on property count
    count = max(1, property_count)
    if count in rates:
        return rates[count]
    # For 3rd and beyond, use the highest tier
    max_key = max(rates.keys())
    if count >= max_key:
        return rates[max_key]
    return 0.0


# ============================================================
# API Endpoints
# ============================================================

class StampDutyRequest(BaseModel):
    price: float = Field(..., gt=0, description="Property purchase price or market value in SGD (whichever is higher)")
    property_type: PropertyType = Field(default=PropertyType.RESIDENTIAL, description="Residential or non-residential")
    buyer_profile: BuyerProfile = Field(default=BuyerProfile.SC, description="Buyer profile for ABSD calculation")
    property_count: int = Field(default=1, ge=1, description="Number of residential properties owned including this one")


class StampDutyResult(BaseModel):
    price: float
    property_type: str
    buyer_profile: str
    property_count: int
    bsd: int
    bsd_breakdown: List[dict]
    absd: int
    absd_rate_percent: float
    absd_applicable: bool
    total_stamp_duty: int
    effective_rate_percent: float
    calculation_date: str
    source: str


@app.get("/", response_class=HTMLResponse)
async def landing_page():
    """Public marketplace landing page."""
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Bounty API — Data APIs for agents</title>
  <meta name="description" content="Bounty API is a marketplace of specialist data APIs built for AI agents, developers, and x402 micropayments." />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --ink: #171717;
      --muted: #5f5f5f;
      --faint: #8a8a8a;
      --line: rgba(0, 0, 0, 0.08);
      --panel: #ffffff;
      --wash: #fafafa;
      --accent: #0a72ef;
      --green: #0f8a55;
      --amber: #a16207;
      --radius: 14px;
      --shadow: rgba(0,0,0,0.08) 0 0 0 1px, rgba(0,0,0,0.04) 0 2px 2px, rgba(0,0,0,0.04) 0 10px 24px -16px;
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      font-family: 'Geist', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      color: var(--ink);
      background: #fff;
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
    }
    a { color: inherit; text-decoration: none; }
    .nav {
      position: sticky; top: 0; z-index: 20;
      display: flex; align-items: center; justify-content: space-between;
      padding: 16px 28px;
      background: rgba(255,255,255,0.82);
      backdrop-filter: blur(18px);
      box-shadow: rgba(0,0,0,0.08) 0 1px 0;
    }
    .brand { display: flex; align-items: center; gap: 10px; font-weight: 600; letter-spacing: -0.03em; }
    .mark { width: 24px; height: 24px; border-radius: 7px; background: #171717; color: #fff; display: grid; place-items: center; font-size: 13px; font-family: 'Geist Mono', monospace; }
    .navlinks { display: flex; align-items: center; gap: 22px; font-size: 14px; color: #4d4d4d; }
    .button { display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 38px; padding: 0 15px; border-radius: 8px; font-size: 14px; font-weight: 500; box-shadow: var(--shadow); background: #fff; }
    .button.primary { background: #171717; color: #fff; box-shadow: none; }
    .hero { max-width: 1180px; margin: 0 auto; padding: 92px 28px 54px; text-align: center; }
    .eyebrow { display: inline-flex; gap: 8px; align-items: center; padding: 6px 10px; border-radius: 999px; background: #f5f5f5; box-shadow: rgba(0,0,0,0.08) 0 0 0 1px; color: #4d4d4d; font-size: 13px; font-weight: 500; }
    .hero h1 { margin: 26px auto 18px; max-width: 900px; font-size: clamp(46px, 8vw, 86px); line-height: 0.94; letter-spacing: -0.065em; font-weight: 600; }
    .hero p { max-width: 720px; margin: 0 auto; color: var(--muted); font-size: 20px; line-height: 1.65; }
    .hero-actions { margin-top: 32px; display: flex; justify-content: center; gap: 12px; flex-wrap: wrap; }
    .terminal { max-width: 920px; margin: 46px auto 0; text-align: left; border-radius: 16px; background: #0d0d0d; color: #f5f5f5; overflow: hidden; box-shadow: rgba(0,0,0,0.18) 0 24px 70px -34px; }
    .termbar { display: flex; align-items: center; gap: 8px; padding: 13px 16px; border-bottom: 1px solid rgba(255,255,255,0.09); color: #9ca3af; font: 13px 'Geist Mono', monospace; }
    .dot { width: 10px; height: 10px; border-radius: 50%; background: #666; }
    pre { margin: 0; padding: 22px; overflow-x: auto; font: 13px/1.7 'Geist Mono', ui-monospace, monospace; color: #d4d4d4; }
    .blue { color: #7dd3fc; } .green { color: #86efac; } .gray { color: #a3a3a3; }
    section { max-width: 1180px; margin: 0 auto; padding: 64px 28px; }
    .section-head { display: flex; justify-content: space-between; align-items: end; gap: 24px; margin-bottom: 22px; }
    .section-head h2 { margin: 0; font-size: clamp(30px, 4vw, 48px); line-height: 1; letter-spacing: -0.055em; }
    .section-head p { max-width: 460px; margin: 0; color: var(--muted); line-height: 1.55; }
    .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
    .card { background: var(--panel); border-radius: var(--radius); padding: 20px; box-shadow: var(--shadow); min-height: 230px; display: flex; flex-direction: column; justify-content: space-between; }
    .card h3 { margin: 0 0 10px; font-size: 20px; line-height: 1.12; letter-spacing: -0.04em; }
    .card p { margin: 0; color: var(--muted); line-height: 1.5; font-size: 14px; }
    .tagrow { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 18px; }
    .tag { font: 12px 'Geist Mono', monospace; padding: 5px 8px; border-radius: 999px; background: #f5f5f5; color: #525252; }
    .price { margin-top: 18px; font: 13px 'Geist Mono', monospace; color: var(--green); }
    .split { display: grid; grid-template-columns: 1.05fr 0.95fr; gap: 16px; }
    .panel { background: var(--wash); border-radius: 18px; padding: 28px; box-shadow: rgba(0,0,0,0.08) 0 0 0 1px; }
    .panel h3 { margin: 0 0 12px; font-size: 26px; letter-spacing: -0.045em; }
    .list { display: grid; gap: 14px; margin-top: 20px; }
    .item { display: flex; gap: 12px; align-items: flex-start; }
    .check { width: 20px; height: 20px; flex: 0 0 auto; border-radius: 50%; display: grid; place-items: center; background: #ecfdf5; color: #047857; font-size: 12px; margin-top: 1px; }
    .item strong { display: block; font-size: 15px; margin-bottom: 3px; }
    .item span { color: var(--muted); font-size: 14px; line-height: 1.45; }
    .codebox { background: #111; border-radius: 14px; overflow: hidden; height: 100%; min-height: 342px; }
    .footer { max-width: 1180px; margin: 0 auto; padding: 48px 28px 72px; color: var(--faint); font-size: 13px; display: flex; justify-content: space-between; gap: 18px; border-top: 1px solid #ebebeb; }
    @media (max-width: 920px) { .grid, .split { grid-template-columns: 1fr; } .navlinks { display: none; } .section-head { align-items: flex-start; flex-direction: column; } .hero { padding-top: 68px; } }
  </style>
</head>
<body>
  <nav class="nav">
    <a class="brand" href="/"><span class="mark">B</span><span>Bounty API</span></a>
    <div class="navlinks">
      <a href="#apis">APIs</a>
      <a href="#agents">Agents</a>
      <a href="/docs">Docs</a>
      <a href="/llms.txt">llms.txt</a>
      <a class="button primary" href="/docs">Start building</a>
    </div>
  </nav>

  <main>
    <div class="hero">
      <div class="eyebrow">Live in Singapore · Scaling globally</div>
      <h1>Specialist data APIs for AI agents.</h1>
      <p>Bounty turns messy public datasets, government portals, and local market rules into clean endpoints agents can call without scraping the web from scratch. Starting in Singapore, expanding worldwide.</p>
      <div class="hero-actions">
        <a class="button primary" href="/docs">Explore API docs</a>
        <a class="button" href="/llms.txt">Read llms.txt</a>
        <a class="button" href="/api">View API JSON</a>
      </div>
      <div class="terminal" aria-label="Example API request">
        <div class="termbar"><span class="dot"></span><span class="dot"></span><span class="dot"></span><span>GET /bsd?price=1000000</span></div>
        <pre><span class="gray">curl</span> <span class="green">https://bountyapi.com/bsd?price=1000000</span>

{
  <span class="blue">"price"</span>: 1000000,
  <span class="blue">"bsd"</span>: 24600,
  <span class="blue">"source"</span>: <span class="green">"iras.gov.sg"</span>
}</pre>
      </div>
    </div>

    <section id="apis">
      <div class="section-head">
        <h2>API catalog</h2>
        <p>Apify-style marketplace clarity, but for high-margin data primitives agents actually need. More regions coming.</p>
      </div>
      <div class="grid">
        <article class="card">
          <div><h3>SG Stamp Duty</h3><p>BSD and ABSD calculations for Singapore property purchases. Rates verified against IRAS.</p></div>
          <div><div class="tagrow"><span class="tag">/bsd</span><span class="tag">/absd</span><span class="tag">/stamp-duty</span></div><div class="price">$0.002 / call · <a href="/apis/stamp-duty">View API →</a></div></div>
        </article>
        <article class="card">
          <div><h3>Postal District Mapper</h3><p>Map Singapore postal codes to districts, regions, and property market areas.</p></div>
          <div><div class="tagrow"><span class="tag">/postal/{code}</span><span class="tag">28 districts</span></div><div class="price">$0.001 / call · <a href="/apis/postal-district">View API →</a></div></div>
        </article>
        <article class="card">
          <div><h3>Rental Yield</h3><p>Gross yield, net yield, cash flow, cap rate, and price-to-rent calculations.</p></div>
          <div><div class="tagrow"><span class="tag">yield</span><span class="tag">cashflow</span></div><div class="price">$0.002 / call · <a href="/apis/rental-yield">View API →</a></div></div>
        </article>
        <article class="card">
          <div><h3>HDB Resale Data</h3><p>HDB resale town data sourced from data.gov.sg, structured for agent workflows.</p></div>
          <div><div class="tagrow"><span class="tag">26 towns</span><span class="tag">sampled data</span></div><div class="price">$0.003 / call · <a href="/apis/hdb-resale">View API →</a></div></div>
        </article>
      </div>
    </section>

    <section id="agents">
      <div class="split">
        <div class="panel">
          <h3>Built for agent economics.</h3>
          <p style="color: var(--muted); line-height: 1.6; margin: 0;">The pitch is simple: a model should not spend 20k tokens and ten browser actions to recover data that should have been a one-line API call.</p>
          <div class="list">
            <div class="item"><span class="check">✓</span><div><strong>x402-ready pricing</strong><span>Micropayment-native API economics for autonomous agents.</span></div></div>
            <div class="item"><span class="check">✓</span><div><strong>MCP discovery path</strong><span>Structured descriptions via llms.txt today, MCP package next.</span></div></div>
            <div class="item"><span class="check">✓</span><div><strong>Source-forward data</strong><span>Every API should expose provenance, not vibes dressed up as precision.</span></div></div>
          </div>
        </div>
        <div class="codebox">
          <pre>{
  <span class="blue">"name"</span>: <span class="green">"Bounty API"</span>,
  <span class="blue">"focus"</span>: <span class="green">"Global, starting in SG"</span>,
  <span class="blue">"payment"</span>: <span class="green">"x402 on Base"</span>,
  <span class="blue">"live_apis"</span>: 4,
  <span class="blue">"docs"</span>: <span class="green">"https://bountyapi.com/docs"</span>,
  <span class="blue">"llms"</span>: <span class="green">"https://bountyapi.com/llms.txt"</span>
}</pre>
        </div>
      </div>
    </section>
  </main>

  <footer class="footer">
    <span>© 2026 Bounty API</span>
    <span>Data APIs for agents, developers, and automated workflows.</span>
  </footer>
</body>
</html>"""


API_CATALOG = {
    "stamp-duty": {
        "title": "SG Stamp Duty Calculator",
        "eyebrow": "Verified property-tax logic",
        "summary": "Calculate Singapore BSD and ABSD for property purchases with source-forward outputs and tier breakdowns.",
        "price": "$0.002 / call",
        "source": "IRAS stamp duty rates, verified against published examples.",
        "endpoints": ["POST /stamp-duty", "GET /bsd", "GET /absd"],
        "params": ["price", "property_type", "buyer_profile", "property_count"],
        "request": "curl 'https://bountyapi.com/bsd?price=1000000'",
        "response": '{\n  "price": 1000000,\n  "property_type": "residential",\n  "bsd": 24600,\n  "source": "iras.gov.sg"\n}',
        "try_url": "/bsd?price=1000000",
        "limit": "ABSD depends on buyer profile and property count. Use POST /stamp-duty for full calculation."
    },
    "postal-district": {
        "title": "SG Postal District Mapper",
        "eyebrow": "Static geography primitive",
        "summary": "Map 6-digit Singapore postal codes to postal districts, regions, and common property-market areas.",
        "price": "$0.001 / call",
        "source": "Static Singapore postal district reference table.",
        "endpoints": ["GET /postal/{code}", "GET /postal/districts", "GET /postal/district/{number}"],
        "params": ["postal_code", "district_number"],
        "request": "curl 'https://bountyapi.com/postal/238582'",
        "response": '{\n  "postal_code": "238582",\n  "district": 9,\n  "name": "Orchard / Cairnhill / River Valley",\n  "general_area": "Core Central (CCR)"\n}',
        "try_url": "/postal/238582",
        "limit": "Singapore-only. Sectors 09-13 are intentionally unassigned under this postal-district scheme."
    },
    "rental-yield": {
        "title": "Rental Yield Calculator",
        "eyebrow": "Pure math investment primitive",
        "summary": "Calculate gross yield, net yield, cap rate, annual cashflow, and price-to-rent ratios for property underwriting.",
        "price": "$0.002 / call",
        "source": "Calculated from standard real-estate formulas. No external data dependency.",
        "endpoints": ["POST /rental-yield/calculate"],
        "params": ["property_price", "monthly_rent", "annual_expenses", "property_tax_rate", "management_fee_monthly", "maintenance_monthly"],
        "request": "curl -X POST 'https://bountyapi.com/rental-yield/calculate' -H 'content-type: application/json' -d '{\"property_price\":1000000,\"monthly_rent\":3500}'",
        "response": '{\n  "gross_annual_rent": 42000,\n  "gross_yield_percent": 4.2,\n  "net_yield_percent": 4.032,\n  "price_to_rent_ratio": 23.81\n}',
        "try_url": "/docs#/rental-yield/calculate_rental_yield_calculate_post",
        "limit": "Does not model mortgage amortization. Property tax defaults to a simplified flat assumption unless caller overrides it."
    },
    "hdb-resale": {
        "title": "HDB Resale Data",
        "eyebrow": "Public transaction data",
        "summary": "Expose HDB resale transaction search and sampled town-level aggregates from Singapore's official open-data portal.",
        "price": "$0.003 / call",
        "source": "data.gov.sg HDB resale flat prices dataset.",
        "endpoints": ["GET /hdb/towns", "GET /hdb/median", "GET /hdb/median/{town}", "GET /hdb/search"],
        "params": ["town", "flat_type", "min_price", "max_price", "min_floor_area_sqm", "limit"],
        "request": "curl 'https://bountyapi.com/hdb/towns'",
        "response": '{\n  "total_towns": 24,\n  "total_transactions": 4000,\n  "note": "Aggregates are based on a bounded sample..."\n}',
        "try_url": "/hdb/towns",
        "limit": "Current aggregate endpoints are sampled and explicitly labelled. Full-history aggregation is planned with persistent storage."
    },
}


def _catalog_html(slug: str, item: dict) -> str:
    endpoints = "".join(f"<li>{e}</li>" for e in item["endpoints"])
    params = "".join(f"<span>{p}</span>" for p in item["params"])
    return f"""<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>{item['title']} — Bounty API</title>
<meta name=\"description\" content=\"{item['summary']}\">
<link href=\"https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap\" rel=\"stylesheet\">
<style>
:root{{--ink:#171717;--muted:#5f5f5f;--line:rgba(0,0,0,.08);--wash:#fafafa;--green:#0f8a55}}
*{{box-sizing:border-box}}body{{margin:0;font-family:Geist,system-ui,sans-serif;color:var(--ink);background:#fff}}a{{color:inherit;text-decoration:none}}
.nav{{display:flex;justify-content:space-between;align-items:center;padding:16px 28px;box-shadow:rgba(0,0,0,.08) 0 1px 0;position:sticky;top:0;background:rgba(255,255,255,.86);backdrop-filter:blur(16px)}}
.brand{{font-weight:600;letter-spacing:-.03em}}.wrap{{max-width:1080px;margin:0 auto;padding:72px 28px}}.back{{font-size:14px;color:var(--muted)}}
h1{{font-size:clamp(42px,7vw,78px);line-height:.94;letter-spacing:-.06em;margin:22px 0 18px;max-width:840px}}.lead{{font-size:20px;line-height:1.65;color:var(--muted);max-width:760px}}
.badges{{display:flex;gap:9px;flex-wrap:wrap;margin:24px 0}}.badge,.params span{{font:13px 'Geist Mono',monospace;padding:7px 10px;border-radius:999px;background:#f5f5f5;color:#525252}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:34px}}.card{{padding:24px;border-radius:16px;box-shadow:rgba(0,0,0,.08) 0 0 0 1px, rgba(0,0,0,.04) 0 8px 24px -14px;background:#fff}}
h2{{font-size:24px;letter-spacing:-.04em;margin:0 0 14px}}p,li{{color:var(--muted);line-height:1.55}}ul{{padding-left:20px}}.price{{color:var(--green);font:15px 'Geist Mono',monospace}}
pre{{margin:0;white-space:pre-wrap;word-break:break-word;background:#111;color:#e5e5e5;padding:20px;border-radius:14px;font:13px/1.7 'Geist Mono',monospace}}
.cta{{display:inline-flex;margin-top:20px;background:#171717;color:#fff;border-radius:8px;padding:11px 15px;font-size:14px;font-weight:500}}.params{{display:flex;gap:8px;flex-wrap:wrap}}
@media(max-width:820px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body>
<nav class=\"nav\"><a class=\"brand\" href=\"/\">Bounty API</a><a href=\"/docs\">Docs</a></nav>
<main class=\"wrap\">
<a class=\"back\" href=\"/#apis\">← API catalog</a>
<div class=\"badges\"><span class=\"badge\">{item['eyebrow']}</span><span class=\"badge\">{item['price']}</span></div>
<h1>{item['title']}</h1><p class=\"lead\">{item['summary']}</p><a class=\"cta\" href=\"{item['try_url']}\">Try endpoint</a>
<div class=\"grid\">
<section class=\"card\"><h2>Endpoints</h2><ul>{endpoints}</ul></section>
<section class=\"card\"><h2>Parameters</h2><div class=\"params\">{params}</div></section>
<section class=\"card\"><h2>Example request</h2><pre>{item['request']}</pre></section>
<section class=\"card\"><h2>Example response</h2><pre>{item['response']}</pre></section>
<section class=\"card\"><h2>Source</h2><p>{item['source']}</p></section>
<section class=\"card\"><h2>Limitations</h2><p>{item['limit']}</p></section>
</div></main></body></html>"""


@app.get("/apis/{slug}", response_class=HTMLResponse)
async def api_catalog_page(slug: str):
    item = API_CATALOG.get(slug)
    if not item:
        raise HTTPException(status_code=404, detail="API catalog page not found")
    return _catalog_html(slug, item)


@app.get("/api")
async def root():
    """Machine-readable API info."""
    return {
        "name": "Bounty API",
        "version": "2.0.0",
        "description": "Specialist data APIs for AI agents. Pay-per-call, agent-native, globally scalable.",
        "endpoints": {
            "/": "Public landing page",
            "/api": "Machine-readable API info",
            "/stamp-duty": "Full stamp duty calculation (BSD + ABSD)",
            "/bsd": "Buyer's Stamp Duty only",
            "/absd": "Additional Buyer's Stamp Duty only",
            "/docs": "Interactive API documentation (Swagger UI)",
            "/llms.txt": "LLM discovery file",
        },
        "pricing": "Pay-per-call. x402 micropayment support planned.",
        "rate_source": "iras.gov.sg, verified Jan 2026",
    }


@app.post("/stamp-duty", response_model=StampDutyResult)
async def calculate_stamp_duty(req: StampDutyRequest):
    """
    Calculate total stamp duty for a Singapore property purchase.
    Returns BSD (Buyer's Stamp Duty) + ABSD (Additional Buyer's Stamp Duty).

    Rates verified against IRAS (iras.gov.sg):
    - BSD residential: 6-tier marginal (1%-6%), effective 15 Feb 2023
    - BSD non-residential: 4-tier marginal (1%-4%)
    - ABSD: 0%-65% depending on buyer profile, effective 27 Apr 2023
    """
    price = req.price

    # BSD calculation
    tiers = BSD_RESIDENTIAL_TIERS if req.property_type == PropertyType.RESIDENTIAL else BSD_NON_RESIDENTIAL_TIERS
    bsd, bsd_breakdown = calculate_marginal_duty(price, tiers)

    # ABSD calculation (only for residential properties)
    absd = 0
    absd_rate = 0.0
    absd_applicable = False

    if req.property_type == PropertyType.RESIDENTIAL:
        absd_rate = get_absd_rate(req.buyer_profile, req.property_count)
        if absd_rate > 0:
            absd = max(1, math.floor(price * absd_rate))
            absd_applicable = True

    total = bsd + absd
    effective_rate = (total / price * 100) if price > 0 else 0

    return StampDutyResult(
        price=price,
        property_type=req.property_type.value,
        buyer_profile=req.buyer_profile.value,
        property_count=req.property_count,
        bsd=bsd,
        bsd_breakdown=bsd_breakdown,
        absd=absd,
        absd_rate_percent=absd_rate * 100,
        absd_applicable=absd_applicable,
        total_stamp_duty=total,
        effective_rate_percent=round(effective_rate, 2),
        calculation_date=datetime.now().strftime("%Y-%m-%d"),
        source="iras.gov.sg, verified Jan 2026",
    )


@app.get("/bsd")
async def calculate_bsd_get(
    price: float = Query(..., gt=0, description="Property price in SGD"),
    property_type: PropertyType = Query(default=PropertyType.RESIDENTIAL),
):
    """Calculate BSD only (no ABSD). Simple GET endpoint for easy testing."""
    tiers = BSD_RESIDENTIAL_TIERS if property_type == PropertyType.RESIDENTIAL else BSD_NON_RESIDENTIAL_TIERS
    bsd, breakdown = calculate_marginal_duty(price, tiers)

    return {
        "price": price,
        "property_type": property_type.value,
        "bsd": bsd,
        "breakdown": breakdown,
        "source": "iras.gov.sg",
    }


@app.get("/absd")
async def calculate_absd_get(
    price: float = Query(..., gt=0, description="Property price in SGD"),
    buyer_profile: BuyerProfile = Query(default=BuyerProfile.SC),
    property_count: int = Query(default=1, ge=1),
):
    """Calculate ABSD only (no BSD). Simple GET endpoint."""
    rate = get_absd_rate(buyer_profile, property_count)
    absd = max(1, math.floor(price * rate)) if rate > 0 else 0

    return {
        "price": price,
        "buyer_profile": buyer_profile.value,
        "property_count": property_count,
        "absd_rate_percent": rate * 100,
        "absd": absd,
        "applicable": rate > 0,
        "source": "iras.gov.sg",
    }


# ============================================================
# Health check
# ============================================================

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


# ============================================================
# Mount additional API routers
# ============================================================

try:
    from apis.postal_district import router as postal_router
    app.include_router(postal_router)
except ImportError as e:
    print(f"Warning: postal_district router not loaded: {e}")

try:
    from apis.rental_yield import router as rental_router
    app.include_router(rental_router)
except ImportError as e:
    print(f"Warning: rental_yield router not loaded: {e}")

try:
    from apis.hdb_resale import router as hdb_router
    app.include_router(hdb_router)
except ImportError as e:
    print(f"Warning: hdb_resale router not loaded: {e}")


# ============================================================
# llms.txt — AI discovery (the #1 AI SEO priority)
# ============================================================

@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt():
    """llms.txt — structured description for LLM discovery at inference time."""
    return """# Bounty API

> Specialist data APIs for AI agents. Government data, computed financial logic, and market intelligence, structured for autonomous workflows. Pay-per-call via x402 micropayments (USDC on Base). No API keys, no subscriptions. Starting in Singapore, scaling globally.

```json
{
  "name": "Bounty API",
  "url": "https://bountyapi.com",
  "category": "Data API Marketplace",
  "focus": "Global, starting with Singapore",
  "payment_protocol": "x402",
  "discovery_protocol": "MCP",
  "settlement_currency": "USDC",
  "settlement_chain": "Base",
  "live_apis": 4
}
```

## What it is

- A marketplace of specialist data APIs where buyers are AI agents
- Discovery via MCP (single endpoint exposes all APIs as tools)
- Payment via x402 (USDC on Base, sub-second settlement)
- Starting with Singapore data, expanding globally

## Available APIs

### SG Stamp Duty Calculator
- Endpoints: POST /stamp-duty, GET /bsd, GET /absd
- Price: $0.002/call
- Coverage: BSD (6-tier marginal, 1%-6%), ABSD (0%-65% by buyer profile)
- Source: iras.gov.sg, verified Jan 2026

### SG Postal Code to District
- Endpoints: GET /postal/{code}, GET /postal/districts
- Price: $0.001/call
- Coverage: All 28 Singapore postal districts with area names

### SG Rental Yield Calculator
- Endpoints: POST /rental-yield/calculate
- Price: $0.002/call
- Coverage: Gross yield, net yield, cap rate, price-to-rent ratio, cashflow

### HDB Resale Price Data
- Endpoints: GET /hdb/towns, GET /hdb/median/{town}, GET /hdb/search
- Price: $0.003/call
- Coverage: 234K+ HDB resale transactions, all 26 towns, 2017-present
- Source: data.gov.sg (live)

## How AI agents connect

Add to Claude Desktop or any MCP-compatible client:
```json
{
  "mcpServers": {
    "bounty-api": {
      "url": "https://bountyapi.com/mcp"
    }
  }
}
```

## Full documentation

See: https://bountyapi.com/llms-full.txt
"""


@app.get("/llms-full.txt", response_class=PlainTextResponse)
async def llms_full_txt():
    """Full crawlable documentation for LLM training and inference."""
    return """# Bounty API — Full Documentation

## SG Stamp Duty Calculator

### POST /stamp-duty
Request:
{
  "price": 1500000,
  "property_type": "residential",
  "buyer_profile": "SC",
  "property_count": 1
}

Response:
{
  "price": 1500000,
  "bsd": 44600,
  "bsd_breakdown": [
    {"range": "$0 - $180,000", "rate_percent": 1, "taxable_amount": 180000, "duty": 1800},
    {"range": "$180,000 - $360,000", "rate_percent": 2, "taxable_amount": 180000, "duty": 3600},
    {"range": "$360,000 - $1,000,000", "rate_percent": 3, "taxable_amount": 640000, "duty": 19200},
    {"range": "$1,000,000 - $1,500,000", "rate_percent": 4, "taxable_amount": 500000, "duty": 20000}
  ],
  "absd": 0,
  "total_stamp_duty": 44600,
  "effective_rate_percent": 2.97
}

### Buyer profiles: SC (Singapore Citizen), SPR (Permanent Resident), FR (Foreigner), entity, developer, trustee
### ABSD rates: SC 1st=0%, 2nd=20%, 3rd=30%. SPR 1st=5%, 2nd=30%, 3rd=35%. FR=60%. Entity=65%.

## SG Postal Code to District

### GET /postal/{postal_code}
Response:
{
  "postal_code": "238801",
  "sector": "23",
  "district_number": 5,
  "district_name": "D5",
  "general_area": "RCR",
  "areas": ["Buona Vista", "Dover", "Pasir Panjang", "West Coast"]
}

## SG Rental Yield Calculator

### POST /rental-yield/calculate
Request:
{
  "property_price": 1200000,
  "monthly_rent": 4200
}

Response:
{
  "gross_annual_rent": 50400,
  "net_annual_rent": 41328,
  "gross_yield_percent": 4.2,
  "net_yield_percent": 3.44,
  "monthly_cashflow": 3444.0,
  "cap_rate": 3.44,
  "price_to_rent_ratio": 23.81,
  "years_to_break_even": 29.04
}

## HDB Resale Data

### GET /hdb/median/{town}
Response:
{
  "town": "ANG MO KIO",
  "flat_types": [
    {"type": "3 ROOM", "median_price": 350000, "count": 5034, "min_price": 200000, "max_price": 600000},
    {"type": "4 ROOM", "median_price": 520000, "count": 6892, "min_price": 350000, "max_price": 800000}
  ]
}
"""
