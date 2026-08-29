import asyncio

from social_scraper.investing.generic_dossier import build_candidate_handoff
from social_scraper.investing.research_runner import GenericInvestmentResearchRunner
from social_scraper.investing.research_store import InvestmentResearchStore


def _handoff():
    return build_candidate_handoff(
        {
            "candidate_id": "candidate-1",
            "label": "T-Mobile plan increase prompting provider-switch consideration",
            "qualification_status": "not_qualified",
            "review_status": "needs_more_evidence",
            "behaviour_type": "switching",
            "summary": "Customers discuss switching.",
            "economic_mechanism": "Higher prices may increase churn.",
            "why_investigate": "Check persistence and materiality.",
            "contradiction": "Complaints may not become churn.",
            "invalidation": "No subsequent switching evidence.",
            "anchor_terms": ["T-Mobile plan increase"],
            "evidence": [{
                "id": "e1", "platform": "tiktok",
                "url": "https://example.com/e1", "text": "I may switch.",
            }],
        },
        source_scan_id="scan-1",
        selection_mode="research_only",
        created_at="2026-08-29T10:00:00Z",
    )


def _create(store):
    run, _ = store.create_research_run(
        workspace_id="default",
        handoff=_handoff(),
        target={"company_name": "T-Mobile US, Inc.", "ticker": "TMUS", "exchange_code": "US"},
        options={"assumptions": {}},
        idempotency_key=None,
    )
    token = store.claim_research_run(run["id"], lease_seconds=300)
    assert token
    return run["id"], token


def test_generic_runner_persists_hash_verified_dossier(tmp_path):
    store = InvestmentResearchStore(tmp_path / "research.db")
    run_id, token = _create(store)

    async def collector(target, handoff, options):
        return {
            "entities": [{"lei": "TEST", "legal_name": target["company_name"]}],
            "instruments": [{"ticker": "TMUS", "security_type": "Common Stock"}],
            "sources": [{"source_type": "regulator_filing", "status": "complete"}],
            "reported_facts": [],
            "filing_passages": [],
            "transcript": {"status": "unavailable", "passages": [], "findings": []},
            "news_checks": [],
            "limitations": ["No verified candidate-level revenue numerator"],
            "coverage_status": "partial",
        }

    runner = GenericInvestmentResearchRunner(store, source_collector=collector)
    payload = asyncio.run(runner.run(run_id, token))

    run = store.get_research_run(run_id)
    assert run["status"] == "partial"
    assert run["progress"] == 100
    assert run["dossier_id"] == payload["dossier_id"]
    assert store.verify_dossier(payload["dossier_id"]) is True
    assert store.get_dossier(payload["dossier_id"])["candidate"]["candidate_id"] == "candidate-1"


def test_generic_runner_persists_error_without_fabricating_dossier(tmp_path):
    store = InvestmentResearchStore(tmp_path / "research.db")
    run_id, token = _create(store)

    async def collector(target, handoff, options):
        raise RuntimeError("source failed")

    runner = GenericInvestmentResearchRunner(store, source_collector=collector)

    try:
        asyncio.run(runner.run(run_id, token))
    except RuntimeError:
        pass

    run = store.get_research_run(run_id)
    assert run["status"] == "error"
    assert run["dossier_id"] is None
    assert run["error_category"] == "RuntimeError"
