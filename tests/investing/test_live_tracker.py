import hashlib
import json
from pathlib import Path

from apis.investing_dashboard_page import INVESTING_DASHBOARD_HTML
from social_scraper.investing.live_tracker import build_investment_tracker


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


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


def test_tracker_projects_real_ghost_monitor_history(tmp_path):
    ghost = tmp_path / "artifacts/dd/ghost-kdp"

    def store(store_id, metro, status):
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

    day_one = {
        "observed_at": "2026-09-04T01:00:00Z",
        "coverage_status": "complete",
        "records": [store(i, f"Store {i}", "out_of_stock") for i in range(6)],
        "summary": {"orderable": 0, "out_of_stock": 6, "not_listed_at_store": 0, "unavailable": 0},
    }
    day_two = {
        "observed_at": "2026-09-05T02:56:22Z",
        "coverage_status": "complete",
        "records": [store(0, "Sacramento", "orderable")] + [store(i, f"Store {i}", "out_of_stock") for i in range(1, 6)],
        "summary": {"orderable": 1, "out_of_stock": 5, "not_listed_at_store": 0, "unavailable": 0},
        "restock_monitor": {"availability_state": "replenishment_started"},
    }
    latest = {
        "observed_at": "2026-09-05T14:38:39Z",
        "coverage_status": "partial",
        "records": [
            store(0, "Sacramento", "orderable"),
            store(1, "Dallas", "unavailable_location_unverified"),
            store(2, "Atlanta", "out_of_stock"),
            store(3, "Chicago", "out_of_stock"),
            store(4, "Miami", "out_of_stock"),
            store(5, "Seattle/Renton", "unavailable_error"),
        ],
        "summary": {"orderable": 1, "out_of_stock": 3, "not_listed_at_store": 0, "unavailable": 2},
        "restock_monitor": {
            "state": "source_failure",
            "availability_state": "replenishment_started",
            "operational_state": "partial",
        },
    }
    write_json(ghost / "walmart_native_latest.json", latest)
    write_jsonl(ghost / "walmart_native_history.jsonl", [day_one, day_two, latest])

    search_one = {
        "observed_at": "2026-09-04T01:47:06Z",
        "search_attention": {
            "status": "complete",
            "state": "SEARCH_BUILDING_BASELINE",
            "current_comparison": "not_falling",
            "geographies": {"US": {"queries": {
                "ghost root beer energy drink": {"latest_to_prior_ratio": 1.22},
                "ghost a&w root beer": {"latest_to_prior_ratio": 1.68},
            }}},
        },
    }
    search_one["conversation_attention"] = {
        "state": "CONVERSATION_BUILDING_BASELINE",
        "operational_state": "HEALTHY_BOUNDED_SAMPLE",
        "comparable_scheduled_runs": 0,
        "platform_canary_matrix": {
            platform: {"status": "healthy"}
            for platform in ["x", "tiktok", "instagram", "reddit", "youtube"]
        },
        "candidate_platform_queries": {
            platform: {"candidate_query_status": "complete"}
            for platform in ["x", "tiktok", "instagram", "reddit", "youtube"]
        },
        "origin_review": {
            "raw_exact_roots_by_platform": {"x": 0, "tiktok": 0, "instagram": 0, "reddit": 3, "youtube": 2},
            "qualifying_independent_roots_by_platform": {"x": 0, "tiktok": 0, "instagram": 0, "reddit": 1, "youtube": 1},
        },
    }
    search_two = {
        "observed_at": "2026-09-05T02:51:56Z",
        "search_attention": {
            "status": "PARTIAL_ROUTE_FALLBACK",
            "state": "SEARCH_BUILDING_BASELINE",
            "current_comparison": "not_falling_us_worldwide_sparse",
            "latest_complete_date": "2026-09-04",
            "query_basket": ["ghost root beer energy drink", "ghost a&w root beer"],
            "geographies": {"US": {
                "latest_to_prior_ratio": {
                    "ghost root beer energy drink": 1.21,
                    "ghost a&w root beer": 1.65,
                },
                "latest_7_values": {
                    "ghost root beer energy drink": [49, 44, 58, 40, 34, 36, 27],
                    "ghost a&w root beer": [72, 78, 100, 70, 51, 52, 41],
                },
                "prior_7_values": {
                    "ghost root beer energy drink": [43, 35, 26, 28, 32, 41, 33],
                    "ghost a&w root beer": [48, 43, 36, 35, 36, 43, 40],
                },
            }},
        },
        "conversation_attention": {
            "state": "CONVERSATION_BUILDING_BASELINE",
            "operational_state": "SOURCE_FAILURE",
            "comparable_scheduled_runs": 0,
            "failed_canaries": ["tiktok"],
            "platform_canary_matrix": {
                platform: {"status": "healthy" if platform != "tiktok" else "degraded"}
                for platform in ["x", "tiktok", "instagram", "reddit", "youtube"]
            },
            "candidate_platform_queries": {
                "x": {"candidate_query_status": "complete", "observed_exact_roots": 0},
                "tiktok": {"candidate_query_status": "failed", "observed_exact_roots": 0},
                "instagram": {"candidate_query_status": "complete", "observed_exact_roots": 0},
                "reddit": {"candidate_query_status": "complete", "observed_exact_roots": 5},
                "youtube": {"candidate_query_status": "complete", "observed_exact_roots": 5},
            },
            "origin_review": {
                "raw_exact_roots_by_platform": {"x": 0, "tiktok": 0, "instagram": 0, "reddit": 5, "youtube": 5},
                "qualifying_independent_roots_by_platform": {"x": 0, "tiktok": 0, "instagram": 0, "reddit": 0, "youtube": 0},
            },
        },
    }
    write_json(ghost / "attention_latest.json", search_two)
    write_jsonl(ghost / "attention_history.jsonl", [search_one, search_two])
    coverage_latest = {
        "observed_at": "2026-09-05T02:51:56Z",
        "parity_state": "NICHE_ONLY",
        "qualifying_independent_business_financial_outlet_count": 0,
        "management_acknowledges_a_and_w_economics": False,
        "official_source_receipts": [
            {"key": "kdp_ir", "source_class": "official_company_ir", "http_status": 200},
            {"key": "kdp_q2_transcript", "source_class": "official_company_transcript", "http_status": 200},
        ],
        "sec_source_receipts": [{"key": "sec_latest", "http_status": 200}],
        "media_search_receipts": [{"query": "GHOST A&W KDP", "status": "complete", "returned_count": 5}],
    }
    write_json(ghost / "coverage_latest.json", coverage_latest)
    write_jsonl(ghost / "coverage_history.jsonl", [
        {
            "observed_at": "2026-09-04T02:42:39Z",
            "parity_state": "NICHE_ONLY",
            "qualifying_independent_business_financial_outlet_count": 0,
            "management_acknowledges_a_and_w_economics": False,
        },
        coverage_latest,
    ])
    write_json(ghost / "conversation-runs/tiktok-targeted-retry-2026-09-05T154011Z.json", {
        "observed_at": "2026-09-05T15:40:11Z",
        "source": {"status": "empty", "error_category": None, "count": 0},
    })
    jobs = tmp_path / "jobs.json"
    write_json(jobs, {"jobs": [{
        "id": "ghost", "name": "GHOST daily watch", "enabled": True,
        "state": "scheduled", "schedule": {"display": "daily"},
    }]})

    tracker = build_investment_tracker(tmp_path, cron_jobs_path=jobs)
    row = next(item for item in tracker["ideas"] if item["idea_id"] == "standing::ghost-aw-kdp")
    monitor = row["monitor_dashboard"]

    assert monitor["headline"] == "Sacramento restocked; the latest full six-store reading was 1 available and 5 out of stock."
    assert monitor["thesis_realization"] == {
        "status": "BUILDING_BASELINE",
        "current_read": "No exit-review trigger. US search remains elevated; conversation momentum is not yet measurable; financial coverage remains niche.",
        "exit_review_triggered": False,
        "triggered_by": [],
    }
    assert monitor["availability"]["current"] == {
        "observed_at": "2026-09-05T14:38:39Z",
        "coverage": "partial",
        "available": 1,
        "out_of_stock": 3,
        "not_listed": 0,
        "unverified": 2,
    }
    assert monitor["availability"]["last_complete"]["out_of_stock"] == 5
    assert [point["available"] for point in monitor["availability"]["history"]] == [0, 1]
    assert monitor["search"]["current_read"] == "Elevated, not falling in the usable US comparison."
    assert [point["ratios"]["ghost a&w root beer"] for point in monitor["search"]["history"]] == [1.68, 1.65]
    assert monitor["conversations"]["platforms"]["tiktok"]["health"] == "healthy"
    assert monitor["conversations"]["platforms"]["tiktok"]["query_status"] == "empty"
    assert monitor["conversations"]["exact_roots"] == 10
    assert monitor["conversations"]["qualifying_roots"] == 0
    assert [point["exact_roots"] for point in monitor["conversations"]["history"]] == [5, 10]
    assert monitor["conversations"]["sentiment"] == {
        "status": "not_collected",
        "note": "Positive and negative reactions both count toward buzz; no comparable sentiment history has been collected yet.",
    }
    assert monitor["street_coverage"]["state"] == "NICHE_ONLY"
    assert monitor["street_coverage"]["qualifying_outlets"] == 0
    assert [point["qualifying_outlets"] for point in monitor["street_coverage"]["history"]] == [0, 0]
    assert monitor["street_coverage"]["source_checks"] == {
        "official_sources": 2,
        "sec_filings": 1,
        "news_queries": 1,
        "earnings_call_or_transcript_checked": True,
    }
    assert monitor["source_receipts"]["upstream_calls"] == 0

    search_two["conversation_attention"]["state"] = "CONVERSATION_COOLING_REVIEW"
    coverage_latest["parity_state"] = "EXTENSIVE_COVERAGE_REVIEW"
    write_json(ghost / "attention_latest.json", search_two)
    write_json(ghost / "coverage_latest.json", coverage_latest)
    exit_tracker = build_investment_tracker(tmp_path, cron_jobs_path=jobs)
    exit_row = next(item for item in exit_tracker["ideas"] if item["idea_id"] == "standing::ghost-aw-kdp")
    exit_state = exit_row["monitor_dashboard"]["thesis_realization"]
    assert exit_state["status"] == "EXIT_REVIEW"
    assert exit_state["exit_review_triggered"] is True
    assert exit_state["triggered_by"] == [
        "independent conversation volume fell through its frozen review rule",
        "financial coverage or management acknowledgment reached the review threshold",
    ]


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
    assert "standingMonitorPanel" in script
    assert "no run until resumed" in script
    assert "Walmart availability over time" in script
    assert "Search attention change" in script
    assert "Conversation coverage" in script
    assert "Buzz over time" in script
    assert "Positive and negative both count toward buzz" in script
    assert "Street awareness" in script
    assert "News and management coverage over time" in script
    assert "Earnings calls checked" in script
    css = (Path(__file__).parents[2] / "public" / "investing-dashboard.css").read_text(encoding="utf-8")
    assert ".tracker-monitor-dashboard" in css
    assert ".tracker-availability-history" in css


def test_public_tracker_release_receipt_proves_private_snapshot_without_leaking_ideas():
    root = Path(__file__).parents[2]
    snapshot_path = root / "data" / "investing-tracker-snapshot.json"
    receipt = json.loads((root / "public" / "investing-tracker-release.json").read_text(encoding="utf-8"))

    assert receipt["schema_version"] == "bounty-investment-tracker-release/1"
    assert receipt["tracker_snapshot_sha256"] == hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    assert receipt["methodology"] == "bounty-corrected-persistence-rerun/1"
    assert receipt["decision_queue"] == 0
    assert receipt["google_series"] == 57
    assert receipt["failed_google_series"] == 0
    assert "ideas" not in receipt
    assert "D:\\" not in json.dumps(receipt)
