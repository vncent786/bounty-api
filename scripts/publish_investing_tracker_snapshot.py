"""Publish private Tracker data and a sanitized release receipt through Railway.

This script never stages the research worktree. It owns only the dedicated
deploy worktree and aborts on unrelated changes or a non-fast-forward main.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "tmp" / "tracker-deploy"
SOURCES = {
    Path("data/investing-tracker-snapshot.json"): ROOT / "data/investing-tracker-snapshot.json",
    Path("public/investing-tracker-release.json"): ROOT / "public/investing-tracker-release.json",
}


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), cwd=DEPLOY, text=True, capture_output=True, check=check
    )


subprocess.run(
    [sys.executable, str(ROOT / "scripts/build_investing_tracker_release_receipt.py")],
    cwd=ROOT,
    check=True,
    capture_output=True,
    text=True,
)
missing = [str(path) for path in SOURCES.values() if not path.exists()]
if missing:
    raise SystemExit(f"tracker publication inputs are missing: {missing}")
if not DEPLOY.exists():
    raise SystemExit("dedicated tracker deploy worktree is missing")

allowed = {str(path).replace("\\", "/") for path in SOURCES}
status = run("git", "status", "--porcelain").stdout.splitlines()
unexpected = []
for line in status:
    path = line[3:].strip().replace("\\", "/")
    if path not in allowed:
        unexpected.append(path)
if unexpected:
    raise SystemExit(f"deploy worktree has unexpected changes: {unexpected}")

run("git", "fetch", "origin", "main")
for relative in SOURCES:
    if any(line[3:].strip().replace("\\", "/") == str(relative).replace("\\", "/") for line in status):
        run("git", "restore", "--", str(relative))
run("git", "merge", "--ff-only", "origin/main")

for relative, source in SOURCES.items():
    target = DEPLOY / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)

relative_paths = [str(path).replace("\\", "/") for path in SOURCES]
if not run("git", "diff", "--quiet", "--", *relative_paths, check=False).returncode:
    print(json.dumps({"status": "no_change", "paths": relative_paths}))
    raise SystemExit(0)

run("git", "add", "--", *relative_paths)
staged = sorted(run("git", "diff", "--cached", "--name-only").stdout.splitlines())
if staged != sorted(relative_paths):
    raise SystemExit(f"refusing unexpected staged paths: {staged}")
run("git", "commit", "-m", "data: refresh private investment tracker")
run("git", "push", "origin", "HEAD:main")
print(json.dumps({
    "status": "published",
    "commit": run("git", "rev-parse", "HEAD").stdout.strip(),
    "paths": relative_paths,
}))
