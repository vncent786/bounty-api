import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apis import dashboard_api
from social_scraper.discovery import DiscoveryStore, ScanBudget
from social_scraper.discovery.prioritization import prioritize_candidates
from social_scraper.discovery.scheduler import DiscoveryScheduler, WorkspacePlanRequest
from social_scraper.discovery.staged_runner import StageHandlerResult, StagedRunner


def candidates():
    return [
        {"id": "c", "keyword": "C", "eligible": True, "search_volume": 1},
        {"id": "b", "keyword": "B", "eligible": True, "standing_read": True},
        {"id": "a", "keyword": "A", "eligible": True, "manual_promoted": True},
        {"id": "z", "keyword": "Z", "eligible": False},
    ]


def test_priority_and_hard_budget_preserve_all_candidates():
    ordered = prioritize_candidates(candidates(), metric_order=("search_volume",))
    assert [item["candidate_id"] for item in ordered[:2]] == ["a", "b"]
    budget = ScanBudget(root_probe_candidates=2, deep_read_candidates=1,
                        horizontal_llm_candidates=1, optional_enrichments=0)
    plan = DiscoveryScheduler().plan(candidates(), budget, required_depth="horizontal_analysis")
    assert len(plan["candidates"]) == 4
    assert plan["effective_budget"] == budget.to_dict()
    assert sum(c["stages"].get("root_probe") == "planned" for c in plan["candidates"]) == 2
    assert sum(c["stages"].get("deep_read") == "planned" for c in plan["candidates"]) == 1
    assert sum(c["stages"].get("horizontal_extraction") == "planned" for c in plan["candidates"]) == 1
    assert next(c for c in plan["candidates"] if c["candidate_id"] == "z")["outcome"] == "screened_out"
    assert any(c["outcome"] == "budget_exhausted" for c in plan["candidates"])


def test_candidate_and_root_depth_do_not_overrun_handlers():
    calls = []
    async def handler(candidate, context):
        calls.append((context["stage"], candidate["candidate_id"]))
        return StageHandlerResult(records_returned=1, candidates=[{"id": "extra"}])

    scheduler = DiscoveryScheduler()
    trends = scheduler.plan(candidates(), ScanBudget(), required_depth="candidate")
    result = asyncio.run(StagedRunner({"root_probe": handler}).run("trends", trends))
    assert calls == [] and result.usages == []

    root = scheduler.plan(candidates(), ScanBudget(root_probe_candidates=1,
        deep_read_candidates=10, horizontal_llm_candidates=10), required_depth="root_probe")
    result = asyncio.run(StagedRunner({"root_probe": handler, "deep_read": handler,
        "horizontal_extraction": handler}).run("root", root))
    assert calls == [("root_probe", "a")]
    assert sum(u.candidates_processed for u in result.usages) == 1
    assert all(u.stage == "root_probe" and u.llm_calls == 0 for u in result.usages)


def test_workspace_union_deduplicates_shared_collection():
    scheduler = DiscoveryScheduler()
    request = lambda workspace: WorkspacePlanRequest(workspace, [{"id": "same", "eligible": True}],
        ScanBudget(root_probe_candidates=1), "root_probe")
    plan = scheduler.plan_workspaces([request("one"), request("two")])
    assert len(plan["shared_work"]) == 1
    assert plan["shared_work"][0]["workspace_ids"] == ["one", "two"]


def test_store_history_and_api_plan_promote(tmp_path, monkeypatch):
    store = DiscoveryStore(tmp_path / "research.db")
    monkeypatch.setattr(dashboard_api, "_discovery_store", store)
    monkeypatch.delenv("BOUNTY_DASHBOARD_TOKEN", raising=False)
    app = FastAPI()
    app.include_router(dashboard_api.router)
    client = TestClient(app)
    response = client.post("/dashboard/api/discovery/research-runs", json={
        "workspace_id": "acme", "required_depth": "root_probe",
        "budget": {"root_probe_candidates": 1, "deep_read_candidates": 0,
                   "horizontal_llm_candidates": 0, "optional_enrichments": 0,
                   "threads_per_platform": 2, "comments_per_thread": 20,
                   "max_thread_depth": 2},
        "candidates": [{"id": "x", "keyword": "X", "eligible": True},
                       {"id": "y", "keyword": "Y", "eligible": True}],
    })
    assert response.status_code == 201
    run = response.json()
    assert run["requested_budget"] == run["effective_budget"]
    run_id = run["id"]
    assert client.get(f"/dashboard/api/discovery/research-runs/{run_id}").status_code == 200
    assert len(client.get(f"/dashboard/api/discovery/research-runs/{run_id}/candidates").json()["candidates"]) == 2
    promoted = client.post(
        f"/dashboard/api/discovery/research-runs/{run_id}/candidates/y/promote"
    )
    assert promoted.status_code == 200
    history = client.get(
        f"/dashboard/api/discovery/research-runs/{run_id}/candidates/y/history"
    ).json()["history"]
    assert history[-1]["outcome"] == "manual_promoted"
