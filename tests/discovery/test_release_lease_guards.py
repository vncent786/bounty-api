"""Release/lease guard regressions for DiscoveryStore (storage layer only).

These tests pin the worker-lease barriers independently of the execution
and radar-scheduler test modules:

* every worker-owned terminal (complete/partial/error/cancelled) mutation
  of a research run requires the matching claim token, ``running`` state
  and an unexpired lease;
* a tokenless cancel (or any tokenless non-terminal flip) must not
  override another live worker, and live leases must block re-claims;
* radar attempt completion, renewal and release reject expired leases and
  wrong tokens, following the research-run renewal pattern;
* claiming is exclusive across store instances sharing one database.
"""

from datetime import datetime, timedelta, timezone

import pytest

import social_scraper.discovery.storage as storage_module
from social_scraper.discovery.storage import DiscoveryStore

T0 = datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)
TERMINAL_STATUSES = ("complete", "partial", "error", "cancelled")


def make_store(tmp_path, name="store.db"):
    return DiscoveryStore(tmp_path / name)


def make_planned_run(store):
    run = store.create_research_run(
        workspace_id="ws1",
        requested_budget={"root_probe_candidates": 1},
        effective_budget={"root_probe_candidates": 1},
        plan={"candidates": []},
        status="planned",
    )
    return run["id"]


def make_claimed_run(store, *, now=T0, lease_minutes=2):
    run_id = make_planned_run(store)
    token = store.claim_research_run(run_id, lease_minutes=lease_minutes, now=now)
    assert token, "expected the fresh planned run to be claimable"
    return run_id, token


def make_due_schedule(store, *, next_run_at=T0, now=T0):
    schedule = store.upsert_radar_schedule(
        scan_mode="trends_snapshot",
        scope_type="geography",
        geo="US",
        interval_minutes=1440,
        next_run_at=next_run_at,
        now=now,
    )
    return schedule["id"]


def make_radar_claim(store, *, now=T0, lease_minutes=1):
    schedule_id = make_due_schedule(store)
    claims = store.claim_due_schedules(now=now, lease_minutes=lease_minutes)
    assert len(claims) == 1, "expected exactly one due schedule claim"
    claim = claims[0]
    assert claim["schedule_id"] == schedule_id
    return schedule_id, claim["claim_token"]


# --- Research runs: terminal mutations ----------------------------------


@pytest.mark.parametrize("status", TERMINAL_STATUSES)
def test_tokenless_terminal_update_cannot_override_live_worker(tmp_path, status):
    store = make_store(tmp_path)
    run_id, token = make_claimed_run(store)
    before = store.get_research_run(run_id)

    with pytest.raises(ValueError, match="require a claim token"):
        store.update_research_run(run_id, status=status, now=T0)

    after = store.get_research_run(run_id)
    assert after["status"] == "running"
    assert after["lease_token"] == token
    assert after["lease_until"] == before["lease_until"]
    assert after["completed_at"] is None


@pytest.mark.parametrize("status", TERMINAL_STATUSES)
def test_terminal_update_rejects_wrong_token(tmp_path, status):
    store = make_store(tmp_path)
    run_id, token = make_claimed_run(store)

    with pytest.raises(ValueError, match="stale claim"):
        store.update_research_run(
            run_id, status=status, claim_token="not-the-worker-token", now=T0,
        )

    after = store.get_research_run(run_id)
    assert after["status"] == "running"
    assert after["lease_token"] == token
    assert after["completed_at"] is None


@pytest.mark.parametrize("status", TERMINAL_STATUSES)
def test_terminal_update_rejects_expired_lease(tmp_path, status):
    store = make_store(tmp_path)
    # Lease valid until T0+2m; the worker reports back after it expired.
    run_id, token = make_claimed_run(store, now=T0, lease_minutes=2)

    with pytest.raises(ValueError, match="stale claim"):
        store.update_research_run(
            run_id, status=status, claim_token=token,
            now=T0 + timedelta(minutes=3),
        )

    after = store.get_research_run(run_id)
    assert after["status"] == "running"
    assert after["lease_token"] == token, "expired completion must not clear the lease"
    assert after["completed_at"] is None


def test_terminal_update_requires_running_state_and_lease(tmp_path):
    store = make_store(tmp_path)
    run_id, token = make_claimed_run(store)

    finished = store.update_research_run(
        run_id, status="complete", claim_token=token, now=T0 + timedelta(seconds=30),
    )
    assert finished["status"] == "complete"
    assert finished["lease_token"] is None
    assert finished["lease_until"] is None

    # Replaying any terminal status with the (now cleared) token must fail:
    # the row is no longer running and holds no lease.
    for status in TERMINAL_STATUSES:
        with pytest.raises(ValueError, match="stale claim"):
            store.update_research_run(
                run_id, status=status, claim_token=token,
                now=T0 + timedelta(seconds=60),
            )

    # A completed run is not claimable by any other worker either.
    assert store.claim_research_run(run_id, now=T0 + timedelta(minutes=5)) is None


# --- Research runs: non-terminal mutations cannot bypass the barrier -----


def test_tokenless_nonterminal_update_cannot_override_live_worker(tmp_path):
    store = make_store(tmp_path)
    run_id, token = make_claimed_run(store)
    lease_until = store.get_research_run(run_id)["lease_until"]

    # Tokenless callers historically reached the non-terminal branch; a
    # live worker's row must be off-limits to them now.
    with pytest.raises(ValueError, match="stale claim"):
        store.update_research_run(run_id, status="planned", now=T0)

    after = store.get_research_run(run_id)
    assert after["status"] == "running"
    assert after["lease_token"] == token
    assert after["lease_until"] == lease_until


def test_nonterminal_update_with_expired_lease_is_rejected_for_token_holder(tmp_path):
    store = make_store(tmp_path)
    run_id, token = make_claimed_run(store, lease_minutes=2)

    with pytest.raises(ValueError, match="stale claim"):
        store.update_research_run(
            run_id, status="running", claim_token=token,
            now=T0 + timedelta(minutes=3),
        )


def test_nonterminal_updates_still_work_on_rows_without_live_leases(tmp_path):
    """Compatibility: tokenless non-terminal updates stay legal where no
    live lease exists (fresh planned rows, expired leases)."""
    store = make_store(tmp_path)
    run_id = make_planned_run(store)

    started = store.update_research_run(run_id, status="running", now=T0)
    assert started["status"] == "running"
    assert started["started_at"] == T0.isoformat()

    # Expired lease (never renewed past T0+2m) is reclaimable ground: a
    # tokenless recovery flip back to planned is allowed at T0+5m.
    reclaimed = store.update_research_run(
        run_id, status="planned", now=T0 + timedelta(minutes=5),
    )
    assert reclaimed["status"] == "planned"


def test_claimed_run_accepts_its_own_nonterminal_update_while_live(tmp_path):
    store = make_store(tmp_path)
    run_id, token = make_claimed_run(store, lease_minutes=5)

    touched = store.update_research_run(
        run_id, status="running", claim_token=token,
        now=T0 + timedelta(seconds=30),
    )
    assert touched["status"] == "running"
    assert touched["lease_token"] == token


# --- Research runs: claim exclusivity, renewal and release ----------------


def test_live_lease_blocks_reclaim_until_it_expires(tmp_path):
    store = make_store(tmp_path)
    run_id, first_token = make_claimed_run(store, lease_minutes=2)

    # Barrier: nobody can claim a row with a live lease.
    assert store.claim_research_run(run_id, now=T0 + timedelta(minutes=1)) is None
    assert store.get_research_run(run_id)["lease_token"] == first_token

    # After expiry the row is reclaimable and exclusivity transfers.
    second_token = store.claim_research_run(
        run_id, now=T0 + timedelta(minutes=3), lease_minutes=2,
    )
    assert second_token and second_token != first_token

    with pytest.raises(ValueError, match="stale claim"):
        store.update_research_run(
            run_id, status="complete", claim_token=first_token,
            now=T0 + timedelta(minutes=3),
        )
    finished = store.update_research_run(
        run_id, status="complete", claim_token=second_token,
        now=T0 + timedelta(minutes=3),
    )
    assert finished["status"] == "complete"


def test_claim_is_exclusive_across_store_instances(tmp_path):
    db_path = tmp_path / "shared.db"
    store_a = DiscoveryStore(db_path)
    store_b = DiscoveryStore(db_path)
    run_id = make_planned_run(store_a)

    token_a = store_a.claim_research_run(run_id, now=T0)
    token_b = store_b.claim_research_run(run_id, now=T0)
    assert token_a
    assert token_b is None


def test_renew_rejects_wrong_token_expired_lease_and_unknown_run(tmp_path):
    store = make_store(tmp_path)
    run_id, token = make_claimed_run(store, lease_minutes=2)

    assert store.renew_research_run_claim(
        run_id, "wrong-token", now=T0,
    ) is False
    assert store.renew_research_run_claim(
        run_id, token, now=T0 + timedelta(minutes=3),
    ) is False, "expired leases must not be renewable"
    assert store.renew_research_run_claim(
        "missing-run", token, now=T0,
    ) is False

    assert store.renew_research_run_claim(
        run_id, token, now=T0 + timedelta(minutes=1), lease_minutes=5,
    ) is True
    assert store.get_research_run(run_id)["lease_until"] == (
        T0 + timedelta(minutes=6)
    ).isoformat()


def test_release_requires_matching_unexpired_token(tmp_path):
    store = make_store(tmp_path)
    run_id, token = make_claimed_run(store, lease_minutes=2)

    assert store.release_research_run_claim(
        run_id, "wrong-token", now=T0,
    ) is False
    assert store.release_research_run_claim(
        run_id, token, now=T0 + timedelta(minutes=3),
    ) is False, "an expired lease is not releasable"
    assert store.get_research_run(run_id)["lease_token"] == token

    assert store.release_research_run_claim(
        run_id, token, now=T0 + timedelta(seconds=30),
    ) is True
    assert store.release_research_run_claim(
        run_id, token, now=T0 + timedelta(seconds=31),
    ) is False, "a released lease cannot be released twice"


def test_save_findings_rejects_expired_lease(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    run_id, token = make_claimed_run(store, lease_minutes=2)

    class _FrozenDatetime:
        """Wall-clock shim: save_findings reads datetime.now().isoformat()."""

        @staticmethod
        def now(_tz=None):
            return T0 + timedelta(minutes=3)

    monkeypatch.setattr(storage_module, "datetime", _FrozenDatetime)

    with pytest.raises(ValueError, match="stale claim"):
        store.save_findings(
            run_id, "cand1", "topic", "supported",
            {"summary": "late write"}, claim_token=token,
        )


# --- Radar schedules: attempt completion, renewal, release ----------------


@pytest.mark.parametrize("status", ("complete", "partial", "error"))
def test_radar_completion_rejects_expired_lease(tmp_path, status):
    store = make_store(tmp_path)
    # Lease lives until T0+1m; the worker finalizes after it expired.
    schedule_id, token = make_radar_claim(store, now=T0, lease_minutes=1)

    with pytest.raises(RuntimeError, match="no longer valid"):
        store.complete_schedule_attempt(
            schedule_id, token, status=status, comparable=True,
            error_category="boom" if status == "error" else None,
            now=T0 + timedelta(minutes=2),
        )

    assert store.list_radar_schedule_runs(schedule_id) == []
    schedule = store.get_radar_schedule(schedule_id)
    assert schedule["lease_token"] == token, "stale completion must not clear the lease"
    assert schedule["next_run_at"] == T0.isoformat(), "cadence must not advance"


def test_radar_completion_rejects_wrong_and_empty_tokens(tmp_path):
    store = make_store(tmp_path)
    schedule_id, token = make_radar_claim(store, now=T0, lease_minutes=10)

    with pytest.raises(ValueError, match="lease_token is required"):
        store.complete_schedule_attempt(
            schedule_id, "", status="complete", comparable=True, now=T0,
        )
    with pytest.raises(RuntimeError, match="no longer valid"):
        store.complete_schedule_attempt(
            schedule_id, "wrong-token", status="complete", comparable=True, now=T0,
        )

    assert store.list_radar_schedule_runs(schedule_id) == []
    receipt = store.complete_schedule_attempt(
        schedule_id, token, status="complete", comparable=True, now=T0,
    )
    assert receipt["run"]["status"] == "complete"


def test_radar_renewal_and_release_reject_expired_leases_and_wrong_tokens(tmp_path):
    store = make_store(tmp_path)
    schedule_id, token = make_radar_claim(store, now=T0, lease_minutes=1)

    assert store.renew_radar_schedule_claim(
        schedule_id, "wrong-token", now=T0, lease_minutes=5,
    ) is None
    assert store.renew_radar_schedule_claim(
        schedule_id, token, now=T0 + timedelta(minutes=2), lease_minutes=5,
    ) is None, "expired radar leases must not be renewable"
    assert store.release_radar_schedule_claim(
        schedule_id, token, now=T0 + timedelta(minutes=2),
    ) is False, "expired radar leases must not be releasable"
    assert store.get_radar_schedule(schedule_id)["lease_token"] == token

    renewed = store.renew_radar_schedule_claim(
        schedule_id, token, now=T0 + timedelta(seconds=30), lease_minutes=5,
    )
    assert renewed is not None
    assert renewed["lease_until"] == (
        T0 + timedelta(seconds=30, minutes=5)
    ).isoformat()

    assert store.release_radar_schedule_claim(
        schedule_id, token, now=T0 + timedelta(seconds=31),
    ) is True
    assert store.release_radar_schedule_claim(
        schedule_id, token, now=T0 + timedelta(seconds=32),
    ) is False, "a released radar claim cannot be released twice"


def test_radar_claim_barrier_across_store_instances(tmp_path):
    db_path = tmp_path / "shared-radar.db"
    store_a = DiscoveryStore(db_path)
    store_b = DiscoveryStore(db_path)
    schedule_id = make_due_schedule(store_a)

    claims_a = store_a.claim_due_schedules(now=T0, lease_minutes=10, limit=5)
    claims_b = store_b.claim_due_schedules(now=T0, lease_minutes=10, limit=5)
    assert len(claims_a) == 1
    assert claims_b == [], "a live radar lease must exclude the row for every replica"

    # Once the lease expires, another replica may reclaim it.
    reclaimed = store_b.claim_due_schedules(
        now=T0 + timedelta(minutes=11), lease_minutes=10, limit=5,
    )
    assert len(reclaimed) == 1
    assert reclaimed[0]["schedule_id"] == schedule_id
    assert reclaimed[0]["claim_token"] != claims_a[0]["claim_token"]
