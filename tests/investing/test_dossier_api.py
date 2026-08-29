from fastapi import FastAPI
from fastapi.testclient import TestClient

from apis import dashboard_api
from social_scraper.investing.private_radar import PrivateRadarStore
from social_scraper.investing.research_store import InvestmentResearchStore


def _seed_private(store):
    run_id, _ = store.create_scan_if_idle()
    store.add_evidence(run_id, [{
        "id": "e1",
        "panel_id": "telecom",
        "platform": "tiktok",
        "external_id": "e1",
        "url": "https://example.com/e1",
        "author": "person-a",
        "text": "I may switch after the T-Mobile plan increase.",
        "observed_at": "2026-08-29T00:00:00Z",
    }])
    store.complete_scan(run_id, [{
        "candidate_id": "candidate-1",
        "panel_id": "telecom",
        "qualification_status": "not_qualified",
        "label": "T-Mobile plan increase prompting provider-switch consideration",
        "behaviour_type": "switching",
        "summary": "A customer discussed switching after a plan increase.",
        "economic_mechanism": "Price increases may increase churn.",
        "why_investigate": "Check persistence and materiality.",
        "contradiction": "Complaints may not become churn.",
        "invalidation": "No subsequent switching evidence.",
        "anchor_terms": ["T-Mobile plan increase"],
        "evidence_ids": ["e1"],
        "gates": {
            "specificity": {"state": "pass", "passed": True, "reason": "fixture", "metrics": {}},
            "behavior": {"state": "fail", "passed": False, "reason": "one voice", "metrics": {}},
        },
    }], limitations=[])
    return run_id


def _client(tmp_path, monkeypatch):
    private_store = PrivateRadarStore(tmp_path / "private.db")
    research_store = InvestmentResearchStore(tmp_path / "research.db")
    scan_id = _seed_private(private_store)
    monkeypatch.setattr(dashboard_api, "_private_radar_store", private_store)
    monkeypatch.setattr(dashboard_api, "_investment_research_store", research_store)
    monkeypatch.setenv("BOUNTY_ENV", "development")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setattr(
        dashboard_api,
        "_schedule_investment_research_run",
        lambda run_id, claim_token: None,
        raising=False,
    )
    app = FastAPI()
    app.include_router(dashboard_api.router)
    return TestClient(app), research_store, scan_id


def test_create_list_and_get_generic_dossier_run(tmp_path, monkeypatch):
    client, store, scan_id = _client(tmp_path, monkeypatch)
    request = {
        "workspace_id": "default",
        "source_scan_id": scan_id,
        "candidate_id": "candidate-1",
        "selection_mode": "research_only",
        "company_name": "T-Mobile US, Inc.",
        "ticker": "TMUS",
        "exchange_code": "US",
        "primary_document_urls": [],
        "assumptions": {},
        "idempotency_key": "same-request",
    }

    response = client.post("/dashboard/api/investing/dossier-runs", json=request)

    assert response.status_code == 202
    body = response.json()
    assert body["started"] is True
    assert body["run"]["status"] == "running"
    assert "claim_token" not in body["run"]
    run_id = body["run"]["id"]

    duplicate = client.post("/dashboard/api/investing/dossier-runs", json=request)
    assert duplicate.status_code == 202
    assert duplicate.json()["started"] is False
    assert duplicate.json()["run"]["id"] == run_id

    listed = client.get("/dashboard/api/investing/dossier-runs?workspace_id=default")
    assert listed.status_code == 200
    assert listed.json()["runs"][0]["id"] == run_id

    status = client.get(f"/dashboard/api/investing/dossier-runs/{run_id}")
    assert status.status_code == 200
    assert status.json()["run"]["candidate_id"] == "candidate-1"

    pending = client.get(f"/dashboard/api/investing/dossier-runs/{run_id}/dossier")
    assert pending.status_code == 409


def test_dossier_run_rejects_missing_candidate_and_non_https_source(tmp_path, monkeypatch):
    client, _store, scan_id = _client(tmp_path, monkeypatch)
    base = {
        "workspace_id": "default",
        "source_scan_id": scan_id,
        "candidate_id": "missing",
        "selection_mode": "research_only",
        "company_name": "T-Mobile US, Inc.",
        "ticker": "TMUS",
        "exchange_code": "US",
        "primary_document_urls": [],
        "assumptions": {},
    }
    assert client.post("/dashboard/api/investing/dossier-runs", json=base).status_code == 404

    base["candidate_id"] = "candidate-1"
    base["primary_document_urls"] = ["http://insecure.example.com/report"]
    assert client.post("/dashboard/api/investing/dossier-runs", json=base).status_code == 422
    base["primary_document_urls"] = ["https://169.254.169.254/latest/meta-data"]
    assert client.post("/dashboard/api/investing/dossier-runs", json=base).status_code == 422


def test_saved_dossier_route_verifies_hash(tmp_path, monkeypatch):
    client, store, _scan_id = _client(tmp_path, monkeypatch)
    payload = {
        "schema_version": "investment-dossier/1",
        "dossier_id": "dossier-1",
        "case_id": "case-1",
        "status": "research_only",
        "created_at": "2026-08-29T00:00:00Z",
    }
    store.append_dossier(payload)

    response = client.get("/dashboard/api/investing/dossiers/dossier-1")

    assert response.status_code == 200
    assert response.json()["dossier"] == payload
    assert response.json()["payload_sha256"]
