from copy import deepcopy

import pytest

from social_scraper.lenses import (
    LensCriterion,
    ResearchLensSpec,
    evaluate_lens,
)


CANDIDATE = {
    "candidate_id": "us:mexico-vs-usa-u20",
    "features": {
        "company_exposure": 0.0,
        "fx_relevance": 0.8,
        "event_uncertainty": 0.7,
        "independent_voices": 0.4,
    },
}


def spec(lens_id, feature, *, mode="score", minimum=None):
    return ResearchLensSpec(
        lens_id=lens_id,
        name=lens_id,
        version="1",
        objective="User-configured research relevance",
        criteria=(LensCriterion(
            criterion_id=feature,
            label=feature,
            feature_key=feature,
            mode=mode,
            weight=1.0,
            minimum=minimum,
            missing_policy="keep_unknown",
        ),),
    )


def test_same_event_can_rank_differently_under_different_user_lenses():
    original = deepcopy(CANDIDATE)
    investing = evaluate_lens(
        CANDIDATE,
        spec("investing", "company_exposure", mode="filter", minimum=0.5),
    )
    fx = evaluate_lens(CANDIDATE, spec("fx", "fx_relevance"))
    prediction = evaluate_lens(
        CANDIDATE, spec("prediction-markets", "event_uncertainty")
    )

    assert investing.status == "excluded"
    assert fx.status == "included" and fx.score == pytest.approx(0.8)
    assert prediction.status == "included" and prediction.score == pytest.approx(0.7)
    assert CANDIDATE == original


def test_missing_features_remain_reviewable_unless_user_explicitly_excludes():
    result = evaluate_lens(CANDIDATE, spec("supply-chain", "inventory_change"))
    assert result.status == "insufficient_evidence"
    assert result.score is None
    assert result.score_coverage == 0.0


def test_unregistered_feature_keys_are_rejected():
    bad = spec("unsafe", "__import__('os').system('x')")
    with pytest.raises(ValueError, match="unregistered feature"):
        evaluate_lens(CANDIDATE, bad)
