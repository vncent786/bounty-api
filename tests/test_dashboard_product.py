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
    assert "Global Explore" in html
    assert "What's changing" in html
    assert "Recommended for deeper reading" in html
    assert "Perspective" in html
    assert all(marker in html for marker in (
        'id="family-grid"', 'id="family-detail"', 'id="global-stage"',
        'id="global-include-rejected"',
    ))
    assert "Candidate checks" in html
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


def test_global_explore_visibly_organizes_composer_register_sheet_and_receipt():
    html = get_dashboard_html().body.decode()
    for label in ("Scan composer", "Candidate register", "Evidence sheet", "Run receipt"):
        assert label in html, label
    # Both co-primary workflows stay obvious at a glance.
    assert "Workflow A · Known topic" in html
    assert "Workflow B · Emerging trends" in html
    assert "Bounded research brief" in html
    assert "Live trend scan" in html
    # The dark receipt keeps both status channels.
    assert 'id="global-explore-status"' in html
    assert 'id="explore-preview"' in html


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


def test_emerging_candidate_research_action_launches_a_named_investing_brief():
    from pathlib import Path

    script = (Path(__file__).parents[1] / "public" / "dashboard.js").read_text(encoding="utf-8")
    assert "`${topic} · emerging-trend investigation`" in script
    assert "$('#direct-preset').value = 'investing-social-arbitrage'" in script
    assert "$('#direct-topic').value = topic" in script
    assert "$('#research-topic-btn').click()" in script


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


def test_dashboard_findings_click_never_passes_the_event_as_the_run_id():
    from pathlib import Path

    script = (Path(__file__).parents[1] / "public" / "dashboard.js").read_text(encoding="utf-8")
    plan = script[
        script.index("async function createResearchPlan"):
        script.index("async function promoteCandidate")
    ]
    assert "findBtn.addEventListener('click', () => loadFindings());" in plan
    assert "findBtn.addEventListener('click', loadFindings);" not in script


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
