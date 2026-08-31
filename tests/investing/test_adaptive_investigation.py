from social_scraper.investing.adaptive_investigation import (
    extract_observation_anchors,
    make_query_lineage_id,
    plan_adaptive_anchor_batches,
    select_adaptive_anchors,
)


def _record(eid, text, author="a", platform="x", panel_id="household_cleaning", engagement=None):
    return {
        "id": eid,
        "panel_id": panel_id,
        "platform": platform,
        "external_id": eid,
        "url": f"https://example.com/{eid}",
        "author": author,
        "text": text,
        "engagement": engagement or {"likes": 1, "comments": 0},
        "query": "household products people switched to",
    }


def test_extracts_specific_object_and_behavior_without_llm():
    anchors = extract_observation_anchors([
        _record("e1", "I switched to a silicone air fryer liner"),
    ], panel_id="household_cleaning", seed_query="household products people switched to")

    assert len(anchors) == 1
    assert anchors[0]["normalized_anchor"] == "silicone air fryer liner"
    assert anchors[0]["behavior_type"] == "switching"
    assert anchors[0]["behavior_phrase"] == "switched to"
    assert anchors[0]["source_evidence_ids"] == ["e1"]
    assert anchors[0]["distinct_authors"] == 1


def test_extraction_stops_at_conjunction_and_does_not_invent_authors():
    records = [
        _record(
            "e1",
            "I switched to a silicone air fryer liner and now cleanup is easier",
            author="",
        ),
        _record(
            "e2",
            "We switched to silicone air fryer liners but still test paper sometimes",
            author="",
            platform="instagram",
        ),
    ]

    anchors = extract_observation_anchors(
        records,
        panel_id="household_cleaning",
        seed_query="household products people switched to",
    )

    assert [item["normalized_anchor"] for item in anchors] == [
        "silicone air fryer liner"
    ]
    assert anchors[0]["support_count"] == 2
    assert anchors[0]["distinct_authors"] == 0


def test_groups_independent_support_for_the_same_anchor():
    anchors = extract_observation_anchors([
        _record("e1", "I switched to a silicone air fryer liner", "a", "x"),
        _record("e2", "We switched to silicone air fryer liners", "b", "instagram"),
    ], panel_id="household_cleaning", seed_query="household products people switched to")

    selected = next(item for item in anchors if item["normalized_anchor"] == "silicone air fryer liner")
    assert selected["support_count"] == 2
    assert selected["distinct_authors"] == 2
    assert selected["platforms"] == ["instagram", "x"]
    assert selected["source_evidence_ids"] == ["e1", "e2"]


def test_rejects_generic_panel_terms_news_and_promotional_copy():
    anchors = extract_observation_anchors([
        _record("e1", "Breaking news: consumers switched to technology"),
        _record("e2", "Sponsored ad: I bought skincare #sale"),
        _record("e3", "People switched to AI"),
    ], panel_id="beauty_skincare", seed_query="beauty products")

    assert anchors == []


def test_selection_is_deterministic_diverse_and_bounded():
    records = []
    products = [
        "silicone air fryer liner", "enzyme laundry sheet", "refillable hand soap",
        "wool dryer ball", "robot window cleaner", "steam floor brush",
        "reusable mop pad", "countertop compost bin",
    ]
    for index, product in enumerate(products):
        records.append(_record(
            f"e{index}", f"I switched to a {product}", f"author-{index}",
            "x" if index % 2 else "tiktok",
            engagement={"likes": 100 - index, "comments": index},
        ))

    anchors = extract_observation_anchors(
        records,
        panel_id="household_cleaning",
        seed_query="household products people switched to",
    )
    first = select_adaptive_anchors(anchors, high_support_limit=4, exploration_limit=2)
    second = select_adaptive_anchors(list(reversed(anchors)), high_support_limit=4, exploration_limit=2)

    assert first == second
    assert len(first) <= 6
    assert len({item["normalized_anchor"] for item in first}) == len(first)


def test_query_lineage_is_stable_and_changes_with_query():
    first = make_query_lineage_id(
        panel_id="household_cleaning",
        platform="reddit",
        seed_query="cleaning",
        anchor_id="anchor-1",
        query="silicone air fryer liner",
    )
    same = make_query_lineage_id(
        panel_id="household_cleaning",
        platform="reddit",
        seed_query="cleaning",
        anchor_id="anchor-1",
        query="silicone air fryer liner",
    )
    changed = make_query_lineage_id(
        panel_id="household_cleaning",
        platform="reddit",
        seed_query="cleaning",
        anchor_id="anchor-1",
        query="enzyme laundry sheet",
    )

    assert first == same
    assert first != changed
    assert len(first) == 24


def test_global_adaptive_plan_covers_each_panel_before_four_exploration_slots():
    panel_order = [f"panel-{index}" for index in range(16)]
    panel_anchors = {
        panel_id: [{
            "anchor_id": f"{panel_id}-primary",
            "normalized_anchor": f"{panel_id} primary product",
            "support_count": 5,
            "engagement_total": 100,
        }, {
            "anchor_id": f"{panel_id}-explore",
            "normalized_anchor": f"{panel_id} unusual product",
            "support_count": 1,
            "engagement_total": index,
        }]
        for index, panel_id in enumerate(panel_order)
    }

    planned = plan_adaptive_anchor_batches(
        panel_anchors,
        panel_order=panel_order,
        max_total=20,
    )

    assert sum(len(values) for values in planned.values()) == 20
    assert all(planned[panel_id][0]["anchor_id"].endswith("-primary") for panel_id in panel_order)
    exploration = [
        item["anchor_id"]
        for values in planned.values()
        for item in values[1:]
    ]
    assert exploration == [
        "panel-0-explore", "panel-1-explore", "panel-2-explore", "panel-3-explore",
    ]
