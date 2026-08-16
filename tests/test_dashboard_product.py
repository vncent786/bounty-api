from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apis import dashboard_api
from apis.dashboard_page import DASHBOARD_HTML, get_dashboard_html
from social_scraper.discovery import DiscoveryStore
from social_scraper.lenses.storage import LensStore
from social_scraper.workspaces import WorkspaceService, WorkspaceStore


def _isolated_client(tmp_path, monkeypatch, token=None):
    path = tmp_path / "dashboard-product.db"
    discovery = DiscoveryStore(path)
    lenses = LensStore(path)
    workspaces = WorkspaceStore(path)
    monkeypatch.setattr(dashboard_api, "_discovery_store", discovery)
    monkeypatch.setattr(dashboard_api, "_lens_store", lenses)
    monkeypatch.setattr(dashboard_api, "_workspace_store", workspaces)
    monkeypatch.setattr(
        dashboard_api, "_workspace_service", WorkspaceService(workspaces, lenses, discovery)
    )
    if token is None:
        monkeypatch.delenv("BOUNTY_DASHBOARD_TOKEN", raising=False)
    else:
        monkeypatch.setenv("BOUNTY_DASHBOARD_TOKEN", token)
    monkeypatch.setenv("BOUNTY_ENV", "development")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    app = FastAPI()
    app.include_router(dashboard_api.router)
    return TestClient(app)


def test_dashboard_shell_contract_uses_product_assets_and_external_files():
    response = get_dashboard_html()
    html = response.body.decode()
    assert response.status_code == 200
    assert '<link rel="stylesheet" href="/dashboard.css">' in html
    assert '<script src="/dashboard.js" defer></script>' in html
    # Typography-led wordmark; the raster logo is retired.
    assert "logo-wordmark" not in html
    assert '<span class="brand-name">BOUNTY</span>' in html
    assert "Research API" in html
    assert all(label in html for label in (
        "Projects", "Explore", "Findings", "Lenses", "Monitors", "Usage"
    ))
    assert "Submit up to five related topics" in html
    assert "Topics, one per line (maximum 5)" in html
    assert "Discovery to evidence" in html
    assert "Saved discoveries" in html
    assert "saved discoveries only" in html
    assert "Perspective" in html
    assert all(marker in html for marker in (
        'id="family-grid"', 'id="family-detail"', 'id="global-stage"',
        'id="global-include-rejected"',
    ))
    assert "Social triage" in html
    assert "Selected sources are attempted" in html
    assert "Unavailable sources remain explicit" in html
    assert "Deep reads" not in html
    assert "sample" not in html.casefold()
    for forbidden in ("horizontal", "ontology", "candidate intelligence"):
        assert forbidden not in html.casefold()
    assert "BOUNTY_DASHBOARD_TOKEN" not in html
    assert DASHBOARD_HTML == html


def test_dashboard_shell_defaults_to_explore_behind_a_numbered_masthead():
    html = get_dashboard_html().body.decode()
    # Explore is the front door: it ships as the active view.
    assert '<section class="view active" id="view-explore"' in html
    assert '<section class="view" id="view-projects"' in html
    # The sidebar shell is gone, replaced by a top masthead.
    assert 'id="masthead"' in html
    assert 'class="sidebar"' not in html
    # Legacy functional IDs remain attached to their new masthead roles.
    assert 'id="menu-toggle"' in html
    assert 'id="sidebar"' in html
    assert 'id="changing-title"' in html
    assert 'aria-label="Product"' in html
    # Numbered navigation in the declared order, Explore first.
    order = ["explore", "projects", "findings", "lenses", "monitors", "usage"]
    positions = [html.index(f'data-view="{view}"') for view in order]
    assert positions == sorted(positions)
    for number in range(1, 7):
        assert f'<span class="nav-no">{number:02d}</span>' in html


def test_explore_is_ordered_as_one_discovery_to_evidence_journey():
    html = get_dashboard_html().body.decode()
    for label in ("Known-topic research", "Live trend scan", "Research progress", "Saved discoveries"):
        assert label in html, label
    journey = [
        html.index('id="explore-form"'),
        html.index('id="discovery-progress"'),
        html.index('id="live-topics-title"'),
        html.index('id="research-progress-title"'),
        html.index('id="saved-discoveries-title"'),
    ]
    assert journey == sorted(journey)
    assert "Run receipt" not in html
    assert 'id="run-receipt-title"' not in html
    # Both entry routes stay obvious, while saved evidence is clearly later.
    assert "Known topic" in html
    assert "Emerging trends" in html
    assert 'id="global-explore-status"' in html
    assert 'id="explore-preview"' in html
    assert "after this scan finishes" not in html
    assert 'placeholder="Meta ads reporting&#10;Ad creative fatigue&#10;AI ad creative tools"' in html


def _product_script():
    from pathlib import Path

    return (Path(__file__).parents[1] / "public" / "dashboard.js").read_text(encoding="utf-8")


def test_explore_country_is_a_full_name_select_backed_by_discover_options():
    html = get_dashboard_html().body.decode()
    script = _product_script()
    # Country is a full-name dropdown, not a two-letter free-text input.
    assert '<select id="explore-geo" required>' in html
    assert '<option value="US">United States</option>' in html
    assert '<input id="explore-geo"' not in html
    # The full validated list is populated from the versioned backend allowlist.
    assert "api('/discover/options')" in script
    assert "option.value = country.code" in script


def test_explore_threshold_fields_are_hidden_zeros():
    html = get_dashboard_html().body.decode()
    for field_id in ("explore-volume", "explore-growth", "explore-age"):
        assert f'<input id="{field_id}" type="hidden" value="0">' in html
    for label in ("Min searches", "Min growth", "Recent (hours)"):
        assert label not in html


def test_explore_topic_area_precedes_search_with_balanced_default():
    html = get_dashboard_html().body.decode()
    script = _product_script()
    start = html.index('id="explore-form"')
    form = html[start:html.index("</form>", start)]
    assert '<label>Topic area<select id="explore-cat-filter">' in form
    assert '<option value="">Balanced across all categories</option>' in form
    assert form.index('id="explore-cat-filter"') < form.index('type="submit"')
    # Choices come from the corrected source-native backend taxonomy and the
    # chosen area rides the existing categories query parameter.
    assert "data.categories" in script
    assert "query.set('categories', category)" in script


def test_explore_copy_explains_scope_defaults_signals_and_missing_metrics():
    html = get_dashboard_html().body.decode()
    assert 'class="scan-instructions" aria-label="How to find topics"' in html
    assert "Choose a country" in html
    assert "No growth number is required" in html
    assert "Find, then research" in html
    assert "Select up to five useful topics" in html
    assert "Country controls which search market is scanned" in html
    assert "Recommended: leave this balanced" in html
    assert "Search activity is a discovery signal, not evidence" in html
    assert "Missing metrics are shown as unavailable, not zero" in html
    assert '<select id="global-geo"><option value="">All countries</option>' in html
    script = _product_script()
    assert "savedCountrySelect" in script
    assert "No saved topic families yet" in script
    assert "Live scan topics stay above in the current journey" in script


def test_live_scan_scope_elapsed_and_ordering_are_explicit_without_fake_eta():
    html = get_dashboard_html().body.decode()
    script = _product_script()
    assert "Google Trends only" in html
    progress_region = html[html.index('id="discovery-progress"'):html.index('id="live-topics-title"')]
    assert "Scan status" in progress_region
    assert "Active scan" not in progress_region
    assert "Trends plus social triage for up to 20 topics" in script
    assert "Instagram, TikTok, YouTube, and Reddit" in html
    assert "Category-balanced: newest first within category, then highest search growth. Search volume is not used." in html
    assert all(label in html for label in (
        "Category-balanced (default)", "Newest", "Fastest growth",
        "Most searches", "Most social activity",
    ))
    assert all(label in html for label in (
        "All social evidence", "Matching public posts", "Not checked or source gaps",
    ))
    scan = script[script.index("async function runExplore"):script.index("function candidateName")]
    assert "formatElapsed" in scan
    assert "elapsed-clock" in scan
    assert "Estimated" not in scan
    assert "ETA" not in scan
    assert "%" not in scan
    assert "status = 'active'" in scan
    assert "Scan ended with partial source coverage" in scan
    assert "Scan status unavailable" in scan
    assert "status === 'complete' ? 'Scan complete' : 'Active scan'" not in scan
    assert "endedAt" in scan


def test_known_and_trend_research_offer_all_default_sources_and_copy_them_per_candidate():
    html = get_dashboard_html().body.decode()
    script = _product_script()
    for scope in ("direct", "trend"):
        for platform in ("reddit", "youtube", "instagram", "tiktok"):
            marker = f'id="{scope}-source-{platform}"'
            start = html.index(marker)
            assert "checked" in html[start:html.index(">", start)]
    assert html.count("Selected sources are attempted") >= 2
    assert html.count("Unavailable sources remain explicit") >= 2
    known = script[script.index("async function researchTopic"):script.index("const researchPolls")]
    assert "selectedResearchPlatforms('direct')" in known
    assert "platforms: selectedPlatforms" in known
    trend = script[script.index("async function createResearchPlan"):script.index("async function promoteCandidate")]
    assert "selectedResearchPlatforms('trend')" in trend
    assert "platforms: selectedPlatforms" in trend


def test_candidate_sort_and_social_filter_operate_on_copies_not_raw_candidates():
    script = _product_script()
    section = script[
        script.index("function candidatesForDisplay"):
        script.index("function renderExploreResults")
    ]
    assert "state.candidates.slice()" in section
    assert "categoryBalancedCandidates" in section
    assert "gate_total_engagement" in section
    assert "matching" in section
    assert "gaps" in section
    assert "state.candidates.sort" not in section
    assert "state.candidates = state.candidates.filter" not in script


def test_candidate_rows_show_rank_age_search_and_root_check_evidence():
    script = _product_script()
    rows = script[
        script.index("function renderExploreResults"):
        script.index("function renderSelectionBar")
    ]
    for marker in (
        "candidate-rank", "searchAge", "searches", "% growth",
        "gate_platforms", "gate_total_items", "gate_total_engagement",
        "publicPostStatus",
    ):
        assert marker in rows, marker


def test_explore_lens_is_labeled_as_a_later_research_angle():
    html = get_dashboard_html().body.decode()
    assert 'id="explore-lens"' in html
    assert "Conversation-research angle (used after you choose a topic)" in html


def test_find_topics_renames_the_search_and_the_public_post_check_is_additive():
    html = get_dashboard_html().body.decode()
    assert ">Find topics</button>" in html
    assert "Search trends" not in html
    assert "Only show topics with social discussion" not in html
    assert "Also check for matching public posts" in html
    assert "A failed post check never hides a search topic" in html


def test_explore_request_stays_zero_thresholded_and_never_gates():
    script = _product_script()
    assert "mode: checkPosts ? 'root_sweep' : 'trends_snapshot'" in script
    assert "min_volume: $('#explore-volume').value" in script
    assert "min_growth: $('#explore-growth').value" in script
    assert "max_age_hours: $('#explore-age').value" in script
    # gate_only is pinned false in every mode: check failures must never
    # remove topics.
    assert "gate_only: 'false'" in script
    assert "checked ? 'true' : 'false'" not in script


def test_public_post_statuses_use_the_approved_plain_english_vocabulary():
    script = _product_script()
    status_copy = script[
        script.index("function publicPostStatus"):
        script.index("function renderExploreResults")
    ].casefold()
    for phrase in (
        "matching public posts found",
        "no matching posts on sites checked",
        "some sites could not be checked",
        "public-post check failed",
        "public posts not checked",
    ):
        assert phrase in status_copy, phrase
    for banned in ("verified", "confirmed", "some sources checked", "not yet checked"):
        assert banned not in status_copy, banned


def test_confirmation_dialog_is_truthful_about_search_only_and_public_post_checks():
    html = get_dashboard_html().body.decode()
    script = _product_script()
    for gone in ("Threads / source", "Comments / thread", "Thread depth"):
        assert gone not in html, gone
    assert "Up to 50" in html
    assert "Up to 20" in html
    review = script[
        script.index("async function reviewExplore"):script.index("function selectedFamilyFilters")
    ]
    assert "Google Trends snapshot only" in review
    assert "check root public posts" in review
    assert "will not hide the topic" in review
    assert "dialog.returnValue = '';" in review


def test_explore_empty_and_failure_states_keep_choices_and_offer_recovery():
    script = _product_script()
    assert "function appendExploreRecovery" in script
    assert "choices are still selected" in script
    assert "'Use balanced topic mix'" in script
    assert "'Choose another country'" in script
    assert "'Try again'" in script


def test_dashboard_assets_carry_no_emoji_or_decoration_glyphs():
    from pathlib import Path

    html = get_dashboard_html().body.decode()
    script = (Path(__file__).parents[1] / "public" / "dashboard.js").read_text(encoding="utf-8")
    offenders = [
        ch for ch in html + script
        if (
            0x1F000 <= ord(ch)
            or 0x2190 <= ord(ch) <= 0x21FF
            or 0x2600 <= ord(ch) <= 0x27BF
        )
    ]
    assert not offenders, offenders


def test_dashboard_css_ships_the_paper_ink_cobalt_system():
    from pathlib import Path

    css = (Path(__file__).parents[1] / "public" / "dashboard.css").read_text(encoding="utf-8")
    assert "#f4f1e8" in css      # warm paper ground
    assert "#111" in css         # ink text and rules
    assert "#085ffe" in css      # the single cobalt accent
    # Flat print system: no gradients, no rounded corners, no movement effects.
    assert "linear-gradient" not in css.casefold()
    assert "radial-gradient" not in css.casefold()
    assert "border-radius" not in css
    for motion in ("translate", "scale(", "rotate("):
        assert motion not in css, motion
    # The dark sidebar is gone; ink appears only as blocks on paper.
    assert "grid-template-columns:224px" not in css
    # Mobile is designed, not accidental.
    assert "@media (max-width: 390px)" in css
    assert ".masthead-nav.open" in css
    assert ".source-link { display: inline-flex; align-items: center; min-height: 44px; }" in css
    # Persisted families are rendered as ledger rows, never feed cards.
    assert ".family-card" not in css


def test_dashboard_script_defaults_to_explore_and_never_auto_tours():
    from pathlib import Path

    script = (Path(__file__).parents[1] / "public" / "dashboard.js").read_text(encoding="utf-8")
    assert "showView(['projects','explore','findings','lenses','monitors','usage'].includes(initial) ? initial : 'explore')" in script
    # First-load auto-tour interruption removed...
    assert "setTimeout(startTour" not in script
    # ...but the manual tour entry point survives.
    assert "$('#start-tour').addEventListener('click', startTour)" in script
    # The old shell is gone, while its functional mobile-nav hooks survive.
    assert "$('#menu-toggle').addEventListener('click'" in script
    assert "$('#sidebar').classList.toggle('open')" in script
    assert "known-topic-section" not in script
    assert "family-card" not in script
    assert "candidate-row" in script


def test_dashboard_script_keeps_explore_anchor_ids_under_the_new_shell():
    from pathlib import Path

    script = (Path(__file__).parents[1] / "public" / "dashboard.js").read_text(encoding="utf-8")
    for anchor in (
        "$('#family-grid')", "$('#family-detail')", "$('#explore-results')",
        "$('#explore-detail')", "$('#explore-preview')", "$('#global-explore-status')",
        "$('#direct-topic')", "$('#explore-form')",
    ):
        assert anchor in script, anchor
    assert "safeUrl(item.record.url)" in script


def test_emerging_candidate_research_action_uses_the_selected_lens_plan():
    from pathlib import Path

    script = (Path(__file__).parents[1] / "public" / "dashboard.js").read_text(encoding="utf-8")
    detail = script[
        script.index("async function renderCandidateDetail"):
        script.index("async function createResearchPlan")
    ]
    assert "state.selectedForPlan.add(id)" in detail
    assert "createResearchPlan({ currentTarget: quickBtn }, { autoStart: true })" in detail
    assert "$('#direct-preset').value = 'investing-social-arbitrage'" not in detail


def test_trend_scan_discards_stale_results_and_preserves_result_geo():
    script = _product_script()
    scan = script[
        script.index("async function runExplore"):
        script.index("function candidateName")
    ]
    assert "const requestScanEpoch = ++state.trendScanEpoch;" in scan
    assert "requestEpoch !== state.workspaceEpoch" in scan
    assert "requestWorkspace !== state.workspace" in scan
    assert "requestScanEpoch !== state.trendScanEpoch" in scan
    assert "if (isStale()) return;" in scan
    assert "if (!isStale()) { button.disabled = false;" in scan
    assert "state.trendScanGeo = requestGeo;" in scan
    assert "state.trendScanCountry = requestCountry;" in scan
    detail = script[
        script.index("async function renderCandidateDetail"):
        script.index("async function createResearchPlan")
    ]
    assert "geo=${enc(scanGeo)}" in detail
    assert "$('#explore-geo')?.value" not in detail


def test_rescan_clears_stale_topic_actions_and_rows_do_not_nest_controls():
    from pathlib import Path

    script = _product_script()
    scan = script[
        script.index("async function runExplore"):
        script.index("function candidateName")
    ]
    assert "state.selectedCandidate = null;" in scan
    assert "state.selectedForPlan.clear();" in scan
    assert "state.discoveryRunId = null;" in scan
    assert "_detailEpoch += 1;" in scan
    assert "$('#explore-detail').replaceChildren" in scan
    rows = script[
        script.index("function renderExploreResults"):
        script.index("function renderSelectionBar")
    ]
    assert "el('article', `data-row trend-result-row" in rows
    assert "el('label', 'trend-select')" in rows
    assert "el('button', 'trend-result-open')" in rows
    assert "append(row, select, open)" in rows
    assert "const row = el('button'" not in rows
    css = (Path(__file__).parents[1] / "public" / "dashboard.css").read_text(encoding="utf-8")
    assert ".trend-select {" in css
    assert "min-width: 52px; min-height: 52px;" in css
    selection_css = css[css.index(".selection-bar {"):css.index("/* ── Tables")]
    assert "position: sticky" not in selection_css


def test_dashboard_script_avoids_iframe_hostile_scrolling_and_plans_real_depth():
    from pathlib import Path

    script = (Path(__file__).parents[1] / "public" / "dashboard.js").read_text(encoding="utf-8")
    # scrollIntoView is only allowed in the guided tour context
    tour_section = script[script.index("function startTour"):]
    non_tour = script[:script.index("function startTour")]
    assert "scrollIntoView" not in non_tour
    assert "required_depth: 'horizontal_analysis'" in script
    assert "`/explore/families?${query}`" in script
    assert "collection_performed === false" in script
    assert "Independent corroboration" in script
    assert "Propagation" in script
    assert "Citations" in script
    assert "Request monitor" in script
    assert "Family added to monitoring" not in script
    assert "globalExploreEpoch" in script
    assert "Loading family evidence" in script
    assert "pollResearchRun" in script
    assert "loadResearchHistory" in script
    assert "lens_preset_id" in script
    assert "const fmtDate =" in script
    assert "fmtDate(item.published_at)" in script
    assert "appendCitationLinks" in script
    assert "analysis.summary_evidence_ids" in script
    assert "const researchPolls = new Map()" in script
    assert "requestEpoch !== state.workspaceEpoch" in script
    assert "requestWorkspace !== state.workspace" in script
    assert "activeResearchRun?.status === 'planned'" in script
    assert "Start saved research" in script
    assert "Use no more than five topics" in script
    assert "'.local'" in script
    assert "a === 127" in script


def test_dashboard_poll_discards_results_from_stale_workspace_contexts():
    from pathlib import Path

    script = (Path(__file__).parents[1] / "public" / "dashboard.js").read_text(encoding="utf-8")
    poll = script[
        script.index("async function pollResearchRunOnce"):
        script.index("async function reviewExplore")
    ]
    assert "const requestEpoch = state.workspaceEpoch;" in poll
    assert "const requestWorkspace = state.workspace;" in poll
    assert """const isStale = () => (
      requestEpoch !== state.workspaceEpoch
      || requestWorkspace !== state.workspace
      || state.researchRunId !== runId
    );""" in poll
    assert poll.count("if (isStale()) return") >= 4
    assert "renderSelectionBar();" in poll


def test_explore_restores_remembered_progress_and_execute_captures_run_scope():
    script = _product_script()
    show = script[script.index("function showView"):script.index("function loading")]
    assert "restoreResearchProgress()" in show

    restore = script[
        script.index("async function restoreResearchProgress"):
        script.index("function researchRunName")
    ]
    assert "localStorage.getItem(latestRunKey())" in restore
    assert "renderResearchProgress(run)" in restore
    assert "pollResearchRun(run.id || runId)" in restore
    assert "requestEpoch !== state.workspaceEpoch" in restore
    assert "const selectedRunAtRequest = state.researchRunId" in restore
    assert "state.researchRunId !== selectedRunAtRequest" in restore
    assert "run.workspace_id !== requestWorkspace" in restore

    execute = script[
        script.index("async function executeResearchRun"):
        script.index("async function startSavedResearchRun")
    ]
    assert "runId = state.researchRunId" in execute
    assert "requestEpoch = state.workspaceEpoch" in execute
    assert "requestWorkspace = state.workspace" in execute
    assert "state.researchRunId !== runId" in execute
    assert "await api(`/discovery/research-runs/${enc(runId)}/execute`" in execute
    assert "await pollResearchRun(runId" in execute
    assert execute.count("if (isStale()) return") >= 3
    assert "/research-runs/${enc(state.researchRunId)}" not in execute


def test_valid_research_retries_clear_errors_and_planned_runs_do_not_show_elapsed_age():
    script = _product_script()
    known = script[script.index("async function researchTopic"):script.index("const researchPolls")]
    trend = script[script.index("async function createResearchPlan"):script.index("async function promoteCandidate")]
    progress = script[script.index("function renderResearchProgress"):script.index("function pollResearchRun")]
    assert "clearError();" in known
    assert "clearError();" in trend
    assert "run.status === 'planned' ? null : run.started_at" in progress
    assert "run.started_at || run.created_at" not in progress
    assert "Research planned" in progress


def test_research_topics_create_and_start_the_persisted_run_in_one_action():
    script = _product_script()
    plan = script[
        script.index("async function createResearchPlan"):
        script.index("async function promoteCandidate")
    ]
    assert "{ autoStart = true }" in plan
    assert "await executeResearchRun" in plan
    assert "Click \"Start research\"" not in plan
    assert "execute-run-btn" not in plan


def test_research_progress_uses_server_units_percent_and_elapsed_or_an_honest_fallback():
    html = get_dashboard_html().body.decode()
    script = _product_script()
    assert 'id="research-progress"' in html
    progress = script[
        script.index("function completedResearchDetails"):
        script.index("function pollResearchRun")
    ]
    assert "run.result?.progress" in progress
    assert "friendlyResearchPhase" in progress
    assert "progress.completed_units" in progress
    assert "progress.total_units" in progress
    assert "progress.percent" in progress
    assert "reported by the server" in progress
    assert "formatElapsed" in progress
    assert "The server has not reported unit progress" in progress
    assert "No percent or arrival time is inferred" in progress
    assert "completed-run-details" in progress
    assert "Go to Findings" in progress


def test_findings_lead_with_interpretable_evidence_and_raw_engagement():
    script = _product_script()
    findings = script[
        script.index("function renderEvidenceSummary"):
        script.index("async function loadLenses")
    ]
    for label in (
        "What the evidence says", "Search attention (Google Trends)", "Observed behavior",
        "Commercial intent", "Negative or rejection", "General discussion",
        "Social trajectory", "Raw observed engagement",
    ):
        assert label in findings, label

    assert "analysis.interpretation" in script
    assert "analysis.status" in findings
    assert "analysis.coverage" in findings
    assert "engagement" in findings
    assert "engagementMetrics" in script
    evidence = script[
        script.index("function addEvidenceSection"):
        script.index("// ── Sparkline")
    ]
    assert "Observed engagement" in evidence
    assert "metricValue === null" in script
    assert "comparablePeriodsAvailable" in script
    assert "Comparative change language was withheld" in script
    assert "did not include comparable periods" in script
    assert "One collection period cannot establish whether conversation is brewing" in script
    assert "Google Trends Trending Now" in script
    assert "trends.google.com/trending" in script
    assert "evidenceCitationNumber" in script
    assert "[${evidenceCitationNumber(item.id, lookup)}]" in script


def test_dashboard_poll_continues_after_a_late_execute_conflict():
    from pathlib import Path

    script = (Path(__file__).parents[1] / "public" / "dashboard.js").read_text(encoding="utf-8")
    api_section = script[
        script.index("async function api"):
        script.index("function statusBadge")
    ]
    poll = script[
        script.index("async function pollResearchRunOnce"):
        script.index("async function reviewExplore")
    ]
    assert "error.status = response.status;" in api_section
    assert "if (error.status !== 409) throw error;" in poll


def test_dashboard_bearer_dependency_and_open_development_mode(tmp_path, monkeypatch):
    secured = _isolated_client(tmp_path, monkeypatch, token="correct-token")
    path = "/dashboard/api/workspaces/default/projects"
    assert secured.get(path).status_code == 401
    assert secured.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    accepted = secured.get(path, headers={"Authorization": "Bearer correct-token"})
    assert accepted.status_code == 200
    assert accepted.json() == {"projects": []}

    open_client = _isolated_client(tmp_path, monkeypatch)
    assert open_client.get(path).status_code == 200


def test_dashboard_fails_closed_when_mode_and_token_are_unset(tmp_path, monkeypatch):
    client = _isolated_client(tmp_path, monkeypatch)
    monkeypatch.delenv("BOUNTY_ENV", raising=False)
    response = client.get("/dashboard/api/workspaces/default/projects")
    assert response.status_code == 503
    assert response.json()["detail"] == "Dashboard authentication is not configured"


def test_discover_surfaces_persisted_collection_failure(tmp_path, monkeypatch):
    client = _isolated_client(tmp_path, monkeypatch)
    run_id = dashboard_api._discovery_store.record_feed(
        geo="US", observed_at=datetime.now(timezone.utc), candidates=[], status="error", comparable=False,
        error_category="collection:TimeoutError",
    )

    class FailedDiscovery:
        last_run_id = run_id

        async def scan_all(self, **_kwargs):
            return []

    monkeypatch.setattr(dashboard_api, "_discovery", FailedDiscovery())
    response = client.get("/dashboard/api/discover?geo=US")
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["run_id"] == run_id
    assert detail["status"] == "error"
    assert detail["error_category"] == "collection:TimeoutError"


def test_core_project_subject_and_monitor_action_contract(tmp_path, monkeypatch):
    client = _isolated_client(tmp_path, monkeypatch)
    created = client.post("/dashboard/api/workspaces/default/projects", json={
        "name": "Editorial research", "description": "Real project",
        "default_geo": "US", "first_subject": {"name": "Transit conversations"},
    })
    assert created.status_code == 201
    project = created.json()["project"]
    subject = created.json()["first_subject"]
    listed = client.get("/dashboard/api/workspaces/default/projects").json()["projects"]
    assert [item["id"] for item in listed] == [project["id"]]

    paused = client.post(
        f"/dashboard/api/workspaces/default/projects/{project['id']}/actions",
        json={
            "action_type": "pause_monitoring", "subject_id": subject["id"],
            "target_type": "subject", "target_id": subject["id"],
        },
    )
    assert paused.status_code == 201
    assert paused.json()["action"]["result"]["subject"]["active"] is False
    assert client.get(
        f"/dashboard/api/workspaces/default/projects/{project['id']}/subjects"
    ).json()["subjects"][0]["active"] is False


def test_research_run_preserves_name_and_use_case_without_mutating_candidates(
    tmp_path, monkeypatch,
):
    client = _isolated_client(tmp_path, monkeypatch)
    payload = {
        "workspace_id": "default",
        "name": "Cairn message research",
        "lens_preset_id": "marketing-intelligence",
        "candidates": [
            {"id": "meta-ads-reporting", "keyword": "Meta ads reporting", "eligible": True},
            {"id": "creative-fatigue", "keyword": "ad creative fatigue", "eligible": True},
        ],
        "required_depth": "horizontal_analysis",
        "budget": {
            "root_probe_candidates": 2,
            "deep_read_candidates": 2,
            "horizontal_llm_candidates": 2,
        },
    }

    created = client.post("/dashboard/api/discovery/research-runs", json=payload)
    assert created.status_code == 201
    plan = created.json()["plan"]
    assert plan["name"] == "Cairn message research"
    assert plan["lens_preset"]["preset_id"] == "marketing-intelligence"
    assert {
        item["candidate"]["keyword"] for item in plan["candidates"]
    } == {"Meta ads reporting", "ad creative fatigue"}

    invalid = {**payload, "lens_preset_id": "make-things-up"}
    rejected = client.post("/dashboard/api/discovery/research-runs", json=invalid)
    assert rejected.status_code == 422


def test_research_run_enforces_named_one_to_five_topic_briefs_and_budget_caps(
    tmp_path, monkeypatch,
):
    client = _isolated_client(tmp_path, monkeypatch)
    base = {
        "workspace_id": "default",
        "name": "Bounded brief",
        "candidates": [
            {"id": "one", "keyword": "topic one", "eligible": True},
            {"id": "two", "keyword": "topic two", "eligible": True},
        ],
        "required_depth": "horizontal_analysis",
        "budget": {
            "root_probe_candidates": 2,
            "deep_read_candidates": 2,
            "horizontal_llm_candidates": 2,
            "threads_per_platform": 2,
            "comments_per_thread": 20,
            "max_thread_depth": 2,
            "optional_enrichments": 0,
        },
    }
    assert client.post(
        "/dashboard/api/discovery/research-runs", json=base
    ).status_code == 201

    invalid_payloads = [
        {**base, "name": "   "},
        {**base, "candidates": []},
        {**base, "candidates": [
            {"id": str(index), "keyword": f"topic {index}", "eligible": True}
            for index in range(6)
        ]},
        {**base, "candidates": [
            {"id": "blank", "keyword": "   ", "eligible": True}
        ]},
        {**base, "candidates": [
            {"id": "a", "keyword": "same topic", "eligible": True},
            {"id": "b", "keyword": "Same Topic", "eligible": True},
        ]},
        {**base, "budget": {**base["budget"], "root_probe_candidates": 6}},
        {**base, "budget": {**base["budget"], "horizontal_llm_candidates": 1}},
        {**base, "budget": {**base["budget"], "comments_per_thread": 101}},
    ]
    for payload in invalid_payloads:
        response = client.post(
            "/dashboard/api/discovery/research-runs", json=payload
        )
        assert response.status_code == 422, (payload, response.text)


def test_global_explore_uses_persisted_families_and_records_actions(tmp_path, monkeypatch):
    client = _isolated_client(tmp_path, monkeypatch)
    store = dashboard_api._discovery_store
    first = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)
    second = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)
    candidate = {
        "keyword": "agentic payments", "related_terms": ["x402"],
        "search_volume": 100, "growth_pct": 20,
        "topic_ids": [], "categories": ["Technology"],
    }
    store.record_feed(geo="US", observed_at=first, candidates=[candidate])
    candidate["search_volume"] = 140
    store.record_feed(geo="US", observed_at=second, candidates=[candidate])
    candidate["search_volume"] = 999
    store.record_feed(
        geo="US", observed_at=datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc),
        candidates=[candidate], status="error", comparable=False,
        error_category="collection:synthetic",
    )
    family = store.create_topic_family(canonical_label="Agentic payments")
    store.link_topic_family_member(
        family_id=family["id"], geo="US", keyword="agentic payments",
        relationship="related_distinct", confidence="high",
        evidence={
            "explanation": "Payment rails designed for autonomous software agents.",
            "support": [
                {"url": "https://example.com/evidence", "title": "Primary evidence"},
                "not-a-citation",
                "https://exa mple.com/path",
                "http://[broken",
            ],
            "features": {"novelty": 0.8},
        },
    )
    shadow = client.post("/dashboard/api/promotion/shadow/evaluate", json={
        "workspace_id": "default",
        "evidence": [{
            "candidate_id": "agentic-payments", "family_id": family["id"],
            "observed_at": second.isoformat(),
            "root_summary": {"unique_root_count": 4, "independent_author_count": 3},
            "usable_text_root_count": 3, "duplicate_only_support": False,
            "source_health": {"reddit": "healthy", "youtube": "healthy"},
            "platform_hits": {"reddit": 2, "youtube": 2},
            "snapshot_windows": [{"snapshot_id": "d1", "status": "present"},
                                 {"snapshot_id": "d2", "status": "present"}],
            "trajectory": {}, "engagement_roots": [], "creator_summary": {},
            "depth_roots": [], "active_discussion_roots": 0,
            "radar_match": {"matched": True, "radar_ids": ["fintech"]},
            "manual_request": {"requested": False, "within_budget": False},
            "stratum": {"category": "fintech", "region": "US"},
        }],
    })
    assert shadow.status_code == 200
    assert shadow.json()["collection_performed"] is False
    assert shadow.json()["executed_actions"] == 0

    response = client.get("/dashboard/api/explore/families?workspace_id=default")
    assert response.status_code == 200
    payload = response.json()
    assert payload["collection_performed"] is False
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["family_id"] == family["id"]
    assert item["what_it_is"]["text"].startswith("Payment rails")
    assert item["what_it_is"]["support"][0]["url"] == "https://example.com/evidence"
    assert len(item["what_it_is"]["support"]) == 1
    assert item["trajectory"]["direction"] == "rising"
    assert item["trajectory"]["reported_volume"] == 140
    assert item["corroboration"]["unique_root_count"] == 4
    assert item["status"] == "active"

    monitored = client.post(
        f"/dashboard/api/explore/families/{family['id']}/actions",
        json={"workspace_id": "default", "action_type": "monitor",
              "route": "personal_radar_recurrence"},
    )
    assert monitored.status_code == 201
    dismissed = client.post(
        f"/dashboard/api/explore/families/{family['id']}/actions",
        json={"workspace_id": "default", "action_type": "dismiss"},
    )
    assert dismissed.status_code == 201
    assert dismissed.json()["family"]["status"] == "active"
    dismissed_view = client.get(
        "/dashboard/api/explore/families?workspace_id=default"
    ).json()["items"][0]
    assert dismissed_view["status"] == "rejected"
    labels = client.get(
        f"/dashboard/api/explore/families/{family['id']}/labels?workspace_id=default"
    ).json()
    assert [row["action_type"] for row in labels] == ["monitor", "dismiss"]


def test_production_browser_runtime_is_pinned_and_unprivileged():
    from pathlib import Path

    root = Path(__file__).parents[1]
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "playwright==1.62.0" in requirements
    assert "playwright-stealth==2.0.3" in requirements
    assert "ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright" in dockerfile
    assert "python -m playwright install --with-deps chromium" in dockerfile
    assert "USER app" in dockerfile
    assert "COPY --chown=app:app . ." in dockerfile
    assert dockerfile.index("USER app") < dockerfile.index("CMD uvicorn")
