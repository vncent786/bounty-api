"""Evidence-boundary release blockers: receipts, gaps, citable URLs, bounds.

Focused coverage for four shipped guarantees, kept in one file to avoid
colliding with the in-flight edits to the existing discovery and dashboard
test modules:

1. llm_calls receipts count calls actually attempted, not posts seen.
2. Deep-read/collection failures become explicit limitations (partial vs total
   coverage), never a silent "insufficient evidence".
3. Only canonical, public, non-reserved URLs are citable as evidence.
4. Research-brief candidates bound nested strings and platform collections.
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apis import dashboard_api
from social_scraper.discovery import DiscoveryStore
from social_scraper.discovery.handlers import (
    make_deep_read_handler,
    make_horizontal_extraction_handler,
)
from social_scraper.discovery.triage import (
    _has_public_source_url,
    analyze_conversation,
)
from social_scraper.lenses.storage import LensStore
from social_scraper.workspaces import WorkspaceService, WorkspaceStore


USABLE_POST = {
    "platform": "reddit",
    "post_id": "r1",
    "text": "I switched because delivery now takes two weeks.",
    "url": "https://www.reddit.com/r/example/comments/r1",
    "author": {"external_id": "u1", "username": "one"},
}


def _llm_returning(payload: dict) -> tuple[object, list]:
    sent: list = []

    async def llm(system, user):
        sent.append((system, user))
        return json.dumps(payload)

    return llm, sent


# ── 1. Truthful llm_calls receipts ────────────────────────────


def test_llm_receipt_counts_zero_calls_when_no_usable_evidence_survives():
    llm, sent = _llm_returning({
        "summary": "", "signals": [], "entities": [], "limitations": [],
    })
    collected = {
        "c1:deep": [dict(USABLE_POST, text="   ")],
        "c1:health": [{"platform": "reddit", "status": "success"}],
    }
    handler = make_horizontal_extraction_handler(
        plan={"effective_budget": {}}, collected=collected, llm_call_fn=llm,
    )

    result = asyncio.run(handler({"candidate_id": "c1", "keyword": "test"}, {}))

    # Posts existed, but preparation kept nothing citable, so no prompt was
    # transmitted and the receipt must say zero calls.
    assert sent == []
    assert result.llm_calls == 0
    assert result.input_records == 0
    assert result.input_characters == 0
    assert collected["c1:findings"]["status"] == "insufficient_evidence"


def test_llm_receipt_counts_one_call_when_evidence_survives():
    llm, sent = _llm_returning({
        "summary": "", "signals": [], "entities": [], "limitations": [],
    })
    collected = {"c1:deep": [USABLE_POST], "c1:health": []}
    handler = make_horizontal_extraction_handler(
        plan={"effective_budget": {}}, collected=collected, llm_call_fn=llm,
    )

    result = asyncio.run(handler({"candidate_id": "c1", "keyword": "test"}, {}))

    assert len(sent) == 1
    assert result.llm_calls == 1
    assert result.input_records == 1
    assert result.input_characters > 0


# ── 2. Explicit deep-read failure limitations / partial coverage ──


def test_partial_source_failures_state_partial_coverage_explicitly():
    llm, _ = _llm_returning({
        "summary": "Customers describe switching after delays.",
        "summary_evidence_ids": ["reddit:post:r1"],
        "signals": [{
            "kind": "switching",
            "claim": "Customers are switching away after delays.",
            "polarity": "negative",
            "evidence_ids": ["reddit:post:r1"],
        }],
        "entities": [],
        "limitations": [],
    })

    result = asyncio.run(analyze_conversation(
        "delays", [USABLE_POST],
        source_health=[
            {"platform": "reddit", "status": "success"},
            {"platform": "youtube", "status": "failed"},
        ],
        llm_call_fn=llm,
    ))

    assert result.status == "supported"
    assert any(
        "1 of 2 collection sources failed (youtube)" in item
        and "coverage is partial" in item
        for item in result.limitations
    )


def test_total_source_failure_states_total_gap_explicitly():
    result = asyncio.run(analyze_conversation(
        "delays", [],
        source_health=[
            {"platform": "reddit", "status": "error"},
            {"platform": "youtube", "status": "failed", "stage": "deep_read"},
        ],
    ))

    assert result.status == "sources_unavailable"
    assert any(
        "All 2 collection sources failed (reddit, youtube)" in item
        for item in result.limitations
    )


def test_deep_read_handler_records_bounded_thread_failure_manifest():
    class FailingThreadBroker:
        async def fetch_thread(self, item, max_comments=0, max_depth=0):
            raise RuntimeError("route unavailable")

    root_items = [
        {"platform": "youtube", "external_id": f"yt{i}", "engagement": {"comments": i}}
        for i in range(6)
    ] + [
        {"platform": "reddit", "external_id": f"rd{i}", "engagement": {"comments": i}}
        for i in range(6)
    ]
    collected = {"c1": root_items}
    plan = {"effective_budget": {
        "threads_per_platform": 6, "comments_per_thread": 2, "max_thread_depth": 2,
    }}

    async def _run():
        handler = await make_deep_read_handler(FailingThreadBroker(), plan, collected)
        return await handler(
            {"candidate_id": "c1", "keyword": "test",
             "platforms": ["youtube", "reddit"]}, {},
        )

    result = asyncio.run(_run())

    # All 12 thread reads failed, but the persisted manifest stays bounded.
    assert result.external_calls == 12
    assert result.status == "empty"
    deep_health = collected["c1:deep_health"]
    assert len(deep_health) == 11
    assert deep_health[0]["stage"] == "deep_read"
    assert deep_health[-1]["error"].startswith("2 more thread reads failed")
    assert collected["c1:deep"] == root_items


def test_horizontal_handler_merges_deep_health_into_limitations():
    llm, _ = _llm_returning({
        "summary": "", "signals": [], "entities": [], "limitations": [],
    })
    collected = {
        "c1:deep": [USABLE_POST],
        "c1:health": [{"platform": "reddit", "status": "success"}],
        "c1:deep_health": [{
            "platform": "youtube", "status": "failed", "stage": "deep_read",
            "external_id": "yt0", "error": "RuntimeError: route unavailable",
        }],
    }
    handler = make_horizontal_extraction_handler(
        plan={"effective_budget": {}}, collected=collected, llm_call_fn=llm,
    )

    result = asyncio.run(handler({"candidate_id": "c1", "keyword": "test"}, {}))

    assert result.llm_calls == 1
    limitations = collected["c1:findings"]["limitations"]
    assert any(
        "1 of 2 collection sources failed (youtube)" in item
        and "coverage is partial" in item
        for item in limitations
    )


# ── 3. Canonical public URL rejection ─────────────────────────


@pytest.mark.parametrize("url", [
    # Noncanonical address spellings that parse as hostnames, not IPs.
    "http://127.1/admin",
    "http://0177.0.0.1/admin",
    "http://0x7f.0.0.1/admin",
    "http://2130706433/admin",
    # Private, loopback, link-local, multicast, reserved, documentation.
    "http://10.0.0.7/private",
    "http://192.168.1.4/private",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/admin",
    "http://[fe80::1]/admin",
    "http://224.0.0.1/stream",
    "http://240.0.0.1/legacy",
    "http://192.0.2.9/test-net",
    "http://[fd00::1]/ula",
    # Non-public host forms.
    "http://localhost/x",
    "https://intranet.internal/x",
    "file:///etc/passwd",
    "https://user:pw@example.com/x",
    "https://exa mple.com/x",
])
def test_non_public_or_noncanonical_urls_are_not_citable(url):
    assert _has_public_source_url(url) is False


@pytest.mark.parametrize("url", [
    "https://www.reddit.com/r/example/comments/r1",
    "https://www.youtube.com/watch?v=y1",
    "https://8.8.8.8/record",
    "https://example.org/evidence",
])
def test_canonical_public_urls_remain_citable(url):
    assert _has_public_source_url(url) is True


def test_noncanonical_loopback_spelling_rejects_cited_claim_end_to_end():
    llm, _ = _llm_returning({
        "summary": "Loopback claim",
        "summary_evidence_ids": ["reddit:post:r1"],
        "signals": [{
            "kind": "pain_point",
            "claim": "A claim hiding behind a noncanonical address.",
            "polarity": "negative",
            "evidence_ids": ["reddit:post:r1"],
        }],
        "entities": [],
        "limitations": [],
    })

    result = asyncio.run(analyze_conversation(
        "topic", [dict(USABLE_POST, url="http://127.1/admin")],
        source_health=[], llm_call_fn=llm,
    ))

    assert result.signals == []
    assert result.summary == ""
    assert any("openable source URL" in item for item in result.limitations)


# ── 4. Bounded candidate nested strings / platform collections ──


def _client(tmp_path, monkeypatch):
    path = tmp_path / "evidence-boundaries.db"
    discovery = DiscoveryStore(path)
    lenses = LensStore(path)
    workspaces = WorkspaceStore(path)
    monkeypatch.setattr(dashboard_api, "_discovery_store", discovery)
    monkeypatch.setattr(dashboard_api, "_lens_store", lenses)
    monkeypatch.setattr(dashboard_api, "_workspace_store", workspaces)
    monkeypatch.setattr(
        dashboard_api, "_workspace_service",
        WorkspaceService(workspaces, lenses, discovery),
    )
    monkeypatch.delenv("BOUNTY_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setenv("BOUNTY_ENV", "development")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    app = FastAPI()
    app.include_router(dashboard_api.router)
    return TestClient(app)


def _brief(**overrides):
    payload = {
        "workspace_id": "default",
        "name": "Bounded brief",
        "candidates": [{"id": "one", "keyword": "topic one", "eligible": True}],
        "required_depth": "horizontal_analysis",
        "budget": {"root_probe_candidates": 1, "horizontal_llm_candidates": 1},
    }
    payload.update(overrides)
    return payload


def test_platform_collections_are_bounded_and_normalized(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    created = client.post("/dashboard/api/discovery/research-runs", json=_brief(
        candidates=[{
            "id": "one", "keyword": "topic one", "eligible": True,
            "platforms": ["Reddit", " YouTube ", "reddit"],
        }],
    ))
    assert created.status_code == 201, created.text
    candidate = created.json()["plan"]["candidates"][0]["candidate"]
    assert candidate["platforms"] == ["reddit", "youtube"]

    invalid = [
        # Too many platforms.
        _brief(candidates=[{
            "id": "one", "keyword": "topic one", "eligible": True,
            "platforms": [f"p{i}" for i in range(9)],
        }]),
        # Overlong platform name.
        _brief(candidates=[{
            "id": "one", "keyword": "topic one", "eligible": True,
            "platforms": ["x" * 33],
        }]),
        # Platforms must be a list of strings.
        _brief(candidates=[{
            "id": "one", "keyword": "topic one", "eligible": True,
            "platforms": "youtube",
        }]),
        _brief(candidates=[{
            "id": "one", "keyword": "topic one", "eligible": True,
            "platforms": [7],
        }]),
    ]
    for payload in invalid:
        rejected = client.post("/dashboard/api/discovery/research-runs", json=payload)
        assert rejected.status_code == 422, (payload, rejected.text)


def test_candidate_nested_strings_and_keywords_are_bounded(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    invalid = [
        _brief(candidates=[{
            "id": "one", "keyword": "k" * 121, "eligible": True,
        }]),
        _brief(candidates=[{
            "id": "one", "keyword": "topic one", "eligible": True,
            "rationale": "x" * 501,
        }]),
        _brief(candidates=[{
            "id": "one", "keyword": "topic one", "eligible": True,
            "notes": ["fine", ["nested", "y" * 501]],
        }]),
    ]
    for payload in invalid:
        rejected = client.post("/dashboard/api/discovery/research-runs", json=payload)
        assert rejected.status_code == 422, (payload, rejected.text)

    # Strings at the bounds still plan successfully.
    ok = client.post("/dashboard/api/discovery/research-runs", json=_brief(
        candidates=[{
            "id": "o" * 100, "keyword": "k" * 120, "eligible": True,
            "rationale": "r" * 500,
        }],
    ))
    assert ok.status_code == 201, ok.text
    candidate = ok.json()["plan"]["candidates"][0]["candidate"]
    assert candidate["keyword"] == "k" * 120
