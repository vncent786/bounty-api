import json
from pathlib import Path

from apis.investing_dashboard_page import INVESTING_DASHBOARD_HTML
from social_scraper.investing.live_tracker import build_investment_tracker


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_tracker_reconciles_one_primary_state_and_separate_monitor_activity(tmp_path):
    write_json(
        tmp_path / "artifacts/investing-dd/social-six-2026-09-03/comparison.json",
        {
            "built_at": "2026-09-03T00:00:00Z",
            "candidates": [
                {"candidate": "Private trend", "node_key": "trend", "verdict": "TREND_NOTE", "reason": "No listed path."},
                {"candidate": "Legacy watch", "node_key": "watch", "verdict": "WATCH", "reason": "Old rule."},
            ],
        },
    )
    write_json(
        tmp_path / "artifacts/investing-discovery/sop-v2-2026-09-04T1135Z/fresh-run-comparison.json",
        {
            "run_id": "fresh",
            "as_of": "2026-09-04",
            "investigations": [
                {"key": "fresh-reject", "title": "Fresh reject", "verdict": "REJECT", "headline": "Failed."},
                {"key": "fresh-watch", "title": "Superseded watch", "verdict": "WATCH"},
            ],
        },
    )
    write_json(
        tmp_path / "artifacts/investing-discovery/overnight-2026-09-05/overnight-decisions.json",
        {
            "run_id": "overnight",
            "as_of_utc": "2026-09-05T00:00:00Z",
            "investigations": [
                {"id": "live-watch", "title": "Live watch", "decision": "WATCH", "paths": [{"instrument": "NYSE:AAA"}]},
                {"id": "closed", "title": "Closed", "decision": "REJECT"},
            ],
        },
    )
    write_json(
        tmp_path / "artifacts/investing-discovery/overnight-2026-09-05/watch-transition-plans.json",
        {
            "watches": [{
                "id": "live-watch",
                "transition_plan": {
                    "missing_assertion": "Missing",
                    "resolution_source_or_observable": ["Issuer results"],
                    "next_check_event_or_date": {"date": "2026-09-10"},
                    "promotion_condition": ["Material surprise"],
                    "kill_condition": ["No attribution"],
                    "expiry_event_or_date": {"date": "2026-09-11"},
                },
            }],
        },
    )
    write_json(
        tmp_path / "artifacts/investing-discovery/expansion-2026-09-05/dd-round-2/dd-round-2.json",
        {
            "run_id": "round2",
            "as_of_utc": "2026-09-05T01:00:00Z",
            "scope": {"exact_lineages": 1},
            "groups": [
                {"group_id": "second-watch", "title": "Second watch", "decision": "WATCH", "transition_plan": {"next_check": "Next filing"}},
                {"group_id": "new-note", "title": "New note", "decision": "TREND_NOTE"},
            ],
        },
    )
    write_json(
        tmp_path / "artifacts/investing-discovery/expansion-2026-09-05/dd-round-2/status.json",
        {"status": "complete"},
    )
    write_json(
        tmp_path / "artifacts/investing-discovery/overnight-2026-09-05/frozen-candidate-batch.json",
        {"denominator": {"excluded_queue_occurrences": 225}},
    )
    write_json(
        tmp_path / "artifacts/investing-discovery/corrected-rerun-2026-09-05/existing-bank-rescore.json",
        {"current_watch_list": {"rows": [{
            "idea": "Live watch",
            "persistence_state": "unverified",
            "recommended_treatment": "QUARANTINE_PENDING_HISTORY",
        }]}},
    )
    write_json(
        tmp_path / "data/investing-tracker-trends.json",
        {"items": [{
            "idea_id": "overnight::live-watch",
            "geography": "US",
            "geography_label": "United States",
            "economic_confirmation_required": "Issuer sales",
            "search_trends": {
                "classification": {"state": "ACTIVE_TREND", "active": True},
                "default_query": "test demand",
                "default_geo": "US",
                "query_options": [],
            },
            "theme_assessment": {
                "state": "UNVERIFIED",
                "active": False,
                "reason": "Company economics are missing.",
            },
        }]},
    )
    write_json(
        tmp_path / "data/investing-watch-monitor-state.json",
        {
            "checked_at": "2026-09-06T00:15:00Z",
            "watches": [{
                "idea_id": "overnight::live-watch",
                "monitor_state": "NO_CHANGE",
                "due_reason": "Next event is not due.",
                "evidence_urls": ["https://example.test/watch"],
            }],
        },
    )
    jobs = tmp_path / "jobs.json"
    write_json(jobs, {"jobs": [
        {"id": "watch", "name": "Bounty Watch transition monitor", "enabled": True, "state": "scheduled", "schedule": {"display": "daily"}},
        {"id": "ghost", "name": "GHOST daily watch", "enabled": True, "state": "scheduled", "schedule": {"display": "daily"}},
        {"id": "chewy", "name": "Chewy result update", "enabled": False, "state": "paused", "schedule": {"display": "once"}},
    ]})

    tracker = build_investment_tracker(tmp_path, cron_jobs_path=jobs)

    assert tracker["status"] == "complete"
    assert tracker["summary"]["primary_state_counts"] == {
        "INVESTIGATING": 0,
        "PURSUE": 0,
        "WATCH": 2,
        "TREND_NOTE": 2,
        "STANDING_MONITOR": 2,
        "REJECTED": 2,
        "ARCHIVED": 1,
    }
    assert tracker["backlog"]["lineages"] == 224
    assert tracker["summary"]["active_monitor_jobs"] == 2
    assert tracker["summary"]["paused_monitor_jobs"] == 1
    assert len({row["idea_id"] for row in tracker["ideas"]}) == len(tracker["ideas"])
    live_watch = next(row for row in tracker["ideas"] if row["title"] == "Live watch")
    assert live_watch["primary_state"] == "WATCH"
    assert live_watch["monitoring"]["status"] == "active"
    assert live_watch["monitoring"]["last_result"] == "NO_CHANGE"
    assert live_watch["monitoring"]["last_checked_at"] == "2026-09-06T00:15:00Z"
    assert live_watch["signal_state"] == "ACTIVE_TREND"
    assert live_watch["active_trend"] is False
    assert live_watch["persistence_treatment"] == "QUARANTINE_PENDING_HISTORY"
    assert tracker["summary"]["decision_queue"] == 0
    assert live_watch["transition_plan"]["expiry"] == {"date": "2026-09-11"}
    legacy = next(row for row in tracker["ideas"] if row["title"] == "Legacy watch")
    assert legacy["primary_state"] == "ARCHIVED"


def test_investing_dashboard_tracker_surface_is_wired():
    assert 'data-view="monitors"><span>03</span>Tracker' in INVESTING_DASHBOARD_HTML
    assert 'id="tracker-ledger"' in INVESTING_DASHBOARD_HTML
    assert 'id="tracker-state-tabs"' in INVESTING_DASHBOARD_HTML
    assert 'id="tracker-search"' in INVESTING_DASHBOARD_HTML
    assert '<script src="/investing-tracker.js" defer></script>' in INVESTING_DASHBOARD_HTML
    script = (Path(__file__).parents[2] / "public" / "investing-tracker.js").read_text(encoding="utf-8")
    assert "sessionStorage.getItem(TOKEN_KEY)" in script
    assert "Authorization: `Bearer ${token}`" in script
    assert "Set the dashboard API token" in script
    assert "DECISION_STATES" in script
    assert "filter: 'DECISION'" in script
    assert "Weekly Google search interest" in script
    assert "['3m', '3M']" in script
    assert "tracker-trend-date-label" in script
    assert "these are not weekly search counts" in script
