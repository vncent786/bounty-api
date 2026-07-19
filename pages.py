"""
Bounty API — Marketplace pages.

Pages that transform Bounty from "an API" into "a platform":
- /pricing — transparent per-call pricing
- /providers — developer onboarding ("Publish your API on Bounty")
- /setup — agent setup guide (MCP + x402 wallet)
- /manifest.json — machine-readable marketplace manifest
- /pricing.json — machine-readable pricing
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()

# Shared CSS — Linear school dark theme (matches homepage)
BASE_CSS = """
:root {
  --ground: #08090A; --surface-1: #141519; --surface-2: #1C1D22; --surface-3: #26272E;
  --text-primary: #F7F8F8; --text-secondary: #9CA3AF; --text-muted: #6B7280;
  --hairline: rgba(255,255,255,0.06); --hairline-strong: rgba(255,255,255,0.10);
  --accent: #D4A537; --accent-dim: rgba(212,165,55,0.08); --accent-text: #E8C766;
  --free: #3BA55D; --free-dim: rgba(59,165,93,0.10);
  --r-sm: 6px; --r-md: 12px; --ease: cubic-bezier(0.22,1,0.36,1); --speed: 150ms;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family:'Geist',system-ui,-apple-system,sans-serif; background:var(--ground); color:var(--text-primary); -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility; font-size:15px; line-height:1.55; }
a { color:inherit; text-decoration:none; }
.nav { position:sticky; top:0; z-index:50; display:flex; align-items:center; justify-content:space-between; padding:0 32px; height:56px; background:rgba(8,9,10,0.80); backdrop-filter:blur(20px) saturate(180%); -webkit-backdrop-filter:blur(20px) saturate(180%); border-bottom:1px solid var(--hairline); }
.nav-brand { display:flex; align-items:center; gap:10px; font-weight:600; font-size:15px; letter-spacing:-0.02em; }
.nav-brand img { border-radius:5px; }
.nav-links { display:flex; align-items:center; gap:28px; font-size:14px; color:var(--text-secondary); }
.nav-links a { transition:color var(--speed) var(--ease); }
.nav-links a:hover { color:var(--text-primary); }
.nav-live { display:flex; align-items:center; gap:6px; font:12px 'Geist Mono',monospace; color:var(--text-muted); padding-left:16px; border-left:1px solid var(--hairline); }
.live-dot { width:6px; height:6px; border-radius:50%; background:var(--free); box-shadow:0 0 0 0 rgba(59,165,93,0.4); animation:live-pulse 2s infinite; }
@keyframes live-pulse { 0%{box-shadow:0 0 0 0 rgba(59,165,93,0.4)} 70%{box-shadow:0 0 0 6px rgba(59,165,93,0)} 100%{box-shadow:0 0 0 0 rgba(59,165,93,0)} }
.btn { display:inline-flex; align-items:center; justify-content:center; gap:8px; height:36px; padding:0 16px; border-radius:var(--r-sm); font-size:14px; font-weight:500; cursor:pointer; transition:all var(--speed) var(--ease); border:1px solid transparent; }
.btn-primary { background:var(--text-primary); color:var(--ground); }
.btn-primary:hover { background:#E5E7EB; }
.btn-ghost { background:transparent; color:var(--text-secondary); border-color:var(--hairline-strong); }
.btn-ghost:hover { color:var(--text-primary); border-color:var(--text-muted); }
.btn-accent { background:var(--accent-dim); color:var(--accent-text); border-color:rgba(212,165,55,0.20); }
.btn-accent:hover { border-color:var(--accent); }
.hero { max-width:1080px; margin:0 auto; padding:80px 32px 40px; }
.hero h1 { font-size:clamp(36px,6vw,56px); line-height:1.05; letter-spacing:-0.03em; margin:20px 0 16px; max-width:680px; font-weight:600; }
.hero p { font-size:18px; line-height:1.6; color:var(--text-secondary); max-width:560px; }
.eyebrow { display:inline-flex; gap:8px; align-items:center; padding:4px 12px; border-radius:999px; background:var(--accent-dim); border:1px solid rgba(212,165,55,0.15); color:var(--accent-text); font:13px 'Geist Mono',monospace; }
section.content { max-width:1080px; margin:0 auto; padding:32px 32px 64px; }
pre { margin:0; padding:24px; overflow-x:auto; font:13px/1.7 'Geist Mono',ui-monospace,monospace; color:var(--text-secondary); background:var(--surface-1); border-radius:var(--r-md); border:1px solid var(--hairline); }
.codeblock { border-radius:var(--r-md); overflow:hidden; border:1px solid var(--hairline); }
.codebar { display:flex; align-items:center; gap:8px; padding:12px 16px; border-bottom:1px solid var(--hairline); color:var(--text-muted); font:12px 'Geist Mono',monospace; }
.dot { width:10px; height:10px; border-radius:50%; background:var(--text-muted); }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:0; border:1px solid var(--hairline); border-radius:var(--r-md); overflow:hidden; }
.grid2 > .card { border-radius:0; box-shadow:none; border-right:1px solid var(--hairline); }
.grid2 > .card:last-child { border-right:none; }
.card { background:transparent; border-radius:var(--r-md); padding:32px; }
.card h3 { margin:0 0 10px; font-size:18px; letter-spacing:-0.02em; font-weight:600; }
.card p { margin:0; color:var(--text-secondary); line-height:1.6; font-size:14px; }
.check { width:20px; height:20px; flex:0 0 auto; border-radius:var(--r-sm); border:1px solid var(--accent); display:grid; place-items:center; color:var(--accent-text); font:11px 'Geist Mono',monospace; margin-top:1px; }
.list { display:grid; gap:16px; margin-top:20px; }
.item { display:flex; gap:12px; align-items:flex-start; }
.item strong { display:block; font-size:14px; font-weight:500; margin-bottom:2px; color:var(--text-primary); }
.item span { color:var(--text-muted); font-size:13px; line-height:1.45; }
.footer { max-width:1080px; margin:0 auto; padding:48px 32px 40px; display:grid; grid-template-columns:2fr 1fr 1fr 1fr; gap:40px; border-top:1px solid var(--hairline); }
.footer-brand { display:flex; align-items:center; gap:10px; font-weight:600; font-size:14px; margin-bottom:10px; letter-spacing:-0.02em; }
.footer-brand img { border-radius:5px; }
.footer-tag { font-size:13px; color:var(--text-muted); line-height:1.5; max-width:280px; }
.footer-col h4 { font:11px 'Geist Mono',monospace; color:var(--text-muted); letter-spacing:0.05em; text-transform:uppercase; margin-bottom:14px; }
.footer-col a { display:block; font-size:14px; color:var(--text-secondary); margin-bottom:8px; transition:color var(--speed) var(--ease); }
.footer-col a:hover { color:var(--text-primary); }
.footer-bottom { max-width:1080px; margin:0 auto; padding:24px 32px 40px; border-top:1px solid var(--hairline); display:flex; justify-content:space-between; font:12px 'Geist Mono',monospace; color:var(--text-muted); }
.tag { font:12px 'Geist Mono',monospace; padding:4px 7px; border-radius:var(--r-sm); background:var(--surface-1); color:var(--text-secondary); border:1px solid var(--hairline); }
.price-free { color:var(--free); font:13px 'Geist Mono',monospace; }
.price-paid { color:var(--accent-text); font:13px 'Geist Mono',monospace; }
.table-row { display:grid; grid-template-columns:2fr 1fr 1fr 2fr; gap:16px; padding:16px 0; border-bottom:1px solid var(--hairline); align-items:center; }
.table-header { font:11px 'Geist Mono',monospace; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.05em; }
.cta { display:inline-flex; margin-top:24px; gap:12px; flex-wrap:wrap; }
.button { display:inline-flex; align-items:center; justify-content:center; gap:8px; height:36px; padding:0 16px; border-radius:var(--r-sm); font-size:14px; font-weight:500; cursor:pointer; transition:all var(--speed) var(--ease); border:1px solid transparent; }
.button.primary { background:var(--text-primary); color:var(--ground); }
.step { display:flex; gap:20px; margin-bottom:32px; align-items:flex-start; }
.step-num { width:36px; height:36px; flex:0 0 auto; border-radius:var(--r-sm); border:1px solid var(--accent); color:var(--accent-text); display:grid; place-items:center; font:14px 'Geist Mono',monospace; }
.step-content h3 { margin:0 0 8px; font-size:18px; letter-spacing:-0.02em; font-weight:600; }
.step-content p { margin:0; color:var(--text-secondary); line-height:1.6; font-size:14px; max-width:680px; }
.tab { display:inline-block; padding:8px 16px; border-radius:var(--r-sm) var(--r-sm) 0 0; background:var(--surface-1); font-size:14px; cursor:pointer; color:var(--text-muted); }
.tab.active { background:var(--surface-2); color:var(--text-primary); }
@media(max-width:768px){ .grid2{grid-template-columns:1fr} .grid2>.card{border-right:none;border-bottom:1px solid var(--hairline)} .grid2>.card:last-child{border-bottom:none} .nav-links{display:none} .table-row{grid-template-columns:1fr;gap:4px} .footer{grid-template-columns:1fr} .footer-bottom{flex-direction:column;gap:8px} }
"""

NAV_HTML = """
<nav class="nav">
  <a class="nav-brand" href="/"><img src="/logo-mark-dark.png" alt="Bounty" width="22" height="22"><span>Bounty</span></a>
  <div style="display:flex;align-items:center;gap:24px">
    <div class="nav-links">
      <a href="/#apis">APIs</a>
      <a href="/pricing">Pricing</a>
      <a href="/setup">Setup</a>
      <a href="/docs">Docs</a>
    </div>
    <div class="nav-live"><span class="live-dot"></span><span>31 APIs &middot; LIVE</span></div>
  </div>
</nav>
"""

FOOTER_HTML = """
<footer class="footer">
  <div>
    <div class="footer-brand"><img src="/logo-mark-dark.png" alt="Bounty" width="20" height="20"><span>Bounty</span></div>
    <p class="footer-tag">Pay-per-call data APIs for AI agents. Global research endpoints plus Singapore property workflows.</p>
  </div>
  <div class="footer-col"><h4>Product</h4><a href="/#apis">APIs</a><a href="/pricing">Pricing</a><a href="/docs">Docs</a><a href="/setup">Setup</a></div>
  <div class="footer-col"><h4>Build</h4><a href="/providers">Publish API</a><a href="/llms.txt">llms.txt</a><a href="https://www.npmjs.com/package/bountyapi-mcp">npm</a><a href="https://github.com/vncent786/bounty-api">GitHub</a></div>
  <div class="footer-col"><h4>Protocol</h4><a href="https://x402.org">x402</a><a href="https://modelcontextprotocol.io">MCP</a></div>
</footer>
<div class="footer-bottom"><span>Bounty API &middot; Singapore</span><span>v1.8.0 &middot; 31 APIs &middot; 27 MCP tools</span></div>
"""


@router.get("/pricing", response_class=HTMLResponse)
async def pricing_page():
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pricing — Bounty API</title>
<meta name="description" content="Transparent per-call pricing for Bounty API endpoints. Free tier for discovery, paid tier for premium data. x402 micropayments in USDC on Base.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>__BASE_CSS__</style>
</head>
<body>
__NAV__
<div class="hero">
  <div class="eyebrow">Pay per call. No subscriptions.</div>
  <h1>Transparent pricing for every endpoint.</h1>
  <p>Agents pay with USDC on Base via the x402 protocol. No API keys, no accounts, no monthly fees. Free endpoints for discovery. Premium endpoints for high-value data.</p>
</div>

<section class="content">
  <div class="table-row table-header">
    <span>Endpoint</span>
    <span>Price</span>
    <span>Type</span>
    <span>Why</span>
  </div>

  <div style="margin:24px 0 8px;font-size:13px;color:var(--faint);font-weight:500;text-transform:uppercase;letter-spacing:.05em">Global research endpoints</div>

  <div class="table-row">
    <span><strong>Company Intelligence</strong><br><span class="tag">/company/{domain}</span></span>
    <span class="price-paid">$0.05 / call</span>
    <span>Website intelligence</span>
    <span style="color:var(--muted);font-size:14px">Tech stack, contacts, social links, SSL, security headers.</span>
  </div>
  <div class="table-row">
    <span><strong>News Search</strong><br><span class="tag">/news/search</span></span>
    <span class="price-paid">$0.01 / call</span>
    <span>Current events</span>
    <span style="color:var(--muted);font-size:14px">Structured news by keyword for monitoring and research.</span>
  </div>
  <div class="table-row">
    <span><strong>Job Search</strong><br><span class="tag">/jobs/search</span></span>
    <span class="price-paid">$0.02 / call</span>
    <span>Hiring signals</span>
    <span style="color:var(--muted);font-size:14px">Job postings and hiring threads for GTM, recruiting, market maps.</span>
  </div>
  <div class="table-row">
    <span><strong>App Reviews</strong><br><span class="tag">/reviews/app/{country}/{app_id}</span></span>
    <span class="price-paid">$0.02 / call</span>
    <span>Product research</span>
    <span style="color:var(--muted);font-size:14px">Recent App Store reviews, rating sample, deterministic complaint flags.</span>
  </div>
  <div class="table-row">
    <span><strong>Social Trend Search</strong><br><span class="tag">/social/trend-search</span></span>
    <span class="price-paid">$0.05 / call</span>
    <span>Trend intel</span>
    <span style="color:var(--muted);font-size:14px">Reddit + YouTube + Instagram in one call. Creators, captions, view counts, pain language. Per-source health reporting.</span>
  </div>

  <div style="margin:24px 0 8px;font-size:13px;color:var(--faint);font-weight:500;text-transform:uppercase;letter-spacing:.05em">Free utility endpoints</div>

  <div class="table-row">
    <span><strong>SG Stamp Duty</strong><br><span class="tag">/bsd</span> <span class="tag">/absd</span></span>
    <span class="price-free">FREE</span>
    <span>Computed math</span>
    <span style="color:var(--muted);font-size:14px">Cheap to serve. Drives discovery.</span>
  </div>
  <div class="table-row">
    <span><strong>SG Address Intelligence</strong><br><span class="tag">/address</span> <span class="tag">/mrt</span></span>
    <span class="price-free">FREE</span>
    <span>Static data</span>
    <span style="color:var(--muted);font-size:14px">District, region (CCR/RCR/OCR), MRT proximity.</span>
  </div>
  <div class="table-row">
    <span><strong>SG Income Tax</strong><br><span class="tag">/tax/income</span></span>
    <span class="price-free">FREE</span>
    <span>Computed math</span>
    <span style="color:var(--muted);font-size:14px">Resident progressive, non-resident flat.</span>
  </div>
  <div class="table-row">
    <span><strong>SG GST</strong><br><span class="tag">/gst</span></span>
    <span class="price-free">FREE</span>
    <span>Computed math</span>
    <span style="color:var(--muted);font-size:14px">Add or remove 9% GST.</span>
  </div>
  <div class="table-row">
    <span><strong>SG Commission</strong><br><span class="tag">/commission</span></span>
    <span class="price-free">FREE</span>
    <span>Computed math</span>
    <span style="color:var(--muted);font-size:14px">Agent commission by property type.</span>
  </div>
  <div class="table-row">
    <span><strong>SG CPF Housing</strong><br><span class="tag">/cpf/housing</span></span>
    <span class="price-free">FREE</span>
    <span>Computed math</span>
    <span style="color:var(--muted);font-size:14px">OA accumulation for housing.</span>
  </div>
  <div class="table-row">
    <span><strong>SG Salary Benchmark</strong><br><span class="tag">/salary/search</span></span>
    <span class="price-free">FREE</span>
    <span>Live data</span>
    <span style="color:var(--muted);font-size:14px">Real employer-posted salary from MyCareersFuture.</span>
  </div>
  <div class="table-row">
    <span><strong>SG Property Tax</strong><br><span class="tag">/property-tax</span></span>
    <span class="price-free">FREE</span>
    <span>Computed math</span>
    <span style="color:var(--muted);font-size:14px">IRAS progressive rates.</span>
  </div>
  <div class="table-row">
    <span><strong>SG Buy-vs-Rent</strong><br><span class="tag">/buy-vs-rent</span></span>
    <span class="price-free">FREE</span>
    <span>Computed math</span>
    <span style="color:var(--muted);font-size:14px">Total cost comparison with opportunity cost.</span>
  </div>
  <div class="table-row">
    <span><strong>SG School Proximity</strong><br><span class="tag">/schools</span></span>
    <span class="price-free">FREE</span>
    <span>Static data</span>
    <span style="color:var(--muted);font-size:14px">294 schools. Within 1km/2km for P1 priority.</span>
  </div>
  <div class="table-row">
    <span><strong>HDB EIP/SPR Quota</strong><br><span class="tag">/hdb/eip</span></span>
    <span class="price-free">FREE</span>
    <span>Rules</span>
    <span style="color:var(--muted);font-size:14px">Ethnic integration policy + SPR quota limits.</span>
  </div>
  <div class="table-row">
    <span><strong>HDB Lease Decay</strong><br><span class="tag">/hdb/lease-decay</span></span>
    <span class="price-free">FREE</span>
    <span>Rules</span>
    <span style="color:var(--muted);font-size:14px">Financing/CPF restriction timeline.</span>
  </div>
  <div class="table-row">
    <span><strong>Postal District Mapper</strong><br><span class="tag">/postal</span></span>
    <span class="price-free">FREE</span>
    <span>Static data</span>
    <span style="color:var(--muted);font-size:14px">No external calls needed.</span>
  </div>
  <div class="table-row">
    <span><strong>Mortgage Calculator</strong><br><span class="tag">/mortgage</span></span>
    <span class="price-free">FREE</span>
    <span>Computed math</span>
    <span style="color:var(--muted);font-size:14px">Standard amortization formula.</span>
  </div>
  <div class="table-row">
    <span><strong>Investment Growth</strong><br><span class="tag">/invest</span></span>
    <span class="price-free">FREE</span>
    <span>Computed math</span>
    <span style="color:var(--muted);font-size:14px">Compound interest formula.</span>
  </div>
  <div class="table-row">
    <span><strong>Currency Converter</strong><br><span class="tag">/currency</span></span>
    <span class="price-free">FREE</span>
    <span>Live data</span>
    <span style="color:var(--muted);font-size:14px">ECB rates, cached hourly.</span>
  </div>

  <div style="margin-top:24px;font-size:13px;color:var(--faint);font-weight:500;text-transform:uppercase;letter-spacing:.05em">Premium data endpoints</div>

  <div class="table-row">
    <span><strong>Rental Yield</strong><br><span class="tag">/rental-yield</span></span>
    <span class="price-paid">$0.005 / call</span>
    <span>Investment analysis</span>
    <span style="color:var(--muted);font-size:14px">Decision-grade output for underwriting.</span>
  </div>
  <div class="table-row">
    <span><strong>SG Affordability (TDSR/MSR)</strong><br><span class="tag">/affordability</span></span>
    <span class="price-paid">$0.01 / call</span>
    <span>MAS/HDB rules</span>
    <span style="color:var(--muted);font-size:14px">Multi-step regulatory computation.</span>
  </div>
  <div class="table-row">
    <span><strong>HDB Resale Data</strong><br><span class="tag">/hdb</span></span>
    <span class="price-paid">$0.01 / call</span>
    <span>Government data</span>
    <span style="color:var(--muted);font-size:14px">Live from data.gov.sg. Costs to fetch.</span>
  </div>
  <div class="table-row">
    <span><strong>URA Transactions</strong><br><span class="tag">/ura/transactions</span></span>
    <span class="price-paid">$0.05 / call</span>
    <span>Exclusive data</span>
    <span style="color:var(--muted);font-size:14px">Private property caveat-level data. URA API.</span>
  </div>
  <div class="table-row">
    <span><strong>URA Median Rentals</strong><br><span class="tag">/ura/rental-median</span></span>
    <span class="price-paid">$0.05 / call</span>
    <span>Exclusive data</span>
    <span style="color:var(--muted);font-size:14px">Private rental rates by project. URA API.</span>
  </div>
  <div class="table-row">
    <span><strong>URA Developer Sales</strong><br><span class="tag">/ura/developer-sales</span></span>
    <span class="price-paid">$0.05 / call</span>
    <span>Exclusive data</span>
    <span style="color:var(--muted);font-size:14px">Units sold by developers. URA API.</span>
  </div>
  <div class="table-row">
    <span><strong>URA Pipeline Supply</strong><br><span class="tag">/ura/pipeline</span></span>
    <span class="price-paid">$0.05 / call</span>
    <span>Exclusive data</span>
    <span style="color:var(--muted);font-size:14px">Future supply pipeline. URA API.</span>
  </div>
  <div class="table-row">
    <span><strong>SG Property Analysis</strong><br><span class="tag">/property/analyze</span></span>
    <span class="price-paid">$0.05 / call</span>
    <span>Composite</span>
    <span style="color:var(--muted);font-size:14px">Full analysis: stamp duty + comps + yield + location.</span>
  </div>
  <div class="table-row">
    <span><strong>SG Property Pitch</strong><br><span class="tag">/property/pitch</span></span>
    <span class="price-paid">$0.05 / call</span>
    <span>Composite</span>
    <span style="color:var(--muted);font-size:14px">Client-ready investment thesis one-pager.</span>
  </div>
  <div class="table-row">
    <span><strong>SG Property Ranking</strong><br><span class="tag">/property/rank</span></span>
    <span class="price-paid">$0.10 / call</span>
    <span>Composite</span>
    <span style="color:var(--muted);font-size:14px">Multi-property comparison with transparent scoring.</span>
  </div>

  <div class="grid2" style="margin-top:48px">
    <div class="card">
      <h3>How payment works</h3>
      <p style="margin-bottom:16px">Every paid endpoint uses the x402 protocol:</p>
      <div class="list">
        <div class="item"><span class="check">1</span><div><strong>Agent requests data</strong><span>Server responds with HTTP 402 + payment instructions.</span></div></div>
        <div class="item"><span class="check">2</span><div><strong>Agent pays USDC on Base</strong><span>On-chain micropayment. Gas &lt; $0.001.</span></div></div>
        <div class="item"><span class="check">3</span><div><strong>Server verifies + returns data</strong><span>Facilitator validates payment. Data returned in same request.</span></div></div>
      </div>
    </div>
    <div class="card">
      <h3>For providers</h3>
      <p>You set the price for every endpoint. From $0.0001 to any amount. Revenue settles to your wallet in USDC on Base. No invoicing, no payout delays.</p>
      <p style="margin-top:16px"><strong>Platform fee:</strong> 3% on successful calls. If your API doesn't earn, you don't pay.</p>
      <div class="cta">
        <a class="button primary" href="/providers">Publish your API</a>
      </div>
    </div>
  </div>

  <div class="codeblock" style="margin-top:32px">
    <div class="codebar"><span class="dot"></span><span class="dot"></span><span class="dot"></span><span>Payment flow example</span></div>
    <pre><span style="color:#a3a3a3"># 1. Agent requests HDB data (no payment header)</span>
curl https://bountyapi.com/hdb/towns

<span style="color:#fbbf24"># HTTP 402 Payment Required</span>
<span style="color:#a3a3a3"># PAYMENT-REQUIRED header with payment instructions</span>

<span style="color:#a3a3a3"># 2. Agent pays and retries with payment proof</span>
curl https://bountyapi.com/hdb/towns \\
  -H <span style="color:#86efac">"PAYMENT-SIGNATURE: &lt;base64-usdc-payment&gt;"</span>

<span style="color:#a3a3a3"># 3. Server verifies, settles on-chain, returns data</span>
<span style="color:#7dd3fc">{ "total_towns": 24, "total_transactions": 4000, ... }</span></pre>
  </div>
</section>

__FOOTER__
</body>
</html>""".replace("__NAV__", NAV_HTML).replace("__FOOTER__", FOOTER_HTML).replace("__BASE_CSS__", BASE_CSS)


@router.get("/providers", response_class=HTMLResponse)
async def providers_page():
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>For Developers — Bounty API</title>
<meta name="description" content="Publish your API on Bounty. x402 payments handled. MCP discovery built in. Earn USDC for every call.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>__BASE_CSS__
</style>
</head>
<body>
__NAV__
<div class="hero">
  <div class="eyebrow">For API providers</div>
  <h1>Publish your API. Earn for every call.</h1>
  <p>Bounty handles payments (x402), discovery (MCP registry), documentation, and schema validation. You focus on data quality. We handle the rest.</p>
  <div class="cta">
    <a class="button primary" href="mailto:hello@bountyapi.com?subject=Publish%20my%20API">Apply to publish</a>
    <a class="button" href="/pricing">See pricing model</a>
  </div>
</div>

<section class="content">
  <div class="grid2">
    <div class="card">
      <h3>What you get</h3>
      <div class="list">
        <div class="item"><span class="check">&#10003;</span><div><strong>x402 payment infrastructure</strong><span>Per-request USDC micropayments. No payment code to write.</span></div></div>
        <div class="item"><span class="check">&#10003;</span><div><strong>MCP discovery</strong><span>Your APIs auto-discoverable by any MCP-compatible agent.</span></div></div>
        <div class="item"><span class="check">&#10003;</span><div><strong>Auto-generated docs</strong><span>API catalog pages with examples, params, and source provenance.</span></div></div>
        <div class="item"><span class="check">&#10003;</span><div><strong>Schema validation</strong><span>Typed inputs and outputs. Agents get predictable responses.</span></div></div>
        <div class="item"><span class="check">&#10003;</span><div><strong>Revenue dashboard</strong><span>Track calls, revenue, and wallet settlements in real time.</span></div></div>
        <div class="item"><span class="check">&#10003;</span><div><strong>97% revenue share</strong><span>You keep 97% of every paid call. 3% platform fee covers facilitation.</span></div></div>
      </div>
    </div>
    <div class="card">
      <h3>What we look for</h3>
      <div class="list">
        <div class="item"><span class="check">&#10003;</span><div><strong>Verified data</strong><span>Not scraped chaos. Structured, sourced, decision-grade.</span></div></div>
        <div class="item"><span class="check">&#10003;</span><div><strong>Clear provenance</strong><span>Every response should expose where data came from.</span></div></div>
        <div class="item"><span class="check">&#10003;</span><div><strong>Stable schemas</strong><span>Breaking changes versioned. Agents need predictability.</span></div></div>
        <div class="item"><span class="check">&#10003;</span><div><strong>Sub-second response</strong><span>Agents won't wait. If your endpoint takes 10s, it won't get called.</span></div></div>
        <div class="item"><span class="check">&#10003;</span><div><strong>Honest limitations</strong><span>Label sampled data. Flag gaps. Don't interpolate.</span></div></div>
      </div>
    </div>
  </div>

  <h2 style="font-size:28px;letter-spacing:-.05em;margin:56px 0 28px">How publishing works</h2>

  <div class="step">
    <div class="step-num">1</div>
    <div class="step-content">
      <h3>Submit your API</h3>
      <p>Send us your endpoint spec: URL, params, response schema, data source, and desired price per call. We review for data quality and schema stability.</p>
    </div>
  </div>
  <div class="step">
    <div class="step-num">2</div>
    <div class="step-content">
      <h3>We wrap it with x402 + MCP</h3>
      <p>Your endpoint gets payment middleware, an MCP tool definition, a catalog page, and llms.txt entry. Discovery handled.</p>
    </div>
  </div>
  <div class="step">
    <div class="step-num">3</div>
    <div class="step-content">
      <h3>Agents discover and pay</h3>
      <p>Any x402-compatible agent can find your API via MCP or web search. They pay per call. Revenue settles to your wallet in USDC on Base.</p>
    </div>
  </div>
  <div class="step">
    <div class="step-num">4</div>
    <div class="step-content">
      <h3>You track and optimize</h3>
      <p>Monitor calls, revenue, and response times. Adjust pricing. Add endpoints. Scale.</p>
    </div>
  </div>

  <div class="codeblock" style="margin-top:40px">
    <div class="codebar"><span class="dot"></span><span class="dot"></span><span class="dot"></span><span>Provider manifest example</span></div>
    <pre><span style="color:#a3a3a3"># Every provider gets a manifest entry like this:</span>
{{
  <span style="color:#7dd3fc">"provider"</span>: <span style="color:#86efac">"your-name"</span>,
  <span style="color:#7dd3fc">"api"</span>: <span style="color:#86efac">"SG Company Registry Lookup"</span>,
  <span style="color:#7dd3fc">"endpoints"</span>: [<span style="color:#86efac">"/company/{uen}"</span>],
  <span style="color:#7dd3fc">"price_per_call"</span>: <span style="color:#86efac">"$0.02"</span>,
  <span style="color:#7dd3fc">"network"</span>: <span style="color:#86efac">"eip155:8453"</span>,
  <span style="color:#7dd3fc">"pay_to"</span>: <span style="color:#86efac">"0xYourWallet..."</span>,
  <span style="color:#7dd3fc">"source"</span>: <span style="color:#86efac">"bizfile.acra.gov.sg"</span>,
  <span style="color:#7dd3fc">"schema_version"</span>: <span style="color:#86efac">"1.0.0"</span>
}}</pre>
  </div>
</section>

__FOOTER__
</body>
</html>""".replace("__NAV__", NAV_HTML).replace("__FOOTER__", FOOTER_HTML).replace("__BASE_CSS__", BASE_CSS)


@router.get("/setup", response_class=HTMLResponse)
async def setup_page():
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Setup — Bounty API</title>
<meta name="description" content="Install Bounty in your AI agent. MCP server, x402 wallet setup, and quickstart guide for Claude, Cursor, GPT, and more.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>__BASE_CSS__
</style>
</head>
<body>
__NAV__
<div class="hero">
  <div class="eyebrow">Agent setup guide</div>
  <h1>Give your agent data and a wallet.</h1>
  <p>Two things make Bounty work for autonomous agents: an MCP connection to discover APIs, and a funded wallet to pay for premium endpoints. Here's how to set up both.</p>
</div>

<section class="content">
  <div class="grid2">
    <div class="card">
      <h3>Step 1: Install Bounty MCP</h3>
      <p style="margin-bottom:16px">Connect your agent to Bounty's API catalog via MCP (Model Context Protocol).</p>
      <div class="codeblock">
        <div class="codebar"><span class="dot"></span><span class="dot"></span><span class="dot"></span><span>npm (global install)</span></div>
        <pre><span style="color:#a3a3a3"># Install and run</span>
npx bountyapi-mcp

<span style="color:#a3a3a3"># Or add to your MCP config:</span>
{{
  <span style="color:#7dd3fc">"mcpServers"</span>: {{
    <span style="color:#7dd3fc">"bounty"</span>: {{
      <span style="color:#7dd3fc">"command"</span>: <span style="color:#86efac">"npx"</span>,
      <span style="color:#7dd3fc">"args"</span>: [<span style="color:#86efac">"-y"</span>, <span style="color:#86efac">"bountyapi-mcp"</span>]
    }}
  }}
}}</pre>
      </div>
      <p style="margin-top:12px;color:var(--faint);font-size:13px">Works with Claude Desktop, Cursor, Windsurf, and any MCP-compatible client. Also available as HTTP transport at <span class="tag">/mcp</span></p>
    </div>
    <div class="card">
      <h3>Step 2: Fund a Base wallet</h3>
      <p style="margin-bottom:16px">Premium endpoints cost USDC on Base. Fund a wallet your agent controls.</p>
      <div class="list">
        <div class="item"><span class="check">1</span><div><strong>Create a wallet</strong><span>MetaMask, Rabby, or Coinbase Wallet. Select Base network.</span></div></div>
        <div class="item"><span class="check">2</span><div><strong>Get USDC on Base</strong><span>Bridge USDC from Ethereum, or buy directly on Base via Coinbase.</span></div></div>
        <div class="item"><span class="check">3</span><div><strong>Give agent the private key</strong><span>Set as environment variable. Agent signs payments autonomously.</span></div></div>
        <div class="item"><span class="check">4</span><div><strong>Test with small amounts</strong><span>Start with $1 USDC. Each call costs $0.005-$0.01.</span></div></div>
      </div>
      <div class="codeblock" style="margin-top:16px">
        <div class="codebar"><span class="dot"></span><span class="dot"></span><span class="dot"></span><span>Agent wallet config</span></div>
        <pre><span style="color:#a3a3a3"># Environment variables for x402</span>
<span style="color:#7dd3fc">AGENT_WALLET_KEY</span>=<span style="color:#86efac">0x...your_private_key</span>
<span style="color:#7dd3fc">AGENT_WALLET_ADDRESS</span>=<span style="color:#86efac">0x...your_address</span>
<span style="color:#7dd3fc">X402_NETWORK</span>=<span style="color:#86efac">eip155:8453</span>  <span style="color:#a3a3a3"># Base</span></pre>
      </div>
    </div>
  </div>

  <h2 style="font-size:28px;letter-spacing:-.05em;margin:56px 0 24px">Compatible agents</h2>
  <div class="grid2">
    <div class="card">
      <h3>Claude Desktop</h3>
      <p>Add Bounty to your MCP config file. Claude can then discover and call any Bounty endpoint. Free endpoints work immediately. Paid endpoints need a wallet.</p>
    </div>
    <div class="card">
      <h3>Cursor / Windsurf</h3>
      <p>Settings &rarr; MCP &rarr; Add server. Paste the npx config. Your AI coding assistant can now call data APIs inline.</p>
    </div>
    <div class="card">
      <h3>Custom agents</h3>
      <p>Any agent that speaks MCP or HTTP can use Bounty. For HTTP, hit endpoints directly. For x402, use the x402 client SDK to handle payment negotiation.</p>
    </div>
    <div class="card">
      <h3>ChatGPT / GPT</h3>
      <p>Use the HTTP transport at <span class="tag">https://bountyapi.com/mcp</span> or call free endpoints directly via URL fetch. Paid endpoints require an x402 client.</p>
    </div>
  </div>

  <div class="codeblock" style="margin-top:40px">
    <div class="codebar"><span class="dot"></span><span class="dot"></span><span class="dot"></span><span>Quick test (no wallet needed)</span></div>
    <pre><span style="color:#a3a3a3"># Free endpoints work without payment:</span>
curl https://bountyapi.com/bsd?price=2000000
curl https://bountyapi.com/currency/convert?from=USD&amp;to=SGD&amp;amount=100
curl https://bountyapi.com/mortgage/calculate \\
  -X POST -H <span style="color:#86efac">"content-type: application/json"</span> \\
  -d <span style="color:#86efac">'{{"principal":500000,"annual_interest_rate":3.5,"loan_term_years":30}}'</span>

<span style="color:#a3a3a3"># MCP discovery (stdio):</span>
npx bountyapi-mcp

<span style="color:#a3a3a3"># MCP discovery (HTTP):</span>
curl -X POST https://bountyapi.com/mcp \\
  -H <span style="color:#86efac">"content-type: application/json"</span> \\
  -H <span style="color:#86efac">"accept: application/json, text/event-stream"</span> \\
  -d <span style="color:#86efac">'{{"jsonrpc":"2.0","id":1,"method":"initialize","params":{{"protocolVersion":"2024-11-05","capabilities":{{}},"clientInfo":{{"name":"test","version":"1.0"}}}}}}'</span></pre>
  </div>
</section>

__FOOTER__
</body>
</html>""".replace("__NAV__", NAV_HTML).replace("__FOOTER__", FOOTER_HTML).replace("__BASE_CSS__", BASE_CSS)


@router.get("/manifest.json", response_class=JSONResponse)
async def manifest():
    """Machine-readable marketplace manifest for agent discovery."""
    return {
        "name": "Bounty API",
        "description": "Pay-per-call data APIs for AI agents: company intelligence, news, job postings, app reviews, and Singapore property/financial workflows.",
        "url": "https://bountyapi.com",
        "version": "3.0.0",
        "payment": {
            "protocol": "x402",
            "network": "eip155:8453",
            "token": "USDC",
            "chain": "Base",
            "facilitator": "https://facilitator.payai.network"
        },
        "mcp": {
            "stdio": "npx bountyapi-mcp",
            "http": "https://bountyapi.com/mcp",
            "npm_package": "bountyapi-mcp",
            "npm_version": "1.8.0",
            "tools": 27
        },
        "stats": {
            "total_apis": 35,
            "mcp_tools": 27,
            "free_endpoints": 20,
            "paid_endpoints": 15
        },
        "region_live": "Global + Singapore",
        "positioning": "agent-native data infrastructure, not just Singapore property",
        "global_categories_live": ["company-intelligence", "news", "jobs", "app-reviews"],
        "singapore_vertical_live": ["property", "tax", "affordability", "location", "salary"],
        "region_roadmap": "HK, UAE, AU, JP",
        "apis": [
            {"name": "Company Intelligence", "slug": "company-intel", "endpoints": ["/company/{domain}"], "price": "$0.05", "free": False, "region": "Global", "category": "company-intelligence", "source": "Company website"},
            {"name": "News Search", "slug": "news-search", "endpoints": ["/news/search"], "price": "$0.01", "free": False, "region": "Global", "category": "news", "source": "News RSS"},
            {"name": "Job Search", "slug": "job-search", "endpoints": ["/jobs/search"], "price": "$0.02", "free": False, "region": "Global", "category": "jobs", "source": "Remote OK, Hacker News Algolia"},
            {"name": "App Reviews", "slug": "app-reviews", "endpoints": ["/reviews/app/{country}/{app_id}"], "price": "$0.02", "free": False, "region": "Global", "category": "app-reviews", "source": "Apple App Store RSS"},
            {"name": "SG Stamp Duty (BSD+ABSD)", "slug": "stamp-duty", "endpoints": ["/bsd", "/absd", "/stamp-duty"], "price": "$0.00", "free": True, "region": "Singapore", "category": "property-tax", "source": "iras.gov.sg"},
            {"name": "Postal District Mapper", "slug": "postal-district", "endpoints": ["/postal/{code}", "/postal/districts"], "price": "$0.00", "free": True, "region": "Singapore", "category": "geography", "source": "Static reference"},
            {"name": "SG Address Intelligence", "slug": "address-intel", "endpoints": ["/address/{code}"], "price": "$0.00", "free": True, "region": "Singapore", "category": "geography", "source": "URA/LTA/SLA"},
            {"name": "SG MRT Intelligence", "slug": "mrt", "endpoints": ["/mrt/near/{code}", "/mrt/search"], "price": "$0.00", "free": True, "region": "Singapore", "category": "transport", "source": "LTA"},
            {"name": "SG Income Tax", "slug": "income-tax", "endpoints": ["/tax/income"], "price": "$0.00", "free": True, "region": "Singapore", "category": "tax", "source": "iras.gov.sg"},
            {"name": "SG GST Calculator", "slug": "gst", "endpoints": ["/gst"], "price": "$0.00", "free": True, "region": "Singapore", "category": "tax", "source": "iras.gov.sg"},
            {"name": "SG Agent Commission", "slug": "commission", "endpoints": ["/commission"], "price": "$0.00", "free": True, "region": "Singapore", "category": "property", "source": "CEA norms"},
            {"name": "SG CPF Housing", "slug": "cpf-housing", "endpoints": ["/cpf/housing"], "price": "$0.00", "free": True, "region": "Singapore", "category": "property", "source": "CPF Board"},
            {"name": "SG Salary Benchmark", "slug": "salary", "endpoints": ["/salary/search"], "price": "$0.00", "free": True, "region": "Singapore", "category": "careers", "source": "MyCareersFuture"},
            {"name": "SG Property Tax", "slug": "property-tax", "endpoints": ["/property-tax"], "price": "$0.00", "free": True, "region": "Singapore", "category": "tax", "source": "iras.gov.sg"},
            {"name": "SG Buy-vs-Rent Analysis", "slug": "buy-vs-rent", "endpoints": ["/buy-vs-rent"], "price": "$0.00", "free": True, "region": "Singapore", "category": "property", "source": "Composite"},
            {"name": "SG School Proximity", "slug": "schools", "endpoints": ["/schools/near/{code}", "/schools/list"], "price": "$0.00", "free": True, "region": "Singapore", "category": "education", "source": "OpenStreetMap"},
            {"name": "HDB EIP/SPR Quota", "slug": "hdb-eip", "endpoints": ["/hdb/eip/{town}"], "price": "$0.00", "free": True, "region": "Singapore", "category": "property", "source": "HDB"},
            {"name": "HDB Lease Decay", "slug": "hdb-lease", "endpoints": ["/hdb/lease-decay"], "price": "$0.00", "free": True, "region": "Singapore", "category": "property", "source": "HDB/MAS/CPF"},
            {"name": "Mortgage Calculator", "slug": "mortgage", "endpoints": ["/mortgage/calculate"], "price": "$0.00", "free": True, "region": "Global", "category": "finance", "source": "Standard formula"},
            {"name": "Investment Growth", "slug": "compound", "endpoints": ["/invest/calculate"], "price": "$0.00", "free": True, "region": "Global", "category": "finance", "source": "Standard formula"},
            {"name": "Currency Converter", "slug": "currency", "endpoints": ["/currency/convert", "/currency/rates"], "price": "$0.00", "free": True, "region": "Global", "category": "fx", "source": "ECB via frankfurter.app"},
            {"name": "URA Status", "slug": "ura-status", "endpoints": ["/ura/status"], "price": "$0.00", "free": True, "region": "Singapore", "category": "property-data", "source": "URA Developer API"},
            {"name": "Rental Yield", "slug": "rental-yield", "endpoints": ["/rental-yield/calculate"], "price": "$0.005", "free": False, "region": "Global", "category": "real-estate", "source": "Standard formulas"},
            {"name": "SG Affordability (TDSR/MSR)", "slug": "affordability", "endpoints": ["/affordability"], "price": "$0.01", "free": False, "region": "Singapore", "category": "property", "source": "MAS/HDB rules"},
            {"name": "HDB Resale Data", "slug": "hdb-resale", "endpoints": ["/hdb/towns", "/hdb/median/{town}", "/hdb/search"], "price": "$0.01", "free": False, "region": "Singapore", "category": "property-data", "source": "data.gov.sg"},
            {"name": "URA Transactions", "slug": "ura-transactions", "endpoints": ["/ura/transactions"], "price": "$0.05", "free": False, "region": "Singapore", "category": "property-data", "source": "URA Developer API"},
            {"name": "URA Median Rentals", "slug": "ura-rental-median", "endpoints": ["/ura/rental-median"], "price": "$0.05", "free": False, "region": "Singapore", "category": "property-data", "source": "URA Developer API"},
            {"name": "URA Developer Sales", "slug": "ura-developer-sales", "endpoints": ["/ura/developer-sales"], "price": "$0.05", "free": False, "region": "Singapore", "category": "property-data", "source": "URA Developer API"},
            {"name": "URA Pipeline Supply", "slug": "ura-pipeline", "endpoints": ["/ura/pipeline"], "price": "$0.05", "free": False, "region": "Singapore", "category": "property-data", "source": "URA Developer API"},
            {"name": "URA Rental Contracts", "slug": "ura-rental-contracts", "endpoints": ["/ura/rental-contracts"], "price": "$0.05", "free": False, "region": "Singapore", "category": "property-data", "source": "URA Developer API"},
            {"name": "SG Property Analysis", "slug": "property-analysis", "endpoints": ["/property/analyze"], "price": "$0.05", "free": False, "region": "Singapore", "category": "property", "source": "Composite"},
            {"name": "SG Property Pitch", "slug": "property-pitch", "endpoints": ["/property/pitch"], "price": "$0.05", "free": False, "region": "Singapore", "category": "property", "source": "Composite"},
            {"name": "SG Property Ranking", "slug": "property-rank", "endpoints": ["/property/rank"], "price": "$0.10", "free": False, "region": "Singapore", "category": "property", "source": "Composite"},
        ],
        "provider_program": {
            "url": "https://bountyapi.com/providers",
            "revenue_share": "97%",
            "platform_fee": "3%",
            "requirements": ["verified-data", "stable-schema", "source-provenance", "sub-second-response"]
        },
        "links": {
            "docs": "https://bountyapi.com/docs",
            "pricing": "https://bountyapi.com/pricing",
            "setup": "https://bountyapi.com/setup",
            "providers": "https://bountyapi.com/providers",
            "llms": "https://bountyapi.com/llms.txt",
            "npm": "https://www.npmjs.com/package/bountyapi-mcp"
        }
    }


@router.get("/pricing.json", response_class=JSONResponse)
async def pricing_json():
    """Machine-readable pricing data for agents and crawlers."""
    return {
        "currency": "USDC",
        "network": "Base (eip155:8453)",
        "payment_protocol": "x402",
        "free_endpoints": [
            {"path": "/bsd", "description": "Singapore buyer stamp duty"},
            {"path": "/absd", "description": "Additional buyer stamp duty"},
            {"path": "/stamp-duty", "method": "POST", "description": "Full stamp duty calculation"},
            {"path": "/postal/{code}", "description": "Postal code to district"},
            {"path": "/postal/districts", "description": "All postal districts"},
            {"path": "/address/{code}", "description": "Full address intelligence (district, region, MRT proximity)"},
            {"path": "/mrt/near/{code}", "description": "Nearest MRT stations to a postal code"},
            {"path": "/mrt/search", "description": "Search MRT station database"},
            {"path": "/mortgage/calculate", "method": "POST", "description": "Mortgage payment calculator"},
            {"path": "/invest/calculate", "method": "POST", "description": "Compound interest calculator"},
            {"path": "/currency/convert", "description": "Currency conversion"},
            {"path": "/currency/rates", "description": "Live exchange rates"},
            {"path": "/tax/income", "description": "Singapore income tax calculator"},
            {"path": "/gst", "description": "GST add/remove calculator"},
            {"path": "/commission", "description": "Property agent commission calculator"},
            {"path": "/cpf/housing", "description": "CPF OA accumulation for housing"},
            {"path": "/salary/search", "description": "Salary benchmark from live job postings"},
            {"path": "/property-tax", "description": "Property tax calculator"},
            {"path": "/buy-vs-rent", "description": "Buy-vs-rent total cost comparison"},
            {"path": "/schools/near/{code}", "description": "Schools within 1km/2km of postal code"},
            {"path": "/hdb/eip/{town}", "description": "HDB ethnic integration policy quota check"},
            {"path": "/hdb/lease-decay", "description": "HDB lease decay analysis"},
            {"path": "/ura/status", "description": "URA API connection status"}
        ],
        "paid_endpoints": [
            {"path": "/company/{domain}", "price": "$0.05", "price_atomic": "50000", "description": "Company website intelligence: tech stack, contacts, social links, SSL, security headers"},
            {"path": "/news/search", "price": "$0.01", "price_atomic": "10000", "description": "Current news search by keyword"},
            {"path": "/jobs/search", "price": "$0.02", "price_atomic": "20000", "description": "Job postings and hiring signals"},
            {"path": "/reviews/app/{country}/{app_id}", "price": "$0.02", "price_atomic": "20000", "description": "Recent App Store reviews with rating sample and topic flags"},
            {"path": "/rental-yield/calculate", "method": "POST", "price": "$0.005", "price_atomic": "5000", "description": "Rental yield investment analysis"},
            {"path": "/affordability", "price": "$0.01", "price_atomic": "10000", "description": "TDSR/MSR affordability check"},
            {"path": "/hdb/towns", "price": "$0.01", "price_atomic": "10000", "description": "HDB resale town-level data"},
            {"path": "/hdb/median/{town}", "price": "$0.01", "price_atomic": "10000", "description": "HDB resale median by town"},
            {"path": "/hdb/search", "price": "$0.01", "price_atomic": "10000", "description": "Search HDB resale transactions"},
            {"path": "/ura/transactions", "price": "$0.05", "price_atomic": "50000", "description": "Private property caveat-level transactions"},
            {"path": "/ura/rental-median", "price": "$0.05", "price_atomic": "50000", "description": "Median private rental rates by project"},
            {"path": "/ura/developer-sales", "price": "$0.05", "price_atomic": "50000", "description": "Developer units sold"},
            {"path": "/ura/pipeline", "price": "$0.05", "price_atomic": "50000", "description": "Future supply pipeline"},
            {"path": "/ura/rental-contracts", "price": "$0.05", "price_atomic": "50000", "description": "Rental contract statistics"},
            {"path": "/property/analyze", "method": "POST", "price": "$0.05", "price_atomic": "50000", "description": "Full property analysis composite"},
            {"path": "/property/pitch", "method": "POST", "price": "$0.05", "price_atomic": "50000", "description": "Client-ready investment thesis"},
            {"path": "/property/rank", "method": "POST", "price": "$0.10", "price_atomic": "100000", "description": "Multi-property comparison ranking"}
        ],
        "note": "Atomic units are USDC 6-decimal. Payment via x402 PAYMENT-SIGNATURE header."
    }
