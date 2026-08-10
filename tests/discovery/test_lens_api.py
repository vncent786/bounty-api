import asyncio
from datetime import datetime, timezone

from apis import dashboard_api
from social_scraper.discovery import DiscoveryStore


def test_api_evaluates_latest_persisted_evidence_under_user_lens(tmp_path, monkeypatch):
    store = DiscoveryStore(tmp_path / "discovery.db")
    run_id = store.record_feed(
        geo="US", observed_at=datetime.now(timezone.utc),
        candidates=[{"keyword": "Mexico vs USA U20", "source": "google_trends"}],
    )
    observation_id = store.list_run_candidates(run_id)[0]["observation_id"]
    store.record_gate_check(
        observation_id,
        status="complete", passed=True, platforms=["reddit"], total_items=2,
        analysis={
            "status": "supported",
            "behavior_type": "informational_discussion",
            "independent_voice_count": 2,
            "signals": [{"kind": "narrative", "independent_voices": 2}],
            "durability_evidence": [],
        },
    )
    monkeypatch.setattr(dashboard_api, "_discovery_store", store)
    monkeypatch.delenv("BOUNTY_DASHBOARD_TOKEN", raising=False)
    body = dashboard_api.DiscoveryLensEvaluationRequest(
        geo="US", keyword="Mexico vs USA U20", lens_id="macro-fx",
        name="Macro FX", objective="Surface events relevant to FX research",
        criteria=[dashboard_api.DiscoveryLensCriterionRequest(
            criterion_id="voices", label="Independent voices",
            feature_key="independent_voices", mode="score", weight=1.0,
        )],
    )
    response = asyncio.run(dashboard_api.evaluate_discovery_candidate_lens(body))
    assert response["candidate_id"] == "US:mexico vs usa u20"
    assert response["evidence_status"] == "supported"
    assert response["evaluation"]["lens_id"] == "macro-fx"
    assert response["evaluation_id"] > 0
