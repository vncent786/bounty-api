"""Tests for research run execution, findings persistence, and the handlers layer."""

import asyncio
import json
import sqlite3
import pytest
from datetime import datetime, timedelta, timezone
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
    claim_token = store.claim_research_run(run_id)
    assert claim_token is not None

    finding = store.save_findings(
        run_id, "cand1", "test topic", "supported",
        {"summary": "Test", "signals": [{"kind": "question", "claim": "Why?"}]},
        claim_token=claim_token,
    )
    assert finding["candidate_id"] == "cand1"
    assert finding["topic"] == "test topic"
    assert finding["status"] == "supported"

    findings = store.list_findings(run_id)
    assert len(findings) == 1
    assert findings[0]["analysis"]["summary"] == "Test"
    assert findings[0]["analysis"]["signals"][0]["kind"] == "question"


def test_save_findings_is_idempotent_per_run_candidate(tmp_path):
    store = DiscoveryStore(tmp_path / "test.db")
    run = store.create_research_run(
        workspace_id="ws1", requested_budget={}, effective_budget={},
        plan={"candidates": []}, status="planned",
    )
    claim_token = store.claim_research_run(run["id"])
    assert claim_token is not None
    first = store.save_findings(
        run["id"], "c1", "topic", "insufficient_evidence", {"summary": ""},
        claim_token=claim_token,
    )
    second = store.save_findings(
        run["id"], "c1", "topic", "supported", {"summary": "updated"},
        claim_token=claim_token,
    )

    findings = store.list_findings(run["id"])
    assert len(findings) == 1
    assert first["id"] == second["id"]
    assert findings[0]["status"] == "supported"
    assert findings[0]["analysis"]["summary"] == "updated"


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
    claim_token = store.claim_research_run(run_id)
    assert claim_token is not None

    store.save_findings(
        run_id, "c1", "topic a", "supported", {"summary": "A"},
        claim_token=claim_token,
    )
    store.save_findings(
        run_id, "c2", "topic b", "insufficient_evidence", {"summary": "B"},
        claim_token=claim_token,
    )

    findings = store.list_findings(run_id)
    assert len(findings) == 2
    assert findings[0]["candidate_id"] == "c1"
    assert findings[1]["candidate_id"] == "c2"


def test_stale_worker_cannot_overwrite_current_findings(tmp_path):
    store = DiscoveryStore(tmp_path / "stale-worker.db")
    run = store.create_research_run(
        workspace_id="ws1", requested_budget={}, effective_budget={},
        plan={"candidates": []}, status="planned",
    )
    first_claim = store.claim_research_run(run["id"], lease_minutes=1)
    assert first_claim is not None
    store.save_findings(
        run["id"], "c1", "topic", "supported", {"summary": "first-worker"},
        claim_token=first_claim,
    )

    second_claim = store.claim_research_run(
        run["id"], lease_minutes=1,
        now=datetime.now(timezone.utc) + timedelta(minutes=2),
    )
    assert second_claim is not None and second_claim != first_claim

    with pytest.raises(ValueError, match="stale claim"):
        store.save_findings(
            run["id"], "c1", "topic", "supported", {"summary": "stale-worker"},
            claim_token=first_claim,
        )
    store.save_findings(
        run["id"], "c1", "topic", "supported", {"summary": "current-worker"},
        claim_token=second_claim,
    )
    store.update_research_run(
        run["id"], status="complete", claim_token=second_claim,
    )
    with pytest.raises(ValueError, match="active running claim"):
        store.save_findings(
            run["id"], "c1", "topic", "supported", {"summary": "after-complete"},
            claim_token=second_claim,
        )
    assert store.list_findings(run["id"])[0]["analysis"]["summary"] == "current-worker"


@pytest.mark.parametrize("terminal_status", ["complete", "partial", "error", "cancelled"])
def test_terminal_research_mutations_require_live_running_claim(
    tmp_path, terminal_status,
):
    store = DiscoveryStore(tmp_path / f"terminal-{terminal_status}.db")
    claimed_at = datetime(2026, 8, 15, tzinfo=timezone.utc)
    run = store.create_research_run(
        workspace_id="ws1", requested_budget={}, effective_budget={},
        plan={"candidates": []}, status="planned",
    )
    claim_token = store.claim_research_run(
        run["id"], lease_minutes=1, now=claimed_at,
    )
    assert claim_token is not None

    with pytest.raises(ValueError, match="claim token"):
        store.update_research_run(
            run["id"], status=terminal_status,
            now=claimed_at + timedelta(seconds=10),
        )
    with pytest.raises(ValueError, match="stale claim"):
        store.update_research_run(
            run["id"], status=terminal_status, claim_token="wrong-token",
            now=claimed_at + timedelta(seconds=10),
        )

    finished = store.update_research_run(
        run["id"], status=terminal_status, claim_token=claim_token,
        now=claimed_at + timedelta(seconds=30),
    )
    assert finished["status"] == terminal_status
    assert finished["lease_token"] is None
    assert finished["lease_until"] is None
    with pytest.raises(ValueError, match="stale claim"):
        store.update_research_run(
            run["id"], status=terminal_status, claim_token=claim_token,
            now=claimed_at + timedelta(seconds=40),
        )

    expired_run = store.create_research_run(
        workspace_id="ws1", requested_budget={}, effective_budget={},
        plan={"candidates": []}, status="planned",
    )
    expired_token = store.claim_research_run(
        expired_run["id"], lease_minutes=1, now=claimed_at,
    )
    assert expired_token is not None
    with pytest.raises(ValueError, match="stale claim"):
        store.update_research_run(
            expired_run["id"], status=terminal_status,
            claim_token=expired_token, now=claimed_at + timedelta(minutes=1),
        )
    assert store.get_research_run(expired_run["id"])["status"] == "running"


def test_research_claim_can_be_renewed_only_by_current_worker(tmp_path):
    store = DiscoveryStore(tmp_path / "renew-worker.db")
    claimed_at = datetime(2026, 8, 15, tzinfo=timezone.utc)
    run = store.create_research_run(
        workspace_id="ws1", requested_budget={}, effective_budget={},
        plan={"candidates": []}, status="planned",
    )
    claim_token = store.claim_research_run(
        run["id"], lease_minutes=1, now=claimed_at,
    )
    assert claim_token is not None
    before = store.get_research_run(run["id"])["lease_until"]
    assert store.renew_research_run_claim(
        run["id"], claim_token, lease_minutes=2,
        now=claimed_at + timedelta(seconds=30),
    ) is True
    after = store.get_research_run(run["id"])["lease_until"]
    assert after > before
    assert store.renew_research_run_claim(
        run["id"], "wrong-token", lease_minutes=2,
        now=claimed_at + timedelta(seconds=30),
    ) is False
    assert store.renew_research_run_claim(
        run["id"], claim_token, lease_minutes=2,
        now=claimed_at + timedelta(minutes=3),
    ) is False
    assert store.release_research_run_claim(
        run["id"], claim_token, now=claimed_at + timedelta(minutes=3),
    ) is False


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
                {"platform": "youtube", "external_id": "v1", "url": "https://www.youtube.com/watch?v=v1", "text": "I love this product",
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


def test_horizontal_extraction_keeps_engagement_in_prompt_and_findings(tmp_path):
    from social_scraper.discovery.handlers import make_horizontal_extraction_handler

    async def _run():
        url = "https://www.youtube.com/watch?v=v1&utm_source=kept"
        provenance = {
            "connector": "youtube_free",
            "query": "test",
            "engagement_sources": {"views": "view_count"},
        }
        engagement = {
            "views": 0,
            "likes": 4,
            "comments": None,
            "shares": 2,
            "collects": 1,
            "upvotes": None,
            "replies": 0,
            "reposts": None,
            "bookmarks": 3,
            "creator_followers": 100,
        }
        collected = {
            "c1:deep": [{
                "platform": "youtube", "external_id": "v1", "url": url,
                "text": "I bought this after seeing it demonstrated.",
                "provenance": provenance, "engagement": engagement,
            }],
            "c1:health": [],
        }
        sent = []

        async def mock_llm(_system, user):
            sent.append(json.loads(user))
            return json.dumps({
                "summary": "",
                "summary_evidence_ids": [],
                "signals": [{
                    "kind": "adoption",
                    "claim": "A cited author reports buying the product.",
                    "polarity": "positive",
                    "evidence_ids": ["youtube:post:v1"],
                }],
                "entities": [],
                "limitations": [],
            })

        handler = make_horizontal_extraction_handler(
            plan={"effective_budget": {}}, collected=collected,
            llm_call_fn=mock_llm,
        )
        result = await handler(
            {"candidate_id": "c1", "keyword": "test"}, {},
        )

        prompt_record = sent[0]["evidence_records"][0]
        finding_record = collected["c1:findings"]["evidence"][0]
        returned_record = result.candidates[0]["_findings"]["evidence"][0]
        assert prompt_record["engagement"] == engagement
        assert finding_record["engagement"] == engagement
        assert returned_record["engagement"] == engagement
        assert prompt_record["url"] == finding_record["url"] == url
        assert prompt_record["provenance"] == finding_record["provenance"] == provenance
        assert collected["c1:findings"]["interpretation"]["conversation_state"] == (
            "observed_behavior"
        )

        store = DiscoveryStore(tmp_path / "engagement-findings.db")
        run = store.create_research_run(
            workspace_id="ws1", requested_budget={}, effective_budget={},
            plan={"candidates": []}, status="planned",
        )
        claim_token = store.claim_research_run(run["id"])
        assert claim_token is not None
        store.save_findings(
            run["id"], "c1", "test", "supported", collected["c1:findings"],
            claim_token=claim_token,
        )
        persisted_record = store.list_findings(run["id"])[0]["analysis"]["evidence"][0]
        assert persisted_record["engagement"] == engagement
        assert persisted_record["url"] == url
        assert persisted_record["provenance"] == provenance

    asyncio.run(_run())


def test_horizontal_extraction_receipt_matches_exact_prepared_prompt():
    from social_scraper.discovery.handlers import make_horizontal_extraction_handler
    from social_scraper.discovery.triage import prepare_conversation_prompt

    async def _run():
        # Noisy pool: an exact duplicate and a per-platform flood, so the
        # receipt must reflect prepared evidence, never the raw post count.
        posts = [
            {"platform": "youtube", "external_id": "v1", "url": "https://www.youtube.com/watch?v=v1", "text": "I love this product",
             "author": {"id": "u1", "username": "user1"}, "title": ""},
            {"platform": "youtube", "external_id": "v1", "url": "https://www.youtube.com/watch?v=v1", "text": "I love this product",
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
            {"platform": "youtube", "external_id": "v1", "url": "https://www.youtube.com/watch?v=v1", "text": "I love this product",
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


def test_staged_runner_reports_truthful_unit_progress():
    async def _run():
        snapshots = []

        async def handler(_candidate, _context):
            return StageHandlerResult(records_returned=1)

        async def record_progress(snapshot):
            await asyncio.sleep(0)
            snapshots.append(dict(snapshot))

        plan = {
            "effective_budget": {
                "root_probe_candidates": 2,
                "deep_read_candidates": 1,
            },
            "candidates": [
                {
                    "candidate_id": "c1",
                    "candidate": {"keyword": "one"},
                    "stages": {
                        "root_probe": "planned",
                        "deep_read": "planned",
                    },
                },
                {
                    "candidate_id": "c2",
                    "candidate": {"keyword": "two"},
                    "stages": {
                        "root_probe": "planned",
                        "deep_read": "budget_exhausted",
                    },
                },
            ],
        }

        await StagedRunner(
            {"root_probe": handler, "deep_read": handler},
            progress_recorder=record_progress,
        ).run("progress-run", plan)

        assert [
            (
                item["phase"], item["candidate_id"],
                item["completed_units"], item["total_units"],
                item["phase_completed"], item["phase_total"],
                item["complete"], item["percent"],
            )
            for item in snapshots
        ] == [
            ("starting", None, 0, 3, 0, 0, False, 0.0),
            ("root_probe", None, 0, 3, 0, 2, False, 0.0),
            ("root_probe", "c1", 1, 3, 1, 2, False, 33.33),
            ("root_probe", "c2", 2, 3, 2, 2, False, 66.67),
            ("deep_read", None, 2, 3, 0, 1, False, 66.67),
            ("deep_read", "c1", 3, 3, 1, 1, False, 100.0),
            ("finalizing", None, 3, 3, 0, 0, False, 100.0),
        ]
        assert all(item["estimated_remaining_seconds"] is None for item in snapshots)
        assert all(
            datetime.fromisoformat(item["updated_at"]).tzinfo is not None
            for item in snapshots
        )

    asyncio.run(_run())


def test_handler_chain_persists_source_gap_when_collection_returns_no_evidence():
    from social_scraper.discovery.handlers import build_handlers

    async def _run():
        broker = MagicMock()
        broker.search = AsyncMock(return_value={
            "items": [],
            "source_health": [{
                "platform": "youtube", "status": "failed",
                "error": "upstream unavailable",
            }],
            "platform_results": {"youtube": {"status": "failed"}},
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
                "candidate": {"keyword": "source gap", "platforms": ["youtube"]},
                "stages": {
                    "root_probe": "planned",
                    "deep_read": "planned",
                    "horizontal_extraction": "planned",
                },
            }],
        }
        handlers, collected = build_handlers(broker, plan)
        result = await StagedRunner(handlers).run("gap-run", plan)

        assert result.handler_results["root_probe"]["c1"].status == "failed"
        assert collected["c1:findings"]["status"] == "sources_unavailable"
        assert collected["c1:findings"]["coverage"]["source_status"][0]["status"] == "failed"
        assert "No usable conversation records were collected." in collected["c1:findings"]["limitations"]

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


def test_execute_starts_once_and_persists_running_status(tmp_path, monkeypatch):
    """Starting research returns immediately and the durable run is pollable."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import apis.dashboard_api as dashboard_api

    store = DiscoveryStore(tmp_path / "background-api.db")
    monkeypatch.setattr(dashboard_api, "_discovery_store", store)
    monkeypatch.setenv("BOUNTY_ENV", "test")
    scheduled = []
    monkeypatch.setattr(dashboard_api, "_research_tasks", {})

    def schedule(run_id, claim_token):
        scheduled.append((run_id, claim_token))
        dashboard_api._research_tasks[run_id] = object()

    monkeypatch.setattr(dashboard_api, "_schedule_research_run", schedule)
    run = store.create_research_run(
        workspace_id="ws1",
        requested_budget={"root_probe_candidates": 1},
        effective_budget={"root_probe_candidates": 1},
        plan={"candidates": [], "effective_budget": {}},
        status="planned",
    )

    app = FastAPI()
    app.include_router(dashboard_api.router)
    client = TestClient(app)

    first = client.post(
        f"/dashboard/api/discovery/research-runs/{run['id']}/execute"
    )
    assert first.status_code == 202
    assert first.json()["status"] == "running"
    assert [item[0] for item in scheduled] == [run["id"]]
    persisted = store.get_research_run(run["id"])
    assert persisted["status"] == "running"
    assert persisted["lease_token"]
    public_run = client.get(
        f"/dashboard/api/discovery/research-runs/{run['id']}"
    ).json()
    assert "lease_token" not in public_run
    assert "lease_until" not in public_run

    second = client.post(
        f"/dashboard/api/discovery/research-runs/{run['id']}/execute"
    )
    assert second.status_code == 202
    assert second.json()["status"] == "running"
    assert second.json()["resumed"] is False
    assert [item[0] for item in scheduled] == [run["id"]]

    dashboard_api._research_tasks.clear()
    assert store.release_research_run_claim(run["id"], scheduled[0][1]) is True
    resumed = client.post(
        f"/dashboard/api/discovery/research-runs/{run['id']}/execute"
    )
    assert resumed.status_code == 202
    assert resumed.json()["resumed"] is True
    assert [item[0] for item in scheduled] == [run["id"], run["id"]]


def test_background_worker_persists_findings_and_terminal_status(tmp_path, monkeypatch):
    import apis.dashboard_api as dashboard_api
    import social_scraper.discovery.handlers as handlers_module
    import social_scraper.discovery.staged_runner as runner_module

    store = DiscoveryStore(tmp_path / "worker.db")
    monkeypatch.setattr(dashboard_api, "_discovery_store", store)
    monkeypatch.setattr(dashboard_api, "_get_broker", lambda: object())
    analysis = {
        "status": "supported",
        "summary": "A citation-backed controlled finding.",
        "signals": [],
        "entities": [],
        "evidence": [{"id": "reddit:post:1", "url": "https://reddit.com/r/test/1"}],
        "limitations": [],
    }
    monkeypatch.setattr(
        handlers_module, "build_handlers",
        lambda *_args, **_kwargs: ({}, {"c1:findings": analysis}),
    )

    class FakeResult:
        handler_results = {"horizontal_extraction": {"c1": MagicMock(status="complete")}}
        usages = []

    class FakeRunner:
        def __init__(self, _handlers, progress_recorder=None):
            self.progress_recorder = progress_recorder

        async def run(self, _run_id, _plan):
            if self.progress_recorder:
                self.progress_recorder({
                    "phase": "finalizing", "candidate_id": None,
                    "completed_units": 0, "total_units": 0,
                    "phase_completed": 0, "phase_total": 0,
                    "complete": False, "percent": None,
                    "estimated_remaining_seconds": None,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })
            return FakeResult()

    monkeypatch.setattr(runner_module, "StagedRunner", FakeRunner)
    run = store.create_research_run(
        workspace_id="ws1",
        requested_budget={},
        effective_budget={},
        plan={
            "effective_budget": {},
            "candidates": [{
                "candidate_id": "c1",
                "candidate": {"keyword": "Cairn creative fatigue"},
                "stages": {},
            }],
        },
        status="running",
    )

    claim_token = store.claim_research_run(run["id"])
    assert claim_token is not None
    result = asyncio.run(
        dashboard_api._execute_research_run_background(run["id"], claim_token)
    )

    assert result["status"] == "complete"
    persisted_run = store.get_research_run(run["id"])
    assert persisted_run["status"] == "complete"
    assert persisted_run["result"]["findings_count"] == 1
    public_run = asyncio.run(dashboard_api.get_discovery_research_run(run["id"]))
    assert public_run["result"]["findings_count"] == 1
    findings = store.list_findings(run["id"])
    assert len(findings) == 1
    assert findings[0]["topic"] == "Cairn creative fatigue"
    assert findings[0]["analysis"]["evidence"][0]["url"].startswith("https://")


def test_background_worker_persists_claim_guarded_progress(tmp_path, monkeypatch):
    import apis.dashboard_api as dashboard_api
    import social_scraper.discovery.handlers as handlers_module

    store = DiscoveryStore(tmp_path / "worker-progress.db")
    monkeypatch.setattr(dashboard_api, "_discovery_store", store)
    monkeypatch.setattr(dashboard_api, "_get_broker", lambda: object())
    handler_started = asyncio.Event()
    allow_completion = asyncio.Event()

    async def root_handler(_candidate, _context):
        handler_started.set()
        await allow_completion.wait()
        return StageHandlerResult(records_returned=1)

    monkeypatch.setattr(
        handlers_module, "build_handlers",
        lambda *_args, **_kwargs: ({"root_probe": root_handler}, {}),
    )
    plan = {
        "effective_budget": {"root_probe_candidates": 1},
        "candidates": [{
            "candidate_id": "c1",
            "candidate": {"keyword": "durable progress"},
            "stages": {"root_probe": "planned"},
        }],
    }
    run = store.create_research_run(
        workspace_id="ws1", requested_budget={}, effective_budget={},
        plan=plan, status="planned",
    )
    claim_token = store.claim_research_run(run["id"])
    assert claim_token is not None

    update_calls = []
    real_update = store.update_research_run

    def record_update(*args, **kwargs):
        update_calls.append((args, kwargs))
        return real_update(*args, **kwargs)

    monkeypatch.setattr(store, "update_research_run", record_update)

    async def execute_and_poll():
        task = asyncio.create_task(
            dashboard_api._execute_research_run_background(run["id"], claim_token)
        )
        await handler_started.wait()
        running = store.get_research_run(run["id"])
        allow_completion.set()
        return await task, running

    result, running = asyncio.run(execute_and_poll())

    assert running["status"] == "running"
    assert running["result"]["progress"]["phase"] == "root_probe"
    assert running["result"]["progress"]["completed_units"] == 0
    assert running["result"]["progress"]["total_units"] == 1

    progress_updates = [
        kwargs["result"]["progress"]
        for _args, kwargs in update_calls
        if kwargs.get("status") == "running"
    ]
    assert [snapshot["phase"] for snapshot in progress_updates] == [
        "starting", "root_probe", "root_probe", "finalizing",
    ]
    assert all(
        kwargs["claim_token"] == claim_token
        for _args, kwargs in update_calls
        if kwargs.get("status") == "running"
    )
    assert progress_updates[2]["candidate_id"] == "c1"
    assert progress_updates[2]["completed_units"] == 1

    persisted = store.get_research_run(run["id"])
    assert result["status"] == "complete"
    assert persisted["status"] == "complete"
    assert progress_updates[-1]["phase"] == "finalizing"
    assert progress_updates[-1]["complete"] is False
    assert persisted["result"]["progress"]["phase"] == "complete"
    assert persisted["result"]["progress"]["complete"] is True
    assert persisted["result"]["progress"]["percent"] == 100.0
    assert persisted["result"]["progress"]["estimated_remaining_seconds"] is None


def test_background_worker_fallback_finding_uses_v3_conservative_interpretation(tmp_path, monkeypatch):
    import apis.dashboard_api as dashboard_api
    import social_scraper.discovery.handlers as handlers_module

    store = DiscoveryStore(tmp_path / "fallback-v3.db")
    monkeypatch.setattr(dashboard_api, "_discovery_store", store)
    monkeypatch.setattr(dashboard_api, "_get_broker", lambda: object())

    async def root_handler(_candidate, _context):
        return StageHandlerResult(records_returned=0)

    monkeypatch.setattr(
        handlers_module, "build_handlers",
        lambda *_args, **_kwargs: ({"root_probe": root_handler}, {}),
    )
    plan = {
        "effective_budget": {"root_probe_candidates": 1},
        "candidates": [{
            "candidate_id": "c1",
            "candidate": {"keyword": "bounded fallback"},
            "stages": {"root_probe": "planned"},
        }],
    }
    run = store.create_research_run(
        workspace_id="ws1", requested_budget={}, effective_budget={},
        plan=plan, status="planned",
    )
    claim_token = store.claim_research_run(run["id"])
    result = asyncio.run(
        dashboard_api._execute_research_run_background(run["id"], claim_token)
    )

    assert result["status"] == "complete"
    finding = store.list_findings(run["id"])[0]["analysis"]
    assert finding["schema_version"] == "conversation-analysis/3"
    assert finding["interpretation"]["conversation_state"] == "insufficient_evidence"
    assert any(
        "comparable collection periods" in limitation
        for limitation in finding["interpretation"]["limitations"]
    )


def test_finding_persistence_failure_never_marks_progress_complete(tmp_path, monkeypatch):
    import apis.dashboard_api as dashboard_api
    import social_scraper.discovery.handlers as handlers_module

    store = DiscoveryStore(tmp_path / "persistence-failure.db")
    monkeypatch.setattr(dashboard_api, "_discovery_store", store)
    monkeypatch.setattr(dashboard_api, "_get_broker", lambda: object())

    async def root_handler(_candidate, _context):
        return StageHandlerResult(records_returned=0)

    monkeypatch.setattr(
        handlers_module, "build_handlers",
        lambda *_args, **_kwargs: ({"root_probe": root_handler}, {}),
    )
    plan = {
        "effective_budget": {"root_probe_candidates": 1},
        "candidates": [{
            "candidate_id": "c1",
            "candidate": {"keyword": "persistence failure"},
            "stages": {"root_probe": "planned"},
        }],
    }
    run = store.create_research_run(
        workspace_id="ws1", requested_budget={}, effective_budget={},
        plan=plan, status="planned",
    )
    claim_token = store.claim_research_run(run["id"])

    def fail_save(*_args, **_kwargs):
        raise sqlite3.OperationalError("forced finding persistence failure")

    monkeypatch.setattr(store, "save_findings", fail_save)
    result = asyncio.run(
        dashboard_api._execute_research_run_background(run["id"], claim_token)
    )

    assert result["status"] == "error"
    persisted = store.get_research_run(run["id"])
    assert persisted["status"] == "error"
    progress = persisted["result"]["progress"]
    assert progress["phase"] == "finalizing"
    assert progress["complete"] is False


def test_transient_heartbeat_failure_cancels_research_execution(tmp_path, monkeypatch):
    import apis.dashboard_api as dashboard_api
    import social_scraper.discovery.handlers as handlers_module
    import social_scraper.discovery.staged_runner as runner_module

    store = DiscoveryStore(tmp_path / "heartbeat-failure.db")
    monkeypatch.setattr(dashboard_api, "_discovery_store", store)
    monkeypatch.setattr(dashboard_api, "_get_broker", lambda: object())
    monkeypatch.setattr(
        handlers_module, "build_handlers",
        lambda *_args, **_kwargs: ({}, {}),
    )
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingRunner:
        def __init__(self, _handlers, progress_recorder=None):
            pass

        async def run(self, _run_id, _plan):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

    async def fail_heartbeat(_run_id, _claim_token):
        await started.wait()
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(runner_module, "StagedRunner", BlockingRunner)
    monkeypatch.setattr(
        dashboard_api, "_renew_research_run_lease", fail_heartbeat,
    )
    run = store.create_research_run(
        workspace_id="ws1", requested_budget={}, effective_budget={},
        plan={"effective_budget": {}, "candidates": []}, status="planned",
    )
    claim_token = store.claim_research_run(run["id"])
    assert claim_token is not None

    result = asyncio.run(
        dashboard_api._execute_research_run_background(run["id"], claim_token)
    )

    assert cancelled.is_set()
    assert result["status"] == "error"
    persisted = store.get_research_run(run["id"])
    assert persisted["status"] == "error"
    assert persisted["lease_token"] is None
    assert "database is locked" in persisted["error_category"]
