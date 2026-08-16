"""Semantic, build-free product shell served at ``/dashboard``."""

from fastapi.responses import HTMLResponse

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="light">
  <meta name="theme-color" content="#f4f1e8">
  <title>Bounty — Research API</title>
  <link rel="icon" href="/favicon.ico">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Instrument+Sans:wdth,wght@75..100,400..700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/dashboard.css">
  <script src="/dashboard.js" defer></script>
</head>
<body>
<a class="skip-link" href="#desk">Skip to research desk</a>

<header class="masthead" id="masthead">
  <div class="masthead-top">
    <a class="brand" href="/dashboard" aria-label="Bounty Research API home"><span class="brand-name">BOUNTY</span><span class="brand-sub">Research API</span></a>
    <button class="menu-toggle" id="menu-toggle" type="button" aria-expanded="false" aria-controls="sidebar">Menu</button>
    <div class="masthead-utils">
      <div class="workspace-control">
        <label for="workspace-key">Workspace key</label>
        <div class="input-pair"><input id="workspace-key" value="default" autocomplete="off" spellcheck="false"><button id="save-workspace" class="quiet">Use</button></div>
        <p>Local browser setting. This does not provide user isolation.</p>
      </div>
      <button id="set-token" class="text-button">Set API token</button>
      <button class="tour-btn" id="start-tour">Take the tour</button>
    </div>
  </div>
  <nav class="masthead-nav" id="sidebar" aria-label="Product">
    <button class="nav-item active" data-view="explore"><span class="nav-no">01</span><span class="nav-label">Explore</span></button>
    <button class="nav-item" data-view="projects"><span class="nav-no">02</span><span class="nav-label">Projects</span></button>
    <button class="nav-item" data-view="findings"><span class="nav-no">03</span><span class="nav-label">Findings</span></button>
    <button class="nav-item" data-view="lenses"><span class="nav-no">04</span><span class="nav-label">Lenses</span></button>
    <button class="nav-item" data-view="monitors"><span class="nav-no">05</span><span class="nav-label">Monitors</span></button>
    <button class="nav-item" data-view="usage"><span class="nav-no">06</span><span class="nav-label">Usage</span></button>
  </nav>
</header>

<main id="desk" tabindex="-1">
  <div id="global-error" class="notice error hidden" role="alert"></div>

  <section class="view active" id="view-explore" aria-labelledby="explore-title">
    <header class="page-head"><div><p class="eyebrow">01 / Discovery desk</p><h1 id="explore-title">Global Explore</h1><p>Two ways in: research a niche you can already name, or scan for emerging conversations you did not know about.</p></div><button class="quiet" id="refresh-families">Refresh register</button></header>

    <section class="zone" aria-labelledby="scan-composer-title">
      <div class="zone-head"><h2 id="scan-composer-title"><span class="zone-no">01</span>Scan composer</h2><p class="zone-note">Explicit action only. Nothing runs until you press a button.</p></div>
      <div class="composer-grid">
        <div class="composer-half">
          <p class="eyebrow">Workflow A · Known topic</p>
          <h3>Bounded research brief</h3>
          <p class="composer-copy">Name the niche you want read — for example, Cairn buyer language — and get pain points, objections, workarounds, competitor mentions, and verbatim audience words with citations.</p>
          <div class="research-brief-grid">
            <label>Research brief<input id="direct-research-name" type="text" maxlength="160" required placeholder="e.g. Cairn buyer language"></label>
            <label>Use case<select id="direct-preset"><option value="general-research">General research</option><option value="marketing-intelligence">Marketing intelligence</option><option value="product-opportunity">Product opportunities</option><option value="investing-social-arbitrage">Investing / social arbitrage</option></select></label>
            <label class="research-topics">Topics, one per line (maximum 5)<textarea id="direct-topic" rows="4" aria-describedby="direct-topic-help" placeholder="Meta ads reporting\\nad creative fatigue\\nAI ad creative tools" autocomplete="off"></textarea></label>
            <button class="primary" id="research-topic-btn">Start research</button>
          </div>
          <p class="form-help" id="direct-topic-help">Submit up to five related topics. Bounty saves the run, reads available conversations in the background, and preserves explicit source gaps.</p>
        </div>
        <div class="composer-half">
          <p class="eyebrow">Workflow B · Emerging trends</p>
          <h3>Live trend scan</h3>
          <p class="composer-copy">Use this when you do not yet know which topics matter. Search activity is a discovery signal, not evidence.</p>
          <ol class="scan-instructions" aria-label="How to find topics">
            <li><span>01</span><strong>Choose a country</strong><small>This sets the search market.</small></li>
            <li><span>02</span><strong>Start balanced</strong><small>No growth number is required. Choose an area only when you already have a focus.</small></li>
            <li><span>03</span><strong>Find, then research</strong><small>Select up to five useful topics, then press Research these topics for cited evidence.</small></li>
          </ol>
          <form id="explore-form" class="filter-bar">
            <label>Country<select id="explore-geo" required><option value="US">United States</option></select><span class="control-help">Country controls which search market is scanned.</span></label>
            <label>Topic area<select id="explore-cat-filter"><option value="">Balanced across all categories</option></select><span class="control-help">Recommended: leave this balanced. Choose one area only when you already have a focus.</span></label>
            <input id="explore-volume" type="hidden" value="0">
            <input id="explore-growth" type="hidden" value="0">
            <input id="explore-age" type="hidden" value="0">
            <label class="check"><input id="explore-verified" type="checkbox"><span>Also check for matching public posts<small>Optional. A failed post check never hides a search topic.</small></span></label>
            <label>Conversation-research angle (used after you choose a topic)<select id="explore-lens"><option value="">No lens</option></select></label>
            <p class="filter-note">Search activity is a discovery signal, not evidence. Missing metrics are shown as unavailable, not zero. Bounty does not ask you to guess a growth threshold.</p>
            <button class="primary" type="submit">Find topics</button>
          </form>
        </div>
      </div>
    </section>

    <section class="zone zone-receipt" aria-labelledby="run-receipt-title">
      <div class="zone-head"><h2 id="run-receipt-title"><span class="zone-no">02</span>Run receipt</h2><p class="zone-note">What actually ran, in plain terms. Missing evidence stays missing.</p></div>
      <div class="receipt-block">
        <div class="receipt-row"><span class="receipt-label">Persisted evidence</span><p id="global-explore-status" class="receipt-text">Loading persisted topic families. This view does not start a new collection run.</p></div>
        <div class="receipt-row"><span class="receipt-label">Live run</span><div id="explore-preview" class="receipt-text">Type a topic above to start researching, or choose a country and find topics drawing search attention.</div></div>
      </div>
    </section>

    <div class="register-layout">
      <section class="zone zone-flush" aria-labelledby="candidate-register-title">
        <div class="zone-head"><h2 id="candidate-register-title"><span class="zone-no">03</span>Candidate register</h2><p class="zone-note">Persisted families first, then live scan results.</p></div>
        <div class="register-block">
          <div class="register-block-head"><h3 id="changing-title">Global Explore — What's changing</h3><span id="family-count" class="mono muted">Not loaded</span></div>
          <p class="section-copy">This is the saved standing register, separate from the live topic scan below. Recommendations preserve source coverage, route reasons, trajectory periods, and rejected families.</p>
          <section class="global-controls" aria-label="Global Explore controls">
            <label>Perspective<select id="global-perspective"><option value="">All evidence</option></select></label>
            <label>Stage<select id="global-stage"><option value="">All stages</option><option value="emerging">Emerging</option><option value="confirming">Confirming</option><option value="established">Established</option><option value="event_spike">Event spike</option><option value="cooling">Cooling</option><option value="observed">Observed</option><option value="unclear">Unclear</option></select></label>
            <label>Saved-family country<select id="global-geo"><option value="">All countries</option><option value="GLOBAL">Global / multi-country</option></select></label>
            <label class="check"><input id="global-include-rejected" type="checkbox">Include rejected or unclear</label>
          </section>
          <div id="family-grid" class="family-grid" aria-live="polite"><div class="empty bordered"><p class="eyebrow">Loading</p><h2>Checking persisted evidence</h2><p>No live sources are being called.</p></div></div>
        </div>
        <div class="register-block">
          <div class="register-block-head"><h3>Trend scan results</h3><span id="explore-count" class="mono muted">No search yet</span></div>
          <div id="explore-results" class="row-list" aria-live="polite"><div class="empty compact"><h3>No topics yet</h3><p>Choose a country and topic area, then press Find topics above.</p></div></div>
        </div>
      </section>

      <section class="zone zone-flush" aria-labelledby="evidence-sheet-title">
        <div class="zone-head"><h2 id="evidence-sheet-title"><span class="zone-no">04</span>Evidence sheet</h2><p class="zone-note">Open a register entry to read its evidence.</p></div>
        <div class="register-block">
          <div class="register-block-head"><h3>Topic family evidence</h3></div>
          <aside id="family-detail" class="family-detail" aria-live="polite"><div class="empty"><p class="eyebrow">Evidence</p><h2>Select a topic family</h2><p>Open a family to inspect member terms, trajectory, corroboration, propagation, coverage, and limitations.</p></div></aside>
        </div>
        <div class="register-block">
          <div class="register-block-head"><h3>Scan result evidence</h3></div>
          <section id="explore-detail" class="detail-pane evidence-sheet-pane"><div class="empty"><p class="eyebrow">Search signal</p><h2>Select a topic</h2><p>Inspect its search signal and related queries. Public evidence appears only after a separate research run.</p></div></section>
        </div>
      </section>
    </div>
  </section>

  <section class="view" id="view-projects" aria-labelledby="projects-title">
    <header class="page-head"><div><p class="eyebrow">Research scope</p><h1 id="projects-title">Projects</h1><p>Organize subjects before collecting conversations.</p></div><button class="primary" data-open="project-dialog">New project</button></header>
    <div class="split split-projects">
      <section class="index-pane" aria-labelledby="project-list-title"><div class="section-head"><h2 id="project-list-title">Project index</h2><span id="project-count" class="mono muted">—</span></div><div id="project-list" class="row-list" aria-live="polite"></div></section>
      <section class="detail-pane" id="project-detail" aria-live="polite"><div class="empty"><p class="eyebrow">No selection</p><h2>Select a project</h2><p>Subjects, status, and research actions appear here.</p></div></section>
    </div>
  </section>

  <section class="view" id="view-findings" aria-labelledby="findings-title">
    <header class="page-head"><div><p class="eyebrow">Results</p><h1 id="findings-title">Findings</h1><p>What people are saying, with cited evidence.</p></div><button class="quiet" data-view-link="explore">Explore</button></header>
    <section class="zone"><div class="zone-head"><h2><span class="zone-no">01</span>Saved research</h2><p class="zone-note">Every brief you started, with its durable run state.</p></div><div id="research-run-history" class="research-run-history" aria-live="polite"></div></section>
    <section class="zone"><div class="zone-head"><h2><span class="zone-no">02</span>Results</h2><p class="zone-note">Signals, evidence records, coverage, and limitations.</p></div><div id="findings-content"><div class="empty bordered"><p class="eyebrow">No results yet</p><h2>Nothing analyzed yet</h2><p>Research a topic in Explore to see what people are saying. Findings will appear here with signals, evidence, and limitations.</p></div></div></section>
  </section>

  <section class="view" id="view-lenses" aria-labelledby="lenses-title">
    <header class="page-head"><div><p class="eyebrow">Evaluation rules</p><h1 id="lenses-title">Lenses</h1><p>Versioned criteria for reading findings in context.</p></div><button class="primary" data-open="lens-dialog">New lens</button></header>
    <div id="lens-list" class="table-wrap" aria-live="polite"></div>
  </section>

  <section class="view" id="view-monitors" aria-labelledby="monitors-title">
    <header class="page-head"><div><p class="eyebrow">Subject cadence</p><h1 id="monitors-title">Monitors</h1><p>Start or pause recurring attention to a subject. Actions report their actual state.</p></div></header>
    <div id="monitor-list" class="table-wrap" aria-live="polite"></div>
  </section>

  <section class="view" id="view-usage" aria-labelledby="usage-title">
    <header class="page-head"><div><p class="eyebrow">Receipts, not estimates</p><h1 id="usage-title">Usage</h1><p>Inspect recorded work for a completed live discovery run.</p></div></header>
    <div class="usage-query"><label>Discovery run ID<input id="usage-run" class="mono" placeholder="Run an Explore search first"></label><button id="load-usage" class="primary">Load receipt</button></div>
    <div id="usage-content"><div class="empty bordered"><p class="eyebrow">Not checked</p><h2>No receipt selected</h2><p>Enter an actual discovery run ID or run Explore. Planned research runs do not have discovery usage receipts.</p></div></div>
  </section>
</main>

<dialog id="project-dialog"><form id="project-form"><header><p class="eyebrow">New scope</p><h2>Create project</h2></header><label>Name<input name="name" required maxlength="120"></label><label>Description<textarea name="description" rows="3"></textarea></label><label>Default region<input name="default_geo" maxlength="2" placeholder="US"></label><label>First subject (optional)<input name="subject" maxlength="120"></label><div class="dialog-actions"><button type="button" class="quiet" data-close>Cancel</button><button class="primary" type="submit">Create project</button></div></form></dialog>
<dialog id="subject-dialog"><form id="subject-form"><header><p class="eyebrow">Research target</p><h2>Add subject</h2></header><label>Name<input name="name" required maxlength="120"></label><label>Description<textarea name="description" rows="3"></textarea></label><label>Region<input name="geo" maxlength="2" placeholder="Project default"></label><label>Lens<select name="lens_id"><option value="">No lens</option></select></label><label>Cadence (minutes)<input name="cadence_minutes" type="number" min="1" value="10080"></label><div class="dialog-actions"><button type="button" class="quiet" data-close>Cancel</button><button class="primary" type="submit">Add subject</button></div></form></dialog>
<dialog id="lens-dialog"><form id="lens-form"><header><p class="eyebrow">Versioned definition</p><h2 id="lens-dialog-title">Create lens</h2></header><input type="hidden" name="lens_id"><label>Name<input name="name" required maxlength="120"></label><label>Description<textarea name="description" rows="2"></textarea></label><label>Specification (JSON)<textarea class="mono" name="spec" rows="12" required spellcheck="false">{"objective":"Find unmet needs","criteria":[{"criterion_id":"unmet_need","label":"Unmet need","feature_key":"unmet_need","mode":"display","weight":0,"missing_policy":"keep_unknown"}]}</textarea></label><p class="form-help">Start with a valid unmet-need criterion or replace it with registered criteria. Saving an edit creates a new immutable version. Definition changes do not run research.</p><div class="dialog-actions"><button type="button" class="quiet" data-close>Cancel</button><button class="primary" type="submit">Save lens</button></div></form></dialog>
<dialog id="confirm-dialog"><form method="dialog"><header><p class="eyebrow">Live source check</p><h2>Find these topics?</h2></header><div id="confirm-copy" class="prose"></div><div class="limit-grid"><div><span>Returned topics</span><strong>Up to 50</strong></div><div><span>Public-post checks</span><strong>Up to 20 when selected</strong></div></div><p class="form-help">The scan reads the current Google Trends window for the selected country. Optional public-post checks inspect root posts only. They do not read threads or create findings.</p><div class="dialog-actions"><button value="cancel" class="quiet">Cancel</button><button value="confirm" class="primary">Find topics</button></div></form></dialog>
<div id="toast" class="toast hidden" role="status" aria-live="polite"></div>
</body></html>"""


def get_dashboard_html() -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)
