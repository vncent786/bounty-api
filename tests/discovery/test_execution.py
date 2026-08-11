"""Tests for research run execution, findings persistence, and the handlers layer."""

import asyncio
import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from social_scraper.discovery.storage import DiscoveryStore
from social_scraper.discovery.budgets import ScanBudget, StageUsage
from social_scraper.discovery.scheduler import DiscoveryScheduler
from social_scraper.discovery.staged_runner import StagedRunner, StageHandlerResult


# ── Findings persistence ──────────────────────────────────────

def test_save_and_list_findings(tmp_path):
    store = DiscoveryStore(tmp_path / "test.db")
    run = store.create_research_run(
        workspace_id="ws1",
        requested_budget={"root_probe_candidates": 1},
        effective_budget={"root_probe_candidates": 1},
        plan={"candidates": []},
        status="planned",
    )
    run_id = run["id"]

    finding = store.save_findings(
        run_id, "cand1", "test topic", "supported",
        {"summary": "Test", "signals": [{"kind": "question", "claim": "Why?"}]},
    )
    assert finding["candidate_id"] == "cand1"
    assert finding["topic"] == "test topic"
    assert finding["status"] == "supported"

    findings = store.list_findings(run_id)
    assert len(findings) == 1
    assert findings[0]["analysis"]["summary"] == "Test"
    assert findings[0]["analysis"]["signals"][0]["kind"] == "question"


def test_findings_multiple_candidates(tmp_path):
    store = DiscoveryStore(tmp_path / "test.db")
    run = store.create_research_run(
        workspace_id="ws1",
        requested_budget={"root_probe_candidates": 2},
        effective_budget={"root_probe_candidates": 2},
        plan={"candidates": []},
        status="planned",
    )
    run_id = run["id"]

    store.save_findings(run_id, "c1", "topic a", "supported", {"summary": "A"})
    store.save_findings(run_id, "c2", "topic b", "insufficient_evidence", {"summary": "B"})

    findings = store.list_findings(run_id)
    assert len(findings) == 2
    assert findings[0]["candidate_id"] == "c1"
    assert findings[1]["candidate_id"] == "c2"


# ── Handler contract ──────────────────────────────────────────

def test_stage_handler_result_defaults():
    r = StageHandlerResult()
    assert r.records_returned == 0
    assert r.llm_calls == 0
    assert r.status == "complete"


def test_root_probe_handler_with_mock_broker():
    from social_scraper.discovery.handlers import make_root_probe_handler

    async def _run():
        broker = MagicMock()
        broker.search = AsyncMock(return_value={
            "items": [
                {"platform": "youtube", "external_id": "v1", "text": "hello",
                 "engagement": {"likes": 10, "comments": 5}},
            ],
            "source_health": [],
            "platform_results": {"youtube": {"status": "complete"}},
        })

        plan = {"effective_budget": {"threads_per_platform": 2, "comments_per_thread": 10, "max_thread_depth": 2}}
        handler = await make_root_probe_handler(broker, plan)
        result = await handler({"candidate_id": "c1", "keyword": "test"}, {})

        assert result.records_returned == 1
        assert result.status == "complete"
        assert result.external_calls >= 1

    asyncio.run(_run())


def test_horizontal_extraction_handler_with_mock_llm():
    from social_scraper.discovery.handlers import make_horizontal_extraction_handler

    async def _run():
        collected = {
            "c1:deep": [
                {"platform": "youtube", "external_id": "v1", "text": "I love this product",
                 "author": {"id": "u1", "username": "user1"}, "title": ""},
            ],
            "c1:health": [],
        }

        async def mock_llm(system, user):
            return json.dumps({
                "summary": "People discuss this topic",
                "signals": [{
                    "kind": "desire",
                    "claim": "Users want better features",
                    "polarity": "positive",
                    "evidence_ids": ["youtube:post:v1"],
                }],
                "entities": [],
                "limitations": [],
            })

        handler = make_horizontal_extraction_handler(
            plan={"effective_budget": {}}, collected=collected, llm_call_fn=mock_llm,
        )
        result = await handler({"candidate_id": "c1", "keyword": "test"}, {})

        assert result.llm_calls == 1
        assert result.status == "complete"
        assert "c1:findings" in collected

    asyncio.run(_run())


def test_full_handler_chain_with_mocks():
    """Test that root_probe -> deep_read -> horizontal_extraction pass evidence correctly."""
    from social_scraper.discovery.handlers import build_handlers

    async def _run():
        broker = MagicMock()
        broker.search = AsyncMock(return_value={
            "items": [
                {"platform": "youtube", "external_id": "v1", "text": "test post",
                 "engagement": {"likes": 5, "comments": 2}, "url": "https://youtube.com/v1"},
            ],
            "source_health": [{"platform": "youtube", "status": "complete"}],
            "platform_results": {"youtube": {"status": "complete"}},
        })

        thread_record = MagicMock()
        thread_record.platform = "youtube"
        thread_record.external_id = "c1"
        thread_record.record_type = "comment"
        thread_record.parent_external_id = "v1"
        thread_record.root_post_external_id = "v1"
        thread_record.depth = 1
        thread_record.url = "https://youtube.com/v1"
        thread_record.author_external_id = "u1"
        thread_record.author_username = "user1"
        thread_record.text = "Great video about this topic"
        thread_record.published_at = "2026-01-01T00:00:00Z"
        thread_record.likes = 3
        thread_result = MagicMock()
        thread_result.records = [thread_record]
        thread_result.attempted_route = "youtube_rss"
        broker.fetch_thread = AsyncMock(return_value=thread_result)

        async def mock_llm(system, user):
            return json.dumps({
                "summary": "Positive discussion",
                "signals": [{
                    "kind": "desire", "claim": "People want more content",
                    "polarity": "positive", "evidence_ids": ["youtube:comment:c1"],
                }],
                "entities": [], "limitations": [],
            })

        plan = {
            "effective_budget": {
                "root_probe_candidates": 1,
                "deep_read_candidates": 1,
                "horizontal_llm_candidates": 1,
                "threads_per_platform": 1,
                "comments_per_thread": 10,
                "max_thread_depth": 2,
            },
            "candidates": [{
                "candidate_id": "c1",
                "candidate": {"keyword": "test"},
                "stages": {
                    "root_probe": "planned",
                    "deep_read": "planned",
                    "horizontal_extraction": "planned",
                },
            }],
        }

        handlers, collected = build_handlers(broker, plan, llm_call_fn=mock_llm)

        runner = StagedRunner(handlers)
        result = await runner.run("test-run-1", plan)

        assert "root_probe" in result.handler_results
        assert "deep_read" in result.handler_results
        assert "horizontal_extraction" in result.handler_results

        hr = result.handler_results["horizontal_extraction"]["c1"]
        assert hr.llm_calls == 1
        assert "c1:findings" in collected
        findings = collected["c1:findings"]
        assert findings["status"] == "supported"
        assert len(findings["signals"]) >= 1

    asyncio.run(_run())


# ── API endpoint tests ────────────────────────────────────────

def test_execute_and_findings_api(tmp_path, monkeypatch):
    """Test the execute + findings endpoints with mocked handlers."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import apis.dashboard_api as dashboard_api

    db_path = str(tmp_path / "api_test.db")
    store = DiscoveryStore(db_path)
    monkeypatch.setattr(dashboard_api, "_discovery_store", store)
    monkeypatch.setenv("BOUNTY_ENV", "test")

    run = store.create_research_run(
        workspace_id="ws1",
        requested_budget={"root_probe_candidates": 1},
        effective_budget={"root_probe_candidates": 1},
        plan={"candidates": [], "effective_budget": {}},
        status="planned",
    )
    run_id = run["id"]

    app = FastAPI()
    app.include_router(dashboard_api.router)
    client = TestClient(app)

    resp = client.get(f"/dashboard/api/discovery/research-runs/{run_id}/findings")
    assert resp.status_code == 200
    assert resp.json()["findings"] == []

    resp = client.post("/dashboard/api/discovery/research-runs/nonexistent/execute")
    assert resp.status_code == 404

    resp = client.get("/dashboard/api/discovery/research-runs/nonexistent/findings")
    assert resp.status_code == 404
