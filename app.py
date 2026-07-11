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

from fastapi import FastAPI, Query, HTTPException, Request
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


# Railway terminates TLS — tell Starlette to use https for redirects/proxies
from starlette.middleware.base import BaseHTTPMiddleware

class ProxyProtoMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        proto = request.headers.get("x-forwarded-proto", "")
        if proto:
            request.scope["scheme"] = proto
        return await call_next(request)

app.add_middleware(ProxyProtoMiddleware)

# ============================================================
# Static files — favicons, OG images, public assets
# ============================================================
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os
_static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")

@app.get("/favicon.ico")
async def favicon_ico():
    path = os.path.join(_static_dir, "favicon.ico")
    if os.path.exists(path):
        return FileResponse(path, media_type="image/x-icon")
    raise HTTPException(status_code=404)

@app.get("/site.webmanifest")
async def webmanifest():
    path = os.path.join(_static_dir, "site.webmanifest")
    if os.path.exists(path):
        return FileResponse(path, media_type="application/manifest+json")
    return {"name": "Bounty API", "theme_color": "#171717"}

# ============================================================
# x402 Payment Middleware — agent-native micropayments (USDC on Base)
# ============================================================
try:
    from payment import create_payment_middleware
    create_payment_middleware(app)
except ImportError:
    print("[x402] x402 package not installed — running in free mode")
except Exception as e:
    print(f"[x402] Payment middleware failed: {e}")


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
    """Public marketplace landing page — Linear school + gold accent."""
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Bounty API — Verified Singapore data APIs for AI agents</title>
  <meta name="description" content="31 specialist data APIs for AI agents. URA private transactions, HDB resale, stamp duty, salary benchmarks, property pitch. MCP-native, x402 micropayments. Singapore live now." />
  <link rel="icon" href="/favicon.ico" sizes="any" />
  <link rel="icon" href="/favicon-32x32.png" type="image/png" sizes="32x32" />
  <link rel="icon" href="/favicon-16x16.png" type="image/png" sizes="16x16" />
  <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
  <link rel="manifest" href="/site.webmanifest" />
  <meta property="og:title" content="Bounty API — Verified Singapore data APIs for AI agents" />
  <meta property="og:description" content="31 APIs. URA private property transactions, HDB resale, salary benchmarks, property investment analysis. MCP-native, x402 payments. Singapore live now." />
  <meta property="og:image" content="/og-image.png" />
  <meta property="og:image:width" content="1200" />
  <meta property="og:image:height" content="630" />
  <meta property="og:type" content="website" />
  <meta property="og:url" content="https://bountyapi.com" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="Bounty API — Verified Singapore data APIs for AI agents" />
  <meta name="twitter:description" content="31 APIs. URA private transactions, HDB resale, salary benchmarks, property analysis. MCP + x402." />
  <meta name="twitter:image" content="/twitter-card.png" />
  <meta name="theme-color" content="#08090A" />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    "name": "Bounty API",
    "applicationCategory": "BusinessApplication",
    "applicationSubCategory": "Data API",
    "operatingSystem": "Web",
    "url": "https://bountyapi.com",
    "description": "Specialist data APIs for AI agents. URA private property transactions, HDB resale data, salary benchmarks, property investment analysis. MCP-native with x402 micropayments.",
    "offers": [
      {"@type": "Offer", "name": "Free tier", "price": "0", "priceCurrency": "USD", "description": "19 free endpoints"},
      {"@type": "Offer", "name": "Paid tier", "price": "0.005", "priceCurrency": "USD", "description": "$0.005-$0.10 per call"}
    ]
  }
  </script>
  <style>
    :root {
      --ground: #08090A; --surface-1: #141519; --surface-2: #1C1D22; --surface-3: #26272E;
      --text-primary: #F7F8F8; --text-secondary: #9CA3AF; --text-muted: #6B7280;
      --hairline: rgba(255,255,255,0.06); --hairline-strong: rgba(255,255,255,0.10);
      --accent: #D4A537; --accent-dim: rgba(212,165,55,0.08); --accent-text: #E8C766;
      --free: #3BA55D; --free-dim: rgba(59,165,93,0.10);
      --r-sm: 6px; --r-md: 12px;
      --ease: cubic-bezier(0.22, 1, 0.36, 1); --speed: 150ms;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html { scroll-behavior: smooth; }
    body {
      font-family: 'Geist', system-ui, -apple-system, sans-serif;
      background: var(--ground); color: var(--text-primary);
      -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
      font-size: 15px; line-height: 1.55; font-weight: 400;
    }
    a { color: inherit; text-decoration: none; }
    code, .mono { font-family: 'Geist Mono', ui-monospace, monospace; }
    .nav {
      position: sticky; top: 0; z-index: 50; display: flex; align-items: center; justify-content: space-between;
      padding: 0 32px; height: 56px; background: rgba(8,9,10,0.80);
      backdrop-filter: blur(20px) saturate(180%); -webkit-backdrop-filter: blur(20px) saturate(180%);
      border-bottom: 1px solid var(--hairline);
    }
    .nav-brand { display: flex; align-items: center; gap: 10px; font-weight: 600; font-size: 15px; letter-spacing: -0.02em; }
    .nav-brand img { border-radius: 5px; }
    .nav-links { display: flex; align-items: center; gap: 28px; font-size: 14px; color: var(--text-secondary); font-weight: 400; }
    .nav-links a { transition: color var(--speed) var(--ease); }
    .nav-links a:hover { color: var(--text-primary); }
    .nav-live {
      display: flex; align-items: center; gap: 6px;
      font: 12px 'Geist Mono', monospace; color: var(--text-muted);
      padding-left: 16px; border-left: 1px solid var(--hairline);
    }
    .live-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--free); box-shadow: 0 0 0 0 rgba(59,165,93,0.4); animation: live-pulse 2s infinite; }
    @keyframes live-pulse { 0% { box-shadow: 0 0 0 0 rgba(59,165,93,0.4); } 70% { box-shadow: 0 0 0 6px rgba(59,165,93,0); } 100% { box-shadow: 0 0 0 0 rgba(59,165,93,0); } }
    .btn {
      display: inline-flex; align-items: center; justify-content: center; gap: 8px;
      height: 36px; padding: 0 16px; border-radius: var(--r-sm);
      font-size: 14px; font-weight: 500; cursor: pointer;
      transition: all var(--speed) var(--ease); border: 1px solid transparent;
    }
    .btn-primary { background: var(--text-primary); color: var(--ground); }
    .btn-primary:hover { background: #E5E7EB; }
    .btn-ghost { background: transparent; color: var(--text-secondary); border-color: var(--hairline-strong); }
    .btn-ghost:hover { color: var(--text-primary); border-color: var(--text-muted); }
    .btn-accent { background: var(--accent-dim); color: var(--accent-text); border-color: rgba(212,165,55,0.20); }
    .btn-accent:hover { border-color: var(--accent); }
    .container { max-width: 1080px; margin: 0 auto; padding: 0 32px; }
    .hero { padding: 96px 0 64px; }
    .hero-eyebrow {
      display: inline-flex; align-items: center; gap: 8px;
      font: 13px 'Geist Mono', monospace; color: var(--accent-text);
      background: var(--accent-dim); border: 1px solid rgba(212,165,55,0.15);
      padding: 4px 12px; border-radius: 999px; margin-bottom: 28px;
    }
    .hero h1 { font-size: 56px; font-weight: 600; letter-spacing: -0.03em; line-height: 1.05; max-width: 680px; margin-bottom: 20px; }
    .hero-sub { font-size: 18px; color: var(--text-secondary); line-height: 1.6; max-width: 560px; margin-bottom: 36px; font-weight: 400; }
    .hero-actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    .hero-actions .install {
      font: 13px 'Geist Mono', monospace; color: var(--text-muted);
      background: var(--surface-1); border: 1px solid var(--hairline);
      padding: 8px 14px; border-radius: var(--r-sm);
      display: flex; align-items: center; gap: 8px;
    }
    .hero-actions .install span { color: var(--accent-text); }
    .terminal {
      margin-top: 56px; border-radius: var(--r-md); overflow: hidden;
      border: 1px solid var(--hairline); background: var(--surface-1);
      box-shadow: 0 1px 2px rgba(0,0,0,0.3), 0 24px 48px -24px rgba(0,0,0,0.5);
    }
    .term-header { display: flex; align-items: center; gap: 8px; padding: 12px 16px; border-bottom: 1px solid var(--hairline); font: 12px 'Geist Mono', monospace; color: var(--text-muted); }
    .term-tab { padding: 4px 10px; border-radius: 4px; font-size: 11px; color: var(--text-secondary); }
    .term-tab.active { background: var(--surface-2); color: var(--text-primary); }
    .term-body { padding: 24px; font: 13px/1.7 'Geist Mono', ui-monospace, monospace; color: var(--text-secondary); overflow-x: auto; }
    .term-comment { color: var(--text-muted); }
    .term-prompt { color: var(--accent); }
    .term-key { color: #79C0FF; }
    .term-str { color: #7EE787; }
    .term-src { display: inline-block; font-size: 11px; color: var(--accent-text); background: var(--accent-dim); padding: 1px 6px; border-radius: 3px; margin-left: 4px; }
    .term-free { display: inline-block; font-size: 11px; color: var(--free); background: var(--free-dim); padding: 1px 6px; border-radius: 3px; margin-left: 4px; }
    .section { padding: 80px 0; border-top: 1px solid var(--hairline); }
    .section-label { font: 12px 'Geist Mono', monospace; color: var(--accent-text); letter-spacing: 0.04em; margin-bottom: 12px; }
    .section h2 { font-size: 32px; font-weight: 600; letter-spacing: -0.025em; line-height: 1.15; margin-bottom: 12px; max-width: 520px; }
    .section-desc { color: var(--text-secondary); font-size: 16px; line-height: 1.6; max-width: 520px; margin-bottom: 48px; }
    .api-table { border: 1px solid var(--hairline); border-radius: var(--r-md); overflow: hidden; }
    .api-table-head {
      display: grid; grid-template-columns: 2fr 1fr 100px 80px;
      padding: 12px 20px; background: var(--surface-1); border-bottom: 1px solid var(--hairline);
      font: 11px 'Geist Mono', monospace; color: var(--text-muted); letter-spacing: 0.05em; text-transform: uppercase;
    }
    .api-row {
      display: grid; grid-template-columns: 2fr 1fr 100px 80px;
      padding: 16px 20px; align-items: center; border-bottom: 1px solid var(--hairline);
      transition: background var(--speed) var(--ease);
    }
    .api-row:last-child { border-bottom: none; }
    .api-row:hover { background: var(--surface-1); }
    .api-name { font-weight: 500; font-size: 15px; }
    .api-desc { font-size: 13px; color: var(--text-muted); margin-top: 2px; }
    .api-src { font: 12px 'Geist Mono', monospace; color: var(--accent-text); }
    .api-price { font: 13px 'Geist Mono', monospace; }
    .api-price.free { color: var(--free); }
    .api-price.paid { color: var(--text-primary); }
    .api-status { font: 11px 'Geist Mono', monospace; text-align: right; }
    .api-status .dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--free); margin-right: 6px; }
    .api-more { text-align: center; padding: 20px; border: 1px solid var(--hairline); border-top: none; border-radius: 0 0 var(--r-md) var(--r-md); }
    .api-more a { font-size: 14px; color: var(--text-secondary); transition: color var(--speed) var(--ease); }
    .api-more a:hover { color: var(--accent-text); }
    .steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0; border: 1px solid var(--hairline); border-radius: var(--r-md); overflow: hidden; }
    .step { padding: 32px 28px; border-right: 1px solid var(--hairline); }
    .step:last-child { border-right: none; }
    .step-num { font: 13px 'Geist Mono', monospace; color: var(--accent-text); margin-bottom: 16px; }
    .step h3 { font-size: 16px; font-weight: 600; margin-bottom: 8px; letter-spacing: -0.01em; }
    .step p { font-size: 14px; color: var(--text-secondary); line-height: 1.5; }
    .split { display: grid; grid-template-columns: 1fr 1fr; gap: 0; border: 1px solid var(--hairline); border-radius: var(--r-md); overflow: hidden; }
    .split-left { padding: 40px; border-right: 1px solid var(--hairline); }
    .split-right { background: var(--surface-1); padding: 0; font: 13px/1.65 'Geist Mono', monospace; overflow-x: auto; }
    .split-right pre { padding: 24px; color: var(--text-secondary); }
    .split-left h3 { font-size: 22px; font-weight: 600; letter-spacing: -0.02em; margin-bottom: 12px; }
    .split-left > p { color: var(--text-secondary); font-size: 15px; line-height: 1.6; margin-bottom: 28px; }
    .feature-list { display: flex; flex-direction: column; gap: 20px; }
    .feature { display: flex; gap: 12px; align-items: flex-start; }
    .feature-icon { width: 20px; height: 20px; flex: 0 0 auto; border-radius: var(--r-sm); border: 1px solid var(--accent); display: grid; place-items: center; font-size: 11px; color: var(--accent-text); margin-top: 1px; }
    .feature strong { display: block; font-size: 14px; font-weight: 500; margin-bottom: 2px; }
    .feature span { font-size: 13px; color: var(--text-muted); line-height: 1.45; }
    .directory-row { display: flex; align-items: center; gap: 32px; flex-wrap: wrap; padding: 24px 0; border-top: 1px solid var(--hairline); }
    .directory-label { font: 12px 'Geist Mono', monospace; color: var(--text-muted); }
    .directory-items { display: flex; gap: 24px; flex-wrap: wrap; }
    .directory-item { font: 14px 'Geist', sans-serif; font-weight: 500; color: var(--text-secondary); transition: color var(--speed) var(--ease); }
    .directory-item:hover { color: var(--text-primary); }
    .footer { border-top: 1px solid var(--hairline); padding: 48px 0 40px; }
    .footer-inner { display: grid; grid-template-columns: 2fr 1fr 1fr 1fr; gap: 40px; }
    .footer-brand { display: flex; align-items: center; gap: 10px; font-weight: 600; font-size: 14px; margin-bottom: 10px; letter-spacing: -0.02em; }
    .footer-tag { font-size: 13px; color: var(--text-muted); line-height: 1.5; max-width: 280px; }
    .footer-col h4 { font: 11px 'Geist Mono', monospace; color: var(--text-muted); letter-spacing: 0.05em; text-transform: uppercase; margin-bottom: 14px; }
    .footer-col a { display: block; font-size: 14px; color: var(--text-secondary); margin-bottom: 8px; transition: color var(--speed) var(--ease); }
    .footer-col a:hover { color: var(--text-primary); }
    .footer-bottom { padding: 24px 0 0; border-top: 1px solid var(--hairline); margin-top: 40px; display: flex; justify-content: space-between; font: 12px 'Geist Mono', monospace; color: var(--text-muted); }
    @media (max-width: 768px) {
      .hero h1 { font-size: 36px; } .hero-sub { font-size: 16px; }
      .steps { grid-template-columns: 1fr; }
      .step { border-right: none; border-bottom: 1px solid var(--hairline); }
      .split { grid-template-columns: 1fr; }
      .split-left { border-right: none; border-bottom: 1px solid var(--hairline); }
      .footer-inner { grid-template-columns: 1fr; }
      .nav-links { display: none; }
      .api-table-head { display: none; }
      .api-row { grid-template-columns: 1fr; gap: 4px; }
    }
  </style>
</head>
<body>
  <nav class="nav">
    <a class="nav-brand" href="/"><img src="/logo-mark-dark.png" alt="Bounty" width="22" height="22"><span>Bounty</span></a>
    <div style="display:flex;align-items:center;gap:24px">
      <div class="nav-links">
        <a href="#apis">APIs</a>
        <a href="/pricing">Pricing</a>
        <a href="/setup">Setup</a>
        <a href="/docs">Docs</a>
      </div>
      <div class="nav-live"><span class="live-dot"></span><span>31 APIs &middot; LIVE</span></div>
    </div>
  </nav>

  <div class="container">
    <div class="hero">
      <div class="hero-eyebrow">Singapore &middot; MCP-native &middot; x402</div>
      <h1>Verified data APIs<br>for AI agents.</h1>
      <p class="hero-sub">Property transactions, salary benchmarks, tax calculators, and investment analysis. Every response carries source provenance. Agents discover via MCP and pay per call in USDC.</p>
      <div class="hero-actions">
        <a class="btn btn-primary" href="/setup">Get started</a>
        <div class="install"><span>$</span> npx bountyapi-mcp</div>
      </div>
      <div class="terminal">
        <div class="term-header"><div class="term-tab active">bountyapi-mcp</div><div class="term-tab">stdout</div></div>
        <div class="term-body">
<span class="term-comment"># Agent calls stamp duty calculator</span>
<span class="term-prompt">&gt;</span> bounty_stamp_duty(price=1_000_000)

{
  <span class="term-key">"price"</span>: 1000000,
  <span class="term-key">"bsd"</span>: 24600,
  <span class="term-key">"absd"</span>: 0,
  <span class="term-key">"total"</span>: 24600 <span class="term-free">FREE</span> <span class="term-src">src: iras.gov.sg</span>
}

<span class="term-comment"># Agent queries URA private transactions</span>
<span class="term-prompt">&gt;</span> bounty_ura_transactions(area="orchard")

{
  <span class="term-key">"records"</span>: 293,
  <span class="term-key">"median_psf"</span>: 2847,
  <span class="term-key">"sample"</span>: [
    { <span class="term-key">"project"</span>: <span class="term-str">"ION ORCHARD"</span>, <span class="term-key">"price"</span>: 3850000, <span class="term-key">"psf"</span>: 3128 }
  ] <span class="term-src">src: ura.gov.sg</span>
}

<span class="term-comment"># 19 free endpoints. 12 paid ($0.005-$0.10).</span>
<span class="term-comment"># Every value traces to a primary source.</span>
        </div>
      </div>
    </div>
  </div>

  <div class="container">
    <section class="section" id="apis">
      <div class="section-label">// API CATALOG</div>
      <h2>31 endpoints. Every value sourced.</h2>
      <p class="section-desc">No interpolated data. No fabricated values. Every response links to its primary source.</p>
      <div class="api-table">
        <div class="api-table-head"><div>Endpoint</div><div>Source</div><div>Price</div><div>Status</div></div>
        <div class="api-row"><div><div class="api-name">URA Private Transactions</div><div class="api-desc">Caveat-level transaction records. Price, PSF, tenure, area.</div></div><div class="api-src">ura.gov.sg</div><div class="api-price paid">$0.01</div><div class="api-status"><span class="dot"></span>293 records</div></div>
        <div class="api-row"><div><div class="api-name">Salary Benchmark</div><div class="api-desc">Real salary ranges from MyCareersFuture listings.</div></div><div class="api-src">mycareersfuture.gov.sg</div><div class="api-price free">FREE</div><div class="api-status"><span class="dot"></span>live</div></div>
        <div class="api-row"><div><div class="api-name">Stamp Duty (BSD + ABSD)</div><div class="api-desc">Buyer's and Additional Buyer's Stamp Duty. Verified against IRAS.</div></div><div class="api-src">iras.gov.sg</div><div class="api-price free">FREE</div><div class="api-status"><span class="dot"></span>verified</div></div>
        <div class="api-row"><div><div class="api-name">Property Pitch</div><div class="api-desc">Full investment thesis: URA data + yield + stamp duty + affordability.</div></div><div class="api-src">composite</div><div class="api-price paid">$0.05</div><div class="api-status"><span class="dot"></span>decision-grade</div></div>
        <div class="api-row"><div><div class="api-name">HDB Resale Search</div><div class="api-desc">Search resale transactions by town, flat type, price range.</div></div><div class="api-src">data.gov.sg</div><div class="api-price paid">$0.01</div><div class="api-status"><span class="dot"></span>26 towns</div></div>
        <div class="api-row"><div><div class="api-name">Schools Nearby</div><div class="api-desc">Primary/secondary schools within 1km and 2km of any postal code.</div></div><div class="api-src">openstreetmap</div><div class="api-price free">FREE</div><div class="api-status"><span class="dot"></span>294 schools</div></div>
        <div class="api-row"><div><div class="api-name">Income Tax Calculator</div><div class="api-desc">Resident and non-resident SG income tax. Progressive tiers.</div></div><div class="api-src">iras.gov.sg</div><div class="api-price free">FREE</div><div class="api-status"><span class="dot"></span>verified</div></div>
        <div class="api-row"><div><div class="api-name">URA Rental Median</div><div class="api-desc">Median rental PSF by project and quarter.</div></div><div class="api-src">ura.gov.sg</div><div class="api-price paid">$0.01</div><div class="api-status"><span class="dot"></span>917 records</div></div>
        <div class="api-more"><a href="/docs">View all 31 endpoints &rarr;</a></div>
      </div>
    </section>
  </div>

  <div class="container">
    <section class="section">
      <div class="section-label">// HOW IT WORKS</div>
      <h2>Install. Discover. Pay per call.</h2>
      <p class="section-desc">No API keys. No billing dashboard. Agents handle everything autonomously.</p>
      <div class="steps">
        <div class="step"><div class="step-num">01</div><h3>Install the MCP server</h3><p>Add Bounty to any MCP-compatible agent. Works with Claude Desktop, Cursor, Hermes, and any MCP client.</p></div>
        <div class="step"><div class="step-num">02</div><h3>Agent discovers tools</h3><p>Your agent sees all 31 endpoints as MCP tools and picks the right one based on context.</p></div>
        <div class="step"><div class="step-num">03</div><h3>Pay per call in USDC</h3><p>Paid endpoints settle via x402 on Base. Free endpoints cost nothing, forever.</p></div>
      </div>
    </section>
  </div>

  <div class="container">
    <section class="section">
      <div class="section-label">// PAYMENTS</div>
      <h2>x402: agents pay autonomously.</h2>
      <p class="section-desc">AI agents cannot fill forms, enter credit cards, or sign contracts. x402 lets them pay per request with USDC on Base.</p>
      <div class="split">
        <div class="split-left">
          <div class="feature-list">
            <div class="feature"><div class="feature-icon">$</div><div><strong>Per-call pricing</strong><span>Free for commoditized data. $0.005 to $0.10 for decision-grade endpoints. No subscriptions.</span></div></div>
            <div class="feature"><div class="feature-icon">$</div><div><strong>MCP-native discovery</strong><span>Agents see all 31 tools automatically via stdio and HTTP transport.</span></div></div>
            <div class="feature"><div class="feature-icon">$</div><div><strong>Source-forward data</strong><span>Every response carries provenance. No interpolated or fabricated values. Ever.</span></div></div>
            <div class="feature"><div class="feature-icon">$</div><div><strong>Provider marketplace</strong><span>Publish your own APIs. Keep 97% of revenue. We handle payments and discovery.</span></div></div>
          </div>
          <div style="margin-top:28px;display:flex;gap:12px;flex-wrap:wrap">
            <a class="btn btn-accent" href="/setup">Agent setup guide</a>
            <a class="btn btn-ghost" href="/providers">Become a provider</a>
          </div>
        </div>
        <div class="split-right">
<pre><span class="term-comment"># Install</span>
<span class="term-prompt">$</span> npm install bountyapi-mcp

<span class="term-comment"># Add to agent config</span>
{
  <span class="term-key">"mcpServers"</span>: {
    <span class="term-key">"bounty"</span>: {
      <span class="term-key">"command"</span>: <span class="term-str">"bountyapi-mcp"</span>
    }
  }
}

<span class="term-comment"># 31 tools available immediately</span>
<span class="term-comment"># 19 free. 12 paid ($0.005-$0.10)</span>
<span class="term-comment"># Agents pay autonomously via x402</span>
<span class="term-comment"># Settlement: USDC on Base, &lt;$0.001 fee</span>

<span class="term-comment"># Sources:</span>
<span class="term-comment">#   ura.gov.sg, iras.gov.sg,</span>
<span class="term-comment">#   data.gov.sg, mycareersfuture,</span>
<span class="term-comment">#   openstreetmap, ecb.europa.eu</span></pre>
        </div>
      </div>
    </section>
  </div>

  <div class="container">
    <section class="section" style="padding-bottom:0">
      <div class="directory-row">
        <span class="directory-label">Listed on</span>
        <div class="directory-items">
          <a class="directory-item" href="https://mcp.so">mcp.so</a>
          <a class="directory-item" href="https://glama.ai">Glama</a>
          <a class="directory-item" href="https://pulsemcp.com">PulseMCP</a>
          <a class="directory-item" href="https://www.npmjs.com/package/bountyapi-mcp">npm</a>
        </div>
      </div>
      <div class="directory-row">
        <span class="directory-label">Works with</span>
        <div class="directory-items">
          <span class="directory-item">Claude Desktop</span>
          <span class="directory-item">Cursor</span>
          <span class="directory-item">Hermes</span>
          <span class="directory-item">LangChain</span>
        </div>
      </div>
    </section>
  </div>

  <div class="container">
    <footer class="footer">
      <div class="footer-inner">
        <div>
          <div class="footer-brand"><img src="/logo-mark-dark.png" alt="Bounty" width="20" height="20"><span>Bounty</span></div>
          <p class="footer-tag">Specialist data APIs for AI agents. MCP-native, x402 payments. Singapore live now.</p>
        </div>
        <div class="footer-col"><h4>Product</h4><a href="#apis">APIs</a><a href="/pricing">Pricing</a><a href="/docs">Docs</a><a href="/setup">Setup</a></div>
        <div class="footer-col"><h4>Build</h4><a href="/providers">Publish API</a><a href="/llms.txt">llms.txt</a><a href="https://www.npmjs.com/package/bountyapi-mcp">npm</a><a href="https://github.com/vncent786/bounty-api">GitHub</a></div>
        <div class="footer-col"><h4>Protocol</h4><a href="https://x402.org">x402</a><a href="https://modelcontextprotocol.io">MCP</a></div>
      </div>
      <div class="footer-bottom"><span>Bounty API &middot; Singapore</span><span>v1.8.0 &middot; 31 APIs &middot; 27 MCP tools</span></div>
    </footer>
  </div>
</body>
</html>"""


API_CATALOG = {
    "stamp-duty": {
        "title": "SG Stamp Duty Calculator",
        "eyebrow": "Verified property-tax logic",
        "summary": "Calculate Singapore BSD and ABSD for property purchases with source-forward outputs and tier breakdowns.",
        "price": "FREE",
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
        "price": "FREE",
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
        "price": "$0.005 / call",
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
        "price": "$0.01 / call",
        "source": "data.gov.sg HDB resale flat prices dataset.",
        "endpoints": ["GET /hdb/towns", "GET /hdb/median", "GET /hdb/median/{town}", "GET /hdb/search"],
        "params": ["town", "flat_type", "min_price", "max_price", "min_floor_area_sqm", "limit"],
        "request": "curl 'https://bountyapi.com/hdb/towns'",
        "response": '{\n  "total_towns": 24,\n  "total_transactions": 4000,\n  "note": "Aggregates are based on a bounded sample..."\n}',
        "try_url": "/hdb/towns",
        "limit": "Current aggregate endpoints are sampled and explicitly labelled. Full-history aggregation is planned with persistent storage."
    },
    "mortgage": {
        "title": "Mortgage Calculator",
        "eyebrow": "Pure math financial primitive",
        "summary": "Calculate monthly mortgage payments, total interest, and amortization schedules for any loan worldwide.",
        "price": "FREE",
        "source": "Calculated from standard amortization formula. No external data dependency.",
        "endpoints": ["POST /mortgage/calculate"],
        "params": ["principal", "annual_interest_rate", "loan_term_years", "down_payment"],
        "request": "curl -X POST 'https://bountyapi.com/mortgage/calculate' -H 'content-type: application/json' -d '{\"principal\":200000,\"annual_interest_rate\":6.5,\"loan_term_years\":30}'",
        "response": '{\n  "monthly_payment": 1264.14,\n  "total_interest": 255089.78,\n  "total_paid": 455089.78\n}',
        "try_url": "/docs#/mortgage/calculate_mortgage_calculate_post",
        "limit": "Amortization schedule returns first 12 and last 12 months to keep response compact."
    },
    "compound": {
        "title": "Investment Growth Calculator",
        "eyebrow": "Pure math investment primitive",
        "summary": "Project compound interest growth with periodic contributions. Supports compound and simple interest, multiple compounding frequencies.",
        "price": "FREE",
        "source": "Calculated from standard compound interest formulas. No external data dependency.",
        "endpoints": ["POST /invest/calculate"],
        "params": ["principal", "annual_rate", "years", "contribution_monthly", "contribution_frequency", "interest_type", "compounding_frequency"],
        "request": "curl -X POST 'https://bountyapi.com/invest/calculate' -H 'content-type: application/json' -d '{\"principal\":10000,\"annual_rate\":7,\"years\":10}'",
        "response": '{\n  "final_balance": 20096.61,\n  "total_interest_earned": 10096.61,\n  "multiplier": 2.01\n}',
        "try_url": "/docs#/invest/calculate_investment_calculate_post",
        "limit": "Monthly compounding by default. Supports annual, quarterly, daily, and continuous compounding."
    },
    "currency": {
        "title": "Currency Converter",
        "eyebrow": "Live exchange rates",
        "summary": "Convert between 30+ currencies using live ECB reference rates. Cached hourly for fast response.",
        "price": "FREE",
        "source": "European Central Bank reference rates via frankfurter.app. Updated daily on business days.",
        "endpoints": ["GET /currency/convert", "GET /currency/rates", "GET /currency/supported"],
        "params": ["from", "to", "amount", "base"],
        "request": "curl 'https://bountyapi.com/currency/convert?from=USD&to=SGD&amount=100'",
        "response": '{\n  "from_currency": "USD",\n  "to_currency": "SGD",\n  "amount": 100,\n  "rate": 1.2905,\n  "result": 129.05\n}',
        "try_url": "/currency/convert?from=USD&to=SGD&amount=100",
        "limit": "Rates are ECB reference rates, updated on business days. Not real-time market rates."
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
# MCP Streamable HTTP transport — lets ChatGPT/web clients connect
# ============================================================

def _build_mcp_http_server():
    """Build a FastMCP server that exposes our API as MCP tools over HTTP."""
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    import httpx

    API_BASE = "https://bountyapi.com"
    mcp_server = FastMCP(
        "bountyapi",
        host="0.0.0.0",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["bountyapi.com", "localhost:*", "127.0.0.1:*", "[::1]:*"],
            allowed_origins=["https://bountyapi.com", "http://localhost:*", "http://127.0.0.1:*", "http://[::1]:*"],
        ),
    )
    mcp_server.settings.streamable_http_path = "/"

    @mcp_server.tool()
    async def sg_stamp_duty(
        price: float,
        property_type: str = "residential",
        buyer_profile: str = "SC",
        property_count: int = 1,
    ) -> str:
        """Calculate Singapore property stamp duty (BSD + ABSD).
        buyer_profile: SC (citizen), SPR (PR), FR (foreigner), entity, developer.
        Returns total duty, tier breakdown, and effective rate."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{API_BASE}/stamp-duty",
                json={
                    "price": price,
                    "property_type": property_type,
                    "buyer_profile": buyer_profile,
                    "property_count": property_count,
                },
            )
            return r.text

    @mcp_server.tool()
    async def sg_postal_lookup(postal_code: str) -> str:
        """Look up a Singapore 6-digit postal code to find its district number,
        district name, and area classification (CCR/RCR/OCR)."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API_BASE}/postal/{postal_code}")
            return r.text

    @mcp_server.tool()
    async def sg_address_intel(postal_code: str) -> str:
        """Full address intelligence: district, planning area, CCR/RCR/OCR region,
        HDB town, approximate coordinates, and 5 nearest MRT stations with walking distance."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API_BASE}/address/{postal_code}")
            return r.text

    @mcp_server.tool()
    async def sg_mrt_near(postal_code: str, limit: int = 5) -> str:
        """Find nearest MRT stations to a Singapore postal code.
        Returns station name, lines, distance, and walking time."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API_BASE}/mrt/near/{postal_code}?limit={limit}")
            return r.text

    @mcp_server.tool()
    async def sg_mrt_search(q: str, limit: int = 10) -> str:
        """Search MRT stations by name. Returns all matching stations with line codes."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API_BASE}/mrt/search?q={q}&limit={limit}")
            return r.text

    @mcp_server.tool()
    async def sg_affordability(
        monthly_income: float,
        property_price: float,
        loan_type: str = "bank_private",
        existing_monthly_debt: float = 0,
        loan_tenure_years: int = 30,
        borrower_age: int = 35,
        housing_loan_count: int = 1,
    ) -> str:
        """Calculate MAS TDSR/MSR affordability. Checks if a property is affordable
        under Singapore's mortgage regulations. Returns max loan and property price."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{API_BASE}/affordability/calculate",
                json={
                    "monthly_income": monthly_income,
                    "property_price": property_price,
                    "loan_type": loan_type,
                    "existing_monthly_debt": existing_monthly_debt,
                    "loan_tenure_years": loan_tenure_years,
                    "borrower_age": borrower_age,
                    "housing_loan_count": housing_loan_count,
                },
            )
            return r.text

    @mcp_server.tool()
    async def sg_property_analyze(
        property_price: float,
        property_type: str = "hdb",
        region: str = "SG",
        town: str = "",
        flat_type: str = "",
        postal_code: str = "",
        monthly_rent: float = 0,
        buyer_profile: str = "SC",
        property_count: int = 1,
        monthly_income: float = 0,
        existing_monthly_debt: float = 0,
        loan_tenure_years: int = 30,
        borrower_age: int = 35,
    ) -> str:
        """Complete property investment analysis: stamp duty, comparables, yield,
        affordability, and location in one call. The most comprehensive SG property API."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{API_BASE}/property/analyze",
                json={
                    "property_type": property_type,
                    "region": region,
                    "property_price": property_price,
                    "town": town,
                    "flat_type": flat_type,
                    "postal_code": postal_code,
                    "monthly_rent": monthly_rent,
                    "buyer_profile": buyer_profile,
                    "property_count": property_count,
                    "monthly_income": monthly_income,
                    "existing_monthly_debt": existing_monthly_debt,
                    "loan_tenure_years": loan_tenure_years,
                    "borrower_age": borrower_age,
                },
            )
            return r.text

    @mcp_server.tool()
    async def sg_property_rank(
        candidates: List[dict],
        region: str = "SG",
        buyer_profile: str = "SC",
        monthly_income: float = 0,
        existing_monthly_debt: float = 0,
    ) -> str:
        """Rank candidate Singapore properties by investment value.
        Accepts properties from any source, then scores value vs comps, yield,
        affordability, and location. Region-ready: SG live, HK/AE/AU/JP planned."""
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{API_BASE}/property/rank",
                json={
                    "candidates": candidates,
                    "region": region,
                    "buyer_profile": buyer_profile,
                    "monthly_income": monthly_income if monthly_income else None,
                    "existing_monthly_debt": existing_monthly_debt,
                },
            )
            return r.text

    @mcp_server.tool()
    async def sg_property_pitch(
        property_price: float,
        property_type: str = "hdb",
        town: str = "",
        flat_type: str = "",
        project_name: str = "",
        postal_code: str = "",
        sqft: float = 0,
        monthly_rent: float = 0,
        tenure: str = "",
        top_year: int = 0,
        buyer_profile: str = "SC",
        property_count: int = 1,
        monthly_income: float = 0,
        existing_monthly_debt: float = 0,
        buyer_notes: str = "",
    ) -> str:
        """Generate a complete property investment pitch — the kind of one-page
        analysis a property agent presents to a client. Includes price fairness,
        stamp duty, affordability, yield, location, tenure risk, and verdict."""
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{API_BASE}/property/pitch",
                json={
                    "property_type": property_type,
                    "property_price": property_price,
                    "town": town,
                    "flat_type": flat_type,
                    "project_name": project_name,
                    "postal_code": postal_code,
                    "sqft": sqft if sqft else None,
                    "monthly_rent": monthly_rent if monthly_rent else None,
                    "tenure": tenure if tenure else None,
                    "top_year": top_year if top_year else None,
                    "buyer_profile": buyer_profile,
                    "property_count": property_count,
                    "monthly_income": monthly_income if monthly_income else None,
                    "existing_monthly_debt": existing_monthly_debt,
                    "buyer_notes": buyer_notes if buyer_notes else None,
                },
            )
            return r.text

    @mcp_server.tool()
    async def sg_rental_yield(
        property_price: float,
        monthly_rent: float,
        annual_expenses: float = 0,
    ) -> str:
        """Calculate rental investment metrics: gross yield, net yield, cap rate,
        price-to-rent ratio, and monthly cashflow."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{API_BASE}/rental-yield/calculate",
                json={
                    "property_price": property_price,
                    "monthly_rent": monthly_rent,
                    "annual_expenses": annual_expenses,
                },
            )
            return r.text

    @mcp_server.tool()
    async def sg_income_tax(
        annual_income: float,
        deductions: float = 0,
        reliefs: float = 0,
        is_resident: bool = True,
    ) -> str:
        """Calculate Singapore individual income tax. Resident progressive rates
        (YA 2024+): 0-22% marginal. Non-residents: 15% flat or progressive,
        whichever higher. Free."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{API_BASE}/tax/income",
                params={
                    "annual_income": annual_income,
                    "deductions": deductions,
                    "reliefs": reliefs,
                    "is_resident": str(is_resident).lower(),
                },
            )
            return r.text

    @mcp_server.tool()
    async def sg_gst(
        amount: float,
        mode: str = "add",
    ) -> str:
        """Add or remove Singapore GST (9% from 1 Jan 2024).
        mode='add': calculate GST on a GST-exclusive price.
        mode='remove': extract GST component from a GST-inclusive price.
        Free."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{API_BASE}/gst",
                params={"amount": amount, "mode": mode},
            )
            return r.text

    @mcp_server.tool()
    async def sg_property_commission(
        transaction_type: str,
        property_type: str = "hdb",
        price: float = 0,
        is_seller_landlord: bool = True,
    ) -> str:
        """Estimate Singapore property agent commission.
        transaction_type: 'sale' or 'rental'. property_type: 'hdb', 'private', 'landed'.
        Rates are market norms (CEA), not legally fixed. Free."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{API_BASE}/commission",
                params={
                    "transaction_type": transaction_type,
                    "property_type": property_type,
                    "price": price,
                    "is_seller_landlord": str(is_seller_landlord).lower(),
                },
            )
            return r.text

    @mcp_server.tool()
    async def sg_cpf_housing(
        monthly_income: float,
        age: int,
        existing_oa_balance: float = 0,
    ) -> str:
        """Estimate CPF Ordinary Account accumulation for housing use.
        Shows monthly OA contribution, 3-year and 5-year projected balances.
        Free."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{API_BASE}/cpf/housing",
                params={
                    "monthly_income": monthly_income,
                    "age": age,
                    "existing_oa_balance": existing_oa_balance,
                },
            )
            return r.text

    @mcp_server.tool()
    async def sg_salary_benchmark(
        role: str,
        limit: int = 100,
    ) -> str:
        """Benchmark salary for a Singapore role using live MyCareersFuture job postings.
        Returns median, percentile ranges, and annual equivalents from real employer-posted
        salary data. Free."""
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"{API_BASE}/salary/search",
                params={"role": role, "limit": limit},
            )
            return r.text

    @mcp_server.tool()
    async def sg_property_tax(
        annual_value: float,
        property_type: str = "residential",
        is_owner_occupied: bool = True,
    ) -> str:
        """Calculate Singapore property tax. Owner-occupier progressive 0-32%,
        non-owner-occupied 10-20%, non-residential flat 10%. Free."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{API_BASE}/property-tax",
                params={
                    "annual_value": annual_value,
                    "property_type": property_type,
                    "is_owner_occupied": str(is_owner_occupied).lower(),
                },
            )
            return r.text

    @mcp_server.tool()
    async def sg_buy_vs_rent(
        property_price: float,
        monthly_rent: float,
        holding_period_years: int = 10,
        mortgage_rate: float = 2.6,
        down_payment_pct: float = 25,
    ) -> str:
        """Buy-vs-rent analysis: total cost of buying vs renting over a holding period.
        Includes mortgage, stamp duty, property tax, maintenance, opportunity cost.
        Returns net cost comparison and recommendation. Free."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{API_BASE}/buy-vs-rent",
                params={
                    "property_price": property_price,
                    "monthly_rent": monthly_rent,
                    "holding_period_years": holding_period_years,
                    "mortgage_rate": mortgage_rate,
                    "down_payment_pct": down_payment_pct,
                },
            )
            return r.text

    @mcp_server.tool()
    async def sg_school_proximity(
        postal_code: str,
        radius_km: float = 2.0,
        school_type: str = "",
    ) -> str:
        """Find primary/secondary schools near a Singapore postal code.
        Splits schools within 1km and 1-2km for Primary 1 priority analysis. Free."""
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"{API_BASE}/schools/near/{postal_code}",
                params={"radius_km": radius_km, "school_type": school_type},
            )
            return r.text

    @mcp_server.tool()
    async def hdb_eip_quota(
        town: str,
        buyer_ethnicity: str,
        is_spr: bool = False,
        is_malaysian_spr: bool = False,
    ) -> str:
        """Explain HDB Ethnic Integration Policy (EIP) and SPR quota limits.
        Real-time HDB quota requires portal verification, but this returns official quota rules and transaction risk. Free."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{API_BASE}/hdb/eip/{town}",
                params={
                    "buyer_ethnicity": buyer_ethnicity,
                    "is_spr": str(is_spr).lower(),
                    "is_malaysian_spr": str(is_malaysian_spr).lower(),
                },
            )
            return r.text

    @mcp_server.tool()
    async def hdb_lease_decay(
        lease_commencement_year: int,
        current_value: float = 0,
    ) -> str:
        """Analyze HDB lease decay: remaining years, financing/CPF restrictions, risk thresholds, and value impact. Free."""
        params = {"lease_commencement_year": lease_commencement_year}
        if current_value > 0:
            params["current_value"] = current_value
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API_BASE}/hdb/lease-decay", params=params)
            return r.text

    @mcp_server.tool()
    async def ura_status() -> str:
        """Check if URA private property data is connected and available. Free."""
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{API_BASE}/ura/status")
            return r.text

    @mcp_server.tool()
    async def ura_transactions(
        batch: int = 1,
    ) -> str:
        """Get private residential property transactions (caveat data) from URA.
        Returns project name, price, PSF, area, tenure, transaction date, sale type.
        Batch 1 is most recent. $0.05/call."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{API_BASE}/ura/transactions", params={"batch": batch})
            return r.text

    @mcp_server.tool()
    async def ura_rental_median(
        project_name: str = "",
    ) -> str:
        """Get median rental rates ($psf/month) for private residential projects from URA.
        Filter by project name or get all. $0.05/call."""
        params = {}
        if project_name:
            params["project_name"] = project_name
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{API_BASE}/ura/rental-median", params=params or None)
            return r.text

    @mcp_server.tool()
    async def ura_developer_sales(
        ref_period: str = "",
    ) -> str:
        """Get developer units sold by project from URA. Units launched, sold, remaining, median price.
        Leave ref_period empty for latest, or use format like '2506' for Jun 2025. $0.05/call."""
        params = {}
        if ref_period:
            params["ref_period"] = ref_period
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{API_BASE}/ura/developer-sales", params=params or None)
            return r.text

    @mcp_server.tool()
    async def ura_pipeline() -> str:
        """Get future private residential supply pipeline from URA.
        Upcoming projects, units planned, expected completion. Key for supply analysis. $0.05/call."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{API_BASE}/ura/pipeline")
            return r.text

    @mcp_server.tool()
    async def hdb_resale_median(town: str) -> str:
        """Get HDB resale median prices by flat type for a Singapore town.
        Returns median price, count, min, max for each flat type."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API_BASE}/hdb/median/{town}")
            return r.text

    @mcp_server.tool()
    async def hdb_resale_search(
        town: str = "",
        flat_type: str = "",
        min_price: float = 0,
        max_price: float = 0,
        limit: int = 10,
    ) -> str:
        """Search HDB resale transactions with filters.
        Leave params empty for broad results."""
        params = {"limit": limit}
        if town:
            params["town"] = town
        if flat_type:
            params["flat_type"] = flat_type
        if min_price:
            params["min_price"] = min_price
        if max_price:
            params["max_price"] = max_price
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API_BASE}/hdb/search", params=params)
            return r.text

    return mcp_server


# Mount MCP HTTP transport at /agent — use route delegation instead of mount
try:
    _mcp_http = _build_mcp_http_server()
    _mcp_starlette_app = _mcp_http.streamable_http_app()
    _mcp_asgi_handler = _mcp_starlette_app.build_middleware_stack()
    _mcp_session_manager = _mcp_http._session_manager
    _mcp_lifespan_cm = None

    @app.on_event("startup")
    async def _start_mcp_session():
        global _mcp_lifespan_cm
        _mcp_lifespan_cm = _mcp_session_manager.run()
        await _mcp_lifespan_cm.__aenter__()

    @app.on_event("shutdown")
    async def _stop_mcp_session():
        if _mcp_lifespan_cm:
            await _mcp_lifespan_cm.__aexit__(None, None, None)

    @app.api_route("/mcp", methods=["GET", "POST", "DELETE"])
    async def mcp_endpoint(request: Request):
        """MCP Streamable HTTP endpoint."""
        # Strip /mcp from the path so the sub-app sees /
        scope = dict(request.scope)
        scope["path"] = "/"
        scope["raw_path"] = b"/"

        async def receive():
            return await request.receive()

        from starlette.responses import Response
        import io

        # Collect the response from the MCP ASGI app
        response_started = False
        status_code = 200
        headers = []
        body_chunks = []

        async def send(message):
            nonlocal response_started, status_code, headers
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                headers = message.get("headers", [])
            elif message["type"] == "http.response.body":
                body_chunks.append(message.get("body", b""))

        await _mcp_asgi_handler(scope, receive, send)

        # Build response
        body = b"".join(body_chunks)
        response_headers = [
            (k.decode() if isinstance(k, bytes) else k,
             v.decode() if isinstance(v, bytes) else v)
            for k, v in headers
        ]
        return Response(
            content=body,
            status_code=status_code,
            headers=dict(response_headers),
        )

    print("MCP HTTP transport available at /mcp")
except Exception as e:
    print(f"Warning: MCP HTTP transport not available: {e}")

# ============================================================
# Health check
# ============================================================

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


# ============================================================
# Anonymous MCP install ping — tracks real installs vs npm bots
# ============================================================

_mcp_install_log = []


@app.get("/ping")
async def mcp_ping(
    version: str = Query(default="unknown"),
    client: str = Query(default="unknown"),
):
    """Anonymous install/usage ping from MCP server startup.

    Called by bountyapi-mcp on launch to track real installs.
    No user data, no wallet address, just version + client type.
    """
    global _mcp_install_log
    from datetime import datetime as dt
    entry = {
        "version": version,
        "client": client,
        "timestamp": dt.now().isoformat(),
    }
    _mcp_install_log.append(entry)
    # Keep last 10000 entries
    if len(_mcp_install_log) > 10000:
        _mcp_install_log = _mcp_install_log[-10000:]
    return {"status": "ok", "version": version}


@app.get("/ping/stats")
async def mcp_ping_stats():
    """Anonymous MCP install statistics."""
    from collections import Counter
    versions = Counter(e["version"] for e in _mcp_install_log)
    clients = Counter(e["client"] for e in _mcp_install_log)
    return {
        "total_pings": len(_mcp_install_log),
        "by_version": dict(versions),
        "by_client": dict(clients),
        "note": "Tracks anonymous MCP server launches. No user data collected.",
    }


@app.get("/x402-status")
async def x402_status():
    """Diagnostic: shows whether x402 payment middleware is active."""
    import os
    addr = os.environ.get("X402_PAY_TO", "")
    return {
        "env_var_set": bool(addr),
        "env_var_name": "X402_PAY_TO",
        "env_var_value_preview": f"{addr[:8]}...{addr[-4:]}" if len(addr) > 12 else "(empty or too short)",
        "facilitator": os.environ.get("X402_FACILITATOR_URL", "https://facilitator.payai.network"),
        "network": "eip155:8453",
    }


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

try:
    from apis.compound import router as compound_router
    app.include_router(compound_router)
except ImportError as e:
    print(f"Warning: compound router not loaded: {e}")

try:
    from apis.currency import router as currency_router
    app.include_router(currency_router)
except ImportError as e:
    print(f"Warning: currency router not loaded: {e}")

try:
    from apis.mortgage import router as mortgage_router
    app.include_router(mortgage_router)
except ImportError as e:
    print(f"Warning: mortgage router not loaded: {e}")

try:
    from apis.affordability import router as affordability_router
    app.include_router(affordability_router)
except ImportError as e:
    print(f"Warning: affordability router not loaded: {e}")

try:
    from apis.address_intel import router as address_router
    app.include_router(address_router)
except ImportError as e:
    print(f"Warning: address_intel router not loaded: {e}")

try:
    from apis.property_analysis import router as analysis_router
    app.include_router(analysis_router)
except ImportError as e:
    print(f"Warning: property_analysis router not loaded: {e}")

try:
    from apis.property_rank import router as rank_router
    app.include_router(rank_router)
except ImportError as e:
    print(f"Warning: property_rank router not loaded: {e}")

try:
    from apis.property_pitch import router as pitch_router
    app.include_router(pitch_router)
except ImportError as e:
    print(f"Warning: property_pitch router not loaded: {e}")

try:
    from apis.sg_calculators import router as sg_calc_router
    app.include_router(sg_calc_router)
except ImportError as e:
    print(f"Warning: sg_calculators router not loaded: {e}")

try:
    from apis.salary_benchmark import router as salary_router
    app.include_router(salary_router)
except ImportError as e:
    print(f"Warning: salary_benchmark router not loaded: {e}")

try:
    from apis.school_proximity import router as school_router
    app.include_router(school_router)
except ImportError as e:
    print(f"Warning: school_proximity router not loaded: {e}")

try:
    from apis.hdb_quota import router as hdb_quota_router
    app.include_router(hdb_quota_router)
except ImportError as e:
    print(f"Warning: hdb_quota router not loaded: {e}")

try:
    from apis.hdb_lease_decay import router as hdb_lease_router
    app.include_router(hdb_lease_router)
except ImportError as e:
    print(f"Warning: hdb_lease_decay router not loaded: {e}")

try:
    from apis.ura_data import router as ura_router
    app.include_router(ura_router)
except ImportError as e:
    print(f"Warning: ura_data router not loaded: {e}")

try:
    from apis.property_decisions import router as decisions_router
    app.include_router(decisions_router)
except ImportError as e:
    print(f"Warning: property_decisions router not loaded: {e}")

# Marketplace pages (pricing, providers, setup, manifest)
try:
    from pages import router as pages_router
    app.include_router(pages_router)
except ImportError as e:
    print(f"Warning: pages router not loaded: {e}")

# AgentCash discovery — enriches /openapi.json with x402 payment metadata
# so agents running `npx agentcash install` can discover and pay for our API
try:
    from agentcash_discovery import mount_agentcash_discovery
    mount_agentcash_discovery(app)
except ImportError as e:
    print(f"Warning: agentcash_discovery not loaded: {e}")


# ============================================================
# llms.txt — AI discovery (the #1 AI SEO priority)
# ============================================================

@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return """User-agent: *
Allow: /

Sitemap: https://bountyapi.com/sitemap.xml
"""


@app.get("/sitemap.xml", response_class=PlainTextResponse)
async def sitemap_xml():
    urls = [
        "https://bountyapi.com/",
        "https://bountyapi.com/pricing",
        "https://bountyapi.com/providers",
        "https://bountyapi.com/setup",
        "https://bountyapi.com/manifest.json",
        "https://bountyapi.com/pricing.json",
        "https://bountyapi.com/apis/stamp-duty",
        "https://bountyapi.com/apis/postal-district",
        "https://bountyapi.com/apis/rental-yield",
        "https://bountyapi.com/apis/hdb-resale",
        "https://bountyapi.com/apis/mortgage",
        "https://bountyapi.com/apis/compound",
        "https://bountyapi.com/apis/currency",
        "https://bountyapi.com/docs",
        "https://bountyapi.com/llms.txt",
        "https://bountyapi.com/llms-full.txt",
    ]
    body = "".join(f"  <url><loc>{url}</loc></url>\n" for url in urls)
    return f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">
{body}</urlset>
"""


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt():
    """llms.txt — structured description for LLM discovery at inference time."""
    return """# Bounty API

> Singapore property & financial data APIs for AI agents. Government data (URA, IRAS, HDB, MAS), computed financial logic, and market intelligence. Pay-per-call via x402 (USDC on Base). No API keys, no subscriptions. Every response carries its source.

## Base URL

https://bountyapi.com

All endpoints are relative to this base URL.

## Authentication

All paid endpoints require micropayment via x402 protocol. Payments are processed on Base mainnet (eip155:8453). Free endpoints require no payment and no authentication.

## Agent Workflow (Progressive)

### Option A: AgentCash (recommended — automatic payment handling)

1. Install AgentCash MCP server: `npx agentcash@latest install`
2. Fund your wallet: `npx agentcash@latest accounts` (gives you Base/Solana deposit addresses)
3. Discover Bounty endpoints: `mcp__agentcash__discover_api_endpoints("https://bountyapi.com")`
4. Check endpoint schema and pricing: `mcp__agentcash__check_endpoint_schema("https://bountyapi.com/stamp-duty")`
5. Execute: `mcp__agentcash__fetch("https://bountyapi.com/bsd?price=1000000")`

AgentCash handles wallet creation, USDC payment, and 402 challenge resolution automatically. Paid 200s return `{ success: true, data: <HTTP body> }`.

### Option B: Direct MCP (tool discovery without auto-pay)

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

This exposes all 27 MCP tools. Free tools work immediately. Paid tools return 402 with payment instructions — you need an x402-aware HTTP client to complete payment.

### Option C: Direct HTTP (no MCP)

Call endpoints directly with curl or any HTTP client. Free endpoints return data. Paid endpoints return HTTP 402 with a `payment-required` header containing x402 payment instructions.

```bash
# Free — works immediately
curl "https://bountyapi.com/bsd?price=1000000"

# Paid — returns 402
curl "https://bountyapi.com/hdb/towns"
```

If no endpoint matches the user task, stop this flow and tell the user what data is available.

---

# Property Investment Research Methodology

Use this playbook for property analysis requests (e.g., "Is this condo worth buying?", "Can I afford this property?", "Compare these 3 properties"):

1. **Identify the property**: Get the address, postal code, or project name. If the user provides a PropertyGuru/99.co listing URL, extract the price, size (sqft), project name, and postal code from the page.
2. **Stamp duty** (FREE): Call `POST /stamp-duty` with `{"price": <asking_price>, "buyer_profile": "SC", "property_count": 1}`. Returns BSD + ABSD breakdown.
3. **Affordability check** (FREE): Call `GET /buy-vs-rent?monthly_rent=<est_rent>&property_price=<price>&holding_years=10`. Returns full buy-vs-rent comparison.
4. **Mortgage** (FREE): Call `POST /mortgage/calculate` with `{"principal": <price*0.75>, "annual_interest_rate": 3.5, "loan_term_years": 25}`. Returns monthly payment.
5. **Transaction comparables** ($0.05): Call `GET /ura/transactions?batch=1` to get recent private property transactions. Filter by district to find comparable sales.
6. **Rental yield** ($0.005): Call `POST /rental-yield/calculate` with `{"property_price": <price>, "monthly_rent": <est_rent>}`. Returns gross/net yield.
7. **Salary context** (FREE): Call `GET /salary/search?role=<user_job>` to benchmark whether the user can afford the monthly costs.
8. **One-call summary** ($0.05): Call `POST /property/pitch` with the property details. Returns a client-ready investment thesis with verdict.

**Cost optimization**: Steps 2-4 and 7 are FREE. Do those first. Only pay for steps 5-6 and 8 if the user wants deep analysis.

---

# Free Endpoints (no payment required)

## GET /bsd
Buyer's Stamp Duty calculation for residential property.
Query: `?price=1000000`
Price: FREE
Source: iras.gov.sg

Example:
```
GET /bsd?price=1000000
```
Response: `{"price": 1000000, "bsd": 24600, "breakdown": [...], "source": "iras.gov.sg"}`

---

## POST /stamp-duty
Full stamp duty calculation (BSD + ABSD). Use this instead of /bsd when buyer profile matters.
Price: FREE
Source: iras.gov.sg, verified Jan 2026

Example:
```json
{
  "price": 1500000,
  "buyer_profile": "SC",
  "property_count": 1
}
```
buyer_profile values: SC, SPR, FR, entity, developer, trustee
ABSD rates: SC 1st=0%, 2nd=20%, 3rd=30%. SPR 1st=5%, 2nd=30%. FR=60%. Entity=65%.

---

## GET /absd
Additional Buyer's Stamp Duty only.
Query: `?price=1500000&buyer_profile=SPR&property_count=1`
Price: FREE

---

## POST /mortgage/calculate
Fixed-rate mortgage with amortization schedule.
Price: FREE

Example:
```json
{
  "principal": 1125000,
  "annual_interest_rate": 3.5,
  "loan_term_years": 25
}
```
Response: `{"monthly_payment": 5629.76, "total_interest": 563928.18, "total_paid": 1686428.18}`

---

## GET /buy-vs-rent
Total cost comparison of buying vs renting over a holding period. Includes mortgage, stamp duty, property tax, maintenance, appreciation, and opportunity cost.
Query: `?monthly_rent=4500&property_price=1500000&holding_years=10`
Price: FREE
Source: Composite — IRAS rates, standard amortization

---

## GET /property-tax
Singapore property tax. Owner-occupier 0-32%, non-owner-occupied 10-20%.
Query: `?annual_value=36000&owner_occupied=true`
Price: FREE
Source: IRAS, effective 1 Jan 2024

---

## GET /tax/income
Singapore individual income tax. Resident progressive 0-22%, non-resident 15% flat.
Query: `?annual_income=120000`
Price: FREE
Source: IRAS individual income tax rates

---

## GET /gst
Add or remove GST (9% from 1 Jan 2024).
Query: `?amount=100&mode=add`
Price: FREE

---

## GET /commission
Estimated property agent commission for sale/rental.
Query: `?price=1500000&transaction_type=sale`
Price: FREE

---

## GET /cpf/housing
CPF Ordinary Account accumulation for housing.
Query: `?monthly_income=8000&age=30`
Price: FREE

---

## GET /salary/search
Benchmark salary for any Singapore role using live MyCareersFuture job postings. Not self-reported — real employer-posted data.
Query: `?role=software engineer`
Price: FREE
Source: MyCareersFuture (api.mycareersfuture.gov.sg)

---

## GET /address/{postal_code}
Postal code to district, planning area, CCR/RCR/OCR region, HDB town.
Price: FREE
Source: URA Master Plan 2019, SLA postal sectors

---

## GET /mrt/near/{postal_code}
5 nearest MRT stations with walking distance.
Price: FREE
Source: LTA DataMall, 142 stations across all 6 lines

---

## GET /schools/near/{postal_code}
Schools within 1km and 2km for Primary 1 distance priority.
Price: FREE
Source: OpenStreetMap, 294 schools

---

## GET /postal/{code}
Postal code to district number and name.
Price: FREE

---

## GET /hdb/lease-decay
HDB remaining lease analysis — financing thresholds, CPF restrictions, SERS caveat.
Query: `?lease_remaining=65`
Price: FREE

---

## GET /hdb/eip/{town}
HDB Ethnic Integration Policy and SPR quota limits.
Price: FREE

---

## GET /currency/
Currency exchange rates.
Price: FREE

---

## GET /invest/
Investment growth calculator.
Price: FREE

---

# Paid Endpoints (x402 micropayment required)

## GET /hdb/towns
All HDB towns with transaction counts.
Price: $0.01/call
Source: data.gov.sg (live)

---

## GET /hdb/median/{town}
Median resale price by flat type for a specific town.
Price: $0.01/call
Source: data.gov.sg

---

## GET /hdb/search
Search HDB resale transactions with filters (town, flat_type, price range).
Price: $0.01/call
Source: data.gov.sg, 234K+ transactions 2017-present

---

## POST /rental-yield/calculate
Gross yield, net yield, cap rate, price-to-rent ratio, cashflow.
Price: $0.005/call

Example:
```json
{
  "property_price": 1500000,
  "monthly_rent": 4500
}
```

---

## POST /affordability/calculate
MAS TDSR (55%) and HDB MSR (30%) affordability check. Returns max loan and property price.
Price: $0.01/call
Source: MAS TDSR framework, HDB MSR rules

---

## POST /property/analyze
Complete property analysis in one call: stamp duty, comparables, rental yield, affordability, location, risk.
Price: $0.05/call
Source: Composite — IRAS, data.gov.sg, MAS, URA, LTA

---

## POST /property/pitch
Client-ready investment thesis: price fairness, stamp duty, affordability, yield, location, tenure risk, upfront costs, strengths, risk flags, plain-English verdict.
Price: $0.05/call

Example:
```json
{
  "property_type": "condo",
  "project_name": "One Leicester",
  "address": "500 Potong Pasir Ave 1",
  "price": 2499000,
  "monthly_rent": 4500,
  "buyer_profile": "SC"
}
```

---

## POST /property/rank
Rank multiple candidate properties by investment value. Returns 0-100 scores across 4 dimensions.
Price: $0.10/call

---

## GET /ura/transactions
Private residential property transactions (caveat data). Project, street, price, PSF, tenure, sale type.
Query: `?batch=1`
Price: $0.05/call
Source: URA Developer API (PMI_Resi_Transaction)

---

## GET /ura/rental-median
Median rental rates by private residential project.
Query: `?project_name=One Leicester`
Price: $0.05/call
Source: URA Developer API

---

## GET /ura/developer-sales
Developer units launched and sold by project.
Price: $0.05/call
Source: URA Developer API

---

## GET /ura/pipeline
Future private residential supply pipeline.
Query: `?batch=1`
Price: $0.05/call
Source: URA Developer API

---

## GET /ura/rental-contracts
Aggregate rental contract statistics by area and property type.
Price: $0.05/call
Source: URA Developer API

---

## GET /ura/status
Check if URA API is configured. Free — use before paid URA calls.
Price: FREE

---

# Pricing Summary

| Endpoint | Price |
|----------|-------|
| Stamp Duty (BSD/ABSD) | FREE |
| Mortgage Calculator | FREE |
| Buy vs Rent Analysis | FREE |
| Property Tax | FREE |
| Income Tax | FREE |
| GST Calculator | FREE |
| Commission Estimator | FREE |
| CPF Housing | FREE |
| Salary Benchmark | FREE |
| Address Intelligence | FREE |
| MRT Proximity | FREE |
| School Proximity | FREE |
| Postal District | FREE |
| HDB Lease Decay | FREE |
| HDB EIP/SPR Quota | FREE |
| Currency | FREE |
| Investment Growth | FREE |
| URA Status | FREE |
| HDB Resale Data | $0.01 |
| Affordability (TDSR/MSR) | $0.01 |
| Rental Yield | $0.005 |
| Property Analysis | $0.05 |
| Property Pitch | $0.05 |
| URA Transactions | $0.05 |
| URA Rental Median | $0.05 |
| URA Developer Sales | $0.05 |
| URA Pipeline | $0.05 |
| URA Rental Contracts | $0.05 |
| Property Ranking | $0.10 |

---

# MCP Integration

Bounty exposes all endpoints as MCP tools via `https://bountyapi.com/mcp`.

Claude Desktop config:
```json
{
  "mcpServers": {
    "bounty-api": {
      "url": "https://bountyapi.com/mcp"
    }
  }
}
```

npm package for stdio transport: `bountyapi-mcp` (v1.8.0)
```bash
npx bountyapi-mcp
```

---

# Links

- Pricing: https://bountyapi.com/pricing
- Agent setup: https://bountyapi.com/setup
- Provider onboarding: https://bountyapi.com/providers
- Machine manifest: https://bountyapi.com/manifest.json
- Machine pricing: https://bountyapi.com/pricing.json
- Full documentation: https://bountyapi.com/llms-full.txt
- GitHub: https://github.com/vncent786/bounty-api
- npm: https://www.npmjs.com/package/bountyapi-mcp
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

## SG Property Pitch

### POST /property/pitch
Request:
{
  "property_type": "hdb",
  "property_price": 550000,
  "town": "TOA PAYOH",
  "flat_type": "4 ROOM",
  "postal_code": "310074",
  "sqft": 968,
  "monthly_rent": 3200,
  "buyer_profile": "SC",
  "monthly_income": 8000
}

Response includes:
{
  "price_per_sqft": 568.18,
  "price_assessment": {"verdict": "Below median — potential value"},
  "stamp_duty": {"bsd": 11100, "absd": 0, "total": 11100},
  "affordability": {"affordable": true, "binding_constraint": "MSR"},
  "rental_yield": {"gross_yield_pct": 6.98},
  "location": {"region": "RCR", "nearest_mrt": [{"station": "Bishan"}]},
  "verdict": {"recommendation": "WORTH PURSUING"}
}
"""

# ============================================================
# Mount static files LAST — serves /public/ at root level
# Must come after all API routes so it doesn't shadow them.
# Handles: /favicon-*.png, /logo-*.png, /apple-touch-icon.png,
#           /og-image*.png, /twitter-card.png, etc.
# ============================================================
if os.path.isdir(_static_dir):
    app.mount("/", StaticFiles(directory=_static_dir), name="public")
