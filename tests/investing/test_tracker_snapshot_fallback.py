"""Regression coverage for packaged GHOST tracker fallback selection."""

from apis.dashboard_api import (
    _select_tracker_payload,
    _tracker_has_ghost_monitor,
    _tracker_has_verified_ghost_evidence,
)


def payload(
    *,
    status: str = "complete",
    monitor: bool = False,
    evidence_status: str = "verified",
) -> dict:
    return {
        "status": status,
        "source": "live" if not monitor else "packaged",
        "ideas": [
            {
                "idea_id": "standing::ghost-aw-kdp",
                "monitor_dashboard": {
                    "headline": "verified",
                    "conversations": {"evidence": {"status": evidence_status}},
                }
                if monitor
                else None,
            }
        ],
    }


def test_packaged_snapshot_wins_when_live_tree_lacks_ghost_artifacts():
    live = payload(monitor=False)
    packaged = payload(monitor=True)

    assert live["status"] == "complete"
    assert _tracker_has_ghost_monitor(live) is False
    assert _tracker_has_verified_ghost_evidence(packaged) is True
    assert _select_tracker_payload(live, packaged) is packaged


def test_complete_live_monitor_wins_over_packaged_snapshot():
    live = payload(monitor=True)
    live["source"] = "live"
    packaged = payload(monitor=True)

    assert _tracker_has_ghost_monitor(live) is True
    assert _tracker_has_verified_ghost_evidence(live) is True
    assert _select_tracker_payload(live, packaged) is live


def test_missing_packaged_monitor_fails_closed_to_live_payload():
    live = payload(status="partial", monitor=False)

    assert _select_tracker_payload(live, None) is live


def test_non_verified_live_or_packaged_monitor_is_never_preferred():
    live = payload(monitor=True, evidence_status="mismatch")
    live["source"] = "live"
    packaged = payload(monitor=True, evidence_status="mismatch")

    assert _tracker_has_ghost_monitor(live) is True
    assert _tracker_has_verified_ghost_evidence(live) is False
    assert _select_tracker_payload(live, packaged) is live
