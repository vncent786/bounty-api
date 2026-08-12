"""Semantic, build-free product shell served at ``/dashboard``."""

from fastapi.responses import HTMLResponse

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>Bounty — Research desk</title>
  <link rel="icon" href="/favicon.ico">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Instrument+Sans:wght@400;500;600&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/dashboard.css">
  <script src="/dashboard.js" defer></script>
</head>
<body>
<a class="skip-link" href="#desk">Skip to research desk</a>
<div class="app-shell">
  <header class="mobile-head"><img src="/logo-wordmark-dark-master.png" alt="Bounty"><button class="quiet" id="menu-toggle" aria-expanded="false" aria-controls="sidebar">Menu</button></header>
  <aside class="sidebar" id="sidebar">
    <a class="brand" href="/dashboard" aria-label="Bounty research desk"><img src="/logo-wordmark-dark-master.png" alt="Bounty"></a>
    <p class="eyebrow">Research desk</p>
    <nav aria-label="Product">
      <button class="nav-item active" data-view="projects">Projects</button>
      <button class="nav-item" data-view="explore">Explore</button>
      <button class="nav-item" data-view="findings">Findings</button>
      <button class="nav-item" data-view="lenses">Lenses</button>
      <button class="nav-item" data-view="monitors">Monitors</button>
      <button class="nav-item" data-view="usage">Usage</button>
    </nav>
    <div class="workspace-control">
      <label for="workspace-key">Workspace key</label>
      <div class="input-pair"><input id="workspace-key" value="default" autocomplete="off" spellcheck="false"><button id="save-workspace" class="quiet">Use</button></div>
      <p>Local browser setting. This does not provide user isolation.</p>
      <button id="set-token" class="text-button">Set API token</button>
    </div>
    <button class="tour-btn" id="start-tour">Take the tour</button>
  </aside>

  <main id="desk" tabindex="-1">
    <div id="global-error" class="notice error hidden" role="alert"></div>

    <section class="view active" id="view-projects" aria-labelledby="projects-title">
      <header class="page-head"><div><p class="eyebrow">Research scope</p><h1 id="projects-title">Projects</h1><p>Organize subjects before collecting conversations.</p></div><button class="primary" data-open="project-dialog">New project</button></header>
      <div class="split split-projects">
        <section class="index-pane" aria-labelledby="project-list-title"><div class="section-head"><h2 id="project-list-title">Project index</h2><span id="project-count" class="mono muted">—</span></div><div id="project-list" class="row-list" aria-live="polite"></div></section>
        <section class="detail-pane" id="project-detail" aria-live="polite"><div class="empty"><p class="eyebrow">No selection</p><h2>Select a project</h2><p>Subjects, status, and research actions appear here.</p></div></section>
      </div>
    </section>

    <section class="view" id="view-explore" aria-labelledby="explore-title">
      <header class="page-head"><div><p class="eyebrow">Explicit live action</p><h1 id="explore-title">Explore conversations</h1><p>Find current topics, then inspect the evidence behind each result.</p></div></header>
      <form id="explore-form" class="filter-bar">
        <label>Region<input id="explore-geo" value="US" maxlength="2" required></label>
        <label>Lens<select id="explore-lens"><option value="">No lens</option></select></label>
        <label>Minimum volume<input id="explore-volume" type="number" min="0" value="0"></label>
        <label>Minimum growth (%)<input id="explore-growth" type="number" min="0" value="0"></label>
        <label>Freshness (hours)<input id="explore-age" type="number" min="0" step="1" value="0"></label>
        <label class="check"><input id="explore-verified" type="checkbox">Confirmed checks only</label>
        <button class="primary" type="submit">Review search</button>
      </form>
      <div id="explore-preview" class="notice"><strong>Not run.</strong> A search only begins after you review its limits. It may contact live sources and analysis services.</div>
      <div class="split findings-split">
        <section class="index-pane"><div class="section-head"><h2>Results</h2><span id="explore-count" class="mono muted">Not run</span></div><div id="explore-results" class="row-list"><div class="empty compact"><h3>No current-session results</h3><p>Review and run a search to begin. Searches never run on page load.</p></div></div></section>
        <section class="detail-pane" id="explore-detail"><div class="empty"><p class="eyebrow">Evidence desk</p><h2>Select a result</h2><p>Claims, cited records, and source coverage appear here exactly as returned.</p></div></section>
      </div>
    </section>

    <section class="view" id="view-findings" aria-labelledby="findings-title">
      <header class="page-head"><div><p class="eyebrow">Current session</p><h1 id="findings-title">Findings</h1><p>Evidence from searches run in this browser session.</p></div><button class="quiet" data-view-link="explore">Explore</button></header>
      <div id="findings-content"><div class="empty bordered"><p class="eyebrow">Unavailable after reload</p><h2>No persisted findings reader</h2><p>The API does not expose a complete saved-findings collection. Run Explore to inspect truthful current-session results; research plans remain available separately.</p></div></div>
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
</div>

<dialog id="project-dialog"><form id="project-form"><header><p class="eyebrow">New scope</p><h2>Create project</h2></header><label>Name<input name="name" required maxlength="120"></label><label>Description<textarea name="description" rows="3"></textarea></label><label>Default region<input name="default_geo" maxlength="2" placeholder="US"></label><label>First subject (optional)<input name="subject" maxlength="120"></label><div class="dialog-actions"><button type="button" class="quiet" data-close>Cancel</button><button class="primary" type="submit">Create project</button></div></form></dialog>
<dialog id="subject-dialog"><form id="subject-form"><header><p class="eyebrow">Research target</p><h2>Add subject</h2></header><label>Name<input name="name" required maxlength="120"></label><label>Description<textarea name="description" rows="3"></textarea></label><label>Region<input name="geo" maxlength="2" placeholder="Project default"></label><label>Lens<select name="lens_id"><option value="">No lens</option></select></label><label>Cadence (minutes)<input name="cadence_minutes" type="number" min="1" value="10080"></label><div class="dialog-actions"><button type="button" class="quiet" data-close>Cancel</button><button class="primary" type="submit">Add subject</button></div></form></dialog>
<dialog id="lens-dialog"><form id="lens-form"><header><p class="eyebrow">Versioned definition</p><h2 id="lens-dialog-title">Create lens</h2></header><input type="hidden" name="lens_id"><label>Name<input name="name" required maxlength="120"></label><label>Description<textarea name="description" rows="2"></textarea></label><label>Specification (JSON)<textarea class="mono" name="spec" rows="12" required spellcheck="false">{"objective":"Find unmet needs","criteria":[{"criterion_id":"unmet_need","label":"Unmet need","feature_key":"unmet_need","mode":"display","weight":0,"missing_policy":"keep_unknown"}]}</textarea></label><p class="form-help">Start with a valid unmet-need criterion or replace it with registered criteria. Saving an edit creates a new immutable version. Definition changes do not run research.</p><div class="dialog-actions"><button type="button" class="quiet" data-close>Cancel</button><button class="primary" type="submit">Save lens</button></div></form></dialog>
<dialog id="confirm-dialog"><form method="dialog"><header><p class="eyebrow">Live source check</p><h2>Run this search?</h2></header><div id="confirm-copy" class="prose"></div><div class="limit-grid"><div><span>Returned results</span><strong>Up to 50</strong></div><div><span>Candidate checks</span><strong>Up to 20</strong></div><div><span>Threads / source</span><strong>Up to 2</strong></div><div><span>Comments / thread</span><strong>Up to 20</strong></div><div><span>Thread depth</span><strong>Up to 2</strong></div></div><p class="form-help">These are hard operational limits, not a promise that every source is available or every limit will be used.</p><div class="dialog-actions"><button value="cancel" class="quiet">Cancel</button><button value="confirm" class="primary">Run live search</button></div></form></dialog>
<div id="toast" class="toast hidden" role="status" aria-live="polite"></div>
</body></html>"""


def get_dashboard_html() -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)
