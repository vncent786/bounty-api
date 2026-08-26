from social_scraper.investing.qualification import qualify_candidate


def _evidence(eid, author, text, platform="x"):
    return {
        "id": eid,
        "external_id": eid,
        "platform": platform,
        "author": author,
        "text": text,
        "url": f"https://example.com/{eid}",
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
        _evidence("e3", "c", "Bought another silicone air fryer liner", "youtube"),
    ]
    result = qualify_candidate(
        _proposal(), evidence=evidence, windows=_windows(),
        parity={"level": "L1", "status": "niche_coverage", "articles": []},
    )
    assert result["qualification_status"] == "qualified"
    assert all(gate["passed"] is True for gate in result["gates"].values())
    assert result["candidate_id"]


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
        _evidence("e3", "c", "Bought another silicone air fryer liner", "youtube"),
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
