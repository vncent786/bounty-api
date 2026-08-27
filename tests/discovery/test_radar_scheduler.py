"""Radar scheduler one-tick executor and lifecycle wiring (Task 1.3b).

Covers: environment-configured reconciliation of geography and
active-subject schedules without resetting due times, stale-scope
disable/reactivate semantics, replica-safe claim execution isolation,
mode-governed outcomes (complete/partial/error with honest source
health), zero thread/LLM cost on both feed modes, fail-closed positive
integer settings, disabled-scheduler no-work, and start/stop lifecycle
idempotency including app shutdown await wiring.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from apis import scheduler as scheduler_mod
from apis.scheduler import RadarConfig, RadarScheduler
from social_scraper.discovery.storage import DiscoveryStore
from social_scraper.monitoring.conversation_gate import gate_check_keyword
from social_scraper.workspaces.storage import WorkspaceStore


T0 = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
T0_ISO = T0.isoformat()


def clock_at(moment):
    return lambda: moment


def guard_llm(monkeypatch):
    """Any LLM call inside a radar tick is a hard failure."""
    import social_scraper.llm_client as llm_client

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("radar scheduling must never call the LLM")

    monkeypatch.setattr(llm_client, "call_llm", forbidden)


class FakeTopDown:
    """Scripted discovery: records real runs, returns gate-labelled candidates."""

    def __init__(self, store, script=None, raise_on_first=False):
        self.store = store
        self.script = script or {}
        self.raise_on_first = raise_on_first
        self.raised = False
        self.calls = []
        self.last_run_id = ""
        self._seq = 0

    async def scan_all(self, geo="US", mode=None, **kwargs):
        self.calls.append({"geo": geo, "mode": mode, "kwargs": kwargs})
        if self.raise_on_first and not self.raised:
            self.raised = True
            raise RuntimeError("trends backend exploded")
        plan = dict(self.script.get((geo, mode), {}))
        status = plan.pop("run_status", "complete")
        comparable = plan.pop("comparable", True)
        health = plan.pop("health", [
            {"source": "trendspy.trending_now", "status": "complete",
             "items_returned": len(plan.get("candidates", []))},
        ])
        if not plan.pop("skip_run", False):
            self._seq += 1
            self.last_run_id = f"run-{self._seq}"
            self.store.record_feed(
                geo=geo, observed_at=T0, run_id=self.last_run_id,
                candidates=[{"keyword": c.keyword} for c in plan.get("candidates", [])],
                status=status,
                comparable=comparable,
                error_category=plan.pop("error_category", None) if status == "error" else None,
                source_health=health,
            )
        else:
            self.last_run_id = ""
        return list(plan.get("candidates", []))


class UnknownRunHealthStore(DiscoveryStore):
    """Test double that preserves an explicitly unknown discovery health value."""

    def get_discovery_run(self, run_id):
        row = super().get_discovery_run(run_id)
        if row is not None:
            row["source_health"] = None
        return row


_DEFAULT_GATE_HEALTH = object()


def keyword_item(name, gate_status="complete", health=_DEFAULT_GATE_HEALTH):
    return SimpleNamespace(
        keyword=name,
        gate_status=gate_status,
        gate_source_health=(
            [{"platform": "youtube", "status": "complete"}]
            if health is _DEFAULT_GATE_HEALTH else health
        ),
    )


class ScriptedBroker:
    """Deterministic broker for subject root sweeps; scripts per (keyword, platform)."""

    def __init__(self, script=None):
        self.script = script or {}
        self.search_calls = []
        self.fetch_thread_calls = []

    async def search(self, keyword, platforms=None, count=10):
        self.search_calls.append((keyword, tuple(platforms or []), count))
        items, statuses = [], {}
        for name in platforms or []:
            outcome = self.script.get((keyword, name), "ok_empty")
            if outcome == "error":
                statuses[name] = {"status": "error", "error_category": "probe"}
            else:
                statuses[name] = {"status": "complete"}
                if outcome == "ok_items":
                    items.append({
                        "platform": name,
                        "external_id": f"{name}-{len(self.search_calls)}",
                        "title": f"{keyword} explained",
                        "text": f"People are discussing {keyword}",
                        "url": "https://social.example/1",
                        "engagement": {"likes": 150, "comments": 40},
                    })
        return {
            "items": items,
            "source_health": [
                {"platform": name, "status": value["status"]}
                for name, value in statuses.items()
            ],
            "platform_results": statuses,
        }

    async def fetch_thread(self, *_args, **_kwargs):
        self.fetch_thread_calls.append(_args)
        raise AssertionError("radar scheduling must never hydrate threads")


def spy_gate(calls):
    async def wrapped(broker, keyword, **kwargs):
        calls.append((keyword, kwargs))
        return await gate_check_keyword(broker, keyword, **kwargs)
    return wrapped


class StoreSpy:
    """Delegates to a real DiscoveryStore while counting write/claim calls."""

    def __init__(self, store):
        self._store = store
        self.calls = {
            "upsert_radar_schedule": 0,
            "claim_due_schedules": 0,
            "complete_schedule_attempt": 0,
            "set_radar_schedule_enabled": 0,
        }

    def __getattr__(self, name):
        return getattr(self._store, name)

    def upsert_radar_schedule(self, *args, **kwargs):
        self.calls["upsert_radar_schedule"] += 1
        return self._store.upsert_radar_schedule(*args, **kwargs)

    def claim_due_schedules(self, *args, **kwargs):
        self.calls["claim_due_schedules"] += 1
        return self._store.claim_due_schedules(*args, **kwargs)

    def complete_schedule_attempt(self, *args, **kwargs):
        self.calls["complete_schedule_attempt"] += 1
        return self._store.complete_schedule_attempt(*args, **kwargs)

    def set_radar_schedule_enabled(self, *args, **kwargs):
        self.calls["set_radar_schedule_enabled"] += 1
        return self._store.set_radar_schedule_enabled(*args, **kwargs)


def make_scheduler(tmp_path, *, config, topdown=None, broker=None,
                   workspace=None, discovery=None, gate_check=None):
    discovery = discovery or DiscoveryStore(tmp_path / "discovery.db")
    workspace = workspace or WorkspaceStore(tmp_path / "workspaces.db")
    topdown = topdown or FakeTopDown(discovery)
    broker = broker or ScriptedBroker()
    return RadarScheduler(
        config=config,
        discovery_store=discovery,
        workspace_store=workspace,
        topdown=topdown,
        broker=broker,
        clock=clock_at(T0),
        gate_check=gate_check,
    )


# --- Environment configuration (fail-closed) -------------------------------


def test_radar_config_defaults_and_geo_normalization():
    default = RadarConfig.from_env({})
    assert default.enabled is True
    assert default.geos == ()
    assert default.trends_interval_minutes == 1440
    assert default.root_interval_minutes == 10080
    assert default.root_max_candidates == 100
    assert default.lease_minutes == 10
    assert default.claim_limit == 100

    configured = RadarConfig.from_env({
        "BOUNTY_RADAR_GEOS": "us, sg ,US,",
        "BOUNTY_RADAR_TRENDS_INTERVAL_MINUTES": "60",
        "BOUNTY_RADAR_ROOT_INTERVAL_MINUTES": "120",
        "BOUNTY_RADAR_ROOT_MAX_CANDIDATES": "7",
    })
    assert configured.geos == ("US", "SG")
    assert configured.trends_interval_minutes == 60
    assert configured.root_interval_minutes == 120
    assert configured.root_max_candidates == 7


def test_radar_config_empty_geos_means_no_geography_schedules():
    assert RadarConfig.from_env({"BOUNTY_RADAR_GEOS": ""}).geos == ()
    assert RadarConfig.from_env({"BOUNTY_RADAR_GEOS": " , ,"}).geos == ()
    # No Vincent-only default ever sneaks in.
    assert RadarConfig.from_env({}).geos == ()


@pytest.mark.parametrize("value", ["0", "-5", "abc", "3.5", ""])
def test_radar_config_positive_integers_fail_closed(value):
    for name in (
        "BOUNTY_RADAR_TRENDS_INTERVAL_MINUTES",
        "BOUNTY_RADAR_ROOT_INTERVAL_MINUTES",
        "BOUNTY_RADAR_ROOT_MAX_CANDIDATES",
        "BOUNTY_RADAR_LEASE_MINUTES",
        "BOUNTY_RADAR_CLAIM_LIMIT",
    ):
        with pytest.raises(ValueError, match=name):
            RadarConfig.from_env({name: value})


def test_radar_config_enabled_flag_is_strict():
    assert RadarConfig.from_env({}).enabled is True
    for on in ("1", "true", "YES", "On"):
        assert RadarConfig.from_env({"BOUNTY_RADAR_ENABLED": on}).enabled is True
    for off in ("0", "false", "NO", "Off"):
        assert RadarConfig.from_env({"BOUNTY_RADAR_ENABLED": off}).enabled is False
    with pytest.raises(ValueError, match="BOUNTY_RADAR_ENABLED"):
        RadarConfig.from_env({"BOUNTY_RADAR_ENABLED": "definitely"})


def test_radar_config_direct_construction_normalizes_and_validates():
    config = RadarConfig(geos=(" us ", "US", "sg"))
    assert config.geos == ("US", "SG")

    numeric_fields = (
        "trends_interval_minutes", "root_interval_minutes",
        "root_max_candidates", "lease_minutes", "claim_limit",
    )
    for field_name in numeric_fields:
        with pytest.raises(ValueError, match=field_name):
            RadarConfig(**{field_name: 0})
        with pytest.raises(ValueError, match=field_name):
            RadarConfig(**{field_name: True})
    with pytest.raises(ValueError, match="enabled"):
        RadarConfig(enabled="yes")


# --- Store additive: enable/disable without wiping state --------------------


def test_set_radar_schedule_enabled_preserves_due_lease_and_history(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    made = store.upsert_radar_schedule(
        scan_mode="root_sweep", scope_type="geography", geo="US",
        interval_minutes=10080, next_run_at=T0,
    )
    claim = store.claim_due_schedules(now=T0, lease_minutes=30)[0]
    receipt = store.complete_schedule_attempt(
        claim["schedule_id"], claim["claim_token"], status="complete",
        comparable=True, discovery_run_id="run-7", now=T0,
    )
    advanced = receipt["schedule"]["next_run_at"]

    disabled = store.set_radar_schedule_enabled(made["id"], enabled=False, now=T0)
    assert disabled["enabled"] is False
    assert disabled["next_run_at"] == advanced
    assert disabled["last_successful_comparable_run_id"] == receipt["run"]["id"]
    assert len(store.list_radar_schedule_runs(made["id"])) == 1

    reenabled = store.set_radar_schedule_enabled(made["id"], enabled=True, now=T0)
    assert reenabled["enabled"] is True
    assert reenabled["next_run_at"] == advanced
    assert reenabled["last_successful_comparable_run_id"] == receipt["run"]["id"]

    assert store.set_radar_schedule_enabled("missing-id", enabled=False, now=T0) is None


def test_claim_renewal_and_token_checked_release_preserve_due_time(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    schedule = store.upsert_radar_schedule(
        scan_mode="trends_snapshot", scope_type="geography", geo="US",
        interval_minutes=1440, next_run_at=T0,
    )
    claim = store.claim_due_schedules(now=T0, lease_minutes=1)[0]
    renewed = store.renew_radar_schedule_claim(
        schedule["id"], claim["claim_token"],
        now=T0 + timedelta(seconds=30), lease_minutes=1,
    )
    assert renewed["lease_until"] == (T0 + timedelta(seconds=90)).isoformat()
    assert store.renew_radar_schedule_claim(
        schedule["id"], "stale", now=T0, lease_minutes=1,
    ) is None
    assert store.release_radar_schedule_claim(
        schedule["id"], "stale", now=T0,
    ) is False
    assert store.release_radar_schedule_claim(
        schedule["id"], claim["claim_token"], now=T0,
    ) is True
    released = store.get_radar_schedule(schedule["id"])
    assert released["lease_token"] is None
    assert released["lease_until"] is None
    assert released["next_run_at"] == T0_ISO
    assert store.list_radar_schedule_runs(schedule["id"]) == []


def test_expired_claim_cannot_be_renewed_or_released(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    schedule = store.upsert_radar_schedule(
        scan_mode="trends_snapshot", scope_type="geography", geo="US",
        interval_minutes=1440, next_run_at=T0,
    )
    claim = store.claim_due_schedules(now=T0, lease_minutes=1)[0]
    expired_at = T0 + timedelta(minutes=1)

    assert store.renew_radar_schedule_claim(
        schedule["id"], claim["claim_token"],
        now=expired_at, lease_minutes=1,
    ) is None
    assert store.release_radar_schedule_claim(
        schedule["id"], claim["claim_token"], now=expired_at,
    ) is False
    persisted = store.get_radar_schedule(schedule["id"])
    assert persisted["lease_token"] == claim["claim_token"]
    assert persisted["lease_until"] == expired_at.isoformat()


# --- Reconciliation ---------------------------------------------------------


def test_reconcile_creates_exact_geo_modes_and_cadences(tmp_path):
    scheduler = make_scheduler(tmp_path, config=RadarConfig(
        geos=("US", "SG"), trends_interval_minutes=60, root_interval_minutes=120,
    ))
    scheduler._reconcile(T0)

    rows = {
        (row["scan_mode"], row["geo"]): row
        for row in scheduler.discovery_store.list_radar_schedules()
    }
    assert set(rows) == {
        ("trends_snapshot", "US"), ("root_sweep", "US"),
        ("trends_snapshot", "SG"), ("root_sweep", "SG"),
    }
    assert all(row["scope_type"] == "geography" for row in rows.values())
    assert all(row["enabled"] is True for row in rows.values())
    assert rows[("trends_snapshot", "US")]["interval_minutes"] == 60
    assert rows[("root_sweep", "US")]["interval_minutes"] == 120
    assert rows[("trends_snapshot", "SG")]["interval_minutes"] == 60
    assert rows[("root_sweep", "SG")]["interval_minutes"] == 120
    assert all(row["subject_id"] is None for row in rows.values())
    # Only feed modes may exist.
    assert {mode for mode, _ in rows} == {"trends_snapshot", "root_sweep"}


def test_reconcile_without_geos_disables_stale_geography_schedules(tmp_path):
    scheduler = make_scheduler(tmp_path, config=RadarConfig(geos=()))
    scheduler.discovery_store.upsert_radar_schedule(
        scan_mode="trends_snapshot", scope_type="geography", geo="US",
        interval_minutes=1440, next_run_at=T0,
    )
    scheduler.discovery_store.upsert_radar_schedule(
        scan_mode="root_sweep", scope_type="geography", geo="DE",
        interval_minutes=10080, next_run_at=T0,
    )

    scheduler._reconcile(T0)

    rows = scheduler.discovery_store.list_radar_schedules()
    assert rows == [] or all(
        row["enabled"] is False for row in rows
    )
    assert all(row["enabled"] is False for row in rows)
    assert all(row["next_run_at"] == T0_ISO for row in rows)


def test_reconcile_subjects_active_skipped_inactive_and_stale(tmp_path):
    workspace = WorkspaceStore(tmp_path / "workspaces.db")
    project = workspace.create_project("ws", "Radar", default_geo="SG")
    explicit = workspace.create_subject(
        "ws", project["id"], "Explicit", geo="MY", cadence_minutes=60,
    )
    inherited = workspace.create_subject(
        "ws", project["id"], "Inherited", cadence_minutes=120,
    )
    no_geo_project = workspace.create_project("ws", "NoGeo", default_geo="")
    skipped = workspace.create_subject(
        "ws", no_geo_project["id"], "Skipped", cadence_minutes=30,
    )
    workspace.create_subject("ws", project["id"], "Paused", active=False)

    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(geos=()), workspace=workspace,
    )
    scheduler._reconcile(T0)

    rows = {
        row["subject_id"]: row
        for row in scheduler.discovery_store.list_radar_schedules()
        if row["scope_type"] == "subject"
    }
    assert set(rows) == {explicit["id"], inherited["id"]}
    assert rows[explicit["id"]]["geo"] == "MY"
    assert rows[explicit["id"]]["interval_minutes"] == 60
    assert rows[inherited["id"]]["geo"] == "SG"
    assert rows[inherited["id"]]["interval_minutes"] == 120
    assert skipped["id"] not in rows


def test_reconcile_reactivation_reenables_without_wiping_due_or_history(tmp_path):
    workspace = WorkspaceStore(tmp_path / "workspaces.db")
    project = workspace.create_project("ws", "Radar", default_geo="SG")
    subject = workspace.create_subject(
        "ws", project["id"], "Turbines", geo="MY", cadence_minutes=60,
    )
    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(geos=()), workspace=workspace,
    )
    store = scheduler.discovery_store
    scheduler._reconcile(T0)
    schedule = next(
        row for row in store.list_radar_schedules()
        if row["subject_id"] == subject["id"]
    )
    claim = store.claim_due_schedules(now=T0, lease_minutes=30)[0]
    receipt = store.complete_schedule_attempt(
        claim["schedule_id"], claim["claim_token"], status="complete",
        comparable=True, discovery_run_id="run-1", now=T0,
    )
    advanced = receipt["schedule"]["next_run_at"]
    assert advanced == (T0 + timedelta(minutes=60)).isoformat()

    workspace.archive_subject("ws", project["id"], subject["id"])
    scheduler._reconcile(T0)
    disabled = store.get_radar_schedule(schedule["id"])
    assert disabled["enabled"] is False
    assert disabled["next_run_at"] == advanced
    assert disabled["last_successful_comparable_run_id"] == receipt["run"]["id"]
    assert len(store.list_radar_schedule_runs(schedule["id"])) == 1

    workspace.update_subject("ws", project["id"], subject["id"], active=True)
    scheduler._reconcile(T0)
    reenabled = store.get_radar_schedule(schedule["id"])
    assert reenabled["enabled"] is True
    assert reenabled["next_run_at"] == advanced
    assert reenabled["last_successful_comparable_run_id"] == receipt["run"]["id"]
    assert len(store.list_radar_schedule_runs(schedule["id"])) == 1


def test_reconcile_does_not_reset_existing_due_times(tmp_path):
    scheduler = make_scheduler(tmp_path, config=RadarConfig(
        geos=("US",), trends_interval_minutes=1440,
    ))
    scheduler._reconcile(T0)
    store = scheduler.discovery_store
    claims = store.claim_due_schedules(now=T0, lease_minutes=30)
    manual = next(c for c in claims if c["scan_mode"] == "trends_snapshot")
    receipt = store.complete_schedule_attempt(
        manual["schedule_id"], manual["claim_token"], status="complete",
        comparable=True, discovery_run_id="run-1", now=T0,
    )
    advanced = receipt["schedule"]["next_run_at"]
    assert advanced == (T0 + timedelta(minutes=1440)).isoformat()

    # A later reconcile with the same config refreshes nothing about due time.
    scheduler._reconcile(T0 + timedelta(minutes=5))
    refreshed = next(
        row for row in store.list_radar_schedules()
        if row["scan_mode"] == "trends_snapshot"
    )
    assert refreshed["next_run_at"] == advanced
    assert refreshed["enabled"] is True


# --- Geography execution ----------------------------------------------------


def test_geo_trends_snapshot_is_complete_comparable_with_zero_cost(tmp_path, monkeypatch):
    guard_llm(monkeypatch)
    discovery = DiscoveryStore(tmp_path / "discovery.db")
    topdown = FakeTopDown(discovery, script={
        ("US", "trends_snapshot"): {"candidates": [
            keyword_item("turbine blade", gate_status="not_checked", health=[]),
            keyword_item("jet fuel", gate_status="not_checked", health=[]),
        ]},
    })
    broker = ScriptedBroker()
    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(geos=("US",)),
        discovery=discovery, topdown=topdown, broker=broker,
    )

    asyncio.run(scheduler.tick())

    # Both geography schedules for the geo executed once each.
    assert len(topdown.calls) == 2
    snapshot_calls = [c for c in topdown.calls if c["mode"] == "trends_snapshot"]
    root_calls = [c for c in topdown.calls if c["mode"] == "root_sweep"]
    assert len(snapshot_calls) == 1
    assert snapshot_calls[0]["geo"] == "US"
    assert "gate_max" not in snapshot_calls[0]["kwargs"]
    assert len(root_calls) == 1
    assert root_calls[0]["kwargs"]["gate_max"] == 100
    assert broker.search_calls == []
    assert broker.fetch_thread_calls == []

    schedule = next(
        row for row in discovery.list_radar_schedules()
        if row["scan_mode"] == "trends_snapshot"
    )
    runs = discovery.list_radar_schedule_runs(schedule["id"])
    assert len(runs) == 1
    run = runs[0]
    assert run["status"] == "complete"
    assert run["comparable"] is True
    assert run["discovery_run_id"]
    stored = discovery.get_discovery_run(run["discovery_run_id"])
    assert stored["status"] == "complete"
    assert stored["source_health"] == [
        {"source": "trendspy.trending_now", "status": "complete", "items_returned": 2},
    ]
    assert schedule["last_status"] == "complete"
    assert schedule["last_successful_comparable_run_id"] == run["id"]
    assert schedule["next_run_at"] == (T0 + timedelta(minutes=1440)).isoformat()


def test_complete_non_comparable_discovery_is_not_promoted(tmp_path, monkeypatch):
    guard_llm(monkeypatch)
    discovery = DiscoveryStore(tmp_path / "discovery.db")
    topdown = FakeTopDown(discovery, script={
        ("US", "trends_snapshot"): {
            "candidates": [keyword_item("alpha", gate_status="not_checked")],
            "comparable": False,
        },
    })
    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(geos=("US",)),
        discovery=discovery, topdown=topdown,
    )

    asyncio.run(scheduler.tick())

    schedule = next(
        row for row in discovery.list_radar_schedules()
        if row["scan_mode"] == "trends_snapshot"
    )
    run = discovery.list_radar_schedule_runs(schedule["id"])[0]
    assert run["status"] == "complete"
    assert run["comparable"] is False
    assert schedule["last_successful_comparable_run_id"] is None


def test_geo_root_sweep_uses_cap_and_completes_when_gates_healthy(tmp_path, monkeypatch):
    guard_llm(monkeypatch)
    discovery = DiscoveryStore(tmp_path / "discovery.db")
    topdown = FakeTopDown(discovery, script={
        ("US", "root_sweep"): {"candidates": [
            keyword_item("alpha", gate_status="complete"),
            keyword_item("beta", gate_status="empty"),
        ]},
    })
    broker = ScriptedBroker()
    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(geos=("US",), root_max_candidates=2),
        discovery=discovery, topdown=topdown, broker=broker,
    )

    asyncio.run(scheduler.tick())

    assert len(topdown.calls) == 2
    root_calls = [c for c in topdown.calls if c["mode"] == "root_sweep"]
    assert len(root_calls) == 1
    assert root_calls[0]["geo"] == "US"
    assert root_calls[0]["kwargs"]["gate_max"] == 2
    # Geography sweeps never touch the broker directly: discovery owns search.
    assert broker.search_calls == []
    assert broker.fetch_thread_calls == []

    schedule = next(
        row for row in discovery.list_radar_schedules()
        if row["scan_mode"] == "root_sweep"
    )
    run = discovery.list_radar_schedule_runs(schedule["id"])[0]
    assert run["status"] == "complete"
    assert run["comparable"] is True
    assert run["discovery_run_id"]
    assert discovery.get_discovery_run(run["discovery_run_id"])["status"] == "complete"
    assert {"source": "trendspy.trending_now", "status": "complete",
            "items_returned": 2} in run["source_health"]
    assert sum(
        1 for entry in run["source_health"]
        if entry == {"platform": "youtube", "status": "complete"}
    ) == 2


def test_healthy_root_sweep_preserves_unknown_health_as_none(tmp_path, monkeypatch):
    guard_llm(monkeypatch)
    discovery = UnknownRunHealthStore(tmp_path / "discovery.db")
    topdown = FakeTopDown(discovery, script={
        ("US", "root_sweep"): {"candidates": [
            keyword_item("alpha", gate_status="complete", health=None),
        ]},
    })
    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(geos=("US",)),
        discovery=discovery, topdown=topdown,
    )

    asyncio.run(scheduler.tick())

    schedule = next(
        row for row in discovery.list_radar_schedules()
        if row["scan_mode"] == "root_sweep"
    )
    run = discovery.list_radar_schedule_runs(schedule["id"])[0]
    assert run["status"] == "complete"
    assert run["comparable"] is True
    assert run["source_health"] is None


def test_geo_root_sweep_cap_gap_is_partial_non_comparable(tmp_path, monkeypatch):
    guard_llm(monkeypatch)
    discovery = DiscoveryStore(tmp_path / "discovery.db")
    topdown = FakeTopDown(discovery, script={
        ("US", "root_sweep"): {"candidates": [
            keyword_item("alpha", gate_status="complete"),
            keyword_item("beta", gate_status="complete"),
            keyword_item("gamma", gate_status="not_checked", health=[]),
            keyword_item("delta", gate_status="not_checked", health=[]),
        ]},
    })
    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(geos=("US",), root_max_candidates=2),
        discovery=discovery, topdown=topdown,
    )

    asyncio.run(scheduler.tick())

    schedule = next(
        row for row in discovery.list_radar_schedules()
        if row["scan_mode"] == "root_sweep"
    )
    run = discovery.list_radar_schedule_runs(schedule["id"])[0]
    assert run["status"] == "partial"
    assert run["comparable"] is False
    assert run["error_category"].startswith("gate_coverage_capped")
    coverage = next(
        entry for entry in run["source_health"]
        if entry.get("source") == "root_sweep_coverage"
    )
    assert coverage == {
        "source": "root_sweep_coverage",
        "status": "partial",
        "error_category": "candidate_cap",
        "candidates_total": 4,
        "candidates_checked": 2,
        "candidates_unchecked": 2,
    }
    # Provenance is still persisted even when coverage is incomplete.
    assert run["discovery_run_id"]
    assert schedule["last_successful_comparable_run_id"] is None


def test_geo_root_sweep_partial_gate_source_is_partial_non_comparable(tmp_path, monkeypatch):
    guard_llm(monkeypatch)
    discovery = DiscoveryStore(tmp_path / "discovery.db")
    topdown = FakeTopDown(discovery, script={
        ("US", "root_sweep"): {"candidates": [
            keyword_item("alpha", gate_status="complete",
                         health=[{"platform": "youtube", "status": "complete"}]),
            keyword_item("beta", gate_status="partial",
                         health=[{"platform": "reddit", "status": "error",
                                  "error_category": "probe"}]),
        ]},
    })
    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(geos=("US",), root_max_candidates=5),
        discovery=discovery, topdown=topdown,
    )

    asyncio.run(scheduler.tick())

    schedule = next(
        row for row in discovery.list_radar_schedules()
        if row["scan_mode"] == "root_sweep"
    )
    run = discovery.list_radar_schedule_runs(schedule["id"])[0]
    assert run["status"] == "partial"
    assert run["comparable"] is False
    assert run["error_category"] == "gate_source_partial"
    # Observed source health is copied, never invented.
    assert {"platform": "reddit", "status": "error", "error_category": "probe"} in run["source_health"]
    assert {"platform": "youtube", "status": "complete"} in run["source_health"]
    assert run["discovery_run_id"]


def test_partial_discovery_root_adds_explicit_coverage_with_unknown_health(
    tmp_path, monkeypatch,
):
    guard_llm(monkeypatch)
    discovery = UnknownRunHealthStore(tmp_path / "discovery.db")
    topdown = FakeTopDown(discovery, script={
        ("US", "root_sweep"): {
            "run_status": "partial",
            "health": None,
            "candidates": [
                keyword_item("alpha", gate_status="not_checked", health=None),
            ],
        },
    })
    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(geos=("US",)),
        discovery=discovery, topdown=topdown,
    )

    asyncio.run(scheduler.tick())

    schedule = next(
        row for row in discovery.list_radar_schedules()
        if row["scan_mode"] == "root_sweep"
    )
    run = discovery.list_radar_schedule_runs(schedule["id"])[0]
    assert run["status"] == "partial"
    assert run["comparable"] is False
    assert run["source_health"] == [{
        "source": "root_sweep_coverage",
        "status": "partial",
        "error_category": "discovery_partial",
        "candidates_total": 1,
        "candidates_checked": 0,
        "candidates_unchecked": 1,
    }]


def test_geo_discovery_error_run_is_error_non_comparable(tmp_path, monkeypatch):
    guard_llm(monkeypatch)
    discovery = DiscoveryStore(tmp_path / "discovery.db")
    topdown = FakeTopDown(discovery, script={
        ("US", "trends_snapshot"): {
            "run_status": "error",
            "error_category": "dependency_missing",
            "health": [{"source": "trendspy.trending_now", "status": "error",
                        "error_category": "ImportError"}],
        },
    })
    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(geos=("US",)),
        discovery=discovery, topdown=topdown,
    )

    asyncio.run(scheduler.tick())

    schedule = next(
        row for row in discovery.list_radar_schedules()
        if row["scan_mode"] == "trends_snapshot"
    )
    run = discovery.list_radar_schedule_runs(schedule["id"])[0]
    assert run["status"] == "error"
    assert run["comparable"] is False
    assert run["error_category"] == "dependency_missing"
    assert run["source_health"] == [
        {"source": "trendspy.trending_now", "status": "error",
         "error_category": "ImportError"},
    ]


def test_geo_missing_run_provenance_is_error_without_fabricated_health(tmp_path, monkeypatch):
    guard_llm(monkeypatch)
    discovery = DiscoveryStore(tmp_path / "discovery.db")
    topdown = FakeTopDown(discovery, script={
        ("US", "trends_snapshot"): {"skip_run": True, "candidates": [
            keyword_item("alpha"),
        ]},
    })
    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(geos=("US",)),
        discovery=discovery, topdown=topdown,
    )

    asyncio.run(scheduler.tick())

    schedule = next(
        row for row in discovery.list_radar_schedules()
        if row["scan_mode"] == "trends_snapshot"
    )
    run = discovery.list_radar_schedule_runs(schedule["id"])[0]
    assert run["status"] == "error"
    assert run["comparable"] is False
    assert run["error_category"] == "missing_discovery_run"
    assert run["discovery_run_id"] is None
    # Unknown source health stays unknown, never [].
    assert run["source_health"] is None


def test_failed_first_claim_does_not_block_later_claims(tmp_path, monkeypatch):
    guard_llm(monkeypatch)
    discovery = DiscoveryStore(tmp_path / "discovery.db")
    topdown = FakeTopDown(discovery, raise_on_first=True)
    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(geos=("US", "SG")),
        discovery=discovery, topdown=topdown,
    )

    asyncio.run(scheduler.tick())

    # Four schedules were created and claimed (2 modes x 2 geos); the first
    # scan raised, everything after it still ran.
    assert len(topdown.calls) == 4
    runs = []
    for row in discovery.list_radar_schedules():
        runs.extend(discovery.list_radar_schedule_runs(row["id"]))
    assert len(runs) == 4
    assert sum(1 for run in runs if run["status"] == "error") == 1
    errored = next(run for run in runs if run["status"] == "error")
    assert errored["error_category"].startswith("executor:")
    assert errored["source_health"] is None
    assert sum(1 for run in runs if run["status"] == "complete") == 3
    # Every attempt advanced its cadence; no lease is left dangling.
    for row in discovery.list_radar_schedules():
        assert row["lease_token"] is None
        assert row["next_run_at"] > T0_ISO


# --- Subject execution ------------------------------------------------------


def subject_workspace(tmp_path):
    workspace = WorkspaceStore(tmp_path / "workspaces.db")
    project = workspace.create_project("ws", "Radar", default_geo="SG")
    return workspace, project


def test_subject_sweep_healthy_empty_is_comparable_without_discovery_run(
    tmp_path, monkeypatch,
):
    guard_llm(monkeypatch)
    workspace, project = subject_workspace(tmp_path)
    subject = workspace.create_subject(
        "ws", project["id"], "Turbines", geo="MY", cadence_minutes=60,
        platforms=["Reddit"],
    )
    workspace.create_alias("ws", project["id"], subject["id"], "turbine blade", "include")
    workspace.create_alias("ws", project["id"], subject["id"], "spam magnet", "exclude")

    discovery = DiscoveryStore(tmp_path / "discovery.db")
    broker = ScriptedBroker()  # everything healthy and empty by default
    gate_calls = []
    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(geos=()), workspace=workspace,
        discovery=discovery, broker=broker, gate_check=spy_gate(gate_calls),
    )

    asyncio.run(scheduler.tick())

    # Name plus include alias only; exclude alias never queried.
    assert {call[0] for call in gate_calls} == {"Turbines", "turbine blade"}
    assert {call[0] for call in broker.search_calls} == {"Turbines", "turbine blade"}
    for keyword, kwargs in gate_calls:
        assert kwargs["max_threads_per_platform"] == 0
        assert kwargs["platforms"] == ["reddit"]
    assert broker.fetch_thread_calls == []

    schedule = next(
        row for row in discovery.list_radar_schedules()
        if row["scope_type"] == "subject"
    )
    run = discovery.list_radar_schedule_runs(schedule["id"])[0]
    assert run["status"] == "complete"
    assert run["comparable"] is True
    assert run["discovery_run_id"] is None
    assert run["error_category"] is None
    # Observed source-health entries are copied and scope-annotated only.
    assert sorted(
        (entry["query"], entry["platform"]) for entry in run["source_health"]
    ) == [("Turbines", "reddit"), ("turbine blade", "reddit")]
    assert all(entry["status"] == "complete" for entry in run["source_health"])
    assert schedule["next_run_at"] == (T0 + timedelta(minutes=60)).isoformat()


def test_subject_sweep_preserves_all_unknown_health_as_none(tmp_path, monkeypatch):
    guard_llm(monkeypatch)
    workspace, project = subject_workspace(tmp_path)
    workspace.create_subject(
        "ws", project["id"], "Unknown", geo="SG", cadence_minutes=60,
        platforms=["Reddit"],
    )
    calls = []

    async def unknown_health_gate(_broker, keyword, **kwargs):
        calls.append((keyword, kwargs))
        return SimpleNamespace(status="empty", source_health=None)

    discovery = DiscoveryStore(tmp_path / "discovery.db")
    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(geos=()), workspace=workspace,
        discovery=discovery, gate_check=unknown_health_gate,
    )

    asyncio.run(scheduler.tick())

    assert [call[0] for call in calls] == ["Unknown"]
    schedule = discovery.list_radar_schedules()[0]
    run = discovery.list_radar_schedule_runs(schedule["id"])[0]
    assert run["status"] == "complete"
    assert run["comparable"] is True
    assert run["source_health"] is None


def test_subject_sweep_mixed_outcomes_partial_and_all_failed(tmp_path, monkeypatch):
    guard_llm(monkeypatch)
    workspace, project = subject_workspace(tmp_path)
    subject = workspace.create_subject(
        "ws", project["id"], "Mixed", geo="SG", cadence_minutes=60,
        platforms=["Reddit", "TikTok"],
    )
    workspace.create_alias("ws", project["id"], subject["id"], "mixed alias", "include")

    discovery = DiscoveryStore(tmp_path / "discovery.db")
    broker = ScriptedBroker(script={
        ("Mixed", "reddit"): "ok_items",
        ("Mixed", "tiktok"): "ok_empty",
        ("mixed alias", "reddit"): "error",
        ("mixed alias", "tiktok"): "ok_empty",
    })
    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(geos=()), workspace=workspace,
        discovery=discovery, broker=broker,
    )

    asyncio.run(scheduler.tick())

    schedule = next(
        row for row in discovery.list_radar_schedules()
        if row["scope_type"] == "subject"
    )
    run = discovery.list_radar_schedule_runs(schedule["id"])[0]
    assert run["status"] == "partial"
    assert run["comparable"] is False
    assert run["error_category"] == "gate_partial_source"
    health = {(entry["query"], entry["platform"], entry["status"])
              for entry in run["source_health"]}
    assert ("mixed alias", "reddit", "error") in health
    assert ("Mixed", "reddit", "complete") in health

    # All queries failing on every platform is an explicit error.
    discovery_two = DiscoveryStore(tmp_path / "second.db")
    broker_two = ScriptedBroker(script={
        ("Mixed", "reddit"): "error",
        ("Mixed", "tiktok"): "error",
        ("mixed alias", "reddit"): "error",
        ("mixed alias", "tiktok"): "error",
    })
    scheduler_two = make_scheduler(
        tmp_path, config=RadarConfig(geos=()), workspace=workspace,
        discovery=discovery_two, broker=broker_two,
    )
    asyncio.run(scheduler_two.tick())
    schedule_two = next(
        row for row in discovery_two.list_radar_schedules()
        if row["scope_type"] == "subject"
    )
    run_two = discovery_two.list_radar_schedule_runs(schedule_two["id"])[0]
    assert run_two["status"] == "error"
    assert run_two["comparable"] is False
    assert run_two["error_category"] == "gate_all_failed"


def test_subject_missing_at_execution_is_error(tmp_path, monkeypatch):
    guard_llm(monkeypatch)
    workspace, project = subject_workspace(tmp_path)
    subject = workspace.create_subject(
        "ws", project["id"], "Doomed", geo="SG", cadence_minutes=60,
        platforms=["Reddit"],
    )
    discovery = DiscoveryStore(tmp_path / "discovery.db")
    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(geos=()), workspace=workspace,
        discovery=discovery, broker=ScriptedBroker(),
    )
    scheduler._reconcile(T0)
    workspace.archive_subject("ws", project["id"], subject["id"])
    # Claimed before reconciliation could disable it: execution must still
    # resolve the lease honestly instead of silently skipping.
    claim = discovery.claim_due_schedules(now=T0, lease_minutes=30)[0]

    asyncio.run(scheduler._execute_claim(claim))

    run = discovery.list_radar_schedule_runs(claim["schedule_id"])[0]
    assert run["status"] == "error"
    assert run["comparable"] is False
    assert run["error_category"] == "subject_inactive"
    assert run["source_health"] is None
    assert discovery.get_radar_schedule(claim["schedule_id"])["lease_token"] is None


def test_unsupported_schedule_combo_is_refused_not_executed(tmp_path):
    discovery = DiscoveryStore(tmp_path / "discovery.db")
    discovery.upsert_radar_schedule(
        scan_mode="trends_snapshot", scope_type="subject", geo="US",
        subject_id="sub-x", interval_minutes=1440, next_run_at=T0,
    )
    topdown = FakeTopDown(discovery)
    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(geos=()), discovery=discovery, topdown=topdown,
    )
    claim = discovery.claim_due_schedules(now=T0, lease_minutes=30)[0]

    asyncio.run(scheduler._execute_claim(claim))

    assert topdown.calls == []
    run = discovery.list_radar_schedule_runs(claim["schedule_id"])[0]
    assert run["status"] == "error"
    assert run["error_category"] == "unsupported_radar_schedule"


# --- Tick gating and lifecycle ----------------------------------------------


def test_disabled_radar_does_not_reconcile_or_claim(tmp_path):
    discovery = DiscoveryStore(tmp_path / "discovery.db")
    spy = StoreSpy(discovery)
    workspace = WorkspaceStore(tmp_path / "workspaces.db")
    project = workspace.create_project("ws", "Radar", default_geo="SG")
    workspace.create_subject("ws", project["id"], "Turbines", geo="SG", cadence_minutes=60)

    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(enabled=False, geos=("US",)),
        discovery=spy, workspace=workspace,
    )

    result = asyncio.run(scheduler.tick())

    assert spy.calls == {
        "upsert_radar_schedule": 0,
        "claim_due_schedules": 0,
        "complete_schedule_attempt": 0,
        "set_radar_schedule_enabled": 0,
    }
    assert discovery.list_radar_schedules() == []
    assert result is None or result.get("enabled") is False


def test_radar_tick_uses_injected_clock_deterministically(tmp_path, monkeypatch):
    guard_llm(monkeypatch)
    scheduler = make_scheduler(tmp_path, config=RadarConfig(geos=("US",)))

    asyncio.run(scheduler.tick())
    # Second tick at the same clock moment: nothing is due anymore.
    asyncio.run(scheduler.tick())

    discovery = scheduler.discovery_store
    for row in discovery.list_radar_schedules():
        runs = discovery.list_radar_schedule_runs(row["id"])
        assert len(runs) == 1
        assert row["next_run_at"] == (
            T0 + timedelta(minutes=row["interval_minutes"])
        ).isoformat()


def test_tick_cancellation_releases_current_claim_without_preclaiming_next(
    tmp_path, monkeypatch,
):
    guard_llm(monkeypatch)

    async def scenario():
        started = asyncio.Event()

        class BlockingTopDown:
            last_run_id = ""

            async def scan_all(self, **_kwargs):
                started.set()
                await asyncio.Event().wait()

        discovery = DiscoveryStore(tmp_path / "discovery.db")
        scheduler = make_scheduler(
            tmp_path, config=RadarConfig(geos=("US",)),
            discovery=discovery, topdown=BlockingTopDown(),
        )
        task = asyncio.create_task(scheduler.tick())
        await started.wait()
        schedules = discovery.list_radar_schedules()
        assert sum(row["lease_token"] is not None for row in schedules) == 1

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        schedules = discovery.list_radar_schedules()
        assert all(row["lease_token"] is None for row in schedules)
        assert all(row["lease_until"] is None for row in schedules)
        assert all(
            discovery.list_radar_schedule_runs(row["id"]) == []
            for row in schedules
        )

    asyncio.run(scenario())


def test_long_claim_runs_with_periodic_lease_renewal(tmp_path, monkeypatch):
    guard_llm(monkeypatch)

    class RenewSpyStore(DiscoveryStore):
        def __init__(self, path):
            super().__init__(path)
            self.renewals = 0

        def renew_radar_schedule_claim(self, *args, **kwargs):
            self.renewals += 1
            return super().renew_radar_schedule_claim(*args, **kwargs)

    class SlowTopDown(FakeTopDown):
        async def scan_all(self, *args, **kwargs):
            await asyncio.sleep(0.04)
            return await super().scan_all(*args, **kwargs)

    discovery = RenewSpyStore(tmp_path / "discovery.db")
    scheduler = make_scheduler(
        tmp_path, config=RadarConfig(geos=("US",)),
        discovery=discovery, topdown=SlowTopDown(discovery),
    )
    scheduler._heartbeat_interval_seconds = 0.01

    asyncio.run(scheduler.tick())

    assert discovery.renewals >= 2
    assert all(
        row["lease_token"] is None
        for row in discovery.list_radar_schedules()
    )


def _hermetic_loop_patches(monkeypatch, tmp_path):
    import apis.dashboard_api as dashboard_api
    import apis.social_search_api as social_search_api

    class FakeRegistry:
        # TrendMonitor builds an ObservationStore from registry.db_path.
        db_path = str(tmp_path / "observations.db")

        def __init__(self):
            self.list_due_calls = 0

        def list_due(self):
            self.list_due_calls += 1
            return []

    registry = FakeRegistry()
    monkeypatch.setattr(dashboard_api, "_get_registry", lambda: registry)
    monkeypatch.setattr(
        social_search_api, "build_default_broker", lambda **kwargs: object()
    )
    monkeypatch.setattr(scheduler_mod, "_RADAR_SCHEDULER", None)
    monkeypatch.setattr(scheduler_mod, "_SCHEDULER_TASK", None)

    async def no_additional_collectors():
        return None

    monkeypatch.setattr(scheduler_mod, "investing_radar_tick_once", no_additional_collectors)
    monkeypatch.setattr(scheduler_mod, "social_pulse_tick_once", no_additional_collectors)
    return registry


def test_start_scheduler_idempotent_and_stop_cancels_and_awaits(tmp_path, monkeypatch):
    registry = _hermetic_loop_patches(monkeypatch, tmp_path)
    monkeypatch.setenv("BOUNTY_RADAR_ENABLED", "0")

    async def scenario():
        task = scheduler_mod.start_scheduler()
        assert scheduler_mod.start_scheduler() is task
        assert not task.done()
        await asyncio.sleep(0.05)
        assert registry.list_due_calls >= 1
        # Disabled radar means no scheduler instance was ever built.
        assert scheduler_mod._RADAR_SCHEDULER is None
        returned = scheduler_mod.stop_scheduler()
        assert returned is task
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()

    asyncio.run(scenario())


def test_radar_failure_does_not_stop_zone_loop(tmp_path, monkeypatch):
    registry = _hermetic_loop_patches(monkeypatch, tmp_path)
    monkeypatch.setattr(scheduler_mod, "_CHECK_INTERVAL_SECONDS", 0.02)

    class ExplodingRadar:
        async def tick(self):
            raise RuntimeError("radar down")

    monkeypatch.setattr(scheduler_mod, "_get_radar_scheduler", lambda: ExplodingRadar())

    async def scenario():
        task = scheduler_mod.start_scheduler()
        for _ in range(50):
            if registry.list_due_calls >= 2:
                break
            await asyncio.sleep(0.02)
        assert not task.done()
        assert registry.list_due_calls >= 2
        scheduler_mod.stop_scheduler()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_production_radar_uses_exclusive_discovery_instance(tmp_path, monkeypatch):
    import apis.dashboard_api as dashboard_api
    from social_scraper.monitoring.topdown import TopDownDiscovery

    discovery = DiscoveryStore(tmp_path / "discovery.db")
    workspace = WorkspaceStore(tmp_path / "workspaces.db")
    broker = ScriptedBroker()
    monkeypatch.setenv("BOUNTY_RADAR_ENABLED", "1")
    monkeypatch.setenv("BOUNTY_RADAR_GEOS", "")
    monkeypatch.setattr(scheduler_mod, "_RADAR_SCHEDULER", None)
    monkeypatch.setattr(dashboard_api, "_get_broker", lambda: broker)
    monkeypatch.setattr(dashboard_api, "_get_discovery_store", lambda: discovery)
    monkeypatch.setattr(dashboard_api, "_get_workspace_store", lambda: workspace)
    monkeypatch.setattr(
        dashboard_api, "_get_discovery",
        lambda: pytest.fail("shared dashboard discovery must not be used"),
    )

    radar = scheduler_mod._get_radar_scheduler()

    assert isinstance(radar.topdown, TopDownDiscovery)
    assert radar.topdown.discovery_store is discovery
    assert radar.topdown.broker is broker


def test_invalid_env_settings_fail_closed_with_clear_error(tmp_path, monkeypatch):
    _hermetic_loop_patches(monkeypatch, tmp_path)
    monkeypatch.setenv("BOUNTY_RADAR_ENABLED", "1")
    monkeypatch.setenv("BOUNTY_RADAR_ROOT_MAX_CANDIDATES", "0")

    async def scenario():
        with pytest.raises(ValueError, match="BOUNTY_RADAR_ROOT_MAX_CANDIDATES"):
            await scheduler_mod.radar_tick_once()

    asyncio.run(scenario())


def test_app_startup_idempotent_and_shutdown_awaits(tmp_path, monkeypatch):
    registry = _hermetic_loop_patches(monkeypatch, tmp_path)
    monkeypatch.setenv("BOUNTY_RADAR_ENABLED", "0")
    import app as app_module

    async def scenario():
        await app_module._start_monitoring_scheduler()
        first = scheduler_mod._SCHEDULER_TASK
        assert first is not None and not first.done()
        await app_module._start_monitoring_scheduler()
        assert scheduler_mod._SCHEDULER_TASK is first
        await asyncio.sleep(0.05)
        assert registry.list_due_calls >= 1
        await app_module._stop_monitoring_scheduler()
        assert first.done()
        assert first.cancelled()
        # No orphan task is left behind for the next startup to trip over.
        assert scheduler_mod._SCHEDULER_TASK is first

    asyncio.run(scenario())
