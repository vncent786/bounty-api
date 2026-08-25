import asyncio
from datetime import datetime, timedelta, timezone

from apis import dashboard_api, scheduler
from social_scraper.investing import InvestingRadarStore


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
