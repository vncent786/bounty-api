import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apis import dashboard_api
from social_scraper.discovery import DiscoveryStore
from social_scraper.lenses.storage import LensStore


def _client(tmp_path, monkeypatch):
    db_path = tmp_path / "discovery.db"
    DiscoveryStore(db_path)
    monkeypatch.setattr(dashboard_api, "_lens_store", LensStore(db_path))
    monkeypatch.delenv("BOUNTY_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setenv("BOUNTY_ENV", "test")
    monkeypatch.setattr(
        dashboard_api, "_get_broker",
        lambda: (_ for _ in ()).throw(AssertionError("source invoked")),
    )
    monkeypatch.setattr(
        dashboard_api, "_get_discovery",
        lambda: (_ for _ in ()).throw(AssertionError("pipeline invoked")),
    )

    async def fail_llm(*args, **kwargs):
        raise AssertionError("LLM invoked")

    monkeypatch.setattr(dashboard_api, "_llm_call", fail_llm)
    app = FastAPI()
    app.include_router(dashboard_api.router)
    return TestClient(app), db_path


def test_workspace_lens_and_field_crud_api(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    field_response = client.post("/dashboard/api/workspaces/acme/fields", json={
        "key": "purchase_intent", "label": "Purchase intent", "description": "Band",
        "data_type": "enum", "source_stage": "custom_extraction",
        "extraction_mode": "llm", "definition": {"values": ["low", "high"]},
    })
    assert field_response.status_code == 201
    field = field_response.json()
    assert client.get(f"/dashboard/api/workspaces/acme/fields/{field['id']}").status_code == 200

    payload = {
        "name": "Buyer radar", "description": "Find likely buyers", "spec": {
            "objective": "Find likely buyers", "criteria": [{
                "criterion_id": "intent", "label": "Intent",
                "feature_key": "purchase_intent", "mode": "display", "weight": 0,
            }],
        },
    }
    created_response = client.post("/dashboard/api/workspaces/acme/lenses", json=payload)
    assert created_response.status_code == 201
    lens = created_response.json()
    assert lens["latest_version"]["compiled_requirements"]["required_depth"] == "custom_extraction"

    payload["spec"]["criteria"][0]["feature_key"] = "novelty"
    version_response = client.post(
        f"/dashboard/api/workspaces/acme/lenses/{lens['id']}/versions", json=payload
    )
    assert version_response.status_code == 201
    assert version_response.json()["version"] == 2
    assert client.get(
        f"/dashboard/api/workspaces/acme/lenses/{lens['id']}/versions/1"
    ).json()["version"] == 1

    duplicate = client.post(
        f"/dashboard/api/workspaces/acme/lenses/{lens['id']}/duplicate",
        json={"name": "Buyer radar clone"},
    )
    assert duplicate.status_code == 201
    assert len(client.get("/dashboard/api/workspaces/acme/lenses").json()) == 2
    assert client.post(
        f"/dashboard/api/workspaces/acme/lenses/{lens['id']}/archive"
    ).status_code == 200
    assert len(client.get("/dashboard/api/workspaces/acme/lenses").json()) == 1

    assert client.post("/dashboard/api/workspaces/acme/fields", json={
        "key": "purchase_intent", "label": "Duplicate", "data_type": "string",
        "source_stage": "candidate", "extraction_mode": "deterministic", "definition": {},
    }).status_code == 409
    assert client.get("/dashboard/api/workspaces/acme/lenses/missing").status_code == 404
    assert client.post("/dashboard/api/workspaces/acme/fields", json={
        "key": "Bad Key", "label": "Bad", "data_type": "string",
        "source_stage": "candidate", "extraction_mode": "deterministic", "definition": {},
    }).status_code == 422

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT count(*) FROM discovery_stage_usage").fetchone()[0] == 0
