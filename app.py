"""
Bounty API — Pay-per-call data APIs for AI agents.
Global research endpoints plus Singapore property/financial workflows.
Designed for x402 micropayments and MCP/AgentCash discovery.

APIs:
- Company intelligence, news search, job signals, app reviews
- SG property/tax/location/finance workflows
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
    description="Pay-per-call data APIs for AI agents: company intelligence, news, jobs, app reviews, and Singapore property workflows.",
    version="2.1.0",
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
    """Public landing page — agent-native data infrastructure."""
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Bounty API — Pay-per-call data APIs for AI agents</title>
  <meta name="description" content="Agent-native data APIs for company intelligence, news, job postings, app reviews, and Singapore property analysis. MCP discovery, x402 payments, no API keys or subscriptions." />
  <link rel="icon" href="/favicon.ico" sizes="any" />
  <link rel="icon" href="/favicon-32x32.png" type="image/png" sizes="32x32" />
  <link rel="icon" href="/favicon-16x16.png" type="image/png" sizes="16x16" />
  <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
  <link rel="manifest" href="/site.webmanifest" />
  <meta property="og:title" content="Bounty API — Pay-per-call data APIs for AI agents" />
  <meta property="og:description" content="Company intelligence, news, jobs, app reviews, and Singapore property workflows. Agents discover via MCP and pay per call with x402." />
  <meta property="og:image" content="/og-image.png" />
  <meta property="og:type" content="website" />
  <meta property="og:url" content="https://bountyapi.com" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="theme-color" content="#08090A" />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    "name": "Bounty API",
    "applicationCategory": "DeveloperApplication",
    "applicationSubCategory": "Agent data API",
    "operatingSystem": "Web",
    "url": "https://bountyapi.com",
    "description": "Pay-per-call data APIs for AI agents: company intelligence, news, job postings, app reviews, and Singapore property analysis. MCP-native with x402 micropayments.",
    "offers": [
      {"@type": "Offer", "name": "Free utility endpoints", "price": "0", "priceCurrency": "USD"},
      {"@type": "Offer", "name": "Paid data endpoints", "price": "0.005", "priceCurrency": "USD", "description": "$0.005-$0.10 per call"}
    ]
  }
  </script>
  <style>
    :root {
      --ground:#08090A; --surface-1:#141519; --surface-2:#1C1D22; --surface-3:#26272E;
      --text-primary:#F7F8F8; --text-secondary:#A7ADB8; --text-muted:#6F7682;
      --hairline:rgba(255,255,255,.07); --hairline-strong:rgba(255,255,255,.12);
      --accent:#D4A537; --accent-dim:rgba(212,165,55,.09); --accent-text:#E8C766;
      --ok:#3BA55D; --ok-dim:rgba(59,165,93,.11); --r-sm:6px; --r-md:14px;
      --ease:cubic-bezier(.22,1,.36,1); --speed:150ms;
    }
    *{box-sizing:border-box;margin:0;padding:0} html{scroll-behavior:smooth}
    body{font-family:'Geist',system-ui,-apple-system,sans-serif;background:var(--ground);color:var(--text-primary);font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
    a{color:inherit;text-decoration:none} code,.mono{font-family:'Geist Mono',ui-monospace,monospace}
    .nav{position:sticky;top:0;z-index:50;height:56px;padding:0 32px;display:flex;align-items:center;justify-content:space-between;background:rgba(8,9,10,.82);backdrop-filter:blur(20px) saturate(180%);border-bottom:1px solid var(--hairline)}
    .nav-brand{display:flex;align-items:center;gap:10px;font-weight:600;font-size:15px;letter-spacing:-.02em}.nav-brand img{border-radius:5px}.nav-links{display:flex;align-items:center;gap:28px;font-size:14px;color:var(--text-secondary)}.nav-links a:hover{color:var(--text-primary)}
    .nav-live{display:flex;align-items:center;gap:6px;font:12px 'Geist Mono',monospace;color:var(--text-muted);padding-left:16px;border-left:1px solid var(--hairline)}.live-dot{width:6px;height:6px;border-radius:50%;background:var(--ok)}
    .btn{display:inline-flex;align-items:center;justify-content:center;height:36px;padding:0 16px;border-radius:var(--r-sm);font-size:14px;font-weight:500;border:1px solid transparent;transition:all var(--speed) var(--ease)}.btn-primary{background:var(--text-primary);color:var(--ground)}.btn-primary:hover{background:#E5E7EB}.btn-ghost{color:var(--text-secondary);border-color:var(--hairline-strong)}.btn-ghost:hover{color:var(--text-primary);border-color:var(--text-muted)}.btn-accent{background:var(--accent-dim);color:var(--accent-text);border-color:rgba(212,165,55,.20)}
    .container{max-width:1120px;margin:0 auto;padding:0 32px}.hero{padding:96px 0 64px}.hero-eyebrow{display:inline-flex;align-items:center;gap:8px;font:13px 'Geist Mono',monospace;color:var(--accent-text);background:var(--accent-dim);border:1px solid rgba(212,165,55,.15);padding:4px 12px;border-radius:999px;margin-bottom:28px}.hero h1{font-size:58px;font-weight:600;letter-spacing:-.035em;line-height:1.04;max-width:780px;margin-bottom:20px}.hero-sub{font-size:18px;color:var(--text-secondary);line-height:1.65;max-width:650px;margin-bottom:34px}.hero-actions{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.install{font:13px 'Geist Mono',monospace;color:var(--text-muted);background:var(--surface-1);border:1px solid var(--hairline);padding:8px 14px;border-radius:var(--r-sm)}.install span{color:var(--accent-text)}
    .terminal{margin-top:54px;border-radius:var(--r-md);overflow:hidden;border:1px solid var(--hairline);background:var(--surface-1);box-shadow:0 24px 48px -24px rgba(0,0,0,.55)}.term-header{display:flex;gap:8px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--hairline);font:12px 'Geist Mono',monospace;color:var(--text-muted)}.term-tab{padding:4px 10px;border-radius:4px;font-size:11px}.term-tab.active{background:var(--surface-2);color:var(--text-primary)}.term-body{padding:24px;font:13px/1.75 'Geist Mono',monospace;color:var(--text-secondary);overflow-x:auto}.term-comment{color:var(--text-muted)}.term-prompt{color:var(--accent)}.term-key{color:#79C0FF}.term-str{color:#7EE787}.term-src{display:inline-block;font-size:11px;color:var(--accent-text);background:var(--accent-dim);padding:1px 6px;border-radius:3px;margin-left:4px}.term-paid{display:inline-block;font-size:11px;color:var(--text-primary);background:rgba(255,255,255,.08);padding:1px 6px;border-radius:3px;margin-left:4px}
    .section{padding:78px 0;border-top:1px solid var(--hairline)}.section-label{font:12px 'Geist Mono',monospace;color:var(--accent-text);letter-spacing:.04em;margin-bottom:12px}.section h2{font-size:34px;font-weight:600;letter-spacing:-.026em;line-height:1.15;max-width:620px;margin-bottom:12px}.section-desc{color:var(--text-secondary);font-size:16px;line-height:1.65;max-width:640px;margin-bottom:44px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--hairline);border:1px solid var(--hairline);border-radius:var(--r-md);overflow:hidden}.card{background:var(--ground);padding:28px}.card h3{font-size:16px;font-weight:600;margin-bottom:8px}.card p{color:var(--text-secondary);font-size:14px;line-height:1.55}.tag{display:inline-flex;margin-bottom:18px;font:12px 'Geist Mono',monospace;color:var(--accent-text);background:var(--accent-dim);padding:3px 8px;border-radius:999px}.api-table{border:1px solid var(--hairline);border-radius:var(--r-md);overflow:hidden}.api-table-head,.api-row{display:grid;grid-template-columns:1.6fr 1.1fr 120px 1fr;gap:20px;align-items:center}.api-table-head{padding:12px 20px;background:var(--surface-1);border-bottom:1px solid var(--hairline);font:11px 'Geist Mono',monospace;color:var(--text-muted);letter-spacing:.05em;text-transform:uppercase}.api-row{padding:18px 20px;border-bottom:1px solid var(--hairline)}.api-row:last-child{border-bottom:none}.api-name{font-weight:500}.api-desc{font-size:13px;color:var(--text-muted);margin-top:2px}.api-src{font:12px 'Geist Mono',monospace;color:var(--accent-text)}.api-price{font:13px 'Geist Mono',monospace}.api-price.free{color:var(--ok)}.api-use{font-size:13px;color:var(--text-secondary)}.split{display:grid;grid-template-columns:1fr 1fr;border:1px solid var(--hairline);border-radius:var(--r-md);overflow:hidden}.split-left{padding:40px;border-right:1px solid var(--hairline)}.split-right{background:var(--surface-1);overflow-x:auto}.split-right pre{padding:24px;font:13px/1.7 'Geist Mono',monospace;color:var(--text-secondary)}.feature-list{display:flex;flex-direction:column;gap:20px}.feature{display:flex;gap:12px}.feature-icon{width:20px;height:20px;flex:0 0 auto;border-radius:var(--r-sm);border:1px solid var(--accent);display:grid;place-items:center;font:11px 'Geist Mono';color:var(--accent-text);margin-top:1px}.feature strong{display:block;font-size:14px;font-weight:500;margin-bottom:2px}.feature span{display:block;font-size:13px;color:var(--text-muted);line-height:1.45}.footer{border-top:1px solid var(--hairline);padding:48px 0 40px}.footer-inner{display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:40px}.footer-brand{display:flex;align-items:center;gap:10px;font-weight:600;font-size:14px;margin-bottom:10px}.footer-tag{font-size:13px;color:var(--text-muted);line-height:1.5;max-width:310px}.footer-col h4{font:11px 'Geist Mono',monospace;color:var(--text-muted);letter-spacing:.05em;text-transform:uppercase;margin-bottom:14px}.footer-col a{display:block;font-size:14px;color:var(--text-secondary);margin-bottom:8px}.footer-col a:hover{color:var(--text-primary)}.footer-bottom{padding-top:24px;border-top:1px solid var(--hairline);margin-top:40px;display:flex;justify-content:space-between;font:12px 'Geist Mono',monospace;color:var(--text-muted)}
    @media(max-width:900px){.hero h1{font-size:40px}.grid{grid-template-columns:1fr}.api-table-head{display:none}.api-row{grid-template-columns:1fr;gap:6px}.split{grid-template-columns:1fr}.split-left{border-right:none;border-bottom:1px solid var(--hairline)}.footer-inner{grid-template-columns:1fr}.nav-links{display:none}}
  </style>
</head>
<body>
  <nav class="nav">
    <a class="nav-brand" href="/"><img src="/logo-mark-dark.png" alt="Bounty" width="22" height="22"><span>Bounty</span></a>
    <div style="display:flex;align-items:center;gap:24px"><div class="nav-links"><a href="#apis">APIs</a><a href="/pricing">Pricing</a><a href="/setup">Setup</a><a href="/docs">Docs</a></div><div class="nav-live"><span class="live-dot"></span><span>GLOBAL + SG LIVE</span></div></div>
  </nav>
  <main>
    <div class="container"><section class="hero">
      <div class="hero-eyebrow">MCP discovery · x402 payments · no API keys</div>
      <h1>Data APIs agents can discover, price, and call by themselves.</h1>
      <p class="hero-sub">Bounty gives AI agents pay-per-call access to company intelligence, news, job postings, app reviews, and Singapore property workflows. No accounts. No subscriptions. Every response keeps source provenance and leaves missing data as missing.</p>
      <div class="hero-actions"><a class="btn btn-primary" href="/setup">Connect an agent</a><a class="btn btn-ghost" href="/pricing">View pricing</a><div class="install"><span>$</span> npx agentcash@latest install</div></div>
      <div class="terminal"><div class="term-header"><div class="term-tab active">agent workflow</div><div class="term-tab">stdout</div></div><div class="term-body">
<span class="term-comment"># Agent researches a prospect without creating 4 vendor accounts</span>
<span class="term-prompt">&gt;</span> GET /company/stripe.com <span class="term-paid">$0.05</span>
<span class="term-prompt">&gt;</span> GET /news/search?q=Stripe+AI <span class="term-paid">$0.01</span>
<span class="term-prompt">&gt;</span> GET /jobs/search?q=Stripe+AI+engineer <span class="term-paid">$0.02</span>

{
  <span class="term-key">"workflow"</span>: <span class="term-str">"company due diligence"</span>,
  <span class="term-key">"sources"</span>: [<span class="term-str">"company website"</span>, <span class="term-str">"Google News RSS"</span>, <span class="term-str">"job feeds"</span>],
  <span class="term-key">"auth"</span>: <span class="term-str">"x402 payment, no API key"</span>
}
      </div></div>
    </section></div>

    <div class="container"><section class="section"><div class="section-label">// POSITIONING</div><h2>Not a Singapore property site. That was the first vertical.</h2><p class="section-desc">The product is an agent-native data layer: wrap useful data sources behind clean HTTP, MCP discovery, transparent per-call prices, and x402 payment. Singapore property remains live because it has verified source-backed workflows. The new global layer targets agent research tasks with broader demand.</p><div class="grid"><div class="card"><span class="tag">research</span><h3>Company intelligence</h3><p>Website tech stack, contacts, social links, SSL, and security headers for prospecting and due diligence.</p></div><div class="card"><span class="tag">monitoring</span><h3>News search</h3><p>Current articles by query for launches, lawsuits, funding, layoffs, and market events.</p></div><div class="card"><span class="tag">gtm</span><h3>Job signals</h3><p>Hiring demand and expansion vectors from job postings and hiring threads.</p></div><div class="card"><span class="tag">product</span><h3>App reviews</h3><p>Recent App Store review snapshots for competitor complaints, ratings, and feature gaps.</p></div></div></section></div>

    <div class="container"><section class="section" id="apis"><div class="section-label">// LIVE API CATALOG</div><h2>Global research APIs plus Singapore property workflows.</h2><p class="section-desc">Agents should start with the global research endpoints for company, market, GTM, and product questions. Use the Singapore vertical when the task is explicitly property, tax, affordability, or local location analysis.</p><div class="api-table"><div class="api-table-head"><div>Endpoint</div><div>Source</div><div>Price</div><div>Best for</div></div>
      <div class="api-row"><div><div class="api-name">Company Intelligence</div><div class="api-desc">Tech stack, contacts, social links, SSL, security headers.</div></div><div class="api-src">company website</div><div class="api-price">$0.05</div><div class="api-use">B2B prospecting, DD</div></div>
      <div class="api-row"><div><div class="api-name">News Search</div><div class="api-desc">Structured current news by keyword.</div></div><div class="api-src">news RSS</div><div class="api-price">$0.01</div><div class="api-use">monitoring, events</div></div>
      <div class="api-row"><div><div class="api-name">Job Search</div><div class="api-desc">Job postings and hiring signals.</div></div><div class="api-src">job feeds</div><div class="api-price">$0.02</div><div class="api-use">GTM, recruiting, market maps</div></div>
      <div class="api-row"><div><div class="api-name">App Reviews</div><div class="api-desc">Recent App Store reviews, rating sample, topic flags.</div></div><div class="api-src">Apple RSS</div><div class="api-price">$0.02</div><div class="api-use">product research</div></div>
      <div class="api-row"><div><div class="api-name">Social Trend Search</div><div class="api-desc">Cross-platform social intel: Reddit + YouTube + Instagram in one call.</div></div><div class="api-src">PullPush, yt-dlp, IG tags</div><div class="api-price">$0.05</div><div class="api-use">trend discovery, creator maps, pain language</div></div>
      <div class="api-row"><div><div class="api-name">Singapore Property Analysis</div><div class="api-desc">Stamp duty, URA/HDB comps, yield, affordability, location.</div></div><div class="api-src">IRAS, URA, HDB</div><div class="api-price">FREE-$0.10</div><div class="api-use">property workflows</div></div>
    </div></section></div>

    <div class="container"><section class="section"><div class="section-label">// HOW AGENTS USE IT</div><h2>Discovery before documentation. Payment before accounts.</h2><p class="section-desc">AgentCash and OpenAPI metadata tell agents what exists, what it costs, and how to pay. Humans can still use curl, but the main customer is an autonomous agent that cannot fill vendor signup forms.</p><div class="split"><div class="split-left"><div class="feature-list"><div class="feature"><div class="feature-icon">1</div><div><strong>Discover endpoints</strong><span>OpenAPI, llms.txt, and AgentCash expose routes, schemas, prices, and use cases.</span></div></div><div class="feature"><div class="feature-icon">2</div><div><strong>Call free utilities first</strong><span>Calculators and source-light routes remain free to reduce friction.</span></div></div><div class="feature"><div class="feature-icon">3</div><div><strong>Pay only for data-heavy calls</strong><span>x402 returns HTTP 402, the agent pays USDC on Base, then retries with proof.</span></div></div><div class="feature"><div class="feature-icon">4</div><div><strong>Preserve data integrity</strong><span>No interpolation. No invented fields. Missing values stay null.</span></div></div></div></div><div class="split-right"><pre><span class="term-comment"># Option A: easiest agent setup</span>
<span class="term-prompt">$</span> npx agentcash@latest install
<span class="term-prompt">&gt;</span> discover_api_endpoints("https://bountyapi.com")
<span class="term-prompt">&gt;</span> fetch("https://bountyapi.com/company/stripe.com")

<span class="term-comment"># Option B: direct HTTP</span>
<span class="term-prompt">$</span> curl -i https://bountyapi.com/jobs/search?q=AI+engineer
<span class="term-comment"># HTTP 402 Payment Required + payment-required header</span></pre></div></div></section></div>
  </main>

  <div class="container"><footer class="footer"><div class="footer-inner"><div><div class="footer-brand"><img src="/logo-mark-dark.png" alt="Bounty" width="20" height="20"><span>Bounty</span></div><p class="footer-tag">Pay-per-call data APIs for AI agents. Global research endpoints plus Singapore property workflows. MCP-native, x402 payments.</p></div><div class="footer-col"><h4>Product</h4><a href="#apis">APIs</a><a href="/pricing">Pricing</a><a href="/docs">Docs</a><a href="/setup">Setup</a></div><div class="footer-col"><h4>Build</h4><a href="/providers">Publish API</a><a href="/llms.txt">llms.txt</a><a href="https://www.npmjs.com/package/bountyapi-mcp">npm</a><a href="https://github.com/vncent786/bounty-api">GitHub</a></div><div class="footer-col"><h4>Protocol</h4><a href="https://x402.org">x402</a><a href="https://modelcontextprotocol.io">MCP</a><a href="https://agentcash.dev">AgentCash</a></div></div><div class="footer-bottom"><span>Bounty API · Global + Singapore</span><span>Agent data infrastructure</span></div></footer></div>
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
        "version": "2.1.0",
        "description": "Pay-per-call data APIs for AI agents: company intelligence, news, job postings, app reviews, and Singapore property workflows.",
        "positioning": "agent-native data infrastructure, not just Singapore property",
        "endpoints": {
            "/": "Public landing page",
            "/api": "Machine-readable API info",
            "/company/{domain}": "Company website intelligence",
            "/news/search": "Current news search",
            "/jobs/search": "Job postings and hiring signals",
            "/reviews/app/{country}/{app_id}": "App Store review intelligence",
            "/social/trend-search": "Cross-platform social trend search (Reddit + YouTube + Instagram)",
            "/stamp-duty": "Full stamp duty calculation (BSD + ABSD)",
            "/bsd": "Buyer's Stamp Duty only",
            "/absd": "Additional Buyer's Stamp Duty only",
            "/openapi.json": "OpenAPI with x402 payment metadata",
            "/llms.txt": "LLM discovery and workflow instructions",
            "/.well-known/agentcash.json": "AgentCash discovery manifest",
        },
        "pricing": "Free utility endpoints plus paid x402 endpoints from $0.005-$0.10 per call.",
        "payment": "x402 USDC on Base mainnet (eip155:8453)",
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

# Company Intelligence — tech stack, contacts, security (replaces BuiltWith)
try:
    from apis.company_intel import router as company_intel_router
    app.include_router(company_intel_router)
except ImportError as e:
    print(f"Warning: company_intel router not loaded: {e}")

# News Search — aggregated news from free RSS (replaces NewsAPI)
try:
    from apis.news_search import router as news_router
    app.include_router(news_router)
except ImportError as e:
    print(f"Warning: news_search router not loaded: {e}")

try:
    from apis.job_search import router as job_search_router
    app.include_router(job_search_router)
except ImportError as e:
    print(f"Warning: job_search router not loaded: {e}")

try:
    from apis.app_reviews import router as app_reviews_router
    app.include_router(app_reviews_router)
except ImportError as e:
    print(f"Warning: app_reviews router not loaded: {e}")

# Social Trend Search — multi-platform social media intelligence (Reddit/YouTube/Instagram)
try:
    from apis.social_trends import router as social_trends_router
    app.include_router(social_trends_router)
except ImportError as e:
    print(f"Warning: social_trends router not loaded: {e}")

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
    return '# Bounty API\n\n> Pay-per-call data APIs for AI agents. Bounty provides no-account access to company intelligence, news, job postings, app reviews, and Singapore property/financial workflows. Agents discover endpoints via OpenAPI/MCP/AgentCash and pay per request via x402 USDC on Base. No API keys, no subscriptions. Missing data stays missing; source provenance is preserved.\n\n## Base URL\n\nhttps://bountyapi.com\n\nAll endpoints are relative to this base URL.\n\n## What Bounty is now\n\nBounty is agent-native data infrastructure, not just Singapore property.\n\nUse Bounty when an AI agent needs structured data without creating vendor accounts, storing API keys, or buying monthly subscriptions. The current live product has two layers:\n\n1. **Global research endpoints** for company, market, GTM, and product research.\n2. **Singapore property/finance vertical** for source-backed property, tax, affordability, and location workflows.\n\n## Discovery\n\n- OpenAPI with x402 metadata: https://bountyapi.com/openapi.json\n- AgentCash manifest: https://bountyapi.com/.well-known/agentcash.json\n- Pricing JSON: https://bountyapi.com/pricing.json\n- Machine manifest: https://bountyapi.com/manifest.json\n\nPaid operations include `x-payment-info` and `responses.402` in OpenAPI.\n\n## Authentication and payment\n\nFree endpoints require no authentication. Paid endpoints require x402 payment on Base mainnet (`eip155:8453`) in USDC.\n\nIf called without payment, paid endpoints return:\n\n- HTTP status: `402 Payment Required`\n- Header: `payment-required` with x402 payment instructions\n\nAgentCash can handle discovery, wallet, payment, and retry automatically.\n\n## Recommended agent workflow\n\n### Option A: AgentCash, recommended\n\n1. Install AgentCash MCP server: `npx agentcash@latest install`\n2. Fund the wallet: `npx agentcash@latest accounts`\n3. Discover endpoints: `mcp__agentcash__discover_api_endpoints("https://bountyapi.com")`\n4. Check schema and price: `mcp__agentcash__check_endpoint_schema("https://bountyapi.com/company/stripe.com")`\n5. Execute: `mcp__agentcash__fetch("https://bountyapi.com/company/stripe.com")`\n\nAgentCash handles the 402 challenge and returns paid 200s as `{ success: true, data: <HTTP body> }`.\n\n### Option B: Direct HTTP\n\n```bash\n# Free endpoint works immediately\ncurl "https://bountyapi.com/bsd?price=1000000"\n\n# Paid endpoint returns 402 unless the client supplies x402 payment proof\ncurl -i "https://bountyapi.com/company/stripe.com"\n```\n\n## Global research endpoints\n\n### GET /company/{domain}\n\nBuiltWith-style website intelligence for any company domain.\n\nPrice: `$0.05 / call`\n\nReturns:\n- detected technology stack by category\n- contact emails found on the site\n- social links\n- SSL certificate summary\n- security headers\n- metadata and source URL\n\nUse for B2B prospecting, competitor research, vendor diligence, and investment research.\n\nExample:\n\n```bash\ncurl -i "https://bountyapi.com/company/stripe.com"\n```\n\n### GET /news/search\n\nCurrent news search by keyword.\n\nPrice: `$0.01 / call`\n\nQuery parameters:\n- `q`: search query\n- `limit`: 1-25\n- `days`: optional recency window\n\nUse for company monitoring, launches, lawsuits, layoffs, funding, and market-event checks.\n\nExample:\n\n```bash\ncurl -i "https://bountyapi.com/news/search?q=OpenAI&limit=5"\n```\n\n### GET /jobs/search\n\nJob postings and hiring signals across public sources.\n\nPrice: `$0.02 / call`\n\nQuery parameters:\n- `q`: role, company, skill, or market query\n- `location`: optional location substring\n- `limit`: 1-25\n\nReturns normalized job results with title, company, location, tags, salary fields when published, URL, source, and notes. Missing salary/location values are returned as null.\n\nUse for hiring-signal research, GTM lead qualification, recruiting, market maps, and expansion analysis.\n\nExample:\n\n```bash\ncurl -i "https://bountyapi.com/jobs/search?q=AI+engineer&location=remote&limit=10"\n```\n\n### GET /reviews/app/{country}/{app_id}\n\nRecent Apple App Store customer reviews for a country/app ID.\n\nPrice: `$0.02 / call`\n\nPath parameters:\n- `country`: two-letter App Store country code, e.g. `us`, `sg`, `gb`\n- `app_id`: numeric App Store app ID\n\nQuery parameters:\n- `limit`: 1-50\n- `min_rating`: optional 1-5\n- `max_rating`: optional 1-5\n\nReturns review title, rating, content, version when Apple provides it, URL, sample rating distribution, and deterministic topic flags. Topic flags are keyword matches, not model-generated sentiment.\n\nUse for subscription-app competitor research, product-validation complaints, feature gaps, and rating snapshots.\n\nExample:\n\n```bash\ncurl -i "https://bountyapi.com/reviews/app/us/544007664?limit=10&max_rating=3"\n```\n\n## Company / GTM / market research playbook\n\nWhen a user asks for company research, competitor research, GTM research, product validation, or market monitoring:\n\n1. Call `/company/{domain}` for website intelligence.\n2. Call `/news/search?q=<company or market>` for current events.\n3. Call `/jobs/search?q=<company role skill>` for hiring signals.\n4. If researching a consumer/subscription app, call `/reviews/app/{country}/{app_id}` for review complaints and rating snapshot.\n5. Do not fabricate missing fields. If source data omits salary, author, version, or date, preserve null/missing.\n\n## Singapore property and finance endpoints\n\nThese remain live, but they are one vertical, not the whole product.\n\n### Free utility endpoints\n\n- `GET /bsd?price=1000000` — Buyer Stamp Duty. Source: IRAS.\n- `POST /stamp-duty` — full BSD + ABSD calculation.\n- `GET /absd` — ABSD only.\n- `POST /mortgage/calculate` — mortgage amortization.\n- `GET /buy-vs-rent` — buy-vs-rent total cost comparison.\n- `GET /property-tax` — Singapore property tax.\n- `GET /tax/income` — Singapore income tax.\n- `GET /gst` — GST add/remove calculator.\n- `GET /commission` — property agent commission estimate.\n- `GET /cpf/housing` — CPF OA housing accumulation.\n- `GET /salary/search` — Singapore salary benchmark from live job postings.\n- `GET /address/{postal_code}` — address intelligence.\n- `GET /mrt/near/{postal_code}` — nearest MRT stations.\n- `GET /schools/near/{postal_code}` — schools within 1km/2km.\n- `GET /postal/{code}` — postal district lookup.\n- `GET /hdb/lease-decay` — HDB lease constraints.\n- `GET /hdb/eip/{town}` — HDB EIP/SPR quota.\n- `GET /currency/convert` — currency conversion.\n- `POST /invest/calculate` — compound growth calculator.\n\n### Paid Singapore endpoints\n\n- `GET /hdb/towns` — `$0.01`\n- `GET /hdb/median/{town}` — `$0.01`\n- `GET /hdb/search` — `$0.01`\n- `POST /rental-yield/calculate` — `$0.005`\n- `POST /affordability/calculate` — `$0.01`\n- `POST /property/analyze` — `$0.05`\n- `POST /property/pitch` — `$0.05`\n- `POST /property/rank` — `$0.10`\n- `GET /ura/transactions` — `$0.05`\n- `GET /ura/rental-median` — `$0.05`\n- `GET /ura/developer-sales` — `$0.05`\n- `GET /ura/pipeline` — `$0.05`\n- `GET /ura/rental-contracts` — `$0.05`\n\n## Property investment playbook\n\nUse this only when the user task is clearly about Singapore property:\n\n1. Identify property address, postal code, project name, price, size, tenure, and estimated rent.\n2. Use free calculators first: `/stamp-duty`, `/buy-vs-rent`, `/mortgage/calculate`.\n3. Use `/address/{postal_code}`, `/mrt/near/{postal_code}`, and `/schools/near/{postal_code}` for location context.\n4. Use paid data if needed: `/ura/transactions`, `/ura/rental-median`, `/hdb/search`.\n5. Use `/property/pitch` or `/property/rank` for composite outputs.\n\n## Data integrity rules for agents\n\n- Do not interpolate, estimate, or fill missing values unless the user explicitly asks.\n- Preserve null/missing fields.\n- Cite the source field returned by the endpoint.\n- Do not treat sample ratings or sample distributions as lifetime values.\n- If an endpoint returns zero records, report zero records instead of broadening the query silently.\n\n## Pricing summary\n\n- Global company intelligence: `$0.05`\n- Global news search: `$0.01`\n- Global job search: `$0.02`\n- Global App Store reviews: `$0.02`\n- Singapore free utility endpoints: `$0.00`\n- Singapore data/composite endpoints: `$0.005-$0.10`\n\n## Links\n\n- Website: https://bountyapi.com\n- Pricing: https://bountyapi.com/pricing\n- Setup: https://bountyapi.com/setup\n- Machine manifest: https://bountyapi.com/manifest.json\n- Machine pricing: https://bountyapi.com/pricing.json\n- OpenAPI: https://bountyapi.com/openapi.json\n'


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
