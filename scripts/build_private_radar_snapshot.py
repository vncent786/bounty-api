"""Build the sanitized phone-review snapshot from persisted private Radar evidence."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from social_scraper.investing.private_radar import (
    PrivateRadarStore,
    candidate_review_status,
    is_supported_qualified,
    review_decision_with_current_methodology,
)
from social_scraper.investing.trajectory import (
    collect_search_trajectory,
    derive_trajectory_query,
)


DEFAULT_DB = ROOT / "data" / "private_radar.db"
DEFAULT_OUTPUT = ROOT / "public" / "private-radar-snapshot.json"


def _snapshot_evidence(item: dict[str, Any]) -> dict[str, Any]:
    platform = str(item.get("platform") or "unknown")
    source_url = str(item.get("url") or "")
    external_id = str(item.get("external_id") or "")
    display_url = source_url
    if platform == "x" and external_id.isdigit():
        display_url = (
            "https://platform.twitter.com/embed/Tweet.html?dnt=true&id="
            f"{external_id}"
        )
    return {
        "id": str(item.get("id") or ""),
        "platform": platform,
        "url": display_url,
        "source_url": source_url,
        "author": item.get("author"),
        "text": str(item.get("text") or "")[:500],
        "created_at": item.get("created_at"),
        "engagement": (
            dict(item.get("engagement"))
            if isinstance(item.get("engagement"), dict)
            else {}
        ),
    }


def _recheck_decisions(
    store: PrivateRadarStore,
    scan: dict[str, Any],
    *,
    trajectory_provider=collect_search_trajectory,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence = store.evidence_for_run(scan["id"])
    evidence_by_id = {str(item["id"]): item for item in evidence}
    panel_by_id = {
        evidence_id: str(item.get("panel_id") or "")
        for evidence_id, item in evidence_by_id.items()
    }
    qualified = []
    reviewed = []
    for saved in scan.get("decisions") or []:
        candidate_saved = dict(saved)
        if not isinstance(candidate_saved.get("trajectory"), dict):
            trajectory_query = str(
                candidate_saved.get("trajectory_query")
                or derive_trajectory_query(candidate_saved)
            ).strip()
            candidate_saved["trajectory"] = trajectory_provider(trajectory_query)
        decision, linked_records = review_decision_with_current_methodology(
            candidate_saved, evidence_by_id
        )
        if not decision or not linked_records:
            continue
        linked = [_snapshot_evidence(item) for item in linked_records]
        if is_supported_qualified(decision, set(evidence_by_id), panel_by_id):
            qualified.append({**decision, "evidence": linked})
            continue
        if not linked:
            continue
        review_status, blocking_reasons, caveats = candidate_review_status(decision)
        reviewed.append({
            **decision,
            "review_status": review_status,
            "blocking_reasons": blocking_reasons,
            "caveats": caveats,
            "evidence": linked,
        })
    reviewed.sort(key=lambda item: (
        {"search_movement_only": 0, "needs_more_evidence": 1, "rejected": 2}.get(
            str(item.get("review_status")), 3
        ),
        str(item.get("label") or ""),
    ))
    return qualified, reviewed


def build_snapshot(db_path: Path) -> dict[str, Any]:
    store = PrivateRadarStore(db_path)
    scan = store.latest_attempt()
    if not scan or scan.get("status") == "running":
        raise RuntimeError("a terminal private Radar scan is required")
    payload = store.public_payload()
    qualified, reviewed = _recheck_decisions(store, scan)
    payload["items"] = qualified
    payload["review_items"] = reviewed
    payload["review_scan"] = store._public_scan(scan)
    payload["coverage"]["summary"] = (
        f"{len(qualified)} trade-ready leads and {len(reviewed)} reviewed subjects "
        f"from {scan['evidence_count']} stored evidence records; "
        + payload["coverage"]["summary"].split(";", 1)[-1].strip()
    )
    payload["coverage"]["sources"] = []
    payload["snapshot_observed_at"] = scan.get("completed_at") or scan.get("started_at")
    payload["snapshot_mode"] = "read_only"
    payload["methodology_recheck"] = {
        "performed": True,
        "source_run_id": scan["id"],
        "new_collection": False,
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.getenv("BOUNTY_PRIVATE_RADAR_DB") or DEFAULT_DB),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_snapshot(args.db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "qualified": len(payload["items"]),
        "reviewed": len(payload["review_items"]),
        "statuses": [item["review_status"] for item in payload["review_items"]],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
