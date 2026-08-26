import asyncio
import sqlite3

import pytest

from social_scraper.investing.private_radar import (
    DEFAULT_PANELS,
    PANEL_VERSION,
    PrivateRadarError,
    PrivateRadarScanner,
    PrivateRadarStore,
    propose_candidates,
)


def _evidence(eid, text, author, platform="x", panel_id="beauty"):
    return {
        "id": eid,
        "panel_id": panel_id,
        "platform": platform,
        "external_id": eid,
        "url": f"https://example.com/{eid}",
        "author": author,
        "text": text,
        "created_at": "2026-08-26T00:00:00Z",
        "observed_at": "2026-08-26T12:00:00Z",
        "window_key": "current",
        "query": "silicone air fryer liner",
    }


class FakeCollector:
    async def collect_discovery(self, panel):
        return {
            "evidence": [
                _evidence(f"{panel.panel_id}-e1", "I switched to a silicone air fryer liner", "a", panel_id=panel.panel_id),
                _evidence(f"{panel.panel_id}-e2", "We switched to silicone air fryer liners", "b", "instagram", panel.panel_id),
                _evidence(f"{panel.panel_id}-e3", "Bought another silicone air fryer liner", "c", "youtube", panel.panel_id),
            ],
            "sources": [{"platform": "x", "status": "complete", "count": 3}],
        }

    async def collect_windows(self, panel, anchor_terms):
        return {
            "windows": [
                {"window_key": "current", "start_date": "2026-08-19", "end_date": "2026-08-26", "anchor_query": '"silicone air fryer liner"', "status": "complete", "result_count": 5, "unique_authors": 5, "capped": False},
                {"window_key": "prior_1", "start_date": "2026-08-12", "end_date": "2026-08-19", "anchor_query": '"silicone air fryer liner"', "status": "complete", "result_count": 1, "unique_authors": 1, "capped": False},
                {"window_key": "prior_2", "start_date": "2026-08-05", "end_date": "2026-08-12", "anchor_query": '"silicone air fryer liner"', "status": "complete", "result_count": 1, "unique_authors": 1, "capped": False},
                {"window_key": "prior_3", "start_date": "2026-07-29", "end_date": "2026-08-05", "anchor_query": '"silicone air fryer liner"', "status": "complete", "result_count": 0, "unique_authors": 0, "capped": False},
            ],
            "evidence": [],
            "sources": [{"platform": "x", "status": "complete", "count": 7}],
        }

    async def collect_corroboration(self, panel, anchor_terms):
        return {"evidence": [], "sources": []}


async def _llm(_system, user):
    assert PANEL_VERSION in user
    return '''{"candidates":[{
      "panel_id":"beauty",
      "label":"Silicone air fryer liners replacing paper liners",
      "behaviour_type":"switching",
      "anchor_terms":["silicone air fryer liner"],
      "summary":"Consumers describe switching to a reusable silicone air fryer liner.",
      "economic_mechanism":"Reusable accessory demand may shift kitchen-accessory unit mix.",
      "why_investigate":"The checked sample shows recent switching behavior.",
      "contradiction":"Some users report worse crisping.",
      "invalidation":"Reject if later samples show one promotion or fast reversal.",
      "evidence_ids":["beauty-e1","beauty-e2","beauty-e3"]
    }],"limitations":[]}'''


async def _news(_label, _anchors):
    return {"level": "L1", "status": "niche_coverage", "articles": [], "checked_source": "Google News RSS"}


def _passing_gates():
    return {
        name: {"state": "pass", "passed": True, "reason": "fixture", "metrics": {}}
        for name in (
            "specificity", "behavior", "anomaly", "breadth", "parity", "investigability"
        )
    }


def _qualified_decision(*evidence_ids, label="Qualified"):
    return {
        "candidate_id": "q",
        "qualification_status": "qualified",
        "label": label,
        "evidence_ids": list(evidence_ids),
        "gates": _passing_gates(),
    }


def test_private_radar_store_exposes_only_supported_qualified_candidates(tmp_path):
    store = PrivateRadarStore(tmp_path / "radar.db")
    run_id, created = store.create_scan_if_idle()
    assert created is True
    store.add_evidence(run_id, [
        _evidence("e1", "I switched to product", "a"),
        _evidence("e2", "We switched to product", "b", "instagram"),
    ])
    store.complete_scan(run_id, [
        _qualified_decision("e1", "e2"),
        {
            "candidate_id": "unsafe",
            "qualification_status": "qualified",
            "label": "Unsupported",
            "evidence_ids": ["missing-1", "missing-2"],
            "gates": _passing_gates(),
        },
        {"candidate_id": "n", "qualification_status": "not_qualified", "label": "Rejected", "evidence_ids": []},
    ], limitations=[])

    payload = store.public_payload()
    assert [item["label"] for item in payload["items"]] == ["Qualified"]
    assert payload["data_scan"]["status"] == "complete"
    assert payload["data_scan"]["candidate_count"] == 1


def test_unknown_candidate_coverage_cannot_be_reported_as_an_empty_cycle(tmp_path):
    store = PrivateRadarStore(tmp_path / "radar.db")
    run_id, _ = store.create_scan_if_idle()

    result = store.complete_scan(run_id, [{
        "candidate_id": "unknown",
        "qualification_status": "unknown_due_to_coverage",
        "label": "Promising but unverified",
        "evidence_ids": [],
        "gates": {},
    }], limitations=["Historical X windows were unavailable."])

    assert result["status"] == "failed"
    assert result["error_category"] == "coverage_incomplete"
    assert result["decisions"][0]["qualification_status"] == "unknown_due_to_coverage"


def test_proposals_drop_broad_panel_terms_but_keep_specific_anchors():
    evidence = [{
        **_evidence(
            "half-caff",
            "I switched to half caff coffee and it worked",
            "a",
            panel_id="food_qsr",
        ),
        "panel_id": "food_qsr",
    }]

    async def model(_system, _user):
        return '''{"candidates":[{
          "panel_id":"food_qsr",
          "label":"Half caff coffee step-down",
          "behaviour_type":"switching",
          "anchor_terms":["half caff","coffee"],
          "summary":"People describe switching to half caff.",
          "economic_mechanism":"A step-down product may reduce switching friction.",
          "why_investigate":"Check whether independent demand is growing.",
          "contradiction":"This may be one anecdote.",
          "invalidation":"Reject if usage does not broaden.",
          "evidence_ids":["half-caff"]
        }],"limitations":[]}'''

    proposals, _ = asyncio.run(propose_candidates(
        evidence,
        llm_call_fn=model,
        panels=(DEFAULT_PANELS[1],),
    ))

    assert proposals[0]["anchor_terms"] == ["half caff"]


def test_current_evidence_failure_skips_expensive_x_history(tmp_path):
    class CountingCollector:
        def __init__(self):
            self.window_calls = 0

        async def collect_discovery(self, panel):
            return {
                "evidence": [
                    _evidence(
                        "single",
                        "I switched to a specific reusable liner",
                        "one-author",
                        panel_id=panel.panel_id,
                    )
                ],
                "sources": [{
                    "panel_id": panel.panel_id,
                    "platform": "x",
                    "status": "complete",
                    "count": 1,
                    "error_category": None,
                }],
            }

        async def collect_corroboration(self, _panel, _anchors):
            return {"evidence": [], "sources": []}

        async def collect_windows(self, _panel, _anchors):
            self.window_calls += 1
            raise AssertionError("history should not run for a failed current-evidence gate")

    async def model(_system, _user):
        return '''{"candidates":[{
          "panel_id":"beauty",
          "label":"Specific reusable liner switch",
          "behaviour_type":"switching",
          "anchor_terms":["specific reusable liner"],
          "summary":"One person describes switching to a specific reusable liner.",
          "economic_mechanism":"Reusable purchases may replace disposables.",
          "why_investigate":"Check whether adoption broadens.",
          "contradiction":"Only one voice supports it.",
          "invalidation":"Reject if no other independent users appear.",
          "evidence_ids":["single"]
        }],"limitations":[]}'''

    collector = CountingCollector()
    scanner = PrivateRadarScanner(
        PrivateRadarStore(tmp_path / "radar.db"),
        collector,
        panels=(DEFAULT_PANELS[0],),
        llm_call_fn=model,
    )

    result = asyncio.run(scanner.run())

    assert result["status"] == "no_qualified_leads"
    assert collector.window_calls == 0
    assert result["decisions"][0]["gates"]["behavior"]["passed"] is False


def test_private_scan_runs_end_to_end_and_persists_evidence(tmp_path):
    store = PrivateRadarStore(tmp_path / "radar.db")
    scanner = PrivateRadarScanner(
        store, FakeCollector(), panels=None, llm_call_fn=_llm, news_check_fn=_news,
    )
    result = asyncio.run(scanner.run())

    assert result["status"] == "complete"
    assert result["candidate_count"] == 1
    payload = store.public_payload()
    assert len(payload["items"]) == 1
    assert len(payload["items"][0]["evidence"]) == 3
    assert payload["items"][0]["gates"]["anomaly"]["passed"] is True


def test_model_failure_fails_closed_without_raw_fallback(tmp_path):
    async def broken(*_args):
        raise RuntimeError("provider down")

    store = PrivateRadarStore(tmp_path / "radar.db")
    scanner = PrivateRadarScanner(
        store, FakeCollector(), panels=None, llm_call_fn=broken, news_check_fn=_news,
    )
    result = asyncio.run(scanner.run())
    assert result["status"] == "failed"
    assert store.public_payload()["items"] == []


def test_unavailable_sources_are_a_failure_not_an_empty_cycle(tmp_path):
    class UnavailableCollector:
        async def collect_discovery(self, panel):
            return {
                "evidence": [],
                "sources": [{
                    "panel_id": panel.panel_id,
                    "platform": "x",
                    "status": "failed",
                    "count": 0,
                    "error_category": "session_expired",
                }],
            }

    async def must_not_run(*_args):
        raise AssertionError("model must not run without trustworthy source coverage")

    store = PrivateRadarStore(tmp_path / "radar.db")
    scanner = PrivateRadarScanner(
        store,
        UnavailableCollector(),
        panels=(DEFAULT_PANELS[0],),
        llm_call_fn=must_not_run,
    )

    result = asyncio.run(scanner.run())

    assert result["status"] == "failed"
    assert result["error_category"] == "PrivateRadarCoverageUnavailable"
    assert store.public_payload()["items"] == []


def test_healthy_empty_sources_are_an_honest_empty_cycle(tmp_path):
    class HealthyEmptyCollector:
        async def collect_discovery(self, panel):
            return {
                "evidence": [],
                "sources": [{
                    "panel_id": panel.panel_id,
                    "platform": "x",
                    "status": "complete",
                    "count": 0,
                    "error_category": None,
                }],
            }

    async def no_candidates(_system, _user):
        return '{"candidates":[],"limitations":[]}'

    store = PrivateRadarStore(tmp_path / "radar.db")
    scanner = PrivateRadarScanner(
        store,
        HealthyEmptyCollector(),
        panels=(DEFAULT_PANELS[0],),
        llm_call_fn=no_candidates,
    )

    result = asyncio.run(scanner.run())

    assert result["status"] == "no_qualified_leads"


def test_failed_new_attempt_preserves_prior_qualified_data_with_disclosure(tmp_path):
    store = PrivateRadarStore(tmp_path / "radar.db")
    first, _ = store.create_scan_if_idle()
    store.add_evidence(first, [
        _evidence("e1", "I switched to product", "a"),
        _evidence("e2", "We switched to product", "b", "instagram"),
    ])
    store.complete_scan(
        first,
        [_qualified_decision("e1", "e2")],
        limitations=[],
    )
    second, _ = store.create_scan_if_idle()
    store.fail_scan(second, "source_failure")

    payload = store.public_payload()
    assert payload["items"][0]["label"] == "Qualified"
    assert payload["displaying_previous_data"] is True
    assert payload["last_attempt"]["status"] == "failed"


def test_fresh_heartbeat_prevents_reclaiming_an_old_active_scan(tmp_path):
    path = tmp_path / "radar.db"
    store = PrivateRadarStore(path, stale_scan_after_seconds=0.05)
    run_id, _ = store.create_scan_if_idle()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE private_radar_scans SET started_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (run_id,),
        )

    same_id, created = PrivateRadarStore(
        path, stale_scan_after_seconds=0.05
    ).create_scan_if_idle()

    assert same_id == run_id
    assert created is False
    assert store.get_scan(run_id)["status"] == "running"


def test_expired_heartbeat_is_recovered_atomically(tmp_path):
    path = tmp_path / "radar.db"
    store = PrivateRadarStore(path, stale_scan_after_seconds=0.05)
    old_id, _ = store.create_scan_if_idle()
    with sqlite3.connect(path) as connection:
        connection.execute(
            """UPDATE private_radar_scans
               SET started_at='2000-01-01T00:00:00+00:00',
                   heartbeat_at='2000-01-01T00:00:00+00:00'
               WHERE id=?""",
            (old_id,),
        )

    new_id, created = store.create_scan_if_idle()

    assert created is True
    assert new_id != old_id
    assert store.get_scan(old_id)["status"] == "failed"
    assert store.get_scan(old_id)["error_category"] == "stale_scan_recovered"
    assert store.get_scan(new_id)["status"] == "running"


def test_fail_scan_is_idempotent_for_an_existing_terminal_scan(tmp_path):
    store = PrivateRadarStore(tmp_path / "radar.db")
    run_id, _ = store.create_scan_if_idle()

    first = store.fail_scan(run_id, "source_failure")
    second = store.fail_scan(run_id, "different_failure")

    assert second == first
    assert second["error_category"] == "source_failure"
    with pytest.raises(PrivateRadarError):
        store.fail_scan("missing", "source_failure")


def test_scanner_heartbeat_keeps_a_blocked_active_scan_owned(tmp_path):
    class BlockingCollector:
        def __init__(self):
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def collect_discovery(self, panel):
            self.entered.set()
            await self.release.wait()
            return {
                "evidence": [],
                "sources": [{
                    "panel_id": panel.panel_id,
                    "platform": "x",
                    "status": "complete",
                    "count": 0,
                    "error_category": None,
                }],
            }

    async def no_candidates(_system, _user):
        return '{"candidates":[],"limitations":[]}'

    async def scenario():
        path = tmp_path / "radar.db"
        store = PrivateRadarStore(path, stale_scan_after_seconds=0.05)
        collector = BlockingCollector()
        scanner = PrivateRadarScanner(
            store,
            collector,
            panels=(DEFAULT_PANELS[0],),
            llm_call_fn=no_candidates,
            heartbeat_interval_seconds=0.01,
        )
        task = asyncio.create_task(scanner.run())
        await collector.entered.wait()
        await asyncio.sleep(0.12)
        same_id, created = PrivateRadarStore(
            path, stale_scan_after_seconds=0.05
        ).create_scan_if_idle()
        collector.release.set()
        result = await task
        return same_id, created, result

    same_id, created, result = asyncio.run(scenario())

    assert same_id == result["id"]
    assert created is False
    assert result["status"] == "no_qualified_leads"


def test_concurrent_terminal_transition_does_not_mask_original_outcome(tmp_path):
    class FinalizingCollector:
        def __init__(self, store, run_id):
            self.store = store
            self.run_id = run_id

        async def collect_discovery(self, panel):
            self.store.fail_scan(self.run_id, "externally_finalized")
            return {
                "evidence": [
                    _evidence("late", "I switched to product", "a", panel_id=panel.panel_id)
                ],
                "sources": [],
            }

    store = PrivateRadarStore(tmp_path / "radar.db")
    run_id, _ = store.create_scan_if_idle()
    scanner = PrivateRadarScanner(
        store,
        FinalizingCollector(store, run_id),
        panels=(DEFAULT_PANELS[0],),
        llm_call_fn=_llm,
        heartbeat_interval_seconds=0.01,
    )

    result = asyncio.run(scanner.run(run_id=run_id))

    assert result["status"] == "failed"
    assert result["error_category"] == "externally_finalized"


def test_existing_database_is_migrated_with_a_heartbeat(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("""
            CREATE TABLE private_radar_scans (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                panel_version TEXT NOT NULL,
                requested_panels_json TEXT NOT NULL,
                evidence_count INTEGER NOT NULL DEFAULT 0,
                candidate_count INTEGER NOT NULL DEFAULT 0,
                decisions_json TEXT NOT NULL DEFAULT '[]',
                limitations_json TEXT NOT NULL DEFAULT '[]',
                sources_json TEXT NOT NULL DEFAULT '[]',
                error_category TEXT
            )
        """)
        connection.execute(
            """INSERT INTO private_radar_scans
               (id,started_at,status,stage,panel_version,requested_panels_json)
               VALUES ('legacy','2026-08-26T00:00:00+00:00','running','starting','v1','[]')"""
        )

    store = PrivateRadarStore(path)

    with sqlite3.connect(path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(private_radar_scans)")
        }
        heartbeat_at = connection.execute(
            "SELECT heartbeat_at FROM private_radar_scans WHERE id='legacy'"
        ).fetchone()[0]
    assert "heartbeat_at" in columns
    assert heartbeat_at == "2026-08-26T00:00:00+00:00"
    assert store.get_scan("legacy")["status"] == "running"
