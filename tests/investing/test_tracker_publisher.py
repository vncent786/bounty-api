"""Regression checks for the disposable Tracker publication worktree."""

from pathlib import Path

from scripts import publish_investing_tracker_snapshot as publisher


def test_tracker_publisher_uses_a_disposable_clean_worktree_and_two_file_allowlist():
    source = Path(publisher.__file__).read_text(encoding="utf-8")

    assert set(path.as_posix() for path in publisher.SOURCES) == {
        "data/investing-tracker-snapshot.json",
        "public/investing-tracker-release.json",
    }
    assert "tracker-publish-" in source
    assert '"worktree", "add", "--detach"' in source
    assert "finally:" in source
    assert "remove_worktree(target)" in source
    assert "for attempt in (1, 2):" in source
    assert "tracker-deploy" not in source
