from social_scraper.discovery.ranking import features_from_analysis, rank_for_lens
from social_scraper.lenses import LensCriterion, ResearchLensSpec


def lens(feature):
    return ResearchLensSpec(
        lens_id=f"custom-{feature}", name="Custom", version="1", objective="test",
        criteria=(LensCriterion(
            criterion_id=feature, label=feature, feature_key=feature,
            mode="score", weight=1.0,
        ),),
    )


def candidate(candidate_id, signal_kind, voices):
    return {
        "candidate_id": candidate_id,
        "conversation_analysis": {
            "status": "supported",
            "behavior_type": "observed_action",
            "independent_voice_count": voices,
            "durability_evidence": [],
            "signals": [{"kind": signal_kind, "independent_voices": voices}],
        },
    }


def test_active_user_lens_changes_order_without_dropping_candidates():
    rows = [
        candidate("sports-event", "narrative", 4),
        candidate("consumer-switching", "switching", 2),
    ]
    voice_rank = rank_for_lens(rows, lens("independent_voices"))
    switch_rank = rank_for_lens(rows, lens("switching"))

    assert [x["candidate_id"] for x in voice_rank] == [
        "sports-event", "consumer-switching"
    ]
    assert [x["candidate_id"] for x in switch_rank] == [
        "consumer-switching", "sports-event"
    ]
    assert len(voice_rank) == len(switch_rank) == 2


def test_feature_extraction_keeps_unknowns_missing():
    features = features_from_analysis({"status": "sources_unavailable"})
    assert features["behavior_evidence"] is None
    assert features["independent_voices"] is None
    assert features["switching"] is None
