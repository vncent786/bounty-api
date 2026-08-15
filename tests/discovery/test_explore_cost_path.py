"""Characterization tests for the current accidental Explore cost path.

Freezes the baseline that Phase 1 Task 1.2 intentionally replaces:
``TopDownDiscovery.scan_all(apply_gate=True)`` hydrates comment threads via
``broker.fetch_thread`` and invokes the LLM once per readable candidate,
before the user has selected anything.

These assertions document current behavior only. They are not a product
specification; when Phase 1 lands, the expectations flip to zero thread and
LLM calls on this path.
"""

import asyncio
import json

from social_scraper.conversations.thread_reader import ThreadFetchResult, ThreadRecord
from social_scraper.monitoring.topdown import EmergingKeyword, TopDownDiscovery

GATE_PLATFORMS = ["youtube", "reddit", "tiktok"]
READABLE_KEYWORDS = ["alpha gizmo", "beta widget"]
EMPTY_KEYWORD = "gamma nothing"


class FakeBroker:
    """Deterministic broker: roots vary by keyword and only YouTube carries them.

    ``alpha gizmo`` and ``beta widget`` each return one YouTube root (readable
    candidates); every other keyword and platform returns nothing, so
    ``gamma nothing`` is deterministically empty.
    """

    def __init__(self):
        self.fetch_thread_calls: list[str] = []

    async def search(self, keyword, platforms=None, count=10):
        items = []
        if "youtube" in (platforms or []) and keyword in READABLE_KEYWORDS:
            stem = keyword.split()[0]
            items.append({
                "platform": "youtube",
                "external_id": f"yt-{stem}",
                "title": f"{keyword} explained",
                "text": f"People are discussing {keyword}",
                "url": f"https://youtube.example/{stem}",
                "engagement": {"likes": 150, "comments": 40},
            })
        return {
            "items": items,
            "source_health": [],
            "platform_results": {p: {"status": "complete"} for p in (platforms or [])},
        }

    async def fetch_thread(self, item, max_comments=20, max_depth=2):
        # Real ThreadFetchResult/ThreadRecord contract, as consumed by
        # conversation_gate._check_platform.
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


def _candidates():
    return [
        EmergingKeyword(
            keyword=keyword,
            source="google_trends",
            growth_pct=100.0,
            started_hours_ago=1.0,
        )
        for keyword in READABLE_KEYWORDS + [EMPTY_KEYWORD]
    ]


def test_scan_all_with_gate_hydrates_threads_and_calls_llm_per_readable_candidate(
    monkeypatch,
):
    broker = FakeBroker()
    discovery = TopDownDiscovery(broker=broker, discovery_store=None)

    async def fake_fetch_candidates(geo="US"):
        return _candidates()

    monkeypatch.setattr(discovery, "fetch_candidates", fake_fetch_candidates)

    llm_prompts: list[str] = []

    async def counting_call_llm(system_prompt, user_prompt, **_kwargs):
        llm_prompts.append(user_prompt)
        return json.dumps({
            "summary": "Mocked horizontal synthesis.",
            "signals": [],
            "entities": [],
            "limitations": [],
        })

    import social_scraper.llm_client as llm_client

    monkeypatch.setattr(llm_client, "call_llm", counting_call_llm)

    results = asyncio.run(discovery.scan_all(
        geo="US",
        apply_gate=True,
        gate_max=3,
        gate_platforms=GATE_PLATFORMS,
    ))
    by_keyword = {result.keyword: result for result in results}

    # CHARACTERIZATION: the Trend scan path hydrates one thread per YouTube
    # root before any user selection. Phase 1 removes this.
    assert sorted(broker.fetch_thread_calls) == ["yt-alpha", "yt-beta"]

    # CHARACTERIZATION: exactly one LLM invocation per readable candidate,
    # and the prompt topic matches the candidate that triggered it.
    assert len(llm_prompts) == 2
    assert {json.loads(prompt)["topic"] for prompt in llm_prompts} == set(
        READABLE_KEYWORDS
    )
    for keyword in READABLE_KEYWORDS:
        assert by_keyword[keyword].gate_passed is True
        assert (
            by_keyword[keyword].conversation_analysis.get("summary")
            == "Mocked horizontal synthesis."
        )

    # CHARACTERIZATION: the candidate with no social roots is checked but
    # never reaches thread hydration or the LLM.
    empty = by_keyword[EMPTY_KEYWORD]
    assert empty.gate_status == "empty"
    assert empty.gate_passed is False
    assert empty.conversation_analysis == {}
