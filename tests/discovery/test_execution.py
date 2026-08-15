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


def test_horizontal_extraction_receipt_matches_exact_prepared_prompt():
    from social_scraper.discovery.handlers import make_horizontal_extraction_handler
    from social_scraper.discovery.triage import prepare_conversation_prompt

    async def _run():
        # Noisy pool: an exact duplicate and a per-platform flood, so the
        # receipt must reflect prepared evidence, never the raw post count.
        posts = [
            {"platform": "youtube", "external_id": "v1", "text": "I love this product",
             "author": {"id": "u1", "username": "user1"}, "title": ""},
            {"platform": "youtube", "external_id": "v1", "text": "I love this product",
             "author": {"id": "u1", "username": "user1"}, "title": ""},
        ] + [
            {"platform": "reddit", "post_id": f"p{i}", "text": f"comment number {i}",
             "author": {"id": f"a{i}", "username": f"user{i}"}, "title": ""}
            for i in range(7)
        ]
        collected = {"c1:deep": posts, "c1:health": []}
        sent = []

        async def mock_llm(system, user):
            sent.append((system, user))
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

        prepared = prepare_conversation_prompt("test", posts)
        assert result.llm_calls == 1
        assert result.status == "complete"
        # Live receipt is nonzero and equals the prepared prompt, not the pool.
        assert result.input_records == prepared.input_records
        assert 0 < result.input_records < len(posts)
        assert result.input_characters == prepared.input_characters
        assert result.input_characters > 0
        # The receipt matches the exact strings the model received.
        assert len(sent) == 1
        assert result.input_characters == len(sent[0][0]) + len(sent[0][1])
        # Provider-reported tokens stay null: call_llm returns text only and
        # nothing here may fabricate them.
        assert result.input_tokens is None
        assert result.output_tokens is None
        assert result.input_tokens_reported is None
        assert result.output_tokens_reported is None
        assert result.tokens_estimated is False

    asyncio.run(_run())


def test_horizontal_extraction_receipt_counts_prompt_even_when_llm_fails():
    from social_scraper.discovery.handlers import make_horizontal_extraction_handler
    from social_scraper.discovery.triage import prepare_conversation_prompt

    async def _run():
        posts = [
            {"platform": "youtube", "external_id": "v1", "text": "I love this product",
             "author": {"id": "u1", "username": "user1"}, "title": ""},
        ]
        collected = {"c1:deep": posts, "c1:health": []}
        sent = []

        async def failing_llm(system, user):
            sent.append((system, user))
            raise RuntimeError("provider unavailable")

        handler = make_horizontal_extraction_handler(
            plan={"effective_budget": {}}, collected=collected, llm_call_fn=failing_llm,
        )
        result = await handler({"candidate_id": "c1", "keyword": "test"}, {})

        prepared = prepare_conversation_prompt("test", posts)
        # The prompt was transmitted before the failure, so the receipt keeps
        # the real input size while the analysis reports no findings.
        assert len(sent) == 1
        assert result.input_records == prepared.input_records == 1
        assert result.input_characters == prepared.input_characters
        assert result.status == "empty"
        assert collected["c1:findings"]["status"] == "insufficient_evidence"

    asyncio.run(_run())


def test_horizontal_extraction_receipt_stays_zero_when_no_prompt_is_sent():
    from social_scraper.discovery.handlers import make_horizontal_extraction_handler

    async def _run():
        collected = {
            "c1:deep": [
                {"platform": "youtube", "external_id": "v1", "text": "",
                 "author": {"id": "u1"}, "title": ""},
            ],
            "c1:health": [],
        }

        async def unexpected_llm(system, user):
            raise AssertionError(
                "no prompt should be transmitted without usable evidence"
            )

        handler = make_horizontal_extraction_handler(
            plan={"effective_budget": {}}, collected=collected,
            llm_call_fn=unexpected_llm,
        )
        result = await handler({"candidate_id": "c1", "keyword": "test"}, {})
        assert result.status == "empty"
        assert result.input_records == 0
        assert result.input_characters == 0

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


def test_staged_runner_receipt_from_live_handlers_is_nonzero_and_exact():
    """Live build_handlers chain: the horizontal receipt measures the prompt."""
    from social_scraper.discovery.handlers import build_handlers
    from social_scraper.discovery.triage import prepare_conversation_prompt

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

        sent = []

        async def mock_llm(system, user):
            sent.append((system, user))
            return json.dumps({
                "summary": "Positive discussion",
                "signals": [{
                    "kind": "desire", "claim": "People want more content",
                    "polarity": "positive", "evidence_ids": ["youtube:comment:c1"],
                }],
                "entities": [],
                "limitations": [],
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
        result = await StagedRunner(handlers).run("test-run-receipt", plan)

        usage = next(u for u in result.usages if u.stage == "horizontal_extraction")
        prepared = prepare_conversation_prompt("test", collected["c1:deep"])
        assert usage.llm_calls == 1
        # The aggregated live receipt is nonzero and equals the exact prompt
        # the live handler transmitted (one root post + one comment).
        assert usage.input_records == prepared.input_records == 2
        assert usage.input_characters == prepared.input_characters
        assert usage.input_characters > 0
        assert len(sent) == 1
        assert usage.input_characters == len(sent[0][0]) + len(sent[0][1])
        # No provider token data exists in this path; nothing is invented.
        assert usage.input_tokens is None
        assert usage.output_tokens is None
        assert usage.tokens_estimated is False
        assert usage.input_tokens_reported is None
        assert usage.output_tokens_reported is None

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
