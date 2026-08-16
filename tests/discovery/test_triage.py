import asyncio
import json

import pytest

from social_scraper.discovery.triage import (
    analyze_conversation,
    prepare_conversation_prompt,
)


POSTS = [
    {
        "platform": "reddit",
        "post_id": "r1",
        "text": "I switched from Brand A because delivery now takes two weeks.",
        "url": "https://www.reddit.com/r/example/comments/r1",
        "author": {"external_id": "u1", "username": "one"},
    },
    {
        "platform": "youtube",
        "post_id": "y1",
        "text": "Brand A delivery delays made me cancel my order too.",
        "url": "https://www.youtube.com/watch?v=y1",
        "author": {"external_id": "u2", "username": "two"},
    },
]


def test_horizontal_analysis_is_cited_and_useful_to_multiple_lenses():
    async def llm(_system, _user):
        return json.dumps({
            "summary": "Customers describe abandoning Brand A after delivery delays.",
            "summary_evidence_ids": ["reddit:post:r1", "youtube:post:y1"],
            "signals": [{
                "kind": "switching",
                "claim": "Customers are switching away after delivery delays.",
                "polarity": "negative",
                "evidence_ids": ["reddit:post:r1", "youtube:post:y1"],
            }],
            "entities": [{
                "name": "Brand A",
                "type": "company",
                "relationship": "abandoned",
                "evidence_ids": ["reddit:post:r1", "youtube:post:y1"],
            }],
            "limitations": [],
        })

    result = asyncio.run(analyze_conversation(
        "brand a delays", POSTS,
        source_health=[
            {"platform": "reddit", "status": "success"},
            {"platform": "youtube", "status": "success"},
        ], llm_call_fn=llm,
    ))
    data = result.to_dict()
    assert data["status"] == "supported"
    assert data["summary_evidence_ids"] == [
        "reddit:post:r1", "youtube:post:y1"
    ]
    assert data["coverage"]["independent_voices"] == 2
    assert data["signals"][0]["kind"] == "switching"
    assert data["signals"][0]["independent_voices"] == 2
    assert data["signals"][0]["evidence_ids"] == [
        "reddit:post:r1", "youtube:post:y1"
    ]
    assert data["entities"][0]["relationship"] == "abandoned"
    assert data["behavior_type"] == "observed_action"
    assert data["direction"] == "negative"
    assert data["novelty"] == "unknown"
    assert len(data["durability_evidence"]) == 1
    assert data["representative_record_ids"] == [
        "reddit:post:r1", "youtube:post:y1"
    ]


def test_unknown_citations_are_rejected_not_silently_treated_as_evidence():
    async def llm(_system, _user):
        return json.dumps({
            "summary": "Unsupported summary.",
            "signals": [{
                "kind": "adoption",
                "claim": "Everyone is adopting it.",
                "polarity": "positive",
                "evidence_ids": ["reddit:post:made-up"],
            }],
            "entities": [],
            "limitations": [],
        })

    result = asyncio.run(analyze_conversation(
        "topic", POSTS[:1], source_health=[], llm_call_fn=llm
    ))
    assert result.signals == []
    assert any(
        item.startswith("LLM returned unknown evidence IDs")
        for item in result.limitations
    )


def test_claims_without_openable_source_urls_are_rejected():
    async def llm(_system, _user):
        return json.dumps({
            "summary": "",
            "summary_evidence_ids": [],
            "signals": [{
                "kind": "pain_point",
                "claim": "A cited but unopenable claim.",
                "polarity": "negative",
                "evidence_ids": ["reddit:post:r1"],
            }],
            "entities": [],
            "limitations": [],
        })

    result = asyncio.run(analyze_conversation(
        "topic", [dict(POSTS[0], url=None)], source_health=[], llm_call_fn=llm
    ))
    assert result.status == "insufficient_evidence"
    assert result.signals == []
    assert any("without an openable source URL" in item for item in result.limitations)


@pytest.mark.parametrize("url", [
    "https://exa mple.com/path",
    "https://localhost/private",
    "http://127.0.0.1/admin",
    "http://169.254.169.254/latest/meta-data",
    "https://user:password@example.com/private",
    "https://source.example/evidence",
])
def test_claims_with_non_public_or_malformed_urls_are_rejected(url):
    async def llm(_system, _user):
        return json.dumps({
            "summary": "Malformed-host claim",
            "summary_evidence_ids": ["reddit:post:r1"],
            "signals": [{
                "kind": "pain_point",
                "claim": "Malformed-host claim",
                "polarity": "negative",
                "evidence_ids": ["reddit:post:r1"],
            }],
            "entities": [],
            "limitations": [],
        })

    result = asyncio.run(analyze_conversation(
        "topic", [dict(POSTS[0], url=url)], source_health=[], llm_call_fn=llm
    ))
    assert result.status == "insufficient_evidence"
    assert result.summary == ""
    assert result.signals == []
    assert any("openable source URL" in item for item in result.limitations)


def test_source_failure_is_not_reported_as_no_conversation():
    result = asyncio.run(analyze_conversation(
        "topic",
        [],
        source_health=[
            {"platform": "reddit", "status": "error", "error_category": "timeout"}
        ],
    ))
    assert result.status == "sources_unavailable"
    assert result.coverage["source_status"][0]["status"] == "error"
    assert result.interpretation["conversation_state"] == "insufficient_evidence"
    assert result.interpretation["signal_counts"] == {}


def test_prepared_prompt_helper_matches_exactly_what_the_llm_receives():
    sent = []

    async def llm(system, user):
        sent.append((system, user))
        return json.dumps({
            "summary": "", "signals": [], "entities": [], "limitations": [],
        })

    # Noisy pool: exact duplicates, an unusable-text copy and a flood past the
    # five-per-platform cap, so a raw post count would overstate the input.
    noisy = (
        POSTS
        + POSTS
        + [dict(POSTS[0], text="   ")]
        + [
            {
                "platform": "reddit",
                "post_id": f"r{i}",
                "text": f"extra voice {i}",
                "author": {"external_id": f"u{i}", "username": str(i)},
            }
            for i in range(2, 8)
        ]
    )
    prepared = prepare_conversation_prompt("brand a delays", noisy)

    # 1 youtube record + 5 capped reddit records survive preparation.
    assert prepared.input_records == 6
    assert prepared.input_records < len(noisy)
    assert prepared.input_characters == (
        len(prepared.system_prompt) + len(prepared.user_prompt)
    )

    asyncio.run(analyze_conversation(
        "brand a delays", noisy, source_health=[], llm_call_fn=llm
    ))

    # The helper reports the exact prompt pair transmitted to the model.
    assert sent == [(prepared.system_prompt, prepared.user_prompt)]
    # Reproducible measurement: identical inputs rebuild the identical prompt.
    again = prepare_conversation_prompt("brand a delays", noisy)
    assert again == prepared


def test_prepared_prompt_records_zero_when_no_usable_evidence_survives():
    sent = []

    async def llm(system, user):
        sent.append((system, user))
        return json.dumps({
            "summary": "", "signals": [], "entities": [], "limitations": [],
        })

    posts = [{"platform": "reddit", "post_id": "r1", "text": "   "}]
    prepared = prepare_conversation_prompt("topic", posts)
    assert prepared.user_prompt == ""
    assert prepared.input_records == 0
    # No prompt is transmitted, so the receipt records zero characters
    # rather than the system prompt alone.
    assert prepared.input_characters == 0

    asyncio.run(analyze_conversation(
        "topic", posts, source_health=[], llm_call_fn=llm
    ))
    assert sent == []


def test_conversation_analysis_public_output_shape_includes_interpretation():
    async def llm(_system, _user):
        return json.dumps({
            "summary": "s", "signals": [], "entities": [], "limitations": [],
        })

    result = asyncio.run(analyze_conversation(
        "topic", POSTS[:1], source_health=[], llm_call_fn=llm
    ))
    assert set(result.to_dict()) == {
        "topic", "status", "behavior_type", "direction", "novelty",
        "durability_evidence", "independent_voice_count", "products",
        "representative_record_ids", "summary", "summary_evidence_ids",
        "signals", "entities", "interpretation",
        "evidence", "coverage", "limitations", "llm_error", "schema_version",
    }


def test_engagement_and_provenance_survive_prompt_and_public_evidence():
    sent = []
    provenance = {
        "connector": "youtube_free",
        "query": "topic",
        "engagement_sources": {"views": "view_count"},
    }
    engagement = {
        "views": 0,
        "likes": 12,
        "comments": None,
        "shares": 3,
        "collects": 4,
        "upvotes": 5,
        "replies": 6,
        "reposts": 7,
        "bookmarks": 8,
        "creator_followers": 900,
        "engagement_rate": 0.91,
    }
    posts = [
        {
            "platform": "youtube",
            "external_id": "v1",
            "url": "https://www.youtube.com/watch?v=v1&utm_source=kept",
            "text": "A source record with observed engagement.",
            "provenance": provenance,
            "engagement": engagement,
        },
        {
            "platform": "reddit",
            "external_id": "r2",
            "url": "https://www.reddit.com/r/example/comments/r2",
            "text": "A source record with one explicit metric.",
            "engagement": {"likes": 0},
        },
    ]

    async def llm(_system, user):
        sent.append(json.loads(user))
        return json.dumps({
            "summary": "", "summary_evidence_ids": [], "signals": [],
            "entities": [], "limitations": [],
        })

    prepared = prepare_conversation_prompt("topic", posts)
    result = asyncio.run(analyze_conversation(
        "topic", posts, source_health=[], llm_call_fn=llm,
    ))

    expected_engagement = {
        key: value for key, value in engagement.items()
        if key != "engagement_rate"
    }
    assert prepared.evidence[0]["engagement"] == expected_engagement
    assert prepared.evidence[1]["engagement"] == {"likes": 0}
    assert prepared.evidence[0]["url"] == posts[0]["url"]
    assert prepared.evidence[0]["provenance"] == provenance
    assert sent[0]["evidence_records"][0]["engagement"] == expected_engagement
    assert sent[0]["evidence_records"][0]["url"] == posts[0]["url"]
    assert sent[0]["evidence_records"][0]["provenance"] == provenance
    assert result.evidence[0]["engagement"] == expected_engagement
    assert result.evidence[0]["url"] == posts[0]["url"]
    assert result.evidence[0]["provenance"] == provenance


@pytest.mark.parametrize(("kinds", "expected_state"), [
    (["adoption", "adoption"], "observed_behavior"),
    (["purchase_trigger"], "purchase_or_desire"),
    (["rejection"], "negative_or_rejection"),
    (["question"], "general_discussion"),
    ([], "insufficient_evidence"),
])
def test_interpretation_is_deterministic_and_only_counts_cited_signals(
    kinds, expected_state,
):
    async def llm(_system, _user):
        return json.dumps({
            "summary": "",
            "summary_evidence_ids": [],
            "signals": [
                {
                    "kind": kind,
                    "claim": f"Cited {kind} claim {index}.",
                    "polarity": "negative" if kind == "question" else "neutral",
                    "evidence_ids": ["reddit:post:r1"],
                }
                for index, kind in enumerate(kinds)
            ] + [{
                "kind": "adoption",
                "claim": "This unknown citation must not affect interpretation.",
                "polarity": "positive",
                "evidence_ids": ["reddit:post:not-supplied"],
            }],
            "entities": [],
            "limitations": [],
        })

    result = asyncio.run(analyze_conversation(
        "topic", POSTS[:1], source_health=[], llm_call_fn=llm,
    ))

    expected_counts = {kind: kinds.count(kind) for kind in dict.fromkeys(kinds)}
    assert result.interpretation == {
        "conversation_state": expected_state,
        "signal_counts": expected_counts,
        "limitations": [
            "Brewing, turning, and conversation velocity cannot be concluded "
            "without comparable collection periods."
        ],
    }
    assert "score" not in result.interpretation
    assert "trend" not in result.interpretation
