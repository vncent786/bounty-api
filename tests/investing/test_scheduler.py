import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from apis import dashboard_api, scheduler
from social_scraper.investing import InvestingRadarStore
from social_scraper.investing.social_pulse import SocialPulseStore


def test_investing_scheduler_can_be_disabled_without_touching_store(monkeypatch):
    monkeypatch.setattr(
        dashboard_api,
        "_get_investing_store",
        lambda: (_ for _ in ()).throw(AssertionError("store should not be touched")),
    )
    result = asyncio.run(scheduler.investing_radar_tick_once(
        environ={scheduler.INVESTING_RADAR_ENV_ENABLED: "false"},
    ))
    assert result is None


def test_investing_scheduler_skips_recent_completed_sweep(tmp_path, monkeypatch):
    now = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    store = InvestingRadarStore(tmp_path / "scheduler.db")
    sweep_id = store.create_sweep(1, started_at=now - timedelta(minutes=5))
    store.record_market_success(sweep_id, "US", [], observed_at=now - timedelta(minutes=4))
    store.finalize_sweep(sweep_id, completed_at=now - timedelta(minutes=3))
    monkeypatch.setattr(dashboard_api, "_get_investing_store", lambda: store)

    result = asyncio.run(scheduler.investing_radar_tick_once(environ={}, now=now))

    assert result == {"status": "not_due", "run_id": sweep_id, "started": False}
    assert store.latest_sweep()["id"] == sweep_id


def test_investing_scheduler_creates_one_central_sweep_when_due(tmp_path, monkeypatch):
    import social_scraper.investing as investing

    now = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    store = InvestingRadarStore(tmp_path / "scheduler.db")
    prior = store.create_sweep(1, started_at=now - timedelta(hours=8))
    store.record_market_success(prior, "US", [], observed_at=now - timedelta(hours=8))
    store.finalize_sweep(prior, completed_at=now - timedelta(hours=8))
    monkeypatch.setattr(dashboard_api, "_get_investing_store", lambda: store)
    calls = []

    class FakeGlobalSweep:
        def __init__(self, bound_store):
            assert bound_store is store

        async def run(self, *, sweep_id):
            calls.append(sweep_id)
            return store.finalize_sweep(sweep_id, completed_at=now)

    monkeypatch.setattr(investing, "GlobalRadarSweep", FakeGlobalSweep)

    result = asyncio.run(scheduler.investing_radar_tick_once(environ={}, now=now))

    assert result["started"] is True
    assert result["run_id"] == calls[0]
    assert result["status"] == "failed"
    assert store.latest_sweep()["total_markets"] == 125


def test_stale_running_sweep_is_closed_before_replacement(tmp_path, monkeypatch):
    import social_scraper.investing as investing

    now = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    store = InvestingRadarStore(tmp_path / "scheduler.db")
    stale_id = store.create_sweep(125, started_at=now - timedelta(hours=3))
    monkeypatch.setattr(dashboard_api, "_get_investing_store", lambda: store)

    class FakeGlobalSweep:
        def __init__(self, _store):
            pass

        async def run(self, *, sweep_id):
            return store.finalize_sweep(sweep_id, completed_at=now)

    monkeypatch.setattr(investing, "GlobalRadarSweep", FakeGlobalSweep)

    result = asyncio.run(scheduler.investing_radar_tick_once(environ={}, now=now))

    assert store.get_sweep(stale_id)["status"] == "failed"
    assert result["run_id"] != stale_id
    assert result["started"] is True


def test_social_pulse_scheduler_defaults_off_until_quality_gate(monkeypatch):
    monkeypatch.setattr(
        dashboard_api,
        "_get_social_pulse_store",
        lambda: (_ for _ in ()).throw(AssertionError("store should not be touched")),
    )
    assert asyncio.run(scheduler.social_pulse_tick_once(environ={})) is None


def test_social_pulse_scheduler_can_be_disabled(monkeypatch):
    monkeypatch.setattr(
        dashboard_api,
        "_get_social_pulse_store",
        lambda: (_ for _ in ()).throw(AssertionError("store should not be touched")),
    )
    result = asyncio.run(scheduler.social_pulse_tick_once(
        environ={scheduler.SOCIAL_PULSE_ENV_ENABLED: "false"},
    ))
    assert result is None


def test_social_pulse_scheduler_runs_central_collector_when_due(tmp_path, monkeypatch):
    import social_scraper.investing.social_pulse as pulse_module

    now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    store = SocialPulseStore(tmp_path / "social-scheduler.db")
    monkeypatch.setattr(dashboard_api, "_get_social_pulse_store", lambda: store)
    calls = []

    async def fake_build():
        calls.append("build")
        return {}

    class FakeCollector:
        def __init__(self, bound_store, fetchers):
            assert bound_store is store
            assert fetchers == {}

        async def run(self, *, run_id):
            calls.append(run_id)
            store.fail_stale_run(run_id, "controlled_completion")
            return store.get_run(run_id)

    monkeypatch.setattr(pulse_module, "build_default_social_fetchers", fake_build)
    monkeypatch.setattr(pulse_module, "SocialPulseCollector", FakeCollector)

    result = asyncio.run(scheduler.social_pulse_tick_once(
        environ={scheduler.SOCIAL_PULSE_ENV_ENABLED: "true"}, now=now
    ))

    assert result["started"] is True
    assert result["status"] == "failed"
    assert calls == ["build", result["run_id"]]


def test_social_pulse_scheduler_marks_cancelled_run_failed(tmp_path, monkeypatch):
    import social_scraper.investing.social_pulse as pulse_module

    store = SocialPulseStore(tmp_path / "social-cancel.db")
    monkeypatch.setattr(dashboard_api, "_get_social_pulse_store", lambda: store)

    async def fake_build():
        return {}

    class BlockingCollector:
        def __init__(self, _store, _fetchers):
            pass

        async def run(self, *, run_id):
            await asyncio.Event().wait()

    monkeypatch.setattr(pulse_module, "build_default_social_fetchers", fake_build)
    monkeypatch.setattr(pulse_module, "SocialPulseCollector", BlockingCollector)

    async def scenario():
        task = asyncio.create_task(scheduler.social_pulse_tick_once(
            environ={scheduler.SOCIAL_PULSE_ENV_ENABLED: "true"}
        ))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert store.latest_attempt()["status"] == "failed"
    assert store.latest_attempt()["analysis_error_category"] == "collector_cancelled"


def test_social_pulse_scheduler_skips_recent_successful_attempt(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    store = SocialPulseStore(tmp_path / "social-scheduler.db")
    run_id = store.create_run()
    for platform in ("reddit", "youtube", "tiktok", "instagram", "x"):
        store.record_source(run_id, platform, status="empty")
    store.complete_run(run_id, {
        "status": "insufficient_evidence",
        "candidates": [],
        "limitations": [],
        "error_category": None,
    })
    monkeypatch.setattr(dashboard_api, "_get_social_pulse_store", lambda: store)

    result = asyncio.run(scheduler.social_pulse_tick_once(
        environ={scheduler.SOCIAL_PULSE_ENV_ENABLED: "true"}, now=now
    ))

    assert result == {"status": "not_due", "run_id": run_id, "started": False}
