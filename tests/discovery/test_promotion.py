"""Task 4.1: configurable eligibility and nine explicit promotion routes."""

from social_scraper.discovery.promotion import (
    AUTOMATIC_ROUTES,
    POLICY_VERSION,
    ROUTE_NAMES,
    PromotionPolicy,
    evaluate_promotion,
    select_exploration_sample,
)
from social_scraper.discovery.prioritization import prioritize_candidates


def route(result, name):
    return next(r for r in result["routes"] if r["route"] == name)


def component(item, name):
    if isinstance(item, dict) and "gates" in item:
        items = item["gates"]
    else:
        items = item["components"]
    return next(c for c in items if c["name"] == name)


def snapshots(*statuses):
    return [
        {"snapshot_id": f"d{i}", "status": status}
        for i, status in enumerate(statuses, start=1)
    ]


def passing_evidence(candidate_id="alpha", **overrides):
    evidence = {
        "candidate_id": candidate_id,
        "root_summary": {
            "unique_root_count": 5,
            "independent_author_count": 4,
            "repost_cluster_count": 1,
            "largest_repost_cluster_size": 3,
        },
        "usable_text_root_count": 4,
        "duplicate_only_support": False,
        "source_health": {"youtube": "healthy", "reddit": "healthy"},
        "platform_hits": {"youtube": 3, "reddit": 2},
        "snapshot_windows": snapshots("present", "present", "present"),
        "trajectory": {
            "volume": {"previous": 100, "current": 140},
            "growth": {"previous": 5.0, "current": None},
            "related_query_breadth": {"previous": None, "current": None},
            "regional_breadth": {"previous": 4, "current": 7},
        },
        "engagement_roots": [
            {"root_id": "r1", "author_id": "a1", "engagement_percentile": 96.5,
             "baseline_status": "supported", "repost_cluster_id": None},
            {"root_id": "r2", "author_id": "a2", "engagement_percentile": 97.2,
             "baseline_status": "supported", "repost_cluster_id": None},
        ],
        "creator_summary": {
            "independent_creator_count": 4,
            "previous_independent_creator_count": 2,
        },
        "depth_roots": [
            {"root_id": "r1", "author_id": "a1", "comment_percentile": 93.0,
             "baseline_status": "supported"},
            {"root_id": "r2", "author_id": "a2", "comment_percentile": None,
             "baseline_status": "weak"},
        ],
        "active_discussion_roots": 2,
        "radar_match": {"matched": False, "radar_ids": []},
        "manual_request": {"requested": False, "within_budget": False},
        "stratum": {"category": "fintech", "region": "US"},
    }
    evidence.update(overrides)
    return evidence


def test_policy_is_versioned_with_inspectable_config():
    policy = PromotionPolicy()
    assert POLICY_VERSION == policy.version
    snapshot = policy.config_snapshot()
    assert snapshot["policy_version"] == POLICY_VERSION
    assert snapshot["eligibility"] == {
        "minimum_unique_roots": 3,
        "minimum_independent_authors": 2,
        "require_usable_text": True,
        "require_source_health": True,
        "reject_duplicate_only_support": True,
    }
    assert set(snapshot["routes"]) == set(ROUTE_NAMES)
    assert len(ROUTE_NAMES) == 9
    assert set(AUTOMATIC_ROUTES) == set(ROUTE_NAMES) - {
        "manual_promotion", "exploration_allocation"
    }


def test_config_rejects_unknown_keys_and_bad_thresholds():
    for bad in (
        {"eligibility": {"minimum_unique_roots": 3, "bogus_gate": True}},
        {"routes": {"nonexistent_route": {}}},
        {"routes": {"cross_platform_breadth": {"min_healthy_platforms": "many"}}},
        {"routes": {"age_adjusted_engagement_breakout": {"percentile_threshold": 140.0}}},
        {"radar_floor_overrides": {"require_usable_text": False}},
    ):
        try:
            PromotionPolicy(bad)
        except ValueError:
            continue
        raise AssertionError(f"config should have been rejected: {bad}")


def test_eligible_requires_all_universal_gates():
    assert evaluate_promotion(passing_evidence())["eligible"] is True

    failing = {
        "minimum_unique_roots": {"root_summary": {
            "unique_root_count": 2, "independent_author_count": 4}},
        "minimum_independent_authors": {"root_summary": {
            "unique_root_count": 5, "independent_author_count": 1}},
        "require_usable_text": {"usable_text_root_count": 0},
        "require_source_health": {"source_health": {"youtube": "unavailable"}},
        "reject_duplicate_only_support": {"duplicate_only_support": True},
    }
    for gate, override in failing.items():
        result = evaluate_promotion(passing_evidence(**override))
        assert result["eligible"] is False, gate
        assert component(result["eligibility"], gate)["passed"] is False, gate
        assert result["automatically_promoted"] is False, gate
        assert any(gate in reason for reason in result["reasons"]), gate


def test_missing_gate_evidence_is_an_explicit_gap():
    evidence = passing_evidence()
    del evidence["root_summary"]
    result = evaluate_promotion(evidence)
    assert result["eligible"] is False
    gate = component(result["eligibility"], "minimum_unique_roots")
    assert gate["status"] == "unknown"
    assert gate["observed"] is None
    assert any("gap" in limitation or "unknown" in limitation
               for limitation in result["limitations"])


def test_daily_persistence_keeps_gaps_as_gaps():
    ok = evaluate_promotion(
        passing_evidence(snapshot_windows=snapshots("present", "present", "gap")))
    presence = component(route(ok, "daily_search_persistence"), "snapshot_presence")
    assert route(ok, "daily_search_persistence")["passed"] is True
    assert presence["observed"] == {
        "present": 2, "absent": 0, "gap": 1, "observed_snapshots": 3,
    }

    sparse = evaluate_promotion(
        passing_evidence(snapshot_windows=snapshots("present", "gap", "gap")))
    presence = component(route(sparse, "daily_search_persistence"), "snapshot_presence")
    assert route(sparse, "daily_search_persistence")["passed"] is False
    assert presence["observed"]["present"] == 1
    assert presence["observed"]["gap"] == 2
    assert presence["observed"]["absent"] == 0

    no_sweep = evaluate_promotion(
        passing_evidence(root_summary={"unique_root_count": 0,
                                       "independent_author_count": 0}))
    sweep = component(route(no_sweep, "daily_search_persistence"),
                      "root_evidence_after_weekly_sweep")
    assert route(no_sweep, "daily_search_persistence")["passed"] is False
    assert sweep["passed"] is False


def test_trajectory_needs_persistence_and_an_observed_increase():
    assert route(evaluate_promotion(passing_evidence()),
                 "search_trajectory_expansion")["passed"] is True

    no_increase = evaluate_promotion(passing_evidence(
        trajectory={"volume": {"previous": 100, "current": None},
                    "growth": {"previous": None, "current": None},
                    "related_query_breadth": {"previous": None, "current": None},
                    "regional_breadth": {"previous": 5, "current": 5}}))
    result = route(no_increase, "search_trajectory_expansion")
    assert result["passed"] is False
    assert component(result, "own_series_volume_or_growth_increase")["observed"] == {
        "volume": None, "growth": None}
    assert component(result, "regional_breadth_increase")["passed"] is False

    no_persistence = evaluate_promotion(passing_evidence(
        snapshot_windows=snapshots("present", "absent", "absent")))
    assert route(no_persistence, "search_trajectory_expansion")["passed"] is False
    assert component(route(no_persistence, "search_trajectory_expansion"),
                     "snapshot_presence")["passed"] is False


def test_cross_platform_breadth_uses_independent_counts_and_health():
    assert route(evaluate_promotion(passing_evidence()),
                 "cross_platform_breadth")["passed"] is True

    degraded = evaluate_promotion(passing_evidence(
        source_health={"youtube": "healthy", "reddit": "degraded"}))
    result = route(degraded, "cross_platform_breadth")
    assert result["passed"] is False
    assert component(result, "healthy_platforms_with_hits")["observed"]["count"] == 1

    repost_inflated = evaluate_promotion(passing_evidence(root_summary={
        "unique_root_count": 5, "independent_author_count": 2,
        "repost_cluster_count": 2, "largest_repost_cluster_size": 7}))
    result = route(repost_inflated, "cross_platform_breadth")
    assert result["passed"] is False
    authors = component(result, "independent_authors")
    assert authors["observed"]["independent_author_count"] == 2
    assert authors["observed"]["repost_cluster_count"] == 2
    assert authors["observed"]["raw_counts_excluded"] is True

    too_few_roots = evaluate_promotion(passing_evidence(root_summary={
        "unique_root_count": 2, "independent_author_count": 4}))
    assert route(too_few_roots, "cross_platform_breadth")["passed"] is False


def test_engagement_breakout_paths_require_supported_baselines():
    assert route(evaluate_promotion(passing_evidence()),
                 "age_adjusted_engagement_breakout")["passed"] is True

    extreme = evaluate_promotion(passing_evidence(engagement_roots=[
        {"root_id": "r1", "author_id": "a1", "engagement_percentile": 99.6,
         "baseline_status": "supported", "repost_cluster_id": None},
        {"root_id": "r9", "author_id": "a9", "engagement_percentile": 12.0,
         "baseline_status": "supported", "repost_cluster_id": None},
    ]))
    result = route(extreme, "age_adjusted_engagement_breakout")
    assert result["passed"] is True
    assert component(result, "extreme_outlier_plus_corroboration")["passed"] is True

    same_author = evaluate_promotion(passing_evidence(engagement_roots=[
        {"root_id": "r1", "author_id": "a1", "engagement_percentile": 96.0,
         "baseline_status": "supported", "repost_cluster_id": None},
        {"root_id": "r2", "author_id": "a1", "engagement_percentile": 97.0,
         "baseline_status": "supported", "repost_cluster_id": None},
    ]))
    assert route(same_author, "age_adjusted_engagement_breakout")["passed"] is False

    weak = evaluate_promotion(passing_evidence(engagement_roots=[
        {"root_id": "r1", "author_id": "a1", "engagement_percentile": 99.9,
         "baseline_status": "weak", "repost_cluster_id": None},
        {"root_id": "r2", "author_id": "a2", "engagement_percentile": 98.8,
         "baseline_status": "unavailable", "repost_cluster_id": None},
    ]))
    result = route(weak, "age_adjusted_engagement_breakout")
    assert result["passed"] is False
    assert component(result, "supported_baseline_coverage")["observed"] == {
        "supported": 0, "weak": 1, "unavailable": 1}

    reposts_only = evaluate_promotion(passing_evidence(engagement_roots=[
        {"root_id": "r1", "author_id": "a1", "engagement_percentile": 99.8,
         "baseline_status": "supported", "repost_cluster_id": None},
        {"root_id": "r2", "author_id": "a2", "engagement_percentile": 99.7,
         "baseline_status": "supported", "repost_cluster_id": "cluster-1"},
    ]))
    assert route(reposts_only, "age_adjusted_engagement_breakout")["passed"] is False


def test_creator_breadth_needs_material_increase_vs_own_series():
    assert route(evaluate_promotion(passing_evidence()),
                 "creator_breadth_expansion")["passed"] is True

    missing_previous = evaluate_promotion(passing_evidence(creator_summary={
        "independent_creator_count": 4, "previous_independent_creator_count": None}))
    result = route(missing_previous, "creator_breadth_expansion")
    assert result["passed"] is False
    assert component(result, "material_increase_vs_previous")["status"] == "unknown"

    flat = evaluate_promotion(passing_evidence(creator_summary={
        "independent_creator_count": 3, "previous_independent_creator_count": 3}))
    assert route(flat, "creator_breadth_expansion")["passed"] is False

    below_floor = evaluate_promotion(passing_evidence(creator_summary={
        "independent_creator_count": 2, "previous_independent_creator_count": 1}))
    assert route(below_floor, "creator_breadth_expansion")["passed"] is False


def test_conversation_depth_trigger_paths():
    assert route(evaluate_promotion(passing_evidence()),
                 "conversation_depth_trigger")["passed"] is True

    several = evaluate_promotion(passing_evidence(depth_roots=[
        {"root_id": "r1", "author_id": "a1", "comment_percentile": None,
         "baseline_status": "unavailable"},
    ]))
    result = route(several, "conversation_depth_trigger")
    assert result["passed"] is True
    assert component(result, "comment_activity_above_baseline")["passed"] is False
    assert component(result, "several_independent_active_discussions")["passed"] is True

    weak_baseline = evaluate_promotion(passing_evidence(
        depth_roots=[{"root_id": "r1", "author_id": "a1",
                      "comment_percentile": 95.0, "baseline_status": "weak"}],
        active_discussion_roots=1))
    result = route(weak_baseline, "conversation_depth_trigger")
    assert result["passed"] is False
    assert component(result, "comment_activity_above_baseline")["passed"] is False

    unknown_discussions = evaluate_promotion(passing_evidence(
        depth_roots=[], active_discussion_roots=None))
    result = route(unknown_discussions, "conversation_depth_trigger")
    assert result["passed"] is False
    assert component(result, "several_independent_active_discussions")["status"] == "unknown"


def test_personal_radar_recurrence_relaxes_floors_visibly():
    evidence = passing_evidence(
        candidate_id="radar-hit",
        root_summary={"unique_root_count": 1, "independent_author_count": 1,
                      "repost_cluster_count": 0, "largest_repost_cluster_size": 0},
        usable_text_root_count=1,
        radar_match={"matched": True, "radar_ids": ["vincent-fintech"]},
    )
    result = evaluate_promotion(evidence)
    assert result["eligible"] is True
    gate = component(result["eligibility"], "minimum_unique_roots")
    assert gate["observed"] == 1 and gate["required"] == 1
    assert gate["applied_override"] is True
    radar_route = route(result, "personal_radar_recurrence")
    assert radar_route["passed"] is True
    assert component(radar_route, "saved_radar_match")["observed"] == ["vincent-fintech"]

    unmatched = passing_evidence(
        root_summary={"unique_root_count": 1, "independent_author_count": 1,
                      "repost_cluster_count": 0, "largest_repost_cluster_size": 0},
        usable_text_root_count=1)
    assert evaluate_promotion(unmatched)["eligible"] is False


def test_manual_promotion_respects_budget_and_is_never_automatic():
    approved = evaluate_promotion(passing_evidence(
        manual_request={"requested": True, "within_budget": True}))
    assert route(approved, "manual_promotion")["passed"] is True
    assert route(approved, "manual_promotion")["mode"] == "manual"
    assert approved["promotion_mode"] == "manual"
    assert approved["automatically_promoted"] is False
    assert "manual_promotion" not in approved["automatic_routes_passed"]

    over_budget = evaluate_promotion(passing_evidence(
        manual_request={"requested": True, "within_budget": False}))
    assert route(over_budget, "manual_promotion")["passed"] is False
    assert over_budget["promotion_mode"] == "automatic"


def test_no_mixing_of_components_across_routes():
    result = evaluate_promotion(passing_evidence(
        snapshot_windows=snapshots("present", "absent", "absent"),
        root_summary={"unique_root_count": 5, "independent_author_count": 2,
                      "repost_cluster_count": 3, "largest_repost_cluster_size": 9},
        trajectory={"volume": {"previous": 10, "current": 500},
                    "growth": {"previous": 1, "current": 9},
                    "related_query_breadth": {"previous": 2, "current": 8},
                    "regional_breadth": {"previous": 1, "current": 6}},
        engagement_roots=[{"root_id": "r1", "author_id": "a1",
                           "engagement_percentile": 98.0,
                           "baseline_status": "supported",
                           "repost_cluster_id": None}],
        creator_summary={"independent_creator_count": 4,
                         "previous_independent_creator_count": None},
        depth_roots=[], active_discussion_roots=None,
    ))
    passing_bits = [
        component(route(result, "search_trajectory_expansion"),
                  "own_series_volume_or_growth_increase")["passed"],
        component(route(result, "cross_platform_breadth"),
                  "healthy_platforms_with_hits")["passed"],
        component(route(result, "age_adjusted_engagement_breakout"),
                  "supported_baseline_coverage")["passed"],
    ]
    assert any(passing_bits)
    assert all(r["passed"] is False for r in result["routes"])
    assert result["automatically_promoted"] is False
    assert result["automatic_routes_passed"] == []


def test_exploration_sample_is_capped_stratified_and_deterministic():
    def evaluation(candidate_id, category, region, **override):
        return evaluate_promotion(passing_evidence(
            candidate_id=candidate_id,
            stratum={"category": category, "region": region}, **override))

    def quiet(candidate_id, category, region, **override):
        # Gates pass, every automatic route fails: eligible, not promoted.
        return evaluation(
            candidate_id, category, region,
            snapshot_windows=snapshots("absent", "absent", "absent"),
            platform_hits={"youtube": 3},
            source_health={"youtube": "healthy"},
            engagement_roots=[{"root_id": "r1", "author_id": "a1",
                               "engagement_percentile": 99.9,
                               "baseline_status": "weak",
                               "repost_cluster_id": None}],
            creator_summary={"independent_creator_count": 4,
                             "previous_independent_creator_count": 4},
            depth_roots=[{"root_id": "r1", "author_id": "a1",
                          "comment_percentile": 99.9, "baseline_status": "weak"}],
            active_discussion_roots=0, **override)

    promoted = evaluation("c1", "fintech", "US")
    assert promoted["automatically_promoted"] is True
    assert promoted["promotion_mode"] == "automatic"

    cohort = [
        promoted,
        quiet("c2", "fintech", "US"),
        quiet("c3", "fintech", "SG"),
        quiet("c4", "fintech", "SG"),
        quiet("c5", "sports", "US"),
        quiet("c6", "sports", "US"),
        quiet("c7", "sports", "US"),
        quiet("c8", "sports", "US", root_summary={
            "unique_root_count": 0, "independent_author_count": 0}),  # ineligible
    ]
    assert cohort[1]["eligible"] is True
    assert cohort[1]["automatically_promoted"] is False
    selected = select_exploration_sample(cohort)
    # Round-robin over sorted strata: fintech/SG, fintech/US, sports/US.
    assert [e["candidate_id"] for e in selected] == ["c3", "c2", "c5"]
    assert all(e["promotion_mode"] == "exploration" for e in selected)
    assert all(route(e, "exploration_allocation")["passed"] is True for e in selected)
    assert route(cohort[1], "exploration_allocation")["passed"] is False
    assert select_exploration_sample(cohort)[0]["candidate_id"] == "c3"

    capped = PromotionPolicy({"routes": {"exploration_allocation": {"per_run_cap": 1}}})
    assert [e["candidate_id"] for e in select_exploration_sample(cohort, capped)] == ["c3"]


def test_result_exposes_reasons_components_and_config():
    result = evaluate_promotion(passing_evidence())
    assert result["policy_version"] == POLICY_VERSION
    assert result["config"]["policy_version"] == POLICY_VERSION
    assert len(result["routes"]) == 9
    assert {r["mode"] for r in result["routes"]} == {"automatic", "manual", "exploration"}
    assert result["automatic_routes_passed"] == [
        "daily_search_persistence",
        "search_trajectory_expansion",
        "cross_platform_breadth",
        "age_adjusted_engagement_breakout",
        "creator_breadth_expansion",
        "conversation_depth_trigger",
    ]
    assert result["automatically_promoted"] is True
    assert result["promotion_mode"] == "automatic"
    assert result["reasons"]
    for evaluated_route in result["routes"]:
        assert evaluated_route["components"]
    for gate in result["eligibility"]["gates"]:
        assert gate["status"] == "pass"


def test_prioritization_consumes_promotion_evaluation():
    ordered = prioritize_candidates([
        {"id": "a", "keyword": "A", "eligible": True},
        {"id": "b", "keyword": "B", "eligible": True,
         "promotion": {"eligible": False, "promotion_mode": "none"}},
        {"id": "c", "keyword": "C", "eligible": False,
         "promotion": {"eligible": True, "promotion_mode": "manual"}},
    ])
    by_id = {item["candidate_id"]: item["priority_components"] for item in ordered}
    assert by_id["a"]["eligible"] is True
    assert by_id["b"]["eligible"] is False
    assert by_id["c"]["eligible"] is True and by_id["c"]["manual_promoted"] is True
    assert [item["candidate_id"] for item in ordered][0] == "c"
