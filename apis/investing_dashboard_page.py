"""Standalone investing-first product shell served at ``/dashboard``."""

from fastapi.responses import HTMLResponse


INVESTING_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="light">
  <meta name="theme-color" content="#f4f1e8">
  <meta name="description" content="Bounty Investor Radar — persisted signals for investment research.">
  <title>Bounty — Investor Radar</title>
  <link rel="icon" href="/favicon.ico">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Instrument+Sans:wdth,wght@75..100,400..700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/investing-dashboard.css">
  <script src="/investing-dashboard.js" defer></script>
</head>
<body>
<a class="skip-link" href="#investing-desk">Skip to investor radar</a>

<header class="masthead">
  <div class="masthead-inner">
    <a class="brand" href="/dashboard" aria-label="Bounty Investor Radar home">
      <span class="brand-name">BOUNTY</span>
      <span class="brand-sub">Investor Radar</span>
    </a>
    <div class="masthead-actions">
      <button class="text-button" id="set-token" type="button">Set API token</button>
      <a class="classic-link" href="/dashboard/classic">Open Classic Bounty <span aria-hidden="true">↗</span></a>
    </div>
  </div>
</header>

<div class="product-shell">
  <aside class="navigation-rail" aria-label="Workspace navigation">
    <p class="rail-label">Workspace</p>
    <nav class="product-nav" aria-label="Product">
      <button class="nav-item active" type="button" data-view="radar" aria-current="page"><span>01</span>Radar</button>
      <button class="nav-item" type="button" data-view="research"><span>02</span>Research</button>
      <button class="nav-item" type="button" data-view="monitors"><span>03</span>Monitors</button>
      <button class="nav-item" type="button" data-view="usage"><span>04</span>Usage</button>
    </nav>
    <section class="classic-note" aria-labelledby="classic-note-title">
      <p class="eyebrow">The full workbench</p>
      <h2 id="classic-note-title">Your earlier workflows are still here.</h2>
      <p>Classic research, projects, and lenses remain available.</p>
      <a href="/dashboard/classic">Open Classic Bounty</a>
    </section>
  </aside>

  <main id="investing-desk" tabindex="-1">
    <div class="notice error hidden" id="global-error" role="alert"></div>

    <section class="view active" id="view-radar" aria-labelledby="radar-title">
      <header class="page-head">
        <div>
          <p class="eyebrow">01 / Private information-arbitrage desk</p>
          <h1 id="radar-title">Find specific behavior shifts before they become consensus.</h1>
          <p>Run an owned-source scan. Only candidates that pass historical, behavior, breadth, citation, and information-parity checks appear here.</p>
        </div>
        <button class="primary-action" id="reload-radar" type="button">Run private scan</button>
      </header>

      <section class="radar-controls hidden" aria-labelledby="scope-title">
        <div class="control-intro">
          <p class="eyebrow">Scope</p>
          <h2 id="scope-title">Global by default</h2>
          <p>Narrow the persisted feed only when a market or topic area matters to your thesis.</p>
        </div>
        <form id="radar-filters" class="filter-form">
          <label for="country-filter">Country
            <select id="country-filter" name="country">
              <option value="">Global — all markets</option>
            </select>
          </label>
          <label for="category-filter">Category
            <select id="category-filter" name="category">
              <option value="">All categories</option>
            </select>
          </label>
          <button class="secondary-action" type="submit">Apply filters</button>
          <button class="text-button clear-filters" id="clear-filters" type="button">Reset to global</button>
        </form>
      </section>

      <section class="radar-receipt" aria-label="Radar source receipt">
        <div><span>Last private scan</span><strong id="sweep-status">Checking persisted Radar…</strong></div>
        <div><span>Coverage</span><strong id="coverage-status">Coverage pending</strong></div>
      </section>

      <div class="notice stale hidden" id="stale-notice" role="status">
        <strong>Stale radar</strong>
        <span>The displayed data is more than 24 hours old. Collection runs centrally; reload to check for a newer persisted sweep.</span>
      </div>

      <div class="lane-stack">
        <section class="signal-lane social-lane" aria-labelledby="social-title">
          <header class="lane-head">
            <div>
              <p class="lane-number">Lane 01</p>
              <h2 id="social-title">Investment signal review</h2>
              <p>Trade-ready leads stay strict. Early hypotheses remain visible with the exact missing evidence, failed checks, and openable sources.</p>
            </div>
            <span class="lane-status" id="social-status">Checking persisted private scan</span>
          </header>
          <p class="lane-coverage" id="social-coverage">Social coverage pending</p>
          <div class="signal-list" id="social-list" aria-live="polite" aria-busy="true">
            <div class="state-panel loading-state">
              <p class="eyebrow">Loading</p>
              <h3>Checking qualified private Radar output</h3>
              <p>Raw social posts and generic trends are never displayed as leads.</p>
            </div>
          </div>
        </section>

        <section class="signal-lane hidden" aria-labelledby="breaking-title">
          <header class="lane-head">
            <div>
              <p class="lane-number">Lane 02</p>
              <h2 id="breaking-title">Breaking now</h2>
              <p>Live and persisted signals with their source timestamps. Investigate before drawing a conclusion.</p>
            </div>
            <span class="lane-status">Live / persisted</span>
          </header>
          <div class="signal-list" id="breaking-list" aria-live="polite" aria-busy="true">
            <div class="state-panel loading-state">
              <p class="eyebrow">Loading</p>
              <h3>Checking the persisted global feed</h3>
              <p>The radar request is in progress.</p>
            </div>
          </div>
        </section>

        <section class="signal-lane building-lane hidden" aria-labelledby="building-title">
          <header class="lane-head">
            <div>
              <p class="lane-number">Lane 03</p>
              <h2 id="building-title">Building quietly</h2>
              <p>Slower-forming signals intended for longitudinal monitoring.</p>
            </div>
            <span class="lane-status development" id="building-status">In development</span>
          </header>
          <div class="signal-list" id="building-list" aria-live="polite">
            <div class="state-panel development-state">
              <p class="eyebrow">In development</p>
              <h3>No quiet-build feed is available yet</h3>
              <p>This lane stays explicitly empty until the API returns persisted data for it.</p>
            </div>
          </div>
        </section>
      </div>

      <footer class="classic-callout">
        <div>
          <p class="eyebrow">Need the full research desk?</p>
          <h2>Turn a signal into cited research.</h2>
          <p>Classic research, projects, and lenses remain available. Every signal’s Investigate action opens Classic Bounty with the topic pre-filled.</p>
        </div>
        <a class="primary-link" href="/dashboard/classic">Open Classic Bounty</a>
      </footer>
    </section>

    <section class="view" id="view-research" aria-labelledby="research-title">
      <header class="page-head compact-head">
        <div>
          <p class="eyebrow">02 / Evidence workbench</p>
          <h1 id="research-title">Research</h1>
          <p>Move from an observed signal to a bounded, cited read of the conversations behind it.</p>
        </div>
      </header>
      <section class="handoff-panel">
        <p class="eyebrow">Available in Classic Bounty</p>
        <h2>Projects, research runs, findings, and lenses remain available.</h2>
        <p>This investing-first workspace does not invent a second research flow. Use the established Classic Bounty workbench for deep investigation and source-backed findings.</p>
        <a class="primary-link" href="/dashboard/classic">Open Classic Bounty</a>
      </section>
    </section>

    <section class="view" id="view-monitors" aria-labelledby="monitors-title">
      <header class="page-head compact-head">
        <div>
          <p class="eyebrow">03 / Standing reads</p>
          <h1 id="monitors-title">Monitors</h1>
          <p>Track a bounded subject over time without confusing a fresh spike for durable change.</p>
        </div>
      </header>
      <div class="state-panel development-state wide-state">
        <p class="eyebrow">In development</p>
        <h2>Investing monitors are not connected yet</h2>
        <p>No monitor activity is displayed until the investing API provides it. Existing recurring research remains available in Classic Bounty.</p>
        <a href="/dashboard/classic#monitors">Open Classic Bounty monitors</a>
      </div>
    </section>

    <section class="view" id="view-usage" aria-labelledby="usage-title">
      <header class="page-head compact-head">
        <div>
          <p class="eyebrow">04 / Work receipts</p>
          <h1 id="usage-title">Usage</h1>
          <p>Review recorded investing-radar work when usage receipts become available.</p>
        </div>
      </header>
      <div class="state-panel development-state wide-state">
        <p class="eyebrow">In development</p>
        <h2>No investing usage receipt is available</h2>
        <p>This page will remain empty rather than estimate work that the API has not reported.</p>
      </div>
    </section>
  </main>
</div>

<div class="toast hidden" id="toast" role="status" aria-live="polite"></div>
</body>
</html>"""


def get_investing_dashboard_html() -> HTMLResponse:
    return HTMLResponse(INVESTING_DASHBOARD_HTML)
