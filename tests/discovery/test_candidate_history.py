from datetime import datetime, timezone

from social_scraper.discovery.storage import DiscoveryStore


T1 = datetime(2026, 8, 10, 4, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 8, 10, 5, 0, tzinfo=timezone.utc)
T3 = datetime(2026, 8, 10, 6, 0, tzinfo=timezone.utc)
T4 = datetime(2026, 8, 10, 7, 0, tzinfo=timezone.utc)


def candidate(keyword="turbine blade", volume=20_000, growth=700):
    return {
        "keyword": keyword,
        "related_terms": [keyword, "jet engine"],
        "search_volume": volume,
        "growth_pct": growth,
        "source_started_at": "2026-08-10T03:00:00+00:00",
        "topic_ids": [3],
        "categories": ["Business & Finance"],
    }


def test_missing_source_metrics_remain_null(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    run_id = store.record_feed(
        geo="US",
        observed_at=T1,
        candidates=[candidate(volume=None, growth=None)],
    )

    rows = store.list_run_candidates(run_id)
    assert rows[0]["search_volume"] is None
    assert rows[0]["growth_pct"] is None
    assert rows[0]["source_started_at"] == "2026-08-10T03:00:00+00:00"


def test_observation_gaps_are_explicit_and_not_interpolated(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    store.record_feed(geo="US", observed_at=T1, candidates=[candidate()])
    store.record_feed(geo="US", observed_at=T2, candidates=[])

    during_gap = store.get_candidate_history("US", "turbine blade")
    assert len(during_gap["observations"]) == 1
    assert during_gap["series"]["consecutive_observations"] == 0
    assert during_gap["gaps"] == [{
        "started_at": T2.isoformat(),
        "ended_at": None,
        "missed_comparable_runs": 1,
    }]

    store.record_feed(
        geo="US",
        observed_at=T3,
        candidates=[],
        status="error",
        comparable=False,
        error_category="source_timeout",
    )
    unchanged = store.get_candidate_history("US", "turbine blade")
    assert unchanged["gaps"][0]["missed_comparable_runs"] == 1

    store.record_feed(geo="US", observed_at=T4, candidates=[candidate(growth=900)])
    returned = store.get_candidate_history("US", "turbine blade")
    assert len(returned["observations"]) == 2
    assert returned["series"]["consecutive_observations"] == 1
    assert returned["gaps"][0]["ended_at"] == T4.isoformat()
    assert [o["growth_pct"] for o in returned["observations"]] == [700, 900]


def test_unchecked_empty_partial_and_failed_gate_states_are_distinct(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    run_id = store.record_feed(geo="US", observed_at=T1, candidates=[candidate()])
    observation_id = store.list_run_candidates(run_id)[0]["observation_id"]

    ids = [
        store.record_gate_check(observation_id, status="not_checked", passed=None),
        store.record_gate_check(observation_id, status="empty", passed=False),
        store.record_gate_check(
            observation_id, status="partial", passed=None,
            records=[{
                "platform": "youtube", "post_id": "reply-1",
                "record_type": "reply", "parent_external_id": "comment-1",
                "depth": 2, "text": "actual reply",
            }],
        ),
        store.record_gate_check(observation_id, status="failed", passed=None),
    ]
    checks = store.list_gate_checks(observation_id)
    assert [c["id"] for c in checks] == ids
    assert [(c["status"], c["passed"]) for c in checks] == [
        ("not_checked", None),
        ("empty", False),
        ("partial", None),
        ("failed", None),
    ]
    assert checks[2]["records"][0]["parent_external_id"] == "comment-1"


def test_same_observation_keeps_separate_versioned_lens_evaluations(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    run_id = store.record_feed(
        geo="US", observed_at=T1,
        candidates=[candidate("Mexico vs USA U20", 50_000, 800)],
    )
    observation_id = store.list_run_candidates(run_id)[0]["observation_id"]
    first_id = store.record_lens_evaluation(
        observation_id,
        lens_id="vincent-investing", lens_version="1",
        spec={"criteria": ["company_exposure"]},
        features={"company_exposure": 0.0},
        result={"status": "excluded", "score": 0.0, "score_coverage": 1.0},
    )
    second_id = store.record_lens_evaluation(
        observation_id,
        lens_id="macro-fx", lens_version="1",
        spec={"criteria": ["fx_relevance"]},
        features={"fx_relevance": 0.9},
        result={"status": "included", "score": 0.9, "score_coverage": 1.0},
    )
    assert second_id > first_id
    assert store.get_latest_candidate_context("US", "Mexico vs USA U20")[
        "observation"
    ]["observation_id"] == observation_id


def test_duplicate_source_rows_are_preserved_without_duplicate_observations(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    run_id = store.record_feed(
        geo="US", observed_at=T1,
        candidates=[
            candidate("Same Topic", 10_000, 200),
            {**candidate(" same   topic ", 12_000, 200), "related_terms": ["variant"]},
        ],
    )
    observations = store.list_run_candidates(run_id)
    assert len(observations) == 1
    observation = observations[0]
    assert observation["search_volume"] is None
    assert observation["growth_pct"] == 200
    assert observation["related_terms"] == ["Same Topic", "jet engine", "variant"]
    assert observation["raw_payload"]["source_record_count"] == 2
    assert observation["raw_payload"]["metric_conflicts"] == ["search_volume"]
    assert len(observation["raw_payload"]["source_records"]) == 2
