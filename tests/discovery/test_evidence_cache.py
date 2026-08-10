import asyncio
import sqlite3

from social_scraper.discovery.evidence_cache import (
    CachedHorizontalAnalyzer,
    build_evidence_bundle,
)
from social_scraper.discovery.storage import DiscoveryStore


RECORDS = [
    {
        "identity_key": "canonical-reddit-r1",
        "platform": "Reddit",
        "external_id": "r1",
        "object_type": "post",
        "root_post_external_id": "r1",
        "title": "Delivery",
        "text": "Delivery takes two weeks.",
    },
    {
        "platform": "youtube",
        "external_id": "y1",
        "object_type": "comment",
        "parent_external_id": "video-1",
        "root_post_external_id": "video-1",
        "text": "I cancelled too.",
    },
]
COVERAGE = {
    "status": "complete",
    "sources": [
        {"platform": "youtube", "status": "complete"},
        {"platform": "reddit", "status": "complete"},
    ],
}


def test_bundle_hash_is_order_independent_but_content_and_coverage_sensitive():
    first = build_evidence_bundle(RECORDS, COVERAGE)
    reordered = build_evidence_bundle(
        list(reversed(RECORDS)),
        {"sources": list(reversed(COVERAGE["sources"])), "status": "complete"},
    )
    changed_content = [dict(RECORDS[0], text="Delivery takes three weeks."), RECORDS[1]]

    assert first.evidence_hash == reordered.evidence_hash
    assert first.coverage_hash == reordered.coverage_hash
    assert first.evidence_hash != build_evidence_bundle(changed_content, COVERAGE).evidence_hash
    assert first.coverage_hash != build_evidence_bundle(
        RECORDS, {**COVERAGE, "status": "partial"}
    ).coverage_hash


def test_second_shared_analysis_is_exact_zero_llm_hit_and_lens_is_irrelevant(tmp_path):
    store = DiscoveryStore(tmp_path / "cache.db")
    calls = 0

    async def analyze(topic, records):
        nonlocal calls
        calls += 1
        return (
            {"topic": topic, "ids": sorted(row["external_id"] for row in records)},
            {"input_tokens": 17, "output_tokens": 4, "tokens_estimated": False},
        )

    analyzer = CachedHorizontalAnalyzer(
        store,
        analyze,
        extraction_schema_version="horizontal/1",
        prompt_version="prompt/1",
        provider="provider-a",
        model="model-a",
    )
    first = asyncio.run(analyzer.analyze(
        "delivery", RECORDS, coverage=COVERAGE, subject_key="delivery", lens_id="lens-a"
    ))
    second = asyncio.run(analyzer.analyze(
        "delivery", list(reversed(RECORDS)), coverage=COVERAGE,
        subject_key="another-subject", lens_id="lens-b",
    ))

    assert calls == 1
    assert second.result == first.result
    assert first.usage == {
        "cache_hit": False, "llm_calls": 1, "input_records": 2,
        "input_tokens": 17, "output_tokens": 4, "tokens_estimated": False,
    }
    assert second.usage == {
        "cache_hit": True, "llm_calls": 0, "input_records": 2,
        "input_tokens": 17, "output_tokens": 4, "tokens_estimated": False,
    }
    assert second.horizontal_extraction_id == first.horizontal_extraction_id


def test_prompt_and_model_invalidate_extraction_and_optional_interpretation_is_separate(tmp_path):
    store = DiscoveryStore(tmp_path / "cache.db")
    calls = 0

    async def analyze(records):
        nonlocal calls
        calls += 1
        return {"count": len(records)}

    def instance(prompt="p1", model="m1"):
        return CachedHorizontalAnalyzer(
            store, analyze, extraction_schema_version="s1", prompt_version=prompt,
            provider="provider", model=model,
        )

    first = asyncio.run(instance().analyze(RECORDS, coverage=COVERAGE, lens_id="one"))
    asyncio.run(instance(prompt="p2").analyze(RECORDS, coverage=COVERAGE))
    asyncio.run(instance(model="m2").analyze(RECORDS, coverage=COVERAGE))
    assert calls == 3

    horizontal = store.get_horizontal_extraction(first.cache_key)
    stored = store.put_optional_interpretation(
        horizontal_extraction_id=horizontal["id"], interpretation_type="lens",
        interpretation_version="v1", config={"threshold": 2}, provider="provider",
        model="m1", status="complete", result={"decision": "pass"}, input_records=2,
    )
    assert store.get_optional_interpretation(stored["cache_key"])["result"] == {
        "decision": "pass"
    }
    assert store.get_horizontal_extraction(first.cache_key)["result"] == first.result


def test_cache_schema_migrates_a_populated_discovery_database(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE discovery_runs (
            id TEXT PRIMARY KEY, geo TEXT NOT NULL, observed_at TEXT NOT NULL,
            completed_at TEXT NOT NULL, status TEXT NOT NULL,
            comparable INTEGER NOT NULL, candidate_count INTEGER NOT NULL,
            error_category TEXT, source_health_json TEXT NOT NULL DEFAULT '[]'
        );
        INSERT INTO discovery_runs VALUES
            ('old', 'US', 'then', 'then', 'complete', 1, 3, NULL, '[]');
    """)
    connection.commit()
    connection.close()

    DiscoveryStore(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT candidate_count FROM discovery_runs WHERE id='old'"
        ).fetchone() == (3,)
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert {"evidence_bundles", "evidence_bundle_members", "horizontal_extractions",
            "optional_interpretations"} <= tables
