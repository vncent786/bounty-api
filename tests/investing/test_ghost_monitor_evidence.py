"""Auditability regressions for displayed GHOST conversation and sentiment counts."""

from __future__ import annotations

import json
from pathlib import Path

from social_scraper.investing.live_tracker import build_investment_tracker


PLATFORMS = ("x", "tiktok", "instagram", "reddit", "youtube")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def evidence_row(platform: str, external_id: str, record_type: str = "root") -> dict:
    return {
        "platform": platform,
        "external_id": external_id,
        "record_type": record_type,
        "root_post_external_id": external_id.split("-")[0],
        "url": f"https://evidence.example/{platform}/{external_id}",
        "author": f"{platform}-author",
        "created_at": "2026-09-06T00:00:00Z",
        "text": f"Exact {platform} evidence {external_id}",
    }


def build_auditable_fixture(root: Path) -> dict:
    ghost = root / "artifacts" / "dd" / "ghost-kdp"
    all_retained_path = ghost / "conversation-runs" / "all-retained.json"
    deep_comments_path = ghost / "conversation-runs" / "deep-comments.json"
    review_path = ghost / "conversation-runs" / "deep-review.json"
    sentiment_path = ghost / "conversation-runs" / "sentiment-review.json"

    all_retained = {
        "platforms": {
            "x": {
                "originals": [{
                    **evidence_row("x", "x-root"),
                    "text_snippet": "Exact X post",
                    "content_origin": "possible_organic",
                    "counts_as_independent_behavior": True,
                }],
                "comments_replies": [{
                    **evidence_row("x", "x-root-reply", "reply"),
                    "text_snippet": "Exact X reply",
                }],
            },
            "tiktok": {
                "originals": [{
                    **evidence_row("tiktok", "tt-root"),
                    "text_snippet": "Exact TikTok post",
                    "content_origin": "possible_organic",
                    "counts_as_independent_behavior": True,
                }],
                "comments_replies": [],
            },
            "instagram": {
                "originals": [{
                    **evidence_row("instagram", "ig-root"),
                    "text_snippet": "Exact Instagram post",
                    "content_origin": "retailer_promotional",
                    "counts_as_independent_behavior": False,
                }],
                "comments_replies": [{
                    **evidence_row("instagram", "ig-root-comment", "comment"),
                    "text_snippet": "Exact Instagram comment",
                }],
            },
        }
    }
    write_json(all_retained_path, all_retained)

    deep_comments = {
        "evidence": [
            evidence_row("reddit", "reddit-root"),
            evidence_row("youtube", "youtube-root"),
            evidence_row("youtube", "youtube-root-comment", "comment"),
        ]
    }
    write_json(deep_comments_path, deep_comments)
    write_json(review_path, {
        "source_artifacts": [{"path": str(deep_comments_path.relative_to(root)).replace("\\", "/")}]
    })
    write_json(sentiment_path, {
        "scope": "Sentiment in the linked original-post sample.",
        "counts": {"positive": 1, "negative": 0, "neutral": 0, "mixed": 0, "unclassified": 1},
        "total_exact_roots": 2,
        "reviews": [
            {
                "platform": "x",
                "external_id": "x-root",
                "url": "https://evidence.example/x/x-root",
                "label": "positive",
                "basis": "The post expresses clear approval.",
            },
            {
                "platform": "youtube",
                "external_id": "youtube-root",
                "url": "https://evidence.example/youtube/youtube-root",
                "label": "unclassified",
                "basis": "The captured title contains no verdict.",
            },
        ],
    })

    conversation = {
        "state": "CONVERSATION_BUILDING_BASELINE",
        "comparable_scheduled_runs": 1,
        "platform_canary_matrix": {platform: {"status": "healthy"} for platform in PLATFORMS},
        "candidate_platform_queries": {
            platform: {"candidate_query_status": "complete_relevant"}
            for platform in PLATFORMS
        },
        "comments_replies_by_platform": {
            "x": 1,
            "tiktok": 0,
            "instagram": 1,
            "reddit": 0,
            "youtube": 1,
        },
        "origin_review": {
            "raw_exact_roots_by_platform": {platform: 1 for platform in PLATFORMS},
            "qualifying_independent_roots_by_platform": {
                "x": 1,
                "tiktok": 1,
                "instagram": 0,
                "reddit": 0,
                "youtube": 0,
            },
        },
        "sentiment": {
            "status": "partial",
            "counts": {"positive": 9, "negative": 9, "neutral": 9, "mixed": 9, "unclassified": 9},
            "sample_denominator": 45,
            "note": "Legacy aggregate without per-record classifications.",
        },
    }
    attention = {
        "observed_at": "2026-09-06T01:00:00Z",
        "search_attention": {},
        "conversation_attention": conversation,
        "artifacts": {
            "all_retained_reconciliation": str(all_retained_path.relative_to(root)).replace("\\", "/"),
            "deep_social_review": str(review_path.relative_to(root)).replace("\\", "/"),
            "sentiment_review": str(sentiment_path.relative_to(root)).replace("\\", "/"),
        },
    }
    write_json(ghost / "attention_latest.json", attention)
    write_jsonl(ghost / "attention_history.jsonl", [attention])
    write_json(ghost / "coverage_latest.json", {
        "observed_at": "2026-09-06T01:00:00Z",
        "parity_state": "NICHE_ONLY",
    })

    tracker = build_investment_tracker(root)
    return next(
        item["monitor_dashboard"]
        for item in tracker["ideas"]
        if item.get("idea_id") == "standing::ghost-aw-kdp"
    )


def test_displayed_post_and_comment_counts_reconcile_to_complete_linked_evidence(tmp_path):
    monitor = build_auditable_fixture(tmp_path)
    conversations = monitor["conversations"]
    evidence = conversations["evidence"]

    assert conversations["headline"] == (
        "5 exact posts plus 3 comments/replies observed across 5 successful platform reads."
    )
    assert evidence["status"] == "verified"
    assert evidence["displayed_counts"] == {"original_posts": 5, "comments_replies": 3}
    assert evidence["persisted_link_counts"] == {"original_posts": 5, "comments_replies": 3}
    assert evidence["total_clickable_links"] == 8
    assert all(
        row["count_status"] == "verified"
        for row in evidence["platforms"].values()
    )
    assert len(evidence["platforms"]["youtube"]["comments_replies"]) == 1
    assert evidence["platforms"]["instagram"]["original_posts"][0]["content_origin"] == "retailer_promotional"


def test_fully_linked_sentiment_review_replaces_unverifiable_legacy_aggregate(tmp_path):
    sentiment = build_auditable_fixture(tmp_path)["conversations"]["sentiment"]

    assert sentiment["status"] == "complete_linked_sample"
    assert sentiment["classification_unit"] == "original_posts"
    assert sentiment["sample_denominator"] == 2
    assert sentiment["counts"] == {
        "positive": 1,
        "negative": 0,
        "neutral": 0,
        "mixed": 0,
        "unclassified": 1,
    }
    assert len(sentiment["evidence"]) == 2
    assert sentiment["evidence"][0]["basis"]
    assert sentiment["coverage_note"] == (
        "2 of 5 displayed original posts have linked classifications. The remaining 3 are unreviewed, not neutral."
    )
    assert sentiment["withdrawn_aggregate"]["status"] == "not_displayed_missing_record_level_lineage"


def test_linked_comment_sentiment_uses_only_exact_product_rows(tmp_path):
    build_auditable_fixture(tmp_path)
    ghost = tmp_path / "artifacts" / "dd" / "ghost-kdp"
    sentiment_path = ghost / "conversation-runs" / "sentiment-review.json"
    write_json(sentiment_path, {
        "schema_version": "bounty-ghost-social-sentiment-evidence/1",
        "method": {"claim_boundary": "Classified captured responses from persisted text."},
        "summary": {
            "reviewed_product_relevant_comments_replies": 2,
            "sentiment": {
                "positive": 1,
                "negative": 1,
                "neutral": 0,
                "mixed": 0,
                "unclassified": 0,
            },
        },
        "reviewed_rows": [
            {
                "platform": "x",
                "external_id": "x-reply",
                "record_type": "reply",
                "url": "https://evidence.example/x/x-reply",
                "relevance": "exact_product",
                "sentiment": "positive",
                "rationale": "Exact positive response.",
            },
            {
                "platform": "instagram",
                "external_id": "ig-comment",
                "record_type": "comment",
                "url": "https://evidence.example/instagram/ig-comment",
                "relevance": "exact_product",
                "sentiment": "negative",
                "rationale": "Exact negative response.",
            },
            {
                "platform": "youtube",
                "external_id": "yt-adjacent",
                "record_type": "comment",
                "url": "https://evidence.example/youtube/yt-adjacent",
                "relevance": "adjacent_general_ghost_or_rootbeer",
                "sentiment": "positive",
                "rationale": "Not about the exact product.",
            },
        ],
    })

    tracker = build_investment_tracker(tmp_path)
    monitor = next(
        item["monitor_dashboard"]
        for item in tracker["ideas"]
        if item.get("idea_id") == "standing::ghost-aw-kdp"
    )
    sentiment = monitor["conversations"]["sentiment"]

    assert sentiment["classification_unit"] == "comments_replies"
    assert sentiment["sample_denominator"] == 2
    assert sentiment["counts"] == {
        "positive": 1,
        "negative": 1,
        "neutral": 0,
        "mixed": 0,
        "unclassified": 0,
    }
    assert [row["external_id"] for row in sentiment["evidence"]] == [
        "x-reply",
        "ig-comment",
    ]
    assert sentiment["coverage_note"] == (
        "2 of 3 displayed comments/replies have linked exact-product classifications. "
        "The remaining 1 is adjacent, unrelated or unreviewed, not neutral."
    )
    assert "withdrawn_aggregate" not in sentiment


def test_missing_or_mismatched_evidence_never_claims_reconciliation(tmp_path):
    monitor = build_auditable_fixture(tmp_path)
    ghost = tmp_path / "artifacts" / "dd" / "ghost-kdp"
    all_retained_path = ghost / "conversation-runs" / "all-retained.json"
    payload = json.loads(all_retained_path.read_text(encoding="utf-8"))
    payload["platforms"]["x"]["comments_replies"] = []
    write_json(all_retained_path, payload)

    tracker = build_investment_tracker(tmp_path)
    monitor = next(
        item["monitor_dashboard"]
        for item in tracker["ideas"]
        if item.get("idea_id") == "standing::ghost-aw-kdp"
    )

    evidence = monitor["conversations"]["evidence"]
    assert evidence["status"] == "mismatch"
    assert evidence["platforms"]["x"]["count_status"] == "mismatch"
    assert evidence["platforms"]["x"]["displayed_comments_replies"] == 1
    assert evidence["platforms"]["x"]["linked_comments_replies"] == 0


def test_daily_parity_chart_keeps_only_latest_success_and_hides_failed_attempts(tmp_path):
    build_auditable_fixture(tmp_path)
    ghost = tmp_path / "artifacts" / "dd" / "ghost-kdp"
    rows = [
        {
            "observed_at": "2026-09-06T01:00:00Z",
            "parity_state": "SOURCE_FAILURE",
            "qualifying_independent_business_financial_outlet_count": 0,
            "management_acknowledges_a_and_w_economics": False,
        },
        {
            "observed_at": "2026-09-06T02:00:00Z",
            "parity_state": "EXTENSIVE_COVERAGE_REVIEW",
            "qualifying_independent_business_financial_outlet_count": 0,
            "management_acknowledges_a_and_w_economics": True,
        },
        {
            "observed_at": "2026-09-06T03:00:00Z",
            "parity_state": "NICHE_ONLY",
            "qualifying_independent_business_financial_outlet_count": 0,
            "management_acknowledges_a_and_w_economics": False,
        },
    ]
    write_jsonl(ghost / "coverage_history.jsonl", rows)
    write_json(ghost / "coverage_latest.json", rows[-1])

    tracker = build_investment_tracker(tmp_path)
    monitor = next(
        item["monitor_dashboard"]
        for item in tracker["ideas"]
        if item.get("idea_id") == "standing::ghost-aw-kdp"
    )

    assert monitor["street_coverage"]["history"] == [{
        "observed_at": "2026-09-06T03:00:00Z",
        "state": "NICHE_ONLY",
        "qualifying_outlets": 0,
        "management_acknowledged": False,
    }]


def test_failed_latest_parity_attempt_retains_last_verified_read_and_exposes_gap(tmp_path):
    build_auditable_fixture(tmp_path)
    ghost = tmp_path / "artifacts" / "dd" / "ghost-kdp"
    success = {
        "observed_at": "2026-09-06T02:00:00Z",
        "parity_state": "NICHE_ONLY",
        "qualifying_independent_business_financial_outlet_count": 0,
        "management_acknowledges_a_and_w_economics": False,
    }
    failure = {
        "observed_at": "2026-09-06T03:00:00Z",
        "parity_state": "SOURCE_FAILURE",
        "qualifying_independent_business_financial_outlet_count": 0,
        "management_acknowledges_a_and_w_economics": False,
        "source_health": {"source_gaps": ["regulator_filings"]},
    }
    write_jsonl(ghost / "coverage_history.jsonl", [success, failure])
    write_json(ghost / "coverage_latest.json", failure)

    tracker = build_investment_tracker(tmp_path)
    monitor = next(
        item["monitor_dashboard"]
        for item in tracker["ideas"]
        if item.get("idea_id") == "standing::ghost-aw-kdp"
    )

    coverage = monitor["street_coverage"]
    assert coverage["state"] == "NICHE_ONLY"
    assert coverage["observed_at"] == "2026-09-06T02:00:00Z"
    assert coverage["source_health"] == {
        "latest_attempt_state": "SOURCE_FAILURE",
        "latest_attempt_observed_at": "2026-09-06T03:00:00Z",
        "visible_read_uses_last_verified": True,
        "source_gaps": ["regulator_filings"],
    }


def test_late_legacy_writer_cannot_overwrite_the_registered_public_parity_pointer(tmp_path):
    build_auditable_fixture(tmp_path)
    ghost = tmp_path / "artifacts" / "dd" / "ghost-kdp"
    accepted = {
        "schema_version": "bounty-public-information-parity/1",
        "observed_at": "2026-09-06T02:00:00Z",
        "parity_state": "NICHE_ONLY",
        "qualifying_independent_business_financial_outlet_count": 0,
        "management_acknowledges_a_and_w_economics": False,
        "writer": {
            "writer_id": "scripts/run_ghost_thesis_monitor.py",
            "writer_version": 1,
            "owns_latest_pointer": "artifacts/dd/ghost-kdp/coverage_latest.json",
        },
    }
    late_legacy = {
        "schema_version": "ghost-kdp-coverage-observation/1",
        "observed_at": "2026-09-06T03:00:00Z",
        "parity_state": "EXTENSIVE_COVERAGE_REVIEW",
        "qualifying_independent_business_financial_outlet_count": 9,
        "management_acknowledges_a_and_w_economics": True,
    }
    write_jsonl(ghost / "coverage_history.jsonl", [accepted, late_legacy])
    write_json(ghost / "coverage_latest.json", late_legacy)

    tracker = build_investment_tracker(tmp_path)
    monitor = next(
        item["monitor_dashboard"]
        for item in tracker["ideas"]
        if item.get("idea_id") == "standing::ghost-aw-kdp"
    )

    coverage = monitor["street_coverage"]
    assert coverage["observed_at"] == accepted["observed_at"]
    assert coverage["state"] == "NICHE_ONLY"
    assert coverage["qualifying_outlets"] == 0
    assert coverage["management_acknowledged"] is False
    assert [row["observed_at"] for row in coverage["history"]] == [accepted["observed_at"]]
    assert coverage["source_health"]["visible_read_uses_last_verified"] is True


def test_failed_latest_social_attempt_keeps_last_fully_linked_observation_visible(tmp_path):
    build_auditable_fixture(tmp_path)
    ghost = tmp_path / "artifacts" / "dd" / "ghost-kdp"
    attention_path = ghost / "attention_latest.json"
    accepted = json.loads(attention_path.read_text(encoding="utf-8"))
    failed = json.loads(json.dumps(accepted))
    failed["observed_at"] = "2026-09-07T03:08:21Z"
    failed["artifacts"] = {"social_manifest": "missing-manifest.json"}
    conversation = failed["conversation_attention"]
    conversation["operational_state"] = "source_failure"
    conversation["platform_canary_matrix"]["tiktok"]["status"] = "failed"
    conversation["origin_review"]["raw_exact_roots_by_platform"] = {
        platform: 11 for platform in PLATFORMS
    }
    conversation["comments_replies_by_platform"] = {
        platform: 102 for platform in PLATFORMS
    }
    write_jsonl(ghost / "attention_history.jsonl", [accepted, failed])
    write_json(attention_path, failed)

    tracker = build_investment_tracker(tmp_path)
    monitor = next(
        item["monitor_dashboard"]
        for item in tracker["ideas"]
        if item.get("idea_id") == "standing::ghost-aw-kdp"
    )

    conversations = monitor["conversations"]
    assert conversations["headline"] == (
        "5 exact posts plus 3 comments/replies observed across 5 successful platform reads."
    )
    assert conversations["evidence"]["status"] == "verified"
    assert conversations["evidence"]["total_clickable_links"] == 8
    assert [row["observed_at"] for row in conversations["history"]] == [
        accepted["observed_at"]
    ]
    assert conversations["source_health"] == {
        "latest_attempt_observed_at": failed["observed_at"],
        "latest_attempt_state": "source_failure",
        "visible_observed_at": accepted["observed_at"],
        "visible_read_uses_last_verified": True,
    }
