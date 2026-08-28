from pathlib import Path

import pytest

from scripts.build_private_radar_snapshot import build_snapshot
from social_scraper.investing.private_radar import PrivateRadarStore


def test_snapshot_builder_rejects_failed_latest_scan_instead_of_overwriting_last_good_file(
    tmp_path: Path,
):
    store = PrivateRadarStore(tmp_path / "radar.db")
    run_id, created = store.create_scan_if_idle()
    assert created is True
    store.fail_scan(run_id, "preflight_reddit_unavailable")

    with pytest.raises(RuntimeError, match="successful terminal private Radar scan"):
        build_snapshot(tmp_path / "radar.db")
