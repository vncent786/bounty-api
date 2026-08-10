import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apis import dashboard_api
from social_scraper.discovery import DiscoveryScheduler, DiscoveryStore, ScanBudget
from social_scraper.lenses.storage import LensStore
from social_scraper.workspaces import NotFoundError, ValidationError, WorkspaceService, WorkspaceStore


def lens_spec():
    return {"objective": "test", "criteria": [{
        "criterion_id": "novel", "label": "Novel", "feature_key": "novelty",
        "mode": "score", "weight": 1.0, "missing_policy": "keep_unknown",
    }]}


def stores(tmp_path):
    path = tmp_path / "workflow.db"
    discovery = DiscoveryStore(path)
    lens = LensStore(path)
    workspace = WorkspaceStore(path)
    return workspace, lens, discovery, WorkspaceService(workspace, lens, discovery)


def test_additive_migration_on_populated_database(tmp_path):
    path = tmp_path / "populated.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE existing_data (value TEXT)")
        connection.execute("INSERT INTO existing_data VALUES ('keep')")
    WorkspaceStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM existing_data").fetchone()[0] == "keep"
        names = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert {"projects", "monitored_subjects", "subject_aliases", "research_actions"} <= names


def test_workspace_isolation_and_lens_version_validation(tmp_path):
    store, lenses, _, service = stores(tmp_path)
    lens = lenses.create_lens("one", "Lens", "", lens_spec())
    made = service.create_project("one", name="P", first_subject={
        "name": "S", "lens_id": lens["id"], "lens_version": 1,
    })
    project, subject = made["project"], made["first_subject"]
    assert subject["lens_id"] == lens["id"]
    with pytest.raises(NotFoundError):
        store.get_project("two", project["id"])
    with pytest.raises(NotFoundError):
        store.get_subject("two", project["id"], subject["id"])
    other = store.create_project("two", "Other")
    with pytest.raises(ValidationError, match="this workspace"):
        service.create_subject("two", other["id"], name="bad", lens_id=lens["id"], lens_version=1)


def test_idempotency_leases_recovery_and_cancel(tmp_path):
    store, _, _, _ = stores(tmp_path)
    project = store.create_project("one", "P")
    first, created = store.create_action(
        "one", project["id"], "deep_read", idempotency_key="same"
    )
    second, created_again = store.create_action(
        "one", project["id"], "deep_read", idempotency_key="same"
    )
    assert created and not created_again and second["id"] == first["id"]
    at = datetime(2026, 8, 10, tzinfo=timezone.utc)
    claim = store.claim_action("one", project["id"], first["id"], lease_seconds=5, now=at)
    assert claim["status"] == "running"
    assert store.claim_action(
        "one", project["id"], first["id"], now=at + timedelta(seconds=4)
    ) is None
    recovered = store.claim_action(
        "one", project["id"], first["id"], now=at + timedelta(seconds=6)
    )
    assert recovered["lease_token"] != claim["lease_token"]

    queued, _ = store.create_action("one", project["id"], "export_report")
    assert store.cancel_action("one", project["id"], queued["id"])["status"] == "cancelled"


def test_bounded_plan_promotion_monitoring_and_no_external_calls(tmp_path, monkeypatch):
    store, _, discovery, service = stores(tmp_path)
    made = service.create_project("one", name="P", first_subject={"name": "S", "active": False})
    project, subject = made["project"], made["first_subject"]
    monkeypatch.setattr(dashboard_api, "_get_broker", lambda: pytest.fail("external broker called"))
    candidates = [
        {"id": "a", "eligible": True, "search_volume": 2},
        {"id": "b", "eligible": True, "search_volume": 1},
    ]
    action, created = service.create_action(
        "one", project["id"], "run_discovery", subject_id=subject["id"],
        idempotency_key="discover", requested_budget={
            "root_probe_candidates": 1, "deep_read_candidates": 1,
            "horizontal_llm_candidates": 1, "threads_per_platform": 2,
            "comments_per_thread": 20, "max_thread_depth": 2,
            "optional_enrichments": 0,
        }, payload={"required_depth": "root_probe", "candidates": candidates},
    )
    assert created and action["status"] == "completed"
    assert action["result"]["phase"] == "planned"
    assert action["result"]["collection_status"] == "not_started"
    assert action["result"]["effective_budget"]["root_probe_candidates"] == 1
    run_id = action["result"]["run_id"]
    assert sum(row["stages"].get("root_probe") == "planned"
               for row in discovery.list_research_run_candidates(run_id)) == 1

    promoted, _ = service.create_action(
        "one", project["id"], "promote_candidate",
        payload={"run_id": run_id, "candidate_id": "b"},
    )
    assert promoted["result"]["candidate"]["manual_promoted"] is True
    started, _ = service.create_action(
        "one", project["id"], "start_monitoring", subject_id=subject["id"]
    )
    assert started["result"]["subject"]["active"] is True
    paused, _ = service.create_action(
        "one", project["id"], "pause_monitoring", subject_id=subject["id"]
    )
    assert paused["result"]["subject"]["active"] is False


def test_workspace_api_exact_scope_and_crud(tmp_path, monkeypatch):
    store, lenses, discovery, service = stores(tmp_path)
    monkeypatch.setattr(dashboard_api, "_workspace_store", store)
    monkeypatch.setattr(dashboard_api, "_workspace_service", service)
    monkeypatch.setattr(dashboard_api, "_lens_store", lenses)
    monkeypatch.setattr(dashboard_api, "_discovery_store", discovery)
    monkeypatch.delenv("BOUNTY_DASHBOARD_TOKEN", raising=False)
    app = FastAPI()
    app.include_router(dashboard_api.router)
    client = TestClient(app)
    created = client.post("/dashboard/api/workspaces/one/projects", json={
        "name": "Project", "default_geo": "us", "first_subject": {"name": "Subject"},
    })
    assert created.status_code == 201
    project = created.json()["project"]
    subject = created.json()["first_subject"]
    assert project["default_geo"] == "US" and subject["geo"] == "US"
    assert client.get(f"/dashboard/api/workspaces/two/projects/{project['id']}").status_code == 404
    alias = client.post(
        f"/dashboard/api/workspaces/one/projects/{project['id']}/subjects/{subject['id']}/aliases",
        json={"alias": "S", "kind": "include"},
    )
    assert alias.status_code == 201
    action = client.post(
        f"/dashboard/api/workspaces/one/projects/{project['id']}/actions",
        json={"action_type": "export_report", "idempotency_key": "report"},
    )
    assert action.status_code == 201 and action.json()["action"]["status"] == "queued"
    action_id = action.json()["action"]["id"]
    assert client.post(
        f"/dashboard/api/workspaces/one/projects/{project['id']}/actions/{action_id}/cancel"
    ).json()["status"] == "cancelled"
