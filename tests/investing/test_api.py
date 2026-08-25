from fastapi import FastAPI
from fastapi.testclient import TestClient

from apis import dashboard_api
from social_scraper.investing import InvestingRadarStore
from social_scraper.investing.social_pulse import SocialPulseStore


def _client(tmp_path, monkeypatch):
    path = tmp_path / "investing-api.db"
    store = InvestingRadarStore(path)
    social_store = SocialPulseStore(path)
    monkeypatch.setattr(dashboard_api, "_investing_store", store)
    monkeypatch.setattr(dashboard_api, "_social_pulse_store", social_store)
    monkeypatch.setenv("BOUNTY_ENV", "development")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    app = FastAPI()
    app.include_router(dashboard_api.router)
    return TestClient(app), store


def _seed(store):
    sweep_id = store.create_sweep(2, started_at="2026-08-16T10:00:00Z")
    store.record_market_success(
        sweep_id,
        "DE",
        [{
            "keyword": "Air conditioner",
            "source": "google_trends",
            "discovered_at": "2026-08-16T10:01:00Z",
            "search_volume": 20000,
            "growth_pct": 350.0,
            "started_hours_ago": 4.0,
            "categories": ["Shopping"],
        }],
        country_name="Germany",
    )
    store.record_market_success(
        sweep_id,
        "FR",
        [{
            "keyword": "air conditioner",
            "source": "google_trends",
            "discovered_at": "2026-08-16T10:02:00Z",
            "search_volume": 10000,
            "growth_pct": 200.0,
            "started_hours_ago": 6.0,
            "categories": ["Shopping"],
        }],
        country_name="France",
    )
    return store.finalize_sweep(sweep_id, completed_at="2026-08-16T10:03:00Z")


def test_radar_api_returns_persisted_global_items_and_safe_coverage(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    sweep = _seed(store)

    response = client.get("/dashboard/api/investing/radar")

    assert response.status_code == 200
    payload = response.json()
    assert payload["last_sweep"]["id"] == sweep["id"]
    assert payload["last_sweep"]["recorded_markets"] == 2
    assert "markets checked" in payload["coverage"]["summary"]
    assert len(payload["coverage"]["country_options"]) == 125
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["keyword"] == "Air conditioner"
    assert item["lane"] == "breaking_now"
    assert item["market_count"] == 2
    assert {country["code"] for country in item["countries"]} == {"DE", "FR"}
    assert item["source"] == "Google Trends Trending Now"
    assert item["latest_observed_at"] == "2026-08-16T10:02:00+00:00"
    assert any("2 markets" in reason for reason in item["reasons"])
    assert "markets" not in payload["last_sweep"]


def test_radar_api_filters_country_and_category_without_upstream_calls(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    _seed(store)

    response = client.get(
        "/dashboard/api/investing/radar",
        params={"country": "DE", "category": "Shopping"},
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["countries"] == [{"code": "DE", "name": "Germany"}]
    assert item["growth_pct"] == 350.0
    assert item["metric_scope_country"] == "DE"


def test_radar_run_and_candidate_endpoints_do_not_expose_market_errors(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    sweep = _seed(store)
    candidate_id = store.list_radar()[0]["candidate_id"]

    run_response = client.get(f"/dashboard/api/investing/radar/runs/{sweep['id']}")
    candidate_response = client.get(
        f"/dashboard/api/investing/radar/candidates/{candidate_id}"
    )

    assert run_response.status_code == 200
    assert "markets" not in run_response.json()["run"]
    assert candidate_response.status_code == 200
    assert candidate_response.json()["keyword"] == "Air conditioner"


def test_failed_latest_attempt_keeps_prior_data_timestamp_and_attempt_status_separate(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    successful = _seed(store)
    failed_id = store.create_sweep(1, started_at="2026-08-16T12:00:00Z")
    store.record_market_failure(
        failed_id,
        "US",
        "source_unavailable",
        observed_at="2026-08-16T12:01:00Z",
    )
    failed = store.finalize_sweep(failed_id, completed_at="2026-08-16T12:02:00Z")

    payload = client.get("/dashboard/api/investing/radar").json()

    assert payload["last_sweep"]["id"] == failed["id"]
    assert payload["last_sweep"]["status"] == "failed"
    assert payload["data_sweep"]["id"] == successful["id"]
    assert payload["data_observed_at"] == "2026-08-16T10:02:00+00:00"
    assert "displaying the most recent successful data" in payload["coverage"]["summary"]


def test_candidate_endpoint_uses_one_consistent_latest_data_sweep(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    first = store.create_sweep(1)
    store.record_market_success(
        first,
        "US",
        [{"keyword": "Cooling", "search_volume": 900, "growth_pct": 900}],
    )
    store.finalize_sweep(first)
    candidate_id = store.list_radar()[0]["candidate_id"]

    second = store.create_sweep(1)
    store.record_market_success(
        second,
        "GB",
        [{"keyword": "Cooling", "search_volume": 100, "growth_pct": 50}],
    )
    store.finalize_sweep(second)

    payload = client.get(
        f"/dashboard/api/investing/radar/candidates/{candidate_id}"
    ).json()

    assert payload["countries"] == [{"code": "GB", "name": "United Kingdom"}]
    assert payload["search_volume"] == 100
    assert payload["growth_pct"] == 50.0
    assert payload["metric_scope_country"] == "GB"
    assert payload["reasons"][0] == "Appeared in United Kingdom"


def test_customer_api_cannot_trigger_upstream_refresh(tmp_path, monkeypatch):
    client, _store = _client(tmp_path, monkeypatch)
    response = client.post("/dashboard/api/investing/radar/refresh")
    assert response.status_code == 404


def test_social_pulse_api_is_persisted_read_only(tmp_path, monkeypatch):
    client, _store = _client(tmp_path, monkeypatch)

    response = client.get("/dashboard/api/investing/social-pulse")
    forbidden = client.post("/dashboard/api/investing/social-pulse/refresh")

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["coverage"]["summary"] == "No social collection has completed yet"
    assert forbidden.status_code == 404


def test_missing_radar_entities_return_404(tmp_path, monkeypatch):
    client, _store = _client(tmp_path, monkeypatch)

    assert client.get("/dashboard/api/investing/radar/runs/missing").status_code == 404
    assert client.get("/dashboard/api/investing/radar/candidates/999").status_code == 404
