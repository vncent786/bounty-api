"""Fail-closed release checks for the private Tracker's GHOST evidence."""

from __future__ import annotations

import json

import pytest

from scripts.build_investing_tracker_release_receipt import (
    build_release_receipt,
    sha,
    validate_ghost_evidence_for_publication,
)


PLATFORMS = ("x", "tiktok", "instagram", "reddit", "youtube")


def tracker_payload(*, evidence_status: str = "verified") -> dict:
    platform_rows = {
        platform: {
            "count_status": "verified",
            "displayed_original_posts": 1,
            "linked_original_posts": 1,
            "displayed_comments_replies": 2,
            "linked_comments_replies": 2,
        }
        for platform in PLATFORMS
    }
    return {
        "ideas": [
            {
                "idea_id": "standing::ghost-aw-kdp",
                "monitor_dashboard": {
                    "conversations": {
                        "observed_at": "2026-09-06T14:05:13Z",
                        "evidence": {
                            "status": evidence_status,
                            "displayed_counts": {
                                "original_posts": 5,
                                "comments_replies": 10,
                            },
                            "persisted_link_counts": {
                                "original_posts": 5,
                                "comments_replies": 10,
                            },
                            "total_clickable_links": 15,
                            "platforms": platform_rows,
                        },
                    }
                },
            }
        ]
    }


def test_verified_reconciled_ghost_evidence_passes_publication_gate():
    result = validate_ghost_evidence_for_publication(tracker_payload())

    assert result == {
        "status": "verified",
        "observed_at": "2026-09-06T14:05:13Z",
        "original_posts": 5,
        "comments_replies": 10,
        "total_clickable_links": 15,
    }


def test_mismatched_ghost_evidence_is_rejected_before_publication():
    with pytest.raises(ValueError, match="not verified"):
        validate_ghost_evidence_for_publication(
            tracker_payload(evidence_status="mismatch")
        )


def test_platform_or_link_count_mismatch_is_rejected_before_publication():
    payload = tracker_payload()
    evidence = payload["ideas"][0]["monitor_dashboard"]["conversations"]["evidence"]
    evidence["platforms"]["tiktok"]["linked_comments_replies"] = 1

    with pytest.raises(ValueError, match="TikTok evidence counts do not reconcile"):
        validate_ghost_evidence_for_publication(payload)

    payload = tracker_payload()
    evidence = payload["ideas"][0]["monitor_dashboard"]["conversations"]["evidence"]
    evidence["total_clickable_links"] = 0
    with pytest.raises(ValueError, match="clickable link total does not reconcile"):
        validate_ghost_evidence_for_publication(payload)


def release_ready_tracker() -> dict:
    payload = tracker_payload()
    payload.update({
        "schema_version": "bounty-investment-tracker/1",
        "summary": {
            "decision_queue": 0,
            "trade_ready_now": False,
            "primary_state_counts": {"STANDING_MONITOR": 1},
            "signal_state_counts": {"NOT_APPLICABLE": 1},
        },
        "trend_release_metadata": {
            "methodology": "bounty-corrected-persistence-rerun/1",
            "built_at": "2026-09-05T22:30:55Z",
            "requested_series": 57,
            "terminal_series": 57,
            "preflight_status": "healthy",
        },
    })
    return payload


def test_release_receipt_is_built_from_the_validated_snapshot_alone(tmp_path):
    path = tmp_path / "tracker.json"
    path.write_text(json.dumps(release_ready_tracker()), encoding="utf-8")

    receipt = build_release_receipt(path)

    assert receipt["google_series"] == 57
    assert receipt["failed_google_series"] == 0
    assert receipt["ghost_conversation_evidence"]["total_clickable_links"] == 15
    assert "ideas" not in receipt


def test_release_receipt_rejects_snapshot_without_trend_release_metadata(tmp_path):
    payload = release_ready_tracker()
    payload.pop("trend_release_metadata")
    path = tmp_path / "tracker.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="trend release metadata is missing"):
        build_release_receipt(path)


def test_release_hash_is_stable_across_windows_and_railway_line_endings(tmp_path):
    windows = tmp_path / "windows.json"
    railway = tmp_path / "railway.json"
    windows.write_bytes(b'{\r\n  "status": "complete"\r\n}\r\n')
    railway.write_bytes(b'{\n  "status": "complete"\n}\n')

    assert sha(windows) == sha(railway)
