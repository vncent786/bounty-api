import asyncio
import json

from social_scraper.discovery.triage import analyze_conversation


POSTS = [
    {
        "platform": "reddit",
        "post_id": "r1",
        "text": "I switched from Brand A because delivery now takes two weeks.",
        "url": "https://reddit.example/r1",
        "author": {"external_id": "u1", "username": "one"},
    },
    {
        "platform": "youtube",
        "post_id": "y1",
        "text": "Brand A delivery delays made me cancel my order too.",
        "url": "https://youtube.example/y1",
        "author": {"external_id": "u2", "username": "two"},
    },
]


def test_horizontal_analysis_is_cited_and_useful_to_multiple_lenses():
    async def llm(_system, _user):
        return json.dumps({
            "summary": "Customers describe abandoning Brand A after delivery delays.",
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
