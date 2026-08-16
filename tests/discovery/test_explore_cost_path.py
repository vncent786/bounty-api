"""Explore Trend feed cost-path tests (Phase 1 Task 1.2 behavior).

The Explore feed must stay cheap and explicit:

- ``trends_snapshot``: Trends metadata only. Zero broker searches, zero
  thread hydrations, zero LLM calls.
- ``root_sweep``: may search root social records (counts, engagement,
  source health) with ``max_threads=0``; never hydrates threads and
  never calls the LLM.
- Legacy flags (``apply_gate``) map to the lightweight modes; no legacy
  combination may silently trigger LLM analysis.
- Thread hydration and LLM analysis remain available only through
  explicit research-run stages (handlers.py).
- Gate outcomes stay distinct: complete / partial / empty / failed are
  never merged.
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from social_scraper.conversations.thread_reader import (
    ThreadFetchResult,
    ThreadRecord,
)
from social_scraper.discovery.scan_modes import ScanMode
from social_scraper.monitoring.conversation_gate import gate_check_keyword
from social_scraper.monitoring.topdown import EmergingKeyword, TopDownDiscovery

GATE_PLATFORMS = ["youtube", "reddit", "tiktok"]
# One keyword per distinct gate outcome.
COMPLETE_KEYWORD = "alpha gizmo"    # youtube root, all platforms healthy
PARTIAL_KEYWORD = "beta widget"     # youtube root, one platform errored
EMPTY_KEYWORD = "gamma nothing"     # healthy platforms, zero items
FAILED_KEYWORD = "delta dead"       # every platform errored
ALL_KEYWORDS = [COMPLETE_KEYWORD, PARTIAL_KEYWORD, EMPTY_KEYWORD, FAILED_KEYWORD]

# Legacy names of the readable candidates (kept from the Phase 0
# characterization so the flip is directly comparable).
READABLE_KEYWORDS = [COMPLETE_KEYWORD, PARTIAL_KEYWORD]


class CountingBroker:
    """Deterministic broker that records every external call it receives.

    ``alpha gizmo``/``beta widget`` return one YouTube root per platform
    probe; ``gamma nothing`` returns nothing on healthy platforms; ``delta
    dead`` reports every platform as errored.
    """

    def __init__(self):
        self.search_calls: list[tuple[str, tuple[str, ...], int]] = []
        self.fetch_thread_calls: list[str] = []

    async def search(self, keyword, platforms=None, count=10):
        self.search_calls.append((keyword, tuple(platforms or []), count))
        platform = (platforms or ["youtube"])[0]
        items = []
        statuses = {}
        for name in (platforms or ["youtube"]):
            if keyword == FAILED_KEYWORD:
                statuses[name] = {"status": "error", "error_category": "probe"}
            elif keyword == PARTIAL_KEYWORD and name == "reddit":
                statuses[name] = {"status": "error", "error_category": "probe"}
            else:
                statuses[name] = {"status": "complete"}
        if (
            keyword in READABLE_KEYWORDS
            and platform == "youtube"
            and statuses.get("youtube", {}).get("status") == "complete"
        ):
            stem = keyword.split()[0]
            items.append({
                "platform": "youtube",
                "external_id": f"yt-{stem}",
                "title": f"{keyword} explained",
                "text": f"People are discussing {keyword}",
                "url": f"https://www.youtube.com/watch?v={stem}",
                "engagement": {"likes": 150, "comments": 40},
            })
        return {
            "items": items,
            "source_health": [
                {"platform": name, "status": value["status"]}
                for name, value in statuses.items()
            ],
            "platform_results": statuses,
        }

    async def fetch_thread(self, item, max_comments=20, max_depth=2):
        root_id = item["external_id"]
        self.fetch_thread_calls.append(root_id)
        return ThreadFetchResult(
            platform="youtube",
            root_post_external_id=root_id,
            status="complete",
            records=(ThreadRecord(
                platform="youtube",
                external_id=f"{root_id}-c1",
                record_type="comment",
                parent_external_id=root_id,
                root_post_external_id=root_id,
                depth=1,
                text=f"Comment on {root_id}",
                author_username=f"author-{root_id}",
                url=f"{item['url']}#comment",
            ),),
            attempted_route="fake_thread_reader",
            max_comments=max_comments,
            max_depth=max_depth,
        )


def _candidates() -> list[EmergingKeyword]:
    return [
        EmergingKeyword(
            keyword=keyword,
            source="google_trends",
            growth_pct=100.0,
            started_hours_ago=1.0,
            search_volume=500,
        )
        for keyword in ALL_KEYWORDS
    ]


def _install_llm_counter(monkeypatch) -> list[str]:
    prompts: list[str] = []

    async def counting_call_llm(system_prompt, user_prompt, **_kwargs):
        prompts.append(user_prompt)
        return json.dumps({
            "summary": "Mocked horizontal synthesis.",
            "signals": [],
            "entities": [],
            "limitations": [],
        })

    import social_scraper.llm_client as llm_client

    monkeypatch.setattr(llm_client, "call_llm", counting_call_llm)
    return prompts


def _make_discovery(monkeypatch) -> tuple[TopDownDiscovery, CountingBroker]:
    broker = CountingBroker()
    discovery = TopDownDiscovery(broker=broker, discovery_store=None)

    async def fake_fetch_candidates(geo="US"):
        return _candidates()

    monkeypatch.setattr(discovery, "fetch_candidates", fake_fetch_candidates)
    return discovery, broker


# ── trends_snapshot: the Trend feed is free ───────────────────


def test_scan_all_trends_snapshot_makes_zero_broker_thread_and_llm_calls(
    monkeypatch,
):
    discovery, broker = _make_discovery(monkeypatch)
    prompts = _install_llm_counter(monkeypatch)

    results = asyncio.run(discovery.scan_all(
        geo="US",
        mode=ScanMode.TRENDS_SNAPSHOT,
        gate_max=len(ALL_KEYWORDS),
        gate_platforms=GATE_PLATFORMS,
    ))

    assert broker.search_calls == []
    assert broker.fetch_thread_calls == []
    assert prompts == []

    by_keyword = {result.keyword: result for result in results}
    assert set(by_keyword) == set(ALL_KEYWORDS)
    for keyword in ALL_KEYWORDS:
        candidate = by_keyword[keyword]
        assert candidate.gate_status == "not_checked"
        assert candidate.gate_passed is None
        assert candidate.conversation_analysis == {}
        assert candidate.conv_summary == ""
        # Trends metadata survives untouched.
        assert candidate.search_volume == 500
        assert candidate.growth_pct == 100.0
        assert candidate.source == "google_trends"


# ── root_sweep: root evidence only ────────────────────────────


def test_scan_all_root_sweep_searches_roots_with_zero_threads_and_llm(
    monkeypatch,
):
    discovery, broker = _make_discovery(monkeypatch)
    prompts = _install_llm_counter(monkeypatch)

    results = asyncio.run(discovery.scan_all(
        geo="US",
        mode="root_sweep",
        gate_max=len(ALL_KEYWORDS),
        gate_platforms=GATE_PLATFORMS,
    ))

    # Root search happened for every keyword on every gate platform...
    assert len(broker.search_calls) == len(ALL_KEYWORDS) * len(GATE_PLATFORMS)
    assert {call[0] for call in broker.search_calls} == set(ALL_KEYWORDS)
    # ...but nothing else did.
    assert broker.fetch_thread_calls == []
    assert prompts == []

    by_keyword = {result.keyword: result for result in results}

    complete = by_keyword[COMPLETE_KEYWORD]
    assert complete.gate_status == "complete"
    assert complete.gate_passed is True
    assert complete.gate_platforms == "youtube"
    assert complete.gate_total_engagement == 190
    assert complete.gate_total_items == 1
    assert "People are discussing" in complete.gate_sample
    assert complete.gate_source_health  # source health preserved
    # Root evidence was collected, but analysis never ran.
    assert complete.conversation_analysis == {}
    assert complete.conv_summary == ""

    # A candidate with readable roots on a partly failed platform keeps the
    # partial state instead of silently being analyzed or marked empty.
    partial = by_keyword[PARTIAL_KEYWORD]
    assert partial.gate_status == "partial"
    assert partial.gate_passed is None
    assert partial.conversation_analysis == {}

    # Empty and failed stay distinct from each other and from partial.
    assert by_keyword[EMPTY_KEYWORD].gate_status == "empty"
    assert by_keyword[EMPTY_KEYWORD].gate_passed is False
    assert by_keyword[FAILED_KEYWORD].gate_status == "failed"
    assert by_keyword[FAILED_KEYWORD].gate_passed is None


def test_scan_all_root_sweep_persists_distinct_gate_records(monkeypatch, tmp_path):
    from social_scraper.discovery import DiscoveryStore

    store = DiscoveryStore(tmp_path / "gate.db")
    broker = CountingBroker()
    discovery = TopDownDiscovery(broker=broker, discovery_store=store)

    async def fake_fetch_candidates(geo="US"):
        candidates = _candidates()
        # Give each candidate a persisted observation to attach checks to.
        discovery.last_run_id = store.record_feed(
            geo=geo,
            observed_at=candidates[0].discovered_at or "2026-08-15T00:00:00+00:00",
            candidates=[{
                "keyword": item.keyword,
                "related_terms": [],
                "search_volume": item.search_volume,
                "growth_pct": item.growth_pct,
                "source_started_at": None,
                "topic_ids": [],
                "categories": "",
                "source_record_count": 1,
                "source_observations": [],
                "metric_conflicts": [],
            } for item in candidates],
        )
        persisted = {
            row["normalized_keyword"]: row
            for row in store.list_run_candidates(discovery.last_run_id)
        }
        for item in candidates:
            row = persisted.get(" ".join(item.keyword.casefold().split()))
            if row:
                item.discovery_run_id = discovery.last_run_id
                item.candidate_observation_id = row["observation_id"]
        return candidates

    monkeypatch.setattr(discovery, "fetch_candidates", fake_fetch_candidates)
    _install_llm_counter(monkeypatch)

    results = asyncio.run(discovery.scan_all(
        geo="US", mode="root_sweep",
        gate_max=len(ALL_KEYWORDS), gate_platforms=GATE_PLATFORMS,
    ))
    assert len(results) == len(ALL_KEYWORDS)

    statuses = []
    for result in results:
        assert result.candidate_observation_id is not None
        checks = store.list_gate_checks(result.candidate_observation_id)
        assert len(checks) == 1
        check = checks[0]
        assert check["status"] == result.gate_status
        assert check["passed"] == result.gate_passed
        # No analysis is produced or stored on this path.
        assert check["analysis"] is None
        statuses.append(check["status"])
    assert sorted(statuses) == ["complete", "empty", "failed", "partial"]


# ── Legacy flags can no longer trigger the expensive path ─────


def test_scan_all_legacy_apply_gate_maps_to_root_sweep_without_llm(monkeypatch):
    discovery, broker = _make_discovery(monkeypatch)
    prompts = _install_llm_counter(monkeypatch)

    results = asyncio.run(discovery.scan_all(
        geo="US",
        apply_gate=True,  # legacy flag, pre-Phase-1 spelling
        gate_max=len(ALL_KEYWORDS),
        gate_platforms=GATE_PLATFORMS,
    ))

    assert len(broker.search_calls) > 0
    assert broker.fetch_thread_calls == []
    assert prompts == []
    assert all(r.conversation_analysis == {} for r in results)


def test_scan_all_defaults_to_trends_snapshot(monkeypatch):
    discovery, broker = _make_discovery(monkeypatch)
    prompts = _install_llm_counter(monkeypatch)

    asyncio.run(discovery.scan_all(geo="US"))

    assert broker.search_calls == []
    assert broker.fetch_thread_calls == []
    assert prompts == []


def test_scan_all_rejects_research_run_modes(monkeypatch):
    discovery, _broker = _make_discovery(monkeypatch)
    for mode in ("deep_read", "horizontal_synthesis", "optional_interpretation"):
        with pytest.raises(ValueError, match="research"):
            asyncio.run(discovery.scan_all(geo="US", mode=mode))


def test_scan_all_rejects_gate_only_without_conversation_check(monkeypatch):
    discovery, _broker = _make_discovery(monkeypatch)
    with pytest.raises(ValueError, match="gate_only"):
        asyncio.run(discovery.scan_all(geo="US", mode="trends_snapshot", gate_only=True))


# ── Dashboard API: the Trend feed calls the explicit modes ────


def _api_client(monkeypatch, discovery) -> TestClient:
    import apis.dashboard_api as dashboard_api

    monkeypatch.setattr(dashboard_api, "_discovery", discovery)
    monkeypatch.setenv("BOUNTY_ENV", "test")
    app = FastAPI()
    app.include_router(dashboard_api.router)
    return TestClient(app)


def test_discover_api_defaults_to_trends_snapshot_with_zero_cost(monkeypatch):
    discovery, broker = _make_discovery(monkeypatch)
    prompts = _install_llm_counter(monkeypatch)
    client = _api_client(monkeypatch, discovery)

    response = client.get("/dashboard/api/discover?geo=US")

    assert response.status_code == 200
    assert response.json()["total"] == len(ALL_KEYWORDS)
    assert broker.search_calls == []
    assert broker.fetch_thread_calls == []
    assert prompts == []


def test_discover_api_explicit_root_sweep_stays_thread_and_llm_free(monkeypatch):
    discovery, broker = _make_discovery(monkeypatch)
    prompts = _install_llm_counter(monkeypatch)
    client = _api_client(monkeypatch, discovery)

    response = client.get("/dashboard/api/discover?geo=US&mode=root_sweep")

    assert response.status_code == 200
    assert len(broker.search_calls) > 0
    assert broker.fetch_thread_calls == []
    assert prompts == []


def test_discover_api_legacy_gate_flag_maps_to_root_sweep_not_llm(monkeypatch):
    discovery, broker = _make_discovery(monkeypatch)
    prompts = _install_llm_counter(monkeypatch)
    client = _api_client(monkeypatch, discovery)

    response = client.get("/dashboard/api/discover?geo=US&gate=true")

    assert response.status_code == 200
    assert len(broker.search_calls) > 0
    assert broker.fetch_thread_calls == []
    assert prompts == []


def test_discover_api_rejects_heavy_and_unknown_modes(monkeypatch):
    discovery, _broker = _make_discovery(monkeypatch)
    client = _api_client(monkeypatch, discovery)

    for mode in ("deep_read", "horizontal_synthesis", "optional_interpretation"):
        response = client.get(f"/dashboard/api/discover?geo=US&mode={mode}")
        assert response.status_code == 422
        assert "research" in response.json()["detail"]

    response = client.get("/dashboard/api/discover?geo=US&mode=bananas")
    assert response.status_code == 422
    assert "bananas" in response.json()["detail"]


def test_discover_api_rejects_gate_only_on_snapshot(monkeypatch):
    discovery, _broker = _make_discovery(monkeypatch)
    client = _api_client(monkeypatch, discovery)

    response = client.get(
        "/dashboard/api/discover?geo=US&mode=trends_snapshot&gate_only=true"
    )
    assert response.status_code == 422
    assert "gate_only" in response.json()["detail"]


# ── Explicit research-runs keep the deep behavior ─────────────


def test_explicit_research_run_still_hydrates_threads_and_analyzes():
    from unittest.mock import AsyncMock, MagicMock

    from social_scraper.discovery.handlers import build_handlers
    from social_scraper.discovery.staged_runner import StagedRunner

    async def _run():
        broker = MagicMock()
        broker.search = AsyncMock(return_value={
            "items": [{
                "platform": "youtube",
                "external_id": "yt-alpha",
                "title": "alpha gizmo explained",
                "text": "People are discussing alpha gizmo",
                "url": "https://www.youtube.com/watch?v=alpha",
                "engagement": {"likes": 150, "comments": 40},
            }],
            "source_health": [{"platform": "youtube", "status": "complete"}],
            "platform_results": {"youtube": {"status": "complete"}},
        })

        async def fake_fetch_thread(item, max_comments=20, max_depth=2):
            root_id = item["external_id"]
            return ThreadFetchResult(
                platform="youtube",
                root_post_external_id=root_id,
                status="complete",
                records=(ThreadRecord(
                    platform="youtube",
                    external_id=f"{root_id}-c1",
                    record_type="comment",
                    parent_external_id=root_id,
                    root_post_external_id=root_id,
                    depth=1,
                    text="Comment on alpha gizmo",
                    author_username=f"author-{root_id}",
                    url=f"{item['url']}#comment",
                ),),
                attempted_route="fake_thread_reader",
                max_comments=max_comments,
                max_depth=max_depth,
            )

        broker.fetch_thread = AsyncMock(side_effect=fake_fetch_thread)

        prompts: list[str] = []

        async def counting_llm(system_prompt, user_prompt):
            prompts.append(user_prompt)
            return json.dumps({
                "summary": "Mocked horizontal synthesis.",
                "signals": [{
                    "kind": "desire",
                    "claim": "People want gizmos",
                    "polarity": "positive",
                    "evidence_ids": ["youtube:comment:yt-alpha-c1"],
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
                "candidate": {"keyword": COMPLETE_KEYWORD},
                "stages": {
                    "root_probe": "planned",
                    "deep_read": "planned",
                    "horizontal_extraction": "planned",
                },
            }],
        }

        handlers, collected = build_handlers(broker, plan, llm_call_fn=counting_llm)
        result = await StagedRunner(handlers).run("research-run-1", plan)

        # The explicit research run still deep-reads and analyzes.
        assert broker.fetch_thread.await_count == 1
        assert len(prompts) == 1
        horizontal = result.handler_results["horizontal_extraction"]["c1"]
        assert horizontal.llm_calls == 1
        assert collected["c1:findings"]["status"] == "supported"

    asyncio.run(_run())


# ── Direct gate path tests ────────────────────────────────────────────


def test_gate_check_keyword_with_zero_threads_stays_thread_free(monkeypatch):
    broker = CountingBroker()
    result = asyncio.run(gate_check_keyword(
        broker, COMPLETE_KEYWORD,
        platforms=["youtube"],
        max_threads_per_platform=0,
    ))
    assert result is not None
    assert broker.fetch_thread_calls == []


def test_scan_all_legacy_positional_order_makes_one_root_search(monkeypatch):
    discovery, broker = _make_discovery(monkeypatch)
    prompts = _install_llm_counter(monkeypatch)
    # Legacy positional: geo, apply_gate, gate_max, gate_platforms
    results = asyncio.run(discovery.scan_all("US", True, 1, ["youtube"]))
    assert len(broker.search_calls) == 1
    assert broker.fetch_thread_calls == []
    assert prompts == []


def test_apply_conversation_gate_without_max_threads_stays_thread_free(monkeypatch):
    discovery, broker = _make_discovery(monkeypatch)
    prompts = _install_llm_counter(monkeypatch)
    candidates = _candidates()
    results = asyncio.run(discovery.apply_conversation_gate(
        candidates, max_keywords=1, platforms=["youtube"],
    ))
    assert len(broker.search_calls) > 0
    assert broker.fetch_thread_calls == []
    assert prompts == []


# ── Source-native categories (trendspy 0.1.6) ─────────────────


def test_topic_categories_use_trendspy_source_meanings():
    from social_scraper.monitoring.topdown import (
        TOPIC_CATEGORIES,
        _topic_ids_to_categories,
    )

    # Corrected meanings from trendspy 0.1.6 TREND_TOPICS (Google's
    # Trending Now payload topic IDs). The empirically-guessed map had
    # reassigned meanings for these IDs.
    assert TOPIC_CATEGORIES[6] == "Games"
    assert TOPIC_CATEGORIES[18] == "Technology"
    assert TOPIC_CATEGORIES[16] == "Shopping"
    assert TOPIC_CATEGORIES[19] == "Travel & Transportation"
    assert TOPIC_CATEGORIES[20] == "Climate"
    assert TOPIC_CATEGORIES[10] == "Law & Government"
    assert TOPIC_CATEGORIES[11] == "Other"
    assert TOPIC_CATEGORIES[14] == "Politics"
    assert TOPIC_CATEGORIES[9] == "Jobs & Education"
    assert TOPIC_CATEGORIES[8] == "Hobbies & Leisure"
    # Retired wrong meanings must not survive anywhere in the map.
    assert set(TOPIC_CATEGORIES.values()).isdisjoint({
        "Autos",
        "Gaming & Tech",
        "Hobbies & Pets",
        "Education",
        "Society & Culture",
        "News & Current Events",
        "Politics & Government",
        "Travel",
        "Consumer Products",
        "Weather & Nature",
    })
    assert _topic_ids_to_categories([6, 18]) == "Games, Technology"
    assert _topic_ids_to_categories([]) == "Other"


def test_topic_categories_agree_with_installed_trendspy_ids():
    pytest.importorskip("trendspy")
    from trendspy.constants import TREND_TOPICS
    from social_scraper.monitoring.topdown import TOPIC_CATEGORIES

    def canon(name: str) -> str:
        return name.casefold().replace(" and ", " & ")

    assert set(TOPIC_CATEGORIES) == set(TREND_TOPICS)
    for tid, source_name in TREND_TOPICS.items():
        assert canon(TOPIC_CATEGORIES[tid]) == canon(source_name)


# ── Verified Trending Now country allowlist ────────────────────


def test_trending_now_countries_is_a_versioned_verified_allowlist():
    from social_scraper.monitoring.topdown import (
        TRENDING_NOW_COUNTRIES,
        TRENDING_NOW_COUNTRIES_VERSION,
    )

    assert TRENDING_NOW_COUNTRIES_VERSION
    assert len(TRENDING_NOW_COUNTRIES) == 125
    countries = dict(TRENDING_NOW_COUNTRIES)
    for code in ("US", "GB", "SG"):
        assert countries[code]
    for absent in ("UK", "CN", "GLOBAL"):
        assert absent not in countries
    assert countries["US"] == "United States"
    assert countries["GB"] == "United Kingdom"
    assert countries["SG"] == "Singapore"


def test_discover_options_lists_countries_categories_defaults_and_window(monkeypatch):
    discovery, _broker = _make_discovery(monkeypatch)
    client = _api_client(monkeypatch, discovery)

    response = client.get("/dashboard/api/discover/options")

    assert response.status_code == 200
    body = response.json()
    assert body["source_window_hours"] == 24
    codes = {c["code"]: c["name"] for c in body["countries"]}
    assert len(codes) == 125
    assert {"US", "GB", "SG"} <= set(codes)
    assert not ({"UK", "CN", "GLOBAL"} & set(codes))
    assert codes["SG"] == "Singapore"
    names = [c["name"] for c in body["countries"]]
    assert names == sorted(names)  # full-name dropdown order
    categories = {c["name"]: c["id"] for c in body["categories"]}
    assert categories["Games"] == 6
    assert categories["Technology"] == 18
    assert categories["Shopping"] == 16
    assert categories["Travel & Transportation"] == 19
    assert "Sports" in categories  # no hardcoded Sports exclusion
    defaults = body["defaults"]
    assert defaults["geo"] == "US"
    assert defaults["categories"] == []
    assert defaults["mode"] == "trends_snapshot"


# ── Request validation happens before any collection ───────────


class SpyDiscovery:
    """Records scan_all calls so tests can prove no collection happened."""

    def __init__(self):
        self.calls: list[dict] = []
        self.last_run_id = ""

    async def scan_all(self, **kwargs):
        self.calls.append(kwargs)
        return []


def test_discover_api_rejects_unsupported_country_before_collection(monkeypatch):
    spy = SpyDiscovery()
    client = _api_client(monkeypatch, spy)

    for geo in ("UK", "XX", "GLOBAL", "US-NY"):
        response = client.get("/dashboard/api/discover", params={"geo": geo})
        assert response.status_code == 422
        assert "options" in response.json()["detail"]

    assert spy.calls == []


def test_discover_api_normalizes_country_case_before_collection(monkeypatch):
    spy = SpyDiscovery()
    client = _api_client(monkeypatch, spy)

    response = client.get("/dashboard/api/discover", params={"geo": "sg"})

    assert response.status_code == 200
    assert spy.calls[0]["geo"] == "SG"


def test_discover_api_rejects_unknown_categories_before_collection(monkeypatch):
    spy = SpyDiscovery()
    client = _api_client(monkeypatch, spy)

    response = client.get(
        "/dashboard/api/discover",
        params={"geo": "US", "categories": "Gaming & Tech"},  # retired wrong name
    )

    assert response.status_code == 422
    assert "Gaming & Tech" in response.json()["detail"]
    assert spy.calls == []


def test_discover_api_normalizes_category_spellings_before_collection(monkeypatch):
    spy = SpyDiscovery()
    client = _api_client(monkeypatch, spy)

    response = client.get(
        "/dashboard/api/discover",
        params={"geo": "US", "categories": "games, Business and Finance, GAMES"},
    )

    assert response.status_code == 200
    assert spy.calls[0]["categories"] == ["Games", "Business & Finance"]


def test_discover_api_rejects_negative_filters_before_collection(monkeypatch):
    spy = SpyDiscovery()
    client = _api_client(monkeypatch, spy)

    for query in (
        {"min_volume": -1},
        {"min_growth": -5},
        {"max_age_hours": -0.5},
    ):
        response = client.get(
            "/dashboard/api/discover", params={"geo": "US", **query}
        )
        assert response.status_code == 422
        assert response.json()["detail"]

    assert spy.calls == []


# ── Deterministic category-agnostic round-robin selection ──────


def _categorized(keyword: str, categories: str) -> EmergingKeyword:
    return EmergingKeyword(
        keyword=keyword,
        source="google_trends",
        growth_pct=100.0,
        started_hours_ago=1.0,
        search_volume=500,
        categories=categories,
    )


def test_diversified_candidates_is_deterministic_and_category_agnostic():
    from social_scraper.monitoring.topdown import diversified_candidates

    items = (
        [_categorized(f"sports trend {i}", "Sports") for i in range(10)]
        + [_categorized(f"health trend {i}", "Health") for i in range(3)]
    )

    picked = diversified_candidates(items, 5)
    assert [k.keyword for k in picked] == [
        "health trend 0",
        "sports trend 0",
        "health trend 1",
        "sports trend 1",
        "health trend 2",
    ]
    # Same input, same output — deterministic.
    again = diversified_candidates(list(items), 5)
    assert [k.keyword for k in again] == [k.keyword for k in picked]
    # Every candidate survives when the limit exceeds supply.
    assert len(diversified_candidates(items, 999)) == 13
    # Sports is never excluded by rule: a sports-only list still selects.
    sports_only = diversified_candidates(items[:10], 2)
    assert [k.keyword for k in sports_only] == ["sports trend 0", "sports trend 1"]
    # A zero limit selects nothing.
    assert diversified_candidates(items, 0) == []


def test_root_sweep_gate_checks_round_robin_across_categories(monkeypatch):
    discovery, broker = _make_discovery(monkeypatch)
    candidates = (
        [_categorized(f"sports trend {i}", "Sports") for i in range(12)]
        + [_categorized(f"health trend {i}", "Health") for i in range(3)]
    )

    async def fake_fetch(geo="US"):
        return list(candidates)

    monkeypatch.setattr(discovery, "fetch_candidates", fake_fetch)
    _install_llm_counter(monkeypatch)

    results = asyncio.run(discovery.scan_all(
        geo="US", mode="root_sweep", gate_max=4, gate_platforms=["youtube"],
    ))

    searched = {call[0] for call in broker.search_calls}
    assert len(searched) == 4
    # The minority category still gets checked despite Sports dominating.
    assert sum(1 for k in searched if k.startswith("health")) >= 1
    # Unchecked candidates are preserved untouched.
    assert len(results) == 15
    assert sum(1 for r in results if r.gate_status == "not_checked") == 11


def test_discover_api_cap_uses_round_robin_and_category_filters_first(monkeypatch):
    discovery, _broker = _make_discovery(monkeypatch)
    candidates = (
        [_categorized(f"health trend {i:02d}", "Health") for i in range(40)]
        + [_categorized(f"sports trend {i:02d}", "Sports") for i in range(40)]
    )

    async def fake_fetch(geo="US"):
        return list(candidates)

    monkeypatch.setattr(discovery, "fetch_candidates", fake_fetch)
    client = _api_client(monkeypatch, discovery)

    # An explicit category filters server-side BEFORE the response cap.
    filtered = client.get(
        "/dashboard/api/discover", params={"geo": "US", "categories": "Health"}
    ).json()
    assert filtered["total"] == 40
    assert len(filtered["keywords"]) == 40
    assert all(k["categories"] == "Health" for k in filtered["keywords"])

    # Default balanced scan: cap 50, diversified, Sports never excluded.
    broad = client.get("/dashboard/api/discover", params={"geo": "US"}).json()
    assert broad["total"] == 80
    assert len(broad["keywords"]) == 50
    first_six = [k["categories"] for k in broad["keywords"][:6]]
    assert first_six == ["Health", "Sports", "Health", "Sports", "Health", "Sports"]
    assert {k["categories"] for k in broad["keywords"]} == {"Health", "Sports"}


def test_discover_api_root_sweep_gate_only_false_preserves_all_candidates(monkeypatch):
    discovery, _broker = _make_discovery(monkeypatch)
    client = _api_client(monkeypatch, discovery)

    response = client.get(
        "/dashboard/api/discover",
        params={"geo": "US", "mode": "root_sweep", "gate_only": "false"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == len(ALL_KEYWORDS)
    statuses = {k["keyword"]: k["gate_status"] for k in body["keywords"]}
    assert statuses == {
        COMPLETE_KEYWORD: "complete",
        PARTIAL_KEYWORD: "partial",
        EMPTY_KEYWORD: "empty",
        FAILED_KEYWORD: "failed",
    }
