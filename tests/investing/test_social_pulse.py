import asyncio
import sqlite3

import pytest

from social_scraper.investing.social_pulse import (
    SOCIAL_PLATFORMS,
    SocialPulseCollector,
    SocialPulseStore,
    extract_social_candidates,
)


def _item(platform, post_id, text, author, *, likes=1, comments=1):
    domains = {
        "reddit": "reddit.com",
        "youtube": "youtube.com",
        "tiktok": "tiktok.com",
        "instagram": "instagram.com",
        "x": "x.com",
    }
    return {
        "platform": platform,
        "post_id": post_id,
        "url": f"https://www.{domains[platform]}/post/{post_id}",
        "author_username": author,
        "text": text,
        "created_at": "2026-08-25T10:00:00Z",
        "likes": likes,
        "comments": comments,
    }


def _source(platform, items=(), status="complete", error_category=None):
    async def fetch():
        return {
            "status": status,
            "items": list(items),
            "error_category": error_category,
            "observed_at": "2026-08-25T11:00:00Z",
        }
    return fetch


def test_social_pulse_schema_is_additive_and_evidence_is_immutable(tmp_path):
    path = tmp_path / "shared.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy_state(key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO legacy_state VALUES('kept','yes')")
    store = SocialPulseStore(path)
    run_id = store.create_run()
    store.record_source(run_id, "reddit", status="complete", items=[
        _item("reddit", "abc", "Portable ice makers keep selling out", "voice1")
    ])

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM legacy_state").fetchall() == [("kept", "yes")]
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE investing_social_pulse_evidence SET likes=0")


def test_social_pulse_create_run_if_idle_prevents_duplicate_collectors(tmp_path):
    path = tmp_path / "pulse.db"
    first = SocialPulseStore(path)
    second = SocialPulseStore(path)

    first_id, first_created = first.create_run_if_idle()
    second_id, second_created = second.create_run_if_idle()

    assert first_created is True
    assert second_created is False
    assert second_id == first_id


def test_partial_source_with_retained_items_stays_partial(tmp_path):
    store = SocialPulseStore(tmp_path / "pulse.db")
    run_id = store.create_run()
    outcome = store.record_source(
        run_id,
        "youtube",
        status="partial",
        items=[_item("youtube", "y1", "Retained video", "creator")],
        error_category="secondary_route_failed",
    )

    assert outcome["status"] == "partial"
    assert store.get_run(run_id)["sources"][0]["status"] == "partial"
    assert store.get_run(run_id)["sources"][0]["error_category"] == "secondary_route_failed"


def test_extraction_rejects_unknown_citations_and_computes_support():
    evidence = [
        {**_item("reddit", "r1", "I bought a portable ice maker", "a"), "id": "id1", "observed_at": "2026-08-25T11:00:00Z", "views": None, "shares": None},
        {**_item("tiktok", "t1", "Portable ice balls are everywhere", "b"), "id": "id2", "observed_at": "2026-08-25T11:00:00Z", "views": 20000, "shares": 50},
    ]

    async def llm(_system, _user):
        return '''{"candidates":[
          {"label":"Portable ice makers","behaviour_type":"purchase","summary":"People are showing recent purchases.","why_investigate":"Repeated purchase behaviour across platforms.","evidence_ids":["E1","E2"]},
          {"label":"Invented product","behaviour_type":"adoption","summary":"","why_investigate":"","evidence_ids":["E99"]}
        ],"limitations":[]}'''

    result = asyncio.run(extract_social_candidates(evidence, llm_call_fn=llm))

    assert result["status"] == "supported"
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["evidence_ids"] == ["id1", "id2"]
    assert candidate["voice_count"] == 2
    assert candidate["platform_count"] == 2
    assert candidate["support_type"] == "cross_platform"
    assert "1 uncitable" in result["limitations"][0]


def test_extraction_failure_is_analysis_unavailable_not_empty_signal():
    evidence = [{**_item("reddit", "r1", "People are changing products", "a"), "id": "id1", "observed_at": "2026-08-25T11:00:00Z", "views": None, "shares": None}]

    async def broken(_system, _user):
        raise RuntimeError("provider down")

    result = asyncio.run(extract_social_candidates(evidence, llm_call_fn=broken))

    assert result["status"] == "analysis_unavailable"
    assert result["error_category"] == "provider_or_parse_error"
    assert result["candidates"] == []


def test_collector_persists_all_source_outcomes_and_public_citations(tmp_path):
    store = SocialPulseStore(tmp_path / "pulse.db")
    fetchers = {
        "reddit": _source("reddit", [_item("reddit", "r1", "Portable ice maker purchase", "a")]),
        "youtube": _source("youtube", [_item("youtube", "y1", "Portable ice maker review", "b", likes=50)]),
        "tiktok": _source("tiktok", status="failed", error_category="source_unavailable"),
        "instagram": _source("instagram", status="empty"),
        # x deliberately missing to verify explicit unavailable coverage.
    }

    async def llm(_system, _user):
        return '{"candidates":[{"label":"Portable ice makers","behaviour_type":"purchase","summary":"Purchase and review activity is visible.","why_investigate":"Two independent voices across Reddit and YouTube.","evidence_ids":["E1","E2"]}],"limitations":[]}'

    run = asyncio.run(SocialPulseCollector(store, fetchers, llm_call_fn=llm).run())
    payload = store.public_payload()

    assert run["status"] == "partial"
    assert len(run["sources"]) == 5
    assert {source["platform"] for source in run["sources"]} == set(SOCIAL_PLATFORMS)
    assert {source["status"] for source in run["sources"]} >= {"complete", "empty", "failed", "unavailable"}
    assert len(payload["items"]) == 1
    assert len(payload["items"][0]["evidence"]) == 2
    assert all(item["url"].startswith("https://") for item in payload["items"][0]["evidence"])
    assert payload["coverage"]["summary"] == "3 of 5 social sources checked"


def test_failed_latest_attempt_does_not_replace_prior_social_data(tmp_path):
    store = SocialPulseStore(tmp_path / "pulse.db")
    fetchers = {
        platform: _source(platform, [_item(platform, platform, "Reusable bottle adoption", platform)])
        for platform in SOCIAL_PLATFORMS
    }

    async def llm(_system, _user):
        return '{"candidates":[{"label":"Reusable bottles","behaviour_type":"adoption","summary":"Adoption evidence.","why_investigate":"Multiple social records.","evidence_ids":["E1","E2"]}],"limitations":[]}'

    first = asyncio.run(SocialPulseCollector(store, fetchers, llm_call_fn=llm).run())

    failing = {platform: _source(platform, status="failed", error_category="down") for platform in SOCIAL_PLATFORMS}
    second = asyncio.run(SocialPulseCollector(store, failing, llm_call_fn=llm).run())
    payload = store.public_payload()

    assert first["candidate_count"] == 1
    assert second["status"] == "failed"
    assert payload["data_run"]["id"] == first["id"]
    assert payload["last_attempt"]["id"] == second["id"]
    assert payload["coverage"]["displaying_previous_data"] is True
    assert payload["items"][0]["label"] == "Reusable bottles"
