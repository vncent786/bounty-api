import asyncio

import pytest

from social_scraper.owned_worker_lock import AsyncFileLock, OwnedWorkerBusyError


def test_owned_worker_file_lock_reuses_path_across_event_loops(tmp_path):
    path = tmp_path / "profile.lock"
    entered = []

    async def enter_once(value):
        async with AsyncFileLock(path, timeout_seconds=2):
            entered.append(value)

    asyncio.run(enter_once("loop-1"))
    asyncio.run(enter_once("loop-2"))

    assert entered == ["loop-1", "loop-2"]
    assert path.exists()


def test_owned_worker_lock_timeout_has_typed_busy_category(tmp_path):
    path = tmp_path / "profile.lock"

    async def scenario():
        holder = AsyncFileLock(path, timeout_seconds=1)
        await holder.__aenter__()
        try:
            with pytest.raises(OwnedWorkerBusyError) as captured:
                await AsyncFileLock(path, timeout_seconds=0.05).__aenter__()
            assert captured.value.error_category == "owned_worker_profile_busy"
            assert str(captured.value) == "owned_worker_profile_busy:profile.lock"
        finally:
            await holder.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_cancelled_lock_waiter_cannot_acquire_after_cancellation(tmp_path):
    path = tmp_path / "profile.lock"
    entered = []

    async def scenario():
        holder = AsyncFileLock(path, timeout_seconds=1)
        await holder.__aenter__()
        waiter = AsyncFileLock(path, timeout_seconds=1)

        async def wait():
            async with waiter:
                entered.append("cancelled_waiter_entered")

        task = asyncio.create_task(wait())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await holder.__aexit__(None, None, None)
        await asyncio.sleep(0.1)
        async with AsyncFileLock(path, timeout_seconds=0.5):
            entered.append("third_entered")

    asyncio.run(scenario())
    assert entered == ["third_entered"]
