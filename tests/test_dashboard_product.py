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
    assert '/logo-wordmark-dark-master.png' in html
    assert all(label in html for label in (
        "Projects", "Explore", "Findings", "Lenses", "Monitors", "Usage"
    ))
    assert "Searches never run on page load" in html
    assert "Candidate checks" in html
    assert "Deep reads" not in html
    assert "sample" not in html.casefold()
    for forbidden in ("horizontal", "ontology", "candidate intelligence"):
        assert forbidden not in html.casefold()
    assert "BOUNTY_DASHBOARD_TOKEN" not in html
    assert DASHBOARD_HTML == html


def test_dashboard_script_avoids_iframe_hostile_scrolling_and_plans_real_depth():
    from pathlib import Path

    script = (Path(__file__).parents[1] / "public" / "dashboard.js").read_text(encoding="utf-8")
    # scrollIntoView is only allowed in the guided tour context
    tour_section = script[script.index("function startTour"):]
    non_tour = script[:script.index("function startTour")]
    assert "scrollIntoView" not in non_tour
    assert "required_depth: 'horizontal_analysis'" in script


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
