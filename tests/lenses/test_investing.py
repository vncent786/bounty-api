import asyncio
import json

from social_scraper.lenses.investing import analyze_investing_lens


def conversation(relationship="mentioned"):
    return {
        "status": "supported",
        "signals": [{
            "kind": "adoption",
            "claim": "People report buying the product.",
            "evidence_ids": ["reddit:post:r1", "youtube:post:y1"],
            "independent_voices": 2,
        }],
        "entities": [{
            "name": "Nike",
            "type": "company",
            "relationship": relationship,
            "evidence_ids": ["reddit:post:r1"],
        }],
        "evidence": [
            {"id": "reddit:post:r1", "text": "I bought the Nike shoes."},
            {"id": "youtube:post:y1", "text": "I ordered them too."},
        ],
    }


def test_entity_matching_alone_cannot_establish_materiality():
    async def llm(_system, _user):
        return json.dumps({
            "companies": [{
                "company_name": "Nike",
                "ticker": "NKE",
                "mapping_confidence": 0.9,
                "mechanism": "unknown",
                "rationale": "Nike is named.",
                "evidence_ids": ["reddit:post:r1"],
            }],
            "materiality": "plausible",
            "signal_direction": "potential_long",
            "invalidating_evidence": [],
            "required_next_diligence": ["Check whether volume is meaningful."],
            "limitations": [],
        })

    result = asyncio.run(analyze_investing_lens(
        {"keyword": "nike shoes"}, conversation(), llm_call_fn=llm
    ))
    assert result.materiality == "not_established"
    assert result.signal_direction == "no_directional_inference"
    assert any("Entity matching" in item for item in result.limitations)


def test_supported_transmission_mechanism_remains_a_research_hypothesis():
    async def llm(_system, _user):
        return json.dumps({
            "companies": [{
                "company_name": "Nike",
                "ticker": "NKE",
                "mapping_confidence": 0.7,
                "mechanism": "revenue",
                "rationale": "Two cited buyers report purchases.",
                "evidence_ids": ["reddit:post:r1", "youtube:post:y1"],
            }],
            "materiality": "plausible",
            "signal_direction": "potential_long",
            "invalidating_evidence": ["The sample may be too small."],
            "required_next_diligence": ["Compare the observation with channel data."],
            "limitations": ["No denominator is available."],
        })

    result = asyncio.run(analyze_investing_lens(
        {"keyword": "nike shoes"}, conversation("used"), llm_call_fn=llm
    ))
    assert result.materiality == "plausible"
    assert result.signal_direction == "potential_long"
    assert result.companies[0]["mechanism"] == "revenue"
    assert result.required_next_diligence
