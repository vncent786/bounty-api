"""Release evidence-boundary regressions kept separate to avoid worker conflicts.

Complements tests/discovery/test_evidence_boundaries.py with the guarantees
it does not cover:

1. Allowed platforms are validated at both boundaries: research-brief
   creation (request) and the staged collection handlers (execution), so an
   unknown platform name never reaches a broker search.
2. The execution receipt persisted for users is truthful and retrievable:
   llm_calls stays zero unless a model request is actually transmitted, and
   the persisted usage rows come back through the read API after a run.
"""

import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import apis.dashboard_api as dashboard_api
from social_scraper.discovery import DiscoveryStore
from social_scraper.discovery.handlers import (
    DEFAULT_PLATFORMS,
    make_deep_read_handler,
    make_root_probe_handler,
)


def _stub_subreddit_discovery(monkeypatch):
    """Keep root-probe tests offline and fast when reddit is in scope."""
    def _raise(keyword):
        raise RuntimeError("offline test")

    monkeypatch.setattr(
        "social_scraper.connectors.reddit_discover.discover_subreddits",
        _raise,
        raising=False,
    )


class RecordingBroker:
    """Fake broker that records search calls and optional live routes."""

    def __init__(self, items=None, routes=None, expose_routes=True):
        self.items = items or []
        self.searched = []
        self.routes = routes if routes is not None else ["youtube", "reddit"]
        self.expose_routes = expose_routes

    def list_platforms(self):
        if not self.expose_routes:
            return object()  # Non-list shape: must not be trusted as routes.
        return list(self.routes)

    async def search(self, keyword, platforms=None, count=10, platform_options=None):
        self.searched.append(list(platforms or []))
        return {
            "items": list(self.items),
            "source_health": [
                {"platform": platform, "status": "complete"}
                for platform in (platforms or [])
            ],
            "platform_results": {
                platform: {"status": "complete"} for platform in (platforms or [])
            },
        }


# ── Request boundary: allowed platforms ───────────────────────


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        dashboard_api, "_discovery_store", DiscoveryStore(tmp_path / "release.db")
    )
    monkeypatch.delenv("BOUNTY_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setenv("BOUNTY_ENV", "test")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    app = FastAPI()
    app.include_router(dashboard_api.router)
    return TestClient(app)


def _brief(**overrides):
    payload = {
        "workspace_id": "default",
        "name": "Platform allowlist brief",
        "candidates": [{"id": "one", "keyword": "topic one", "eligible": True}],
        "required_depth": "horizontal_analysis",
        "budget": {"root_probe_candidates": 1, "horizontal_llm_candidates": 1},
    }
    payload.update(overrides)
    return payload


def test_request_boundary_accepts_only_allowed_platforms(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    ok = client.post("/dashboard/api/discovery/research-runs", json=_brief(
        candidates=[{
            "id": "one", "keyword": "topic one", "eligible": True,
            "platforms": ["TikTok"],
        }],
    ))
    assert ok.status_code == 201, ok.text
    candidate = ok.json()["plan"]["candidates"][0]["candidate"]
    assert candidate["platforms"] == ["tiktok"]

    invalid = [
        # Wholly unknown platform.
        ["myspace"],
        # One unknown name spoils the whole list.
        ["tiktok", "internal-portal"],
        # Hostname-shaped injection attempts are just unknown platforms.
        ["http://169.254.169.254"],
    ]
    for platforms in invalid:
        rejected = client.post("/dashboard/api/discovery/research-runs", json=_brief(
            candidates=[{
                "id": "one", "keyword": "topic one", "eligible": True,
                "platforms": platforms,
            }],
        ))
        assert rejected.status_code == 422, (platforms, rejected.text)
        assert "unknown platform" in rejected.json()["detail"]


# ── Execution boundary: allowed platforms ─────────────────────


def test_execution_boundary_filters_platforms_to_broker_routes(monkeypatch):
    _stub_subreddit_discovery(monkeypatch)
    broker = RecordingBroker(routes=["youtube", "reddit"])

    async def _run():
        handler = await make_root_probe_handler(broker, plan={})
        return await handler({
            "candidate_id": "c1", "keyword": "test",
            "platforms": ["YouTube", "intranet", "ftp"],
        }, {})

    result = asyncio.run(_run())

    # Only the broker's live route survives; unknown names never search.
    assert broker.searched == [["youtube"]]
    assert result.external_calls == 1


def test_execution_boundary_falls_back_to_defaults_when_no_platform_valid(monkeypatch):
    _stub_subreddit_discovery(monkeypatch)
    broker = RecordingBroker(routes=["youtube"])

    async def _run():
        handler = await make_root_probe_handler(broker, plan={})
        return await handler({
            "candidate_id": "c1", "keyword": "test",
            "platforms": ["intranet"],
        }, {})

    asyncio.run(_run())

    assert broker.searched == [DEFAULT_PLATFORMS]


def test_execution_boundary_uses_static_allowlist_without_broker_routes(monkeypatch):
    _stub_subreddit_discovery(monkeypatch)

    class RoutelessBroker:
        """Exposes search only; no list_platforms for route discovery."""

        def __init__(self):
            self.searched = []

        async def search(self, keyword, platforms=None, count=10, platform_options=None):
            self.searched.append(list(platforms or []))
            return {
                "items": [],
                "source_health": [],
                "platform_results": {},
            }

    broker = RoutelessBroker()

    async def _run():
        handler = await make_root_probe_handler(broker, plan={})
        return await handler({
            "candidate_id": "c1", "keyword": "test",
            "platforms": ["youtube", "gopher"],
        }, {})

    result = asyncio.run(_run())

    assert broker.searched == [["youtube"]]
    assert result.external_calls == 1


def test_execution_boundary_ignores_non_list_route_reports(monkeypatch):
    _stub_subreddit_discovery(monkeypatch)
    # Mock-style brokers "expose" list_platforms but return non-list values;
    # the static allowlist must apply instead of an empty route set.
    broker = RecordingBroker(routes=None, expose_routes=False)

    async def _run():
        handler = await make_root_probe_handler(broker, plan={})
        return await handler({
            "candidate_id": "c1", "keyword": "test",
            "platforms": ["youtube", "gopher"],
        }, {})

    asyncio.run(_run())

    assert broker.searched == [["youtube"]]


def test_deep_read_filters_platforms_before_thread_reads(monkeypatch):
    _stub_subreddit_discovery(monkeypatch)

    class ThreadBroker(RecordingBroker):
        def __init__(self):
            super().__init__(routes=["youtube"])
            self.threads = []

        async def fetch_thread(self, item, max_comments=0, max_depth=0):
            self.threads.append(item)
            raise RuntimeError("route unavailable")

    broker = ThreadBroker()
    collected = {"c1": [{
        "platform": "youtube", "external_id": "v1",
        "engagement": {"comments": 4, "likes": 1},
    }]}
    plan = {"effective_budget": {
        "threads_per_platform": 1, "comments_per_thread": 5, "max_thread_depth": 2,
    }}

    async def _run():
        handler = await make_deep_read_handler(broker, plan, collected)
        return await handler({
            "candidate_id": "c1", "keyword": "test",
            "platforms": ["youtube", "tiktok"],
        }, {})

    result = asyncio.run(_run())

    # tiktok is not a live route: only the youtube thread was attempted, and
    # its failure is persisted as an explicit deep-read health entry.
    assert len(broker.threads) == 1
    assert result.external_calls == 1
    assert collected["c1:deep_health"][0]["platform"] == "youtube"
    assert collected["c1:deep_health"][0]["stage"] == "deep_read"


# ── Truthful, retrievable execution receipts ──────────────────


USABLE_ITEM = {
    "platform": "youtube",
    "external_id": "v1",
    "text": "I switched to this tool after the delays.",
    "url": "https://www.youtube.com/watch?v=v1",
    "engagement": {"likes": 5, "comments": 2},
    "author": {"id": "u1", "username": "user1"},
}


def _run_research_run(tmp_path, monkeypatch, broker, llm_fn):
    """Create a horizontal-analysis brief, execute it, return (client, run_id)."""
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(dashboard_api, "_broker", broker)
    monkeypatch.setattr(dashboard_api, "_llm_call", llm_fn)
    created = client.post("/dashboard/api/discovery/research-runs", json=_brief(
        candidates=[{
            "id": "one", "keyword": "topic one", "eligible": True,
            "platforms": ["youtube"],
        }],
    ))
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    store = dashboard_api._get_discovery_store()
    claim_token = store.claim_research_run(run_id)
    assert claim_token is not None
    receipt = asyncio.run(
        dashboard_api._execute_research_run_background(run_id, claim_token)
    )
    return client, run_id, receipt


def test_research_run_receipt_reports_zero_llm_calls_when_nothing_transmitted(
    tmp_path, monkeypatch,
):
    _stub_subreddit_discovery(monkeypatch)

    async def forbidden_llm(system, user):
        raise AssertionError("no prompt may be transmitted without usable evidence")

    broker = RecordingBroker(
        items=[dict(USABLE_ITEM, text="   ")], routes=["youtube"],
    )

    client, run_id, receipt = _run_research_run(
        tmp_path, monkeypatch, broker, forbidden_llm,
    )

    # Truthful usage: every stage row counts zero model calls.
    usage_rows = receipt["usage"]
    horizontal = next(r for r in usage_rows if r["stage"] == "horizontal_extraction")
    assert horizontal["llm_calls"] == 0
    assert horizontal["input_records"] == 0
    assert horizontal["input_characters"] == 0
    assert sum(row["llm_calls"] for row in usage_rows) == 0

    # The receipt is retrievable through the read API, unchanged.
    persisted = client.get(
        f"/dashboard/api/discovery/research-runs/{run_id}"
    ).json()
    assert persisted["status"] in {"complete", "partial"}
    assert persisted["result"]["usage"] == usage_rows
    assert persisted["result"]["findings_count"] == receipt["findings_count"]

    # Findings state the gap explicitly instead of inventing coverage.
    findings = client.get(
        f"/dashboard/api/discovery/research-runs/{run_id}/findings"
    ).json()["findings"]
    assert len(findings) == 1
    analysis = findings[0]["analysis"]
    assert analysis["status"] == "insufficient_evidence"
    assert "No usable conversation records were collected." in analysis["limitations"]


def test_research_run_receipt_counts_exactly_one_transmitted_call(
    tmp_path, monkeypatch,
):
    _stub_subreddit_discovery(monkeypatch)
    sent = []

    async def llm(system, user):
        sent.append((system, user))
        return json.dumps({
            "summary": "",
            "signals": [{
                "kind": "switching",
                "claim": "Users switched tools after delays.",
                "polarity": "negative",
                "evidence_ids": ["youtube:post:v1"],
            }],
            "entities": [],
            "limitations": [],
        })

    broker = RecordingBroker(items=[USABLE_ITEM], routes=["youtube"])

    client, run_id, receipt = _run_research_run(tmp_path, monkeypatch, broker, llm)

    assert len(sent) == 1
    usage_rows = receipt["usage"]
    horizontal = next(r for r in usage_rows if r["stage"] == "horizontal_extraction")
    assert horizontal["llm_calls"] == 1
    assert horizontal["input_records"] == 1
    assert horizontal["input_characters"] == len(sent[0][0]) + len(sent[0][1])
    assert horizontal["input_characters"] > 0
    # Only the horizontal stage may claim a model call.
    assert sum(row["llm_calls"] for row in usage_rows) == 1

    persisted = client.get(
        f"/dashboard/api/discovery/research-runs/{run_id}"
    ).json()
    assert persisted["status"] == "complete"
    assert persisted["result"]["usage"] == usage_rows

    findings = client.get(
        f"/dashboard/api/discovery/research-runs/{run_id}/findings"
    ).json()["findings"]
    assert findings[0]["analysis"]["status"] == "supported"
    assert findings[0]["analysis"]["signals"][0]["evidence_ids"] == ["youtube:post:v1"]
