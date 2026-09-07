"""Publish the private Tracker snapshot through a disposable clean worktree.

The research worktree is never staged. Every attempt starts from current
origin/main, commits only the two allowlisted data files, and removes its
worktree even after failure.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    Path("data/investing-tracker-snapshot.json"): ROOT / "data/investing-tracker-snapshot.json",
    Path("public/investing-tracker-release.json"): ROOT / "public/investing-tracker-release.json",
}


def run(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), cwd=cwd, text=True, capture_output=True, check=check
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def remove_worktree(target: Path) -> None:
    if target.exists():
        run(ROOT, "git", "worktree", "remove", "--force", str(target), check=False)
    run(ROOT, "git", "worktree", "prune", check=False)


def publish_once(target: Path) -> dict[str, object]:
    remove_worktree(target)
    run(ROOT, "git", "fetch", "origin", "main")
    run(ROOT, "git", "worktree", "add", "--detach", str(target), "origin/main")
    try:
        for relative, source in SOURCES.items():
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            if digest(source) != digest(destination):
                raise RuntimeError(f"copy verification failed: {relative}")

        relative_paths = [path.as_posix() for path in SOURCES]
        changed = run(
            target, "git", "diff", "--name-only", "--", *relative_paths
        ).stdout.splitlines()
        if not changed:
            return {"status": "no_change", "paths": relative_paths}
        if sorted(changed) != sorted(relative_paths):
            raise RuntimeError(f"unexpected publication paths: {changed}")

        run(target, "git", "add", "--", *relative_paths)
        staged = run(target, "git", "diff", "--cached", "--name-only").stdout.splitlines()
        if sorted(staged) != sorted(relative_paths):
            raise RuntimeError(f"refusing unexpected staged paths: {staged}")
        run(target, "git", "commit", "-m", "data: refresh private investment tracker")
        pushed = run(target, "git", "push", "origin", "HEAD:main", check=False)
        if pushed.returncode:
            raise RuntimeError("non_fast_forward" if "non-fast-forward" in pushed.stderr else "push_failed")
        return {
            "status": "published",
            "commit": run(target, "git", "rev-parse", "HEAD").stdout.strip(),
            "paths": relative_paths,
        }
    finally:
        remove_worktree(target)


def main() -> int:
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_investing_tracker_release_receipt.py")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    missing = [str(path) for path in SOURCES.values() if not path.is_file()]
    if missing:
        raise SystemExit(f"tracker publication inputs are missing: {missing}")

    last_error = None
    for attempt in (1, 2):
        target = ROOT / "tmp" / f"tracker-publish-{os.getpid()}-{attempt}"
        try:
            result = publish_once(target)
            result["attempt"] = attempt
            print(json.dumps(result))
            return 0
        except RuntimeError as exc:
            last_error = str(exc)
            if last_error != "non_fast_forward" or attempt == 2:
                break
    print(json.dumps({"status": "failed", "reason": last_error or "publication_failed"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
