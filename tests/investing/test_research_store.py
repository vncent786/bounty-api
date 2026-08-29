import json

import pytest

from social_scraper.investing.research_store import InvestmentResearchStore


def test_research_store_appends_and_verifies_dossier_hash(tmp_path):
    store = InvestmentResearchStore(tmp_path / "research.db")
    payload = {
        "schema_version": "investment-dossier/1",
        "dossier_id": "dossier-1",
        "case_id": "costco-executive",
        "status": "research_only",
        "facts": [{"metric": "executive_sales_penetration", "value": "73.6"}],
    }

    stored = store.append_dossier(payload)
    loaded = store.get_dossier("dossier-1")

    assert loaded == payload
    assert stored["payload_sha256"]
    assert store.verify_dossier("dossier-1") is True


def test_research_store_is_append_only(tmp_path):
    store = InvestmentResearchStore(tmp_path / "research.db")
    payload = {
        "schema_version": "investment-dossier/1",
        "dossier_id": "dossier-1",
        "case_id": "costco-executive",
        "status": "research_only",
    }
    store.append_dossier(payload)

    with pytest.raises(ValueError, match="already exists"):
        store.append_dossier({**payload, "status": "investment_ready"})

    with store._connect() as connection, pytest.raises(Exception, match="immutable"):
        connection.execute(
            "UPDATE investment_dossiers SET payload_json=? WHERE dossier_id=?",
            (json.dumps({"tampered": True}), "dossier-1"),
        )
