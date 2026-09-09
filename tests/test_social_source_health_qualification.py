"""Contract tests for the five-platform source-only qualification runner."""

from scripts.qualify_social_source_health import (
    CANARIES,
    REQUIRED_PLATFORMS,
    SCALE_QUERIES,
    classify_failure_layer,
    qualification_passed,
)


def test_qualification_covers_all_required_platforms_with_known_positive_queries():
    assert REQUIRED_PLATFORMS == ("x", "tiktok", "instagram", "reddit", "youtube")
    assert CANARIES == {
        "x": "nike",
        "tiktok": "nike",
        "instagram": "#nike",
        "reddit": "running shoes",
        "youtube": "iphone",
    }
    assert set(SCALE_QUERIES) == set(REQUIRED_PLATFORMS)
    assert all(SCALE_QUERIES[platform] for platform in REQUIRED_PLATFORMS)


def test_failure_classifier_separates_rate_limit_busy_auth_timeout_and_empty():
    assert classify_failure_layer("x_rate_limited") == "upstream_rate_limit"
    assert classify_failure_layer("x_daily_budget_exhausted") == "local_budget"
    assert classify_failure_layer("tiktok_profile_busy") == "profile_concurrency"
    assert classify_failure_layer("ig_session_expired") == "credential_or_session"
    assert classify_failure_layer("youtube_timeout") == "operation_timeout"
    assert classify_failure_layer("tiktok_empty_response") == "parser_or_response_shape"
    assert classify_failure_layer("complete_no_match") == "candidate_no_match"
    assert classify_failure_layer(None) == "none"


def test_qualification_requires_two_complete_cycles_and_never_accepts_partial():
    healthy_cycle = {
        "platforms": {
            platform: {
                "preflight_status": "healthy",
                "scale_status": "complete",
                "failure_layer": "none",
            }
            for platform in REQUIRED_PLATFORMS
        }
    }
    assert qualification_passed([healthy_cycle, healthy_cycle], required_cycles=2)

    partial = {
        "platforms": {
            **healthy_cycle["platforms"],
            "tiktok": {
                "preflight_status": "healthy",
                "scale_status": "partial",
                "failure_layer": "parser_or_response_shape",
            },
        }
    }
    assert not qualification_passed([healthy_cycle, partial], required_cycles=2)
    assert not qualification_passed([healthy_cycle], required_cycles=2)
