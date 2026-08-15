from social_scraper.discovery.explore_read_model import (
    DEFAULT_STAGE_POLICY,
    apply_perspective,
    build_explore_read_model,
    classify_stage,
)


def _family(**overrides):
    value = {
        "id": "fam-payments",
        "canonical_label": "Agentic payments",
        "members": [
            {"normalized_keyword": "agentic payments", "relationship": "broader"},
            {"normalized_keyword": "x402", "relationship": "enabling_technology"},
        ],
        "geo": "US",
        "category": "Technology",
    }
    value.update(overrides)
    return value


def _evidence(**overrides):
    value = {
        "what_it_is": {
            "text": "Software agents making machine-to-machine payments.",
            "support": [{"url": "https://example.com/root", "record_id": "root-1"}],
        },
        "trajectory": {
            "source": "Google Trends trending_now",
            "period": "2026-08-12/2026-08-15",
            "reported_volume": 5000,
            "comparable_snapshots": 2,
            "direction": "rising",
        },
        "resonance": {"supported_outlier_roots": 1, "status": "weak"},
        "corroboration": {
            "unique_root_count": 4,
            "independent_author_count": 3,
            "healthy_platform_count": 2,
        },
        "propagation": {"repost_cluster_count": 1, "largest_repost_cluster_size": 3},
        "conversation_depth": {"active_root_count": 2, "status": "supported"},
        "coverage": {"healthy_sources": ["reddit", "youtube"], "missing_sources": ["x"]},
    }
    value.update(overrides)
    return value


def test_populated_family_read_model_is_plain_and_separates_evidence_types():
    model = build_explore_read_model(
        _family(),
        evidence=_evidence(),
        promotion={
            "routes": [{"route": "cross_platform_breadth", "passed": True, "evidence": {"roots": 4}}]
        },
    )

    assert model["family_id"] == "fam-payments"
    assert model["label"] == "Agentic payments"
    assert model["member_terms"] == [
        {"term": "agentic payments", "relationship": "broader"},
        {"term": "x402", "relationship": "enabling_technology"},
    ]
    assert model["what_it_is"]["status"] == "supported"
    assert model["stage"] == "confirming"
    assert model["stage_evaluation"]["policy_version"] == DEFAULT_STAGE_POLICY["version"]
    assert model["why_surfaced"][0]["route"] == "cross_platform_breadth"
    assert model["corroboration"]["independent_author_count"] == 3
    assert model["propagation"]["repost_cluster_count"] == 1
    assert model["conversation_depth"]["active_root_count"] == 2
    assert model["available_actions"] == ["investigate", "monitor", "dismiss"]
    assert "investability" not in model


def test_what_it_is_remains_unclear_without_cited_related_or_root_context():
    evidence = _evidence(what_it_is={"text": "A plausible but unsupported gloss", "support": []})
    model = build_explore_read_model(_family(), evidence=evidence)
    assert model["what_it_is"] == {"text": "Not enough context yet.", "status": "unclear"}
    assert "Topic explanation lacks cited related-query or root context." in model["limitations"]


def test_reported_search_volume_is_omitted_without_source_and_period():
    evidence = _evidence(trajectory={"reported_volume": 5000, "comparable_snapshots": 1})
    model = build_explore_read_model(_family(), evidence=evidence)
    assert "reported_volume" not in model["trajectory"]
    assert "Search volume is hidden because its source or period is missing." in model["limitations"]


def test_explicit_one_off_event_is_classified_as_event_spike():
    stage = classify_stage({"one_off_event": True, "unique_root_count": 12})
    assert stage["stage"] == "event_spike"
    assert stage["passed_rule"] == "explicit_event_evidence"


def test_cooling_requires_a_comparable_decline():
    assert classify_stage({"direction": "declining", "trajectory_comparable": False})["stage"] != "cooling"
    assert classify_stage({"direction": "declining", "trajectory_comparable": True})["stage"] == "cooling"


def test_missing_counts_cannot_accidentally_advance_a_stage():
    result = classify_stage({
        "comparable_snapshots": None,
        "unique_root_count": None,
        "independent_author_count": None,
        "healthy_platform_count": None,
    })
    assert result["stage"] == "unclear"


def test_rejected_or_unclear_family_remains_inspectable():
    model = build_explore_read_model(
        _family(status="rejected"),
        evidence={"coverage": {"missing_sources": ["reddit", "x"]}},
        promotion={"eligible": False, "routes": [], "limitations": ["Insufficient independent roots."]},
    )
    assert model["stage"] == "unclear"
    assert model["available_actions"] == ["investigate", "monitor", "dismiss"]
    assert "Insufficient independent roots." in model["limitations"]
    assert model["coverage"]["missing_sources"] == ["reddit", "x"]


def test_inputs_are_not_mutated():
    family = _family()
    evidence = _evidence()
    original_members = [dict(item) for item in family["members"]]
    build_explore_read_model(family, evidence=evidence)
    assert family["members"] == original_members
    assert evidence["trajectory"]["reported_volume"] == 5000


def _lens(lens_id, feature, *, mode="score", minimum=None):
    return {
        "lens_id": lens_id,
        "name": lens_id,
        "version": "1",
        "objective": "Re-rank already collected evidence",
        "criteria": [{
            "criterion_id": feature,
            "label": feature,
            "feature_key": feature,
            "mode": mode,
            "weight": 1.0,
            "minimum": minimum,
            "missing_policy": "keep_unknown",
        }],
    }


def test_perspectives_reorder_the_same_family_records_without_mutation():
    first = build_explore_read_model(
        _family(id="first", canonical_label="First"),
        evidence=_evidence(features={"novelty": 0.9, "behavior_evidence": 0.2}),
    )
    second = build_explore_read_model(
        _family(id="second", canonical_label="Second"),
        evidence=_evidence(features={"novelty": 0.3, "behavior_evidence": 0.8}),
    )
    original = [dict(first), dict(second)]

    novelty = apply_perspective([first, second], _lens("novel", "novelty"))
    behavior = apply_perspective([first, second], _lens("behavior", "behavior_evidence"))

    assert [item["family_id"] for item in novelty["items"]] == ["first", "second"]
    assert [item["family_id"] for item in behavior["items"]] == ["second", "first"]
    assert novelty["collection_performed"] is False
    assert behavior["collection_performed"] is False
    assert first == original[0]
    assert second == original[1]


def test_perspective_filter_excludes_only_from_the_view_not_the_raw_record():
    included = build_explore_read_model(
        _family(id="included"), evidence=_evidence(features={"novelty": 0.8})
    )
    excluded = build_explore_read_model(
        _family(id="excluded"), evidence=_evidence(features={"novelty": 0.2})
    )
    result = apply_perspective(
        [included, excluded], _lens("threshold", "novelty", mode="filter", minimum=0.5)
    )
    assert [item["family_id"] for item in result["items"]] == ["included"]
    assert result["excluded_family_ids"] == ["excluded"]
    assert excluded["features"]["novelty"] == 0.2
