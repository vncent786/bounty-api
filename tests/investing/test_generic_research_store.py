from datetime import datetime, timedelta, timezone

import pytest

from social_scraper.investing.research_store import InvestmentResearchStore


def _handoff():
    return {
        "candidate_id": "candidate-1",
        "source_scan_id": "scan-1",
        "selection_mode": "research_only",
        "candidate_hash": "hash-1",
    }


def test_research_run_create_claim_progress_complete_and_list(tmp_path):
    store = InvestmentResearchStore(tmp_path / "research.db")
    run, created = store.create_research_run(
        workspace_id="default",
        handoff=_handoff(),
        target={"company_name": "Example Inc", "ticker": "EX"},
        options={},
        idempotency_key="key-1",
    )

    assert created is True
    token = store.claim_research_run(run["id"], lease_seconds=60)
    assert token
    store.update_research_run(
        run["id"], claim_token=token, stage="entity_resolution", progress=20,
        result={"source_receipts": []},
    )
    store.complete_research_run(
        run["id"], claim_token=token, status="complete", dossier_id="dossier-1",
        result={"source_receipts": [{"status": "complete"}]},
    )

    completed = store.get_research_run(run["id"])
    assert completed["status"] == "complete"
    assert completed["dossier_id"] == "dossier-1"
    assert store.list_research_runs("default")[0]["id"] == run["id"]


def test_research_run_idempotency_returns_existing_run(tmp_path):
    store = InvestmentResearchStore(tmp_path / "research.db")
    first, created = store.create_research_run(
        workspace_id="default", handoff=_handoff(),
        target={"company_name": "Example"}, options={}, idempotency_key="same",
    )
    second, created_again = store.create_research_run(
        workspace_id="default", handoff=_handoff(),
        target={"company_name": "Different"}, options={}, idempotency_key="same",
    )

    assert created is True
    assert created_again is False
    assert second["id"] == first["id"]


def test_stale_claim_cannot_mutate_run_after_reclaim(tmp_path):
    store = InvestmentResearchStore(tmp_path / "research.db")
    run, _ = store.create_research_run(
        workspace_id="default", handoff=_handoff(),
        target={"company_name": "Example"}, options={}, idempotency_key=None,
    )
    old_token = store.claim_research_run(run["id"], lease_seconds=1)
    assert old_token
    future = datetime.now(timezone.utc) + timedelta(seconds=2)
    new_token = store.claim_research_run(
        run["id"], lease_seconds=60, now=future,
    )
    assert new_token and new_token != old_token

    with pytest.raises(ValueError, match="claim"):
        store.update_research_run(
            run["id"], claim_token=old_token, stage="stale", progress=50,
        )


def test_research_run_public_view_hides_claim_token(tmp_path):
    store = InvestmentResearchStore(tmp_path / "research.db")
    run, _ = store.create_research_run(
        workspace_id="default", handoff=_handoff(),
        target={"company_name": "Example"}, options={}, idempotency_key=None,
    )
    store.claim_research_run(run["id"], lease_seconds=60)

    public = store.get_research_run(run["id"])

    assert "claim_token" not in public
    assert "claim_until" not in public
