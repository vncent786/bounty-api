"""Regression tests for success-led GHOST monitor projection."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from social_scraper.investing.live_tracker import build_investment_tracker


QUERIES = ["ghost root beer energy drink", "ghost a&w root beer"]
PLATFORMS = ["x", "tiktok", "instagram", "reddit", "youtube"]


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def store(store_id: int, metro: str, status: str) -> dict:
    return {
        "record_key": f"walmart:{store_id}:zip:item",
        "status": status,
        "target_location_verified": status in {"orderable", "out_of_stock"},
        "product_identity_verified": status in {"orderable", "out_of_stock"},
        "requested_location": {
            "store_id": str(store_id),
            "metro": metro,
            "postal_code": "00000",
        },
    }


def build_fixture(root: Path, *, sentiment: dict | None = None) -> dict:
    ghost = root / "artifacts" / "dd" / "ghost-kdp"
    complete = {
        "observed_at": "2026-09-05T02:56:22Z",
        "coverage_status": "complete",
        "records": [store(0, "Sacramento", "orderable")]
        + [store(index, f"Store {index}", "out_of_stock") for index in range(1, 6)],
        "summary": {
            "orderable": 1,
            "out_of_stock": 5,
            "not_listed_at_store": 0,
            "unavailable": 0,
        },
        "restock_monitor": {"availability_state": "replenishment_started"},
    }
    failed = {
        "observed_at": "2026-09-06T03:27:35Z",
        "coverage_status": "partial",
        "records": [store(index, f"Store {index}", "unavailable_error") for index in range(6)],
        "summary": {
            "orderable": 0,
            "out_of_stock": 0,
            "not_listed_at_store": 0,
            "unavailable": 6,
        },
    }
    write_json(ghost / "walmart_native_latest.json", failed)
    write_jsonl(ghost / "walmart_native_history.jsonl", [complete, failed])

    dates = [(date(2026, 8, 16) + timedelta(days=index)).isoformat() for index in range(21)]
    conversation = {
        "state": "CONVERSATION_BUILDING_BASELINE",
        "comparable_scheduled_runs": 0,
        "platform_canary_matrix": {
            platform: {"status": "healthy"} for platform in PLATFORMS
        },
        "candidate_platform_queries": {
            platform: {
                "candidate_query_status": "complete_no_match"
                if platform == "instagram"
                else "complete"
            }
            for platform in PLATFORMS
        },
        "origin_review": {
            "raw_exact_roots_by_platform": {
                "x": 0,
                "tiktok": 0,
                "instagram": 0,
                "reddit": 5,
                "youtube": 5,
            },
            "qualifying_independent_roots_by_platform": {
                platform: 0 for platform in PLATFORMS
            },
        },
    }
    if sentiment is not None:
        conversation["sentiment"] = sentiment
    attention = {
        "observed_at": "2026-09-06T03:52:43Z",
        "search_attention": {
            "status": "SOURCE_FAILURE",
            "state": "SEARCH_BUILDING_BASELINE",
            "query_basket": QUERIES,
            "last_verified_observation": {
                "observed_at": "2026-09-05T02:51:56Z",
                "geographies": {
                    "US": {
                        "status": "complete",
                        "latest_complete_date": dates[-2],
                        "returned_values": {
                            "dates": dates,
                            QUERIES[0]: list(range(1, 22)),
                            QUERIES[1]: [10] * 21,
                        },
                        "isPartial_flags": [False] * 20 + [True],
                    }
                },
            },
        },
        "conversation_attention": conversation,
    }
    write_json(ghost / "attention_latest.json", attention)
    write_jsonl(ghost / "attention_history.jsonl", [attention])
    write_json(ghost / "coverage_latest.json", {
        "observed_at": "2026-09-05T02:51:56Z",
        "parity_state": "NICHE_ONLY",
    })

    tracker = build_investment_tracker(root)
    return next(
        item["monitor_dashboard"] for item in tracker["ideas"]
        if item.get("idea_id") == "standing::ghost-aw-kdp"
    )


def test_later_failures_stay_in_audit_while_last_success_remains_visible(tmp_path):
    monitor = build_fixture(tmp_path)

    assert monitor["availability"]["current"]["observed_at"] == "2026-09-05T02:56:22Z"
    assert monitor["availability"]["stores"][0]["status"] == "orderable"
    assert [row["date"] for row in monitor["availability"]["history"]] == ["2026-09-05"]
    assert monitor["availability"]["latest_attempt"]["observed_at"] == "2026-09-06T03:27:35Z"

    search = monitor["search"]
    assert search["status"] == "complete"
    assert search["last_successful_observed_at"] == "2026-09-05T02:51:56Z"
    assert search["latest_complete_date"] == "2026-09-04"
    assert len(search["rolling_seven_day_change"]) == 7
    assert search["rolling_seven_day_change"][0]["date"] == "2026-08-29"
    assert search["rolling_seven_day_change"][-1]["date"] == "2026-09-04"

    attempts = {
        row["source"]: row for row in monitor["source_receipts"]["operational_attempts"]
    }
    assert attempts["Walmart six-store availability"]["usable"] is False
    assert attempts["Google search attention"] == {
        "source": "Google search attention",
        "observed_at": "2026-09-06T03:52:43Z",
        "status": "source_failure",
        "usable": False,
    }


def test_total_conversation_volume_leads_and_missing_sentiment_stays_unclassified(tmp_path):
    monitor = build_fixture(tmp_path)
    conversations = monitor["conversations"]

    assert conversations["headline"] == (
        "10 exact posts plus 0 comments/replies observed across 5 successful platform reads."
    )
    assert conversations["exact_roots"] == 10
    assert conversations["qualifying_roots"] == 0
    assert conversations["sentiment"]["role"] == "secondary_context_only"
    assert conversations["sentiment"]["status"] == "not_collected"
    assert conversations["sentiment"]["counts"] is None
    assert conversations["sentiment"]["sample_denominator"] == 0
    assert "no response is relabeled neutral" in conversations["sentiment"]["note"]
    assert conversations["platforms"]["instagram"]["query_status"] == "complete_no_match"
    assert conversations["history"][0]["captured_comments_replies"] is None


def test_supplied_sentiment_buckets_remain_secondary_context(tmp_path):
    monitor = build_fixture(tmp_path, sentiment={
        "status": "complete",
        "sample_denominator": 10,
        "counts": {"positive": 4, "negative": 2, "neutral": 3, "mixed": 1},
    })

    assert monitor["conversations"]["sentiment"]["counts"] == {
        "positive": 4,
        "negative": 2,
        "neutral": 3,
        "mixed": 1,
        "unclassified": 0,
    }


def test_stale_tiktok_retry_does_not_overwrite_newer_deepcheck(tmp_path):
    build_fixture(tmp_path)
    ghost = tmp_path / "artifacts" / "dd" / "ghost-kdp"
    attention_path = ghost / "attention_latest.json"
    attention = json.loads(attention_path.read_text(encoding="utf-8"))
    attention["observed_at"] = "2026-09-06T09:13:28Z"
    conversation = attention["conversation_attention"]
    conversation["candidate_platform_queries"]["tiktok"] = {
        "candidate_query_status": "complete_relevant",
        "observed_exact_roots": 25,
    }
    conversation["origin_review"]["raw_exact_roots_by_platform"]["tiktok"] = 25
    conversation["origin_review"][
        "qualifying_independent_roots_by_platform"
    ]["tiktok"] = 19
    write_json(attention_path, attention)
    write_json(
        ghost / "conversation-runs" / "tiktok-targeted-retry-2026-09-05T154011Z.json",
        {
            "observed_at": "2026-09-05T15:40:11Z",
            "source": {"status": "empty", "error_category": None, "count": 0},
        },
    )

    tracker = build_investment_tracker(tmp_path)
    monitor = next(
        item["monitor_dashboard"] for item in tracker["ideas"]
        if item.get("idea_id") == "standing::ghost-aw-kdp"
    )
    tiktok = monitor["conversations"]["platforms"]["tiktok"]

    assert tiktok["query_status"] == "complete_relevant"
    assert tiktok["exact_roots"] == 25
    assert tiktok["qualifying_roots"] == 19
    assert monitor["conversations"]["exact_roots"] == 35
