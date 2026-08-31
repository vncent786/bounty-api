from social_scraper.investing.qualification import (
    BEHAVIOUR_PHRASES,
    _behavior_applies_to_anchor,
    qualify_candidate,
)


def _evidence(eid, author, text, platform="x", engagement=None, created_at=None):
    if created_at is None:
        suffix = int(str(eid)[-1]) if str(eid)[-1:].isdigit() else 1
        created_at = f"2026-08-{min(26, 2 + (suffix - 1) * 8):02d}T00:00:00Z"
    return {
        "id": eid,
        "external_id": eid,
        "platform": platform,
        "author": author,
        "text": text,
        "url": f"https://example.com/{eid}",
        "created_at": created_at,
        "engagement": engagement if engagement is not None else {
            "views": 1000, "likes": 20, "comments": 5, "shares": 1,
        },
    }


def _proposal(**overrides):
    value = {
        "label": "Silicone air fryer liners replacing paper liners",
        "behaviour_type": "switching",
        "anchor_terms": ["silicone air fryer liner"],
        "summary": "Consumers describe switching to a reusable silicone air fryer liner.",
        "economic_mechanism": "Reusable accessory demand may shift kitchen-accessory unit mix.",
        "why_investigate": "The checked social sample shows a recent cluster of switching behavior.",
        "contradiction": "Some users report worse crisping and return to paper liners.",
        "invalidation": "Reject if later samples show the discussion came from one promotion or quickly reverses.",
        "evidence_ids": ["e1", "e2", "e3"],
    }
    value.update(overrides)
    return value


def _windows(current=5, prior=(1, 1, 0), capped=False):
    ranges = (
        ("2026-08-19", "2026-08-26"),
        ("2026-08-12", "2026-08-19"),
        ("2026-08-05", "2026-08-12"),
        ("2026-07-29", "2026-08-05"),
    )
    values = []
    for index, count in enumerate((current, *prior)):
        values.append({
            "window_key": "current" if index == 0 else f"prior_{index}",
            "start_date": ranges[index][0],
            "end_date": ranges[index][1],
            "anchor_query": '"silicone air fryer liner"',
            "status": "complete",
            "result_count": count,
            "unique_authors": count,
            "capped": capped,
        })
    return values


def test_candidate_qualifies_only_when_every_gate_passes():
    evidence = [
        _evidence("e1", "a", "I switched to a silicone air fryer liner"),
        _evidence("e2", "b", "We switched to silicone air fryer liners", "instagram"),
        _evidence("e3", "c", "I switched to another silicone air fryer liner", "youtube"),
    ]
    result = qualify_candidate(
        _proposal(), evidence=evidence, windows=_windows(),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )
    assert result["qualification_status"] == "qualified"
    assert all(gate["passed"] is True for gate in result["gates"].values())
    assert result["candidate_id"]


def test_behavior_locality_keeps_comma_separated_product_lists_in_one_claim():
    text = "How I replaced Netflix, Hulu, Apple TV, Paramount Plus, HBO Max with a homeserver"
    assert _behavior_applies_to_anchor(
        text,
        ["replaced netflix hulu apple tv paramount plus hbo max"],
        BEHAVIOUR_PHRASES["switching"],
        "switching",
    ) is True


def test_switch_from_switch_to_mold_and_avoid_language_are_valid_behavior():
    cases = [
        (
            "I've officially switched from iPhone to the Samsung Galaxy Fold 8",
            ["iphone", "samsung galaxy fold 8"],
            "switching",
        ),
        (
            "Making the choice to switch to Branch Basics for our cleaning products",
            ["branch basics"],
            "switching",
        ),
        (
            "Let's clean my mold infested auto pet feeder",
            ["mold infested auto pet feeder"],
            "pain_point",
        ),
        (
            "Avoid this airline after they charged for every extra",
            ["avoid this airline"],
            "rejection",
        ),
    ]
    for text, anchors, behavior in cases:
        assert _behavior_applies_to_anchor(
            text,
            anchors,
            BEHAVIOUR_PHRASES[behavior],
            behavior,
        ) is True, text


def test_new_behavior_phrases_do_not_attach_to_a_different_nearby_object():
    assert _behavior_applies_to_anchor(
        "I switched from Coke near silicone air fryer liners in the store.",
        ["silicone air fryer liner"],
        BEHAVIOUR_PHRASES["switching"],
        "switching",
    ) is False
    assert _behavior_applies_to_anchor(
        "I switched from Coke to Pepsi near silicone air fryer liner displays.",
        ["silicone air fryer liner"],
        BEHAVIOUR_PHRASES["switching"],
        "switching",
    ) is False
    for text in (
        "I switched to a / silicone air fryer liner",
        "I switched to a — silicone air fryer liner",
    ):
        assert _behavior_applies_to_anchor(
            text,
            ["silicone air fryer liner"],
            BEHAVIOUR_PHRASES["switching"],
            "switching",
        ) is False, text

    for text in (
        "The acme widget was mentioned, the shipping box was broken.",
        "Acme widget is here: the shipping box is broken.",
        "The acme widget is not broken, but the shipping box is broken.",
    ):
        assert _behavior_applies_to_anchor(
            text,
            ["acme widget"],
            BEHAVIOUR_PHRASES["pain_point"],
            "pain_point",
        ) is False, text


def test_comments_reposts_and_one_copy_cluster_cannot_inflate_independent_support():
    evidence = [
        {
            **_evidence("e1", "a", "I switched to a silicone air fryer liner", "x"),
            "record_type": "root",
            "copy_cluster_id": "copy-1",
        },
        {
            **_evidence("e2", "b", "We switched to silicone air fryer liners today", "instagram"),
            "record_type": "root",
            "copy_cluster_id": "copy-1",
        },
        {
            **_evidence("e3", "c", "I switched to another silicone air fryer liner", "youtube"),
            "record_type": "comment",
            "root_post_external_id": "e1",
        },
        {
            **_evidence("e4", "d", "I switched to a silicone air fryer liner", "tiktok"),
            "record_type": "root",
            "is_repost": True,
        },
    ]
    result = qualify_candidate(
        _proposal(evidence_ids=["e1", "e2", "e3", "e4"]),
        evidence=evidence,
        windows=_windows(),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )

    assert result["qualification_status"] == "not_qualified"
    assert result["gates"]["behavior"]["metrics"]["records"] == 1
    assert result["gates"]["breadth"]["metrics"]["roots"] == 1
    assert result["citation_filter"]["propagation_dropped"] == 1
    assert result["citation_filter"]["non_root_or_repost_dropped"] == 2


def test_generic_ai_and_model_failure_never_qualify():
    evidence = [
        _evidence("e1", "a", "AI is big"),
        _evidence("e2", "b", "AI keeps growing"),
        _evidence("e3", "c", "Bought AI software"),
    ]
    result = qualify_candidate(
        _proposal(label="AI", anchor_terms=["AI"]),
        evidence=evidence, windows=_windows(),
        parity={"level": "L5", "status": "consensus", "articles": []},
    )
    assert result["qualification_status"] == "not_qualified"
    assert result["gates"]["specificity"]["passed"] is False
    assert result["gates"]["parity"]["passed"] is False


def test_behavior_must_apply_to_the_anchor_not_merely_share_a_citation():
    evidence = [
        _evidence(
            "e1",
            "a",
            "I switched to oat milk and later reviewed a silicone air fryer liner.",
        ),
        _evidence(
            "e2",
            "b",
            "We switched to reusable bottles; a silicone air fryer liner was mentioned.",
            "instagram",
        ),
        _evidence(
            "e3",
            "c",
            "The silicone air fryer liner appears in this kitchen video.",
            "youtube",
        ),
    ]

    result = qualify_candidate(
        _proposal(), evidence=evidence, windows=_windows(),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )

    assert result["qualification_status"] == "not_qualified"
    assert result["gates"]["behavior"]["passed"] is False
    assert result["gates"]["behavior"]["metrics"]["records"] == 0


def test_behavior_rejects_a_nearby_but_different_object_in_the_same_clause():
    evidence = [
        _evidence(
            "e1",
            "a",
            "I switched to Coke near silicone air fryer liners in the store.",
        ),
        _evidence(
            "e2",
            "b",
            "We switched to Pepsi beside silicone air fryer liner displays.",
            "instagram",
        ),
        _evidence(
            "e3",
            "c",
            "A silicone air fryer liner display was visible.",
            "youtube",
        ),
    ]

    result = qualify_candidate(
        _proposal(), evidence=evidence, windows=_windows(),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )

    assert result["qualification_status"] == "not_qualified"
    assert result["gates"]["behavior"]["passed"] is False
    assert result["gates"]["behavior"]["metrics"]["records"] == 0


def test_behavior_rejects_punctuation_and_modifier_bridge_bypasses():
    variants = (
        "Switched to Premium, this silicone air fryer liner was nearby.",
        "Switched to another, that silicone air fryer liner was nearby.",
        "Switched to a replacement, the silicone air fryer liner was nearby.",
        "I switched to a silicone air fryer liner poster.",
    )
    for text in variants:
        evidence = [
            _evidence("e1", "a", text),
            _evidence("e2", "b", text, "instagram"),
            _evidence(
                "e3",
                "c",
                "A silicone air fryer liner display was visible.",
                "youtube",
            ),
        ]

        result = qualify_candidate(
            _proposal(), evidence=evidence, windows=_windows(),
            parity={"level": "L1", "status": "niche_coverage", "articles": []},
        )

        assert result["qualification_status"] == "not_qualified", text
        assert result["gates"]["behavior"]["passed"] is False, text
        assert result["gates"]["behavior"]["metrics"]["records"] == 0, text


def test_capped_or_missing_history_is_unknown_not_anomaly():
    evidence = [
        _evidence("e1", "a", "I switched to a silicone air fryer liner"),
        _evidence("e2", "b", "We switched to silicone air fryer liners", "instagram"),
        _evidence("e3", "c", "I switched to another silicone air fryer liner", "youtube"),
    ]
    result = qualify_candidate(
        _proposal(), evidence=evidence, windows=_windows(capped=True),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )
    assert result["qualification_status"] == "unknown_due_to_coverage"
    assert result["gates"]["anomaly"]["state"] == "unknown"


def test_financial_coverage_or_single_voice_rejects_candidate():
    evidence = [
        _evidence("e1", "same", "I switched to a silicone air fryer liner"),
        _evidence("e2", "same", "We switched to silicone air fryer liners"),
        _evidence("e3", "same", "Bought another silicone air fryer liner"),
    ]
    result = qualify_candidate(
        _proposal(), evidence=evidence, windows=_windows(),
        parity={
            "level": "L3.5",
            "status": "financial_coverage",
            "articles": [{"source": "Reuters", "title": "Retailers benefit from air fryer accessories"}],
        },
    )
    assert result["qualification_status"] == "not_qualified"
    assert result["gates"]["breadth"]["passed"] is False
    assert result["gates"]["parity"]["passed"] is False


def test_two_creator_posts_on_one_platform_are_not_enough_to_prove_a_trend():
    evidence = [
        _evidence(
            "e1",
            "a",
            "Part 5 of replacing streaming services with physical media.",
            "tiktok",
        ),
        _evidence(
            "e2",
            "b",
            "Cancelling Netflix to buy DVDs, months 1 through 4.",
            "tiktok",
        ),
    ]
    result = qualify_candidate(
        _proposal(
            label="Replacing streaming subscriptions with physical media",
            anchor_terms=[
                "replacing streaming services with physical media",
                "cancelling Netflix to buy DVDs",
            ],
            summary="People are replacing streaming with physical media and cancelling Netflix for DVDs.",
            evidence_ids=["e1", "e2"],
        ),
        evidence=evidence,
        windows=_windows(),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )

    assert result["gates"]["behavior"]["state"] == "pass"
    assert result["gates"]["behavior"]["metrics"]["authors"] == 2
    assert result["gates"]["breadth"]["state"] == "pass"
    assert result["gates"]["breadth"]["metrics"]["cross_platform"] is False
    assert result["gates"]["evidence_quality"]["state"] == "fail"
    assert result["gates"]["evidence_quality"]["metrics"]["firsthand_authors"] == 2
    assert result["gates"]["investigability"]["state"] == "pass"
    assert result["qualification_status"] == "not_qualified"


def test_home_gym_causal_language_counts_as_switching_behavior():
    evidence = [
        _evidence(
            "e1",
            "a",
            "It was hell trying to cancel the membership. I bought what I need and now have a true home gym.",
            "x",
        ),
        _evidence(
            "e2",
            "b",
            "I haven't been to the gym ever since I bought a home dumbbell set and a bench.",
            "x",
        ),
    ]
    result = qualify_candidate(
        _proposal(
            label="Gym cancellations pushing home gym adoption",
            anchor_terms=[
                "true home gym",
                "bought a home dumbbell set and a bench",
            ],
            summary="People are leaving gym memberships after building a home gym with dumbbells and a bench.",
            evidence_ids=["e1", "e2"],
        ),
        evidence=evidence,
        windows=_windows(),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )

    assert result["gates"]["behavior"]["metrics"]["records"] == 2
    assert result["gates"]["behavior"]["metrics"]["authors"] == 2
    assert result["gates"]["behavior"]["state"] == "pass"


def test_pain_language_can_follow_a_specific_anchor_without_ending_the_clause():
    evidence = [
        _evidence(
            "e1",
            "a",
            "Tesla hidden door releases are too hard to find after a crash, according to the recall.",
            "x",
        ),
        _evidence(
            "e2",
            "b",
            "The hidden door releases are a safety issue because people cannot find them after a crash.",
            "youtube",
        ),
    ]
    result = qualify_candidate(
        _proposal(
            label="Tesla hidden door release post-crash pain",
            behaviour_type="pain_point",
            anchor_terms=["hidden door releases", "after a crash"],
            summary="People report that hidden door releases are difficult to find after a crash.",
            evidence_ids=["e1", "e2"],
        ),
        evidence=evidence,
        windows=_windows(),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )

    assert result["gates"]["behavior"]["metrics"]["records"] == 2
    assert result["gates"]["behavior"]["state"] == "pass"


def test_news_and_recall_reporting_do_not_count_as_firsthand_behavior():
    evidence = [
        _evidence(
            "e1", "news-a",
            "BREAKING: Tesla recall ordered because hidden door releases are hard to find after a crash.",
            "x",
        ),
        _evidence(
            "e2", "news-b",
            "According to regulators, hidden door releases are a safety issue after a crash.",
            "youtube",
        ),
        _evidence(
            "e3", "news-c",
            "Daily news roundup: Tesla recalled vehicles over hidden door releases after a crash.",
            "x",
        ),
    ]
    result = qualify_candidate(
        _proposal(
            label="Tesla hidden door release post-crash pain",
            behaviour_type="pain_point",
            anchor_terms=["hidden door releases", "after a crash"],
            summary="Reports say hidden door releases are difficult to find after a crash.",
            evidence_ids=["e1", "e2", "e3"],
        ),
        evidence=evidence,
        windows=_windows(),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )

    quality = result["gates"]["evidence_quality"]
    assert quality["state"] == "fail"
    assert quality["metrics"]["firsthand_authors"] == 0
    assert quality["metrics"]["reportage_records"] == 3
    assert result["qualification_status"] == "not_qualified"


def test_firsthand_order_language_is_not_misclassified_as_news():
    evidence = [
        _evidence("e1", "a", "I ordered an acme travel mug", "x"),
        _evidence("e2", "b", "We ordered the acme travel mug", "instagram"),
        _evidence("e3", "c", "I ordered another acme travel mug", "youtube"),
    ]
    result = qualify_candidate(
        _proposal(
            label="Acme travel mug purchases",
            behaviour_type="purchase",
            anchor_terms=["acme travel mug"],
            summary="People describe ordering an acme travel mug.",
            evidence_ids=["e1", "e2", "e3"],
        ),
        evidence=evidence,
        windows=_windows(),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )

    assert result["gates"]["evidence_quality"]["state"] == "pass"
    assert result["gates"]["evidence_quality"]["metrics"]["reportage_records"] == 0


def test_same_handle_across_platforms_does_not_become_three_independent_voices():
    evidence = [
        _evidence("e1", "same_creator", "I switched to a silicone air fryer liner", "x"),
        _evidence("e2", "same_creator", "I switched to silicone air fryer liners", "instagram"),
        _evidence("e3", "same_creator", "I switched to another silicone air fryer liner", "youtube"),
    ]
    result = qualify_candidate(
        _proposal(),
        evidence=evidence,
        windows=_windows(),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )

    assert result["gates"]["behavior"]["metrics"]["authors"] == 1
    assert result["gates"]["evidence_quality"]["metrics"]["firsthand_authors"] == 1
    assert result["qualification_status"] == "not_qualified"


def test_three_cross_platform_but_unengaged_posts_still_fail_quality():
    low = {"views": 40, "likes": 1, "comments": 0, "shares": 0}
    evidence = [
        _evidence("e1", "a", "I switched to a silicone air fryer liner", "x", low),
        _evidence("e2", "b", "We switched to silicone air fryer liners", "instagram", low),
        _evidence("e3", "c", "I switched to another silicone air fryer liner", "youtube", low),
    ]
    result = qualify_candidate(
        _proposal(),
        evidence=evidence,
        windows=_windows(),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )

    quality = result["gates"]["evidence_quality"]
    assert quality["state"] == "fail"
    assert quality["metrics"]["known_engagement_records"] == 3
    assert quality["metrics"]["engaged_records"] == 0


def test_three_firsthand_engaged_voices_can_pass_evidence_quality():
    evidence = [
        _evidence("e1", "a", "I switched to a silicone air fryer liner", "x"),
        _evidence("e2", "b", "We switched to silicone air fryer liners", "instagram"),
        _evidence("e3", "c", "I switched to another silicone air fryer liner", "youtube"),
    ]
    result = qualify_candidate(
        _proposal(),
        evidence=evidence,
        windows=_windows(),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )

    quality = result["gates"]["evidence_quality"]
    assert quality["state"] == "pass"
    assert quality["metrics"]["firsthand_authors"] == 3
    assert quality["metrics"]["engaged_records"] == 3
    assert result["gates"]["persistence"]["state"] == "pass"
    assert result["social_pattern"] == "ongoing"
    assert result["qualification_status"] == "qualified"


def test_one_day_social_cluster_fails_persistence_even_with_engagement():
    evidence = [
        _evidence(
            "e1", "a", "I switched to a silicone air fryer liner", "x",
            created_at="2026-08-20T01:00:00Z",
        ),
        _evidence(
            "e2", "b", "We switched to silicone air fryer liners", "instagram",
            created_at="2026-08-20T08:00:00Z",
        ),
        _evidence(
            "e3", "c", "I switched to another silicone air fryer liner", "youtube",
            created_at="2026-08-20T22:00:00Z",
        ),
    ]

    result = qualify_candidate(
        _proposal(), evidence=evidence, windows=_windows(),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )

    persistence = result["gates"]["persistence"]
    assert persistence["state"] == "fail"
    assert persistence["metrics"]["active_weeks"] == 1
    assert persistence["metrics"]["span_days"] == 0
    assert result["social_pattern"] == "one_day_cluster"
    assert result["qualification_status"] == "not_qualified"


def test_negated_pain_language_does_not_count_as_behavior_support():
    evidence = [
        _evidence("e1", "a", "The acme widget is not the problem; the shipping box was.", "x"),
        _evidence("e2", "b", "The acme widget has no problem at all and works well.", "youtube"),
    ]
    result = qualify_candidate(
        _proposal(
            label="Acme widget reported pain point",
            behaviour_type="pain_point",
            anchor_terms=["acme widget"],
            summary="The acme widget may have a reported problem.",
            evidence_ids=["e1", "e2"],
        ),
        evidence=evidence,
        windows=_windows(),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )

    assert result["gates"]["behavior"]["metrics"]["records"] == 0
    assert result["gates"]["behavior"]["state"] == "fail"


def test_loyalty_ever_since_language_does_not_count_as_switching():
    evidence = [
        _evidence("e1", "a", "I have loved my acme widget ever since day one and will never switch.", "x"),
        _evidence("e2", "b", "My acme widget has been great ever since I bought it.", "youtube"),
        _evidence("e3", "c", "I haven't been disappointed with my acme widget ever since day one.", "tiktok"),
        _evidence("e4", "d", "I have not been let down by my acme widget ever since the update.", "instagram"),
    ]
    result = qualify_candidate(
        _proposal(
            label="Acme widget consumer switching",
            anchor_terms=["acme widget"],
            summary="People may be switching to the acme widget.",
            evidence_ids=["e1", "e2", "e3", "e4"],
        ),
        evidence=evidence,
        windows=_windows(),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )

    assert result["gates"]["behavior"]["metrics"]["records"] == 0
    assert result["gates"]["behavior"]["state"] == "fail"


def test_summary_anchor_requires_a_contiguous_specific_phrase():
    evidence = [
        _evidence("e1", "a", "The hidden door releases are a problem.", "x"),
        _evidence("e2", "b", "Hidden door releases have a safety issue.", "youtube"),
    ]
    result = qualify_candidate(
        _proposal(
            label="Hidden door releases safety pain",
            behaviour_type="pain_point",
            anchor_terms=["hidden door releases"],
            summary="Hidden costs hit door sales while new releases slip.",
            evidence_ids=["e1", "e2"],
        ),
        evidence=evidence,
        windows=_windows(),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )

    assert result["gates"]["investigability"]["state"] == "fail"
    assert result["qualification_status"] == "not_qualified"


def test_candidate_evidence_drops_records_matching_only_a_weak_anchor():
    evidence = [
        _evidence(
            "sale",
            "retail-watcher",
            "St Michael and Aries promoted a green bag that was put live early and instantly sold out.",
            "x",
        ),
        _evidence(
            "church",
            "traveller",
            "I visited the Catholic Church of St Michael while travelling.",
            "tiktok",
        ),
        _evidence(
            "faith",
            "reader",
            "A history of St Michael churches and first masses.",
            "reddit",
        ),
    ]
    result = qualify_candidate(
        _proposal(
            label="St Michael Aries green bag sellout",
            behaviour_type="shortage",
            anchor_terms=["St Michael", "Aries", "green bag", "instantly sold out"],
            summary="The St Michael Aries green bag reportedly sold out.",
            evidence_ids=["sale", "church", "faith"],
        ),
        evidence=evidence,
        windows=_windows(),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )

    assert result["evidence_ids"] == ["sale"]
    assert result["gates"]["behavior"]["metrics"]["records"] == 1
    assert result["gates"]["breadth"]["metrics"]["authors"] == 1
    assert result["qualification_status"] == "not_qualified"
