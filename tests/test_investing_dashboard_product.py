import json
from pathlib import Path

from apis.investing_dashboard_page import (
    INVESTING_DASHBOARD_HTML,
    get_investing_dashboard_html,
)


ROOT = Path(__file__).parents[1]


def _script() -> str:
    return (ROOT / "public" / "investing-dashboard.js").read_text(encoding="utf-8")


def _styles() -> str:
    return (ROOT / "public" / "investing-dashboard.css").read_text(encoding="utf-8")


def test_investing_shell_uses_standalone_assets_and_radar_first_navigation():
    response = get_investing_dashboard_html()
    html = response.body.decode()

    assert response.status_code == 200
    assert html == INVESTING_DASHBOARD_HTML
    assert '<link rel="stylesheet" href="/investing-dashboard.css">' in html
    assert '<script src="/investing-dashboard.js" defer></script>' in html
    assert '<section class="view active" id="view-radar"' in html

    labels = ["Radar", "Research", "Monitors", "Usage"]
    positions = [html.index(f'data-view="{label.lower()}"') for label in labels]
    assert positions == sorted(positions)
    assert all(label in html for label in labels)


def test_investing_shell_keeps_classic_bounty_prominent_and_explains_what_remains():
    html = get_investing_dashboard_html().body.decode()

    assert html.count('href="/dashboard/classic"') >= 3
    assert html.count("Open Classic Bounty") >= 3
    assert "Classic research, projects, and lenses remain available." in html
    assert "Projects, research runs, findings, and lenses remain available." in html
    assert "topic pre-filled" in html


def test_private_radar_separates_trade_ready_watch_and_rejected_states_without_samples():
    html = get_investing_dashboard_html().body.decode()
    script = _script()
    combined = f"{html}\n{script}".casefold()

    assert "Investment signal review" in html
    assert "Run private scan" in html
    assert "Raw social posts and generic trends are never displayed as leads." in html
    assert 'class="signal-lane hidden" aria-labelledby="breaking-title"' in html
    assert 'class="signal-lane building-lane hidden"' in html

    for state_label in ("Scanning", "No qualified leads", "Failed"):
        assert state_label.casefold() in combined

    assert "Worth investigating" in script
    assert "Why it is not qualified" in script
    assert "Rejected after review" in script
    assert "review_items" in script
    assert "search_movement_only" in script
    assert "Google search interest" in script
    assert "movementPanel" in script
    assert "engagementSummary" in script
    assert 'id="movement-geo"' in html
    assert '<option value="WORLDWIDE">Worldwide</option>' in html
    assert '<option value="US">United States</option>' in html
    assert 'id="movement-horizon"' in html
    assert '<option value="3m">3 months</option>' in html
    assert '<option value="1y">1 year</option>' in html
    assert '<option value="5y">5 years</option>' in html
    assert "item?.movement_bundle" in script
    assert "movementTrajectory" in script
    assert "hasSelectedMovement" in script
    assert "asArray(safe.items).filter(hasSelectedMovement)" in script
    assert "asArray(safe.review_items).filter(hasSelectedMovement)" in script
    assert "state.movementGeo" in script
    assert "state.movementHorizon" in script
    assert "Google Trends discovery" in html
    assert 'id="trend-discovery-list"' in html
    assert "renderTrendDiscovery" in script
    assert "keyword_basket" in script
    assert "movementQueryOptions" in script
    assert "movement-query-option" in script
    assert "Queries are tested separately" in script
    assert "Why it may be rising" in script
    assert "Investing read" in script
    assert "first detected" in script
    assert "not a fixed 3-month rate" in script
    assert "movementPanel(item)" in script
    assert "Search attention only" in script
    assert "Needs history" not in script
    assert "Missing evidence" not in script
    assert "missing evidence" not in script
    assert "Insufficient evidence" not in script
    assert "need more history" not in script.casefold()
    assert '<article class="signal-row"' not in html
    assert "synthetic" not in combined
    assert "mock signal" not in combined
    assert "demo signal" not in combined
    assert "const items = [" not in script


def test_private_radar_uses_manual_scan_and_persisted_read_contract():
    html = get_investing_dashboard_html().body.decode()
    script = _script()

    assert 'id="reload-radar"' in html
    assert "const PRIVATE_RADAR_URL = '/dashboard/api/investing/private-radar'" in script
    assert "const PRIVATE_SCAN_URL = '/dashboard/api/investing/private-radar/scans'" in script
    assert "api(PRIVATE_SCAN_URL, { method: 'POST' })" in script
    assert "loadPrivateRadar();" in script
    init_block = script[script.index("function init()") : script.index("init();", script.index("function init()"))]
    assert "loadPrivateRadar()" in init_block
    assert "loadRadar()" not in init_block
    assert "loadSocialPulse()" not in init_block


def test_signal_rows_render_contract_fields_source_time_and_encoded_classic_handoff():
    script = _script()

    for field in (
        "item?.id",
        "item?.keyword",
        "item?.categories",
        "item?.countries",
        "item?.reasons",
        "item?.search_volume",
        "item?.growth_pct",
        "item?.started_hours_ago",
        "item?.latest_observed_at",
        "item?.source",
    ):
        assert field in script

    assert "Observed ${formatTimestamp(item?.latest_observed_at)}" in script
    assert "countrySummary(item?.countries)" in script
    assert "metricScopeName(item)" in script
    assert "sweepStatusText(safePayload.last_sweep)" in script
    assert "safePayload.data_sweep" in script
    assert "safePayload.data_observed_at" in script
    assert "Investigate" in script
    assert "/dashboard/classic?topic=${encodeURIComponent(String(keyword || ''))}" in script
    assert "window.location" not in script[script.index("function classicTopicUrl"):script.index("function showView")]
    assert "innerHTML" not in script


def test_private_radar_has_read_only_snapshot_mode_for_phone_review():
    script = _script()
    snapshot = json.loads(
        (ROOT / "public" / "private-radar-snapshot.json").read_text(encoding="utf-8")
    )

    assert "const PRIVATE_SNAPSHOT_URL = '/private-radar-snapshot.json'" in script
    assert "const READ_ONLY_SNAPSHOT" in script
    assert "new URLSearchParams(window.location.search).get('snapshot') === '1'" in script
    assert "fetch(PRIVATE_SNAPSHOT_URL, { cache: 'no-store' })" in script
    assert "Read-only snapshot" in script
    assert snapshot["items"] == []
    assert len(snapshot["review_items"]) >= 1
    assert {item["review_status"] for item in snapshot["review_items"]} <= {
        "needs_more_evidence", "rejected"
    }
    assert all(
        item.get("trajectory", {}).get("status") == "complete"
        and item.get("movement_bundle", {}).get("default_geo") == "WORLDWIDE"
        and item.get("movement_bundle", {}).get("default_horizon") == "3m"
        and item.get("evidence")
        for item in snapshot["review_items"]
    )
    evidence = [
        record for item in snapshot["review_items"] for record in item["evidence"]
    ]
    assert all(record["url"].startswith(("http://", "https://")) for record in evidence)
    x_evidence = [record for record in evidence if record["platform"] == "x"]
    assert not x_evidence or any(
        record["url"].startswith("https://platform.twitter.com/embed/Tweet.html")
        for record in x_evidence
    )
    assert sum(
        any(value is not None for value in record.get("engagement", {}).values())
        for record in evidence
    ) >= 2
    assert snapshot["data_scan"]["status"] == "no_qualified_leads"
    assert snapshot["data_scan"]["panel_version"] == "camillo-private-panels/5"
    funnel = snapshot["coverage"]["initial_funnel"]
    assert funnel["panel_count"] == 16
    assert funnel["query_scopes"] == 64
    assert funnel["complete_scopes"] == 64
    assert 0 <= funnel["capped_scopes"] <= 64
    assert funnel["reported_records"] > 0
    for platform in ("tiktok", "instagram", "reddit", "youtube"):
        coverage = snapshot["coverage"]["platforms"][platform]
        assert coverage["scopes"] == 16
        assert coverage["failed"] == 0
        assert coverage["reported_records"] > 0
    assert snapshot["coverage"]["platforms"]["reddit"]["complete"] == 16
    assert len(snapshot["trend_discovery"]["candidates"]) > 0
    assert all(
        candidate.get("context", {}).get("what_it_is")
        and candidate.get("context", {}).get("why_rising")
        and candidate.get("context", {}).get("investing_angle")
        and len(candidate.get("movement_bundle", {}).get("query_options") or []) >= 1
        for candidate in snapshot["trend_discovery"]["candidates"]
    )
    assert all(
        len(item.get("movement_bundle", {}).get("query_options") or []) >= 2
        for item in snapshot["review_items"]
    )
    assert snapshot["snapshot_mode"] == "read_only"
    assert snapshot["snapshot_observed_at"]


def test_private_radar_renders_qualified_cited_leads_without_inner_html():
    script = _script()

    for field in (
        "item?.label", "item?.behaviour_type", "item?.summary",
        "item?.economic_mechanism", "item?.why_investigate",
        "item?.contradiction", "item?.invalidation", "item?.voice_count",
        "item?.parity", "item?.windows", "item?.evidence",
    ):
        assert field in script
    assert "Source evidence" in script
    assert "Independent voices" in script
    assert "Retrospective anomaly" in script
    assert "target = '_blank'" in script
    assert "noopener noreferrer" in script
    assert "privateRadarRow" in script
    assert "renderPrivateRadar" in script
    assert "safeSourceUrl" in script
    assert "Coverage caveat: independent support was observed on one platform only." in script
    assert "innerHTML" not in script


def test_auth_and_accessibility_contract_mirror_classic_dashboard():
    html = get_investing_dashboard_html().body.decode()
    script = _script()

    assert "sessionStorage.getItem(TOKEN_KEY)" in script
    assert "bounty.apiToken" in script
    assert "Authorization" in script
    assert "Bearer ${token}" in script
    assert "response.status === 401" in script
    assert 'id="set-token"' in html

    assert 'class="skip-link"' in html
    assert 'aria-label="Product"' in html
    assert 'aria-current="page"' in html
    assert 'aria-live="polite"' in html
    assert 'role="alert"' in html
    assert 'aria-busy="true"' in html


def test_app_restores_classic_default_and_keeps_investing_preview_private():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    classic_script = (ROOT / "public" / "dashboard.js").read_text(encoding="utf-8")

    dashboard_block = app_source[
        app_source.index('@app.get("/dashboard", response_class=HTMLResponse)'):
        app_source.index('@app.get("/dashboard/classic", response_class=HTMLResponse)')
    ]
    preview_block = app_source[
        app_source.index('@app.get("/dashboard/investing-preview", response_class=HTMLResponse)'):
        app_source.index('# ============================================================', app_source.index('@app.get("/dashboard/investing-preview"'))
    ]
    assert "get_dashboard_html" in dashboard_block
    assert "get_investing_dashboard_html" not in dashboard_block
    assert "get_investing_dashboard_html" in preview_block
    assert '@app.get("/dashboard/classic", response_class=HTMLResponse)' in app_source
    assert "new URLSearchParams(window.location.search).get('topic')" in classic_script
    assert "$('#direct-topic').value = inboundTopic.slice(0, 120)" in classic_script


def test_assets_do_not_expose_internal_diagnostics_and_css_is_responsive_warm_paper():
    html = get_investing_dashboard_html().body.decode()
    script = _script()
    styles = _styles()
    combined = f"{html}\n{script}\n{styles}".casefold()

    assert "connector" not in combined
    assert "diagnostic" not in combined
    assert "#f4f1e8" in styles.casefold()
    assert "#085ffe" in styles.casefold()
    assert "@media (max-width: 760px)" in styles
    assert "min-height: 46px" in styles
    assert "overflow-wrap: anywhere" in styles
