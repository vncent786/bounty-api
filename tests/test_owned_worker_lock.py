import asyncio

from social_scraper.owned_worker_lock import AsyncFileLock


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
