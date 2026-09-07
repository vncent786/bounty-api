"""Build a sanitized public receipt for the private Investment Tracker release."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data/investing-tracker-snapshot.json"
OUTPUT = ROOT / "public/investing-tracker-release.json"
GHOST_IDEA_ID = "standing::ghost-aw-kdp"
GHOST_PLATFORMS = ("x", "tiktok", "instagram", "reddit", "youtube")
GHOST_PLATFORM_LABELS = {
    "x": "X",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "reddit": "Reddit",
    "youtube": "YouTube",
}


def sha(path: Path) -> str:
    """Hash text deliverables after canonical LF normalization.

    Git may materialize CRLF on Windows while Railway serves LF from the same
    committed blob. The normalized digest is stable across both environments.
    """

    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _count(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"GHOST publication blocked: {field} must be a non-negative integer")
    return value


def validate_ghost_evidence_for_publication(snapshot: dict) -> dict:
    """Reject a Tracker snapshot whose displayed GHOST counts are not fully linked."""
    ghost = next(
        (
            idea
            for idea in snapshot.get("ideas", [])
            if isinstance(idea, dict) and idea.get("idea_id") == GHOST_IDEA_ID
        ),
        None,
    )
    if not isinstance(ghost, dict):
        raise ValueError("GHOST publication blocked: standing monitor is missing")
    monitor = ghost.get("monitor_dashboard")
    conversations = monitor.get("conversations") if isinstance(monitor, dict) else None
    evidence = conversations.get("evidence") if isinstance(conversations, dict) else None
    if not isinstance(evidence, dict) or evidence.get("status") != "verified":
        raise ValueError("GHOST publication blocked: conversation evidence is not verified")

    platform_rows = evidence.get("platforms")
    if not isinstance(platform_rows, dict):
        raise ValueError("GHOST publication blocked: platform evidence is missing")
    platform_totals = {"original_posts": 0, "comments_replies": 0}
    for platform in GHOST_PLATFORMS:
        row = platform_rows.get(platform)
        if not isinstance(row, dict) or row.get("count_status") != "verified":
            raise ValueError(
                f"GHOST publication blocked: {GHOST_PLATFORM_LABELS[platform]} evidence counts do not reconcile"
            )
        displayed_posts = _count(
            row.get("displayed_original_posts"),
            f"{platform}.displayed_original_posts",
        )
        linked_posts = _count(
            row.get("linked_original_posts"),
            f"{platform}.linked_original_posts",
        )
        displayed_responses = _count(
            row.get("displayed_comments_replies"),
            f"{platform}.displayed_comments_replies",
        )
        linked_responses = _count(
            row.get("linked_comments_replies"),
            f"{platform}.linked_comments_replies",
        )
        if displayed_posts != linked_posts or displayed_responses != linked_responses:
            raise ValueError(
                f"GHOST publication blocked: {GHOST_PLATFORM_LABELS[platform]} evidence counts do not reconcile"
            )
        platform_totals["original_posts"] += displayed_posts
        platform_totals["comments_replies"] += displayed_responses

    displayed = evidence.get("displayed_counts")
    persisted = evidence.get("persisted_link_counts")
    if displayed != platform_totals or persisted != platform_totals:
        raise ValueError("GHOST publication blocked: aggregate evidence counts do not reconcile")
    link_total = _count(evidence.get("total_clickable_links"), "total_clickable_links")
    expected_links = platform_totals["original_posts"] + platform_totals["comments_replies"]
    if link_total <= 0 or link_total != expected_links:
        raise ValueError("GHOST publication blocked: clickable link total does not reconcile")

    return {
        "status": "verified",
        "observed_at": conversations.get("observed_at") or (
            (conversations.get("source_health") or {}).get("visible_observed_at")
            if isinstance(conversations.get("source_health"), dict)
            else None
        ),
        **platform_totals,
        "total_clickable_links": link_total,
    }


def build_release_receipt(snapshot_path: Path = SNAPSHOT) -> dict:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    ghost_evidence = validate_ghost_evidence_for_publication(snapshot)
    trends = snapshot.get("trend_release_metadata")
    if not isinstance(trends, dict):
        raise ValueError("Tracker publication blocked: trend release metadata is missing")
    requested_series = _count(trends.get("requested_series"), "requested_series")
    terminal_series = _count(trends.get("terminal_series"), "terminal_series")
    if terminal_series > requested_series:
        raise ValueError("Tracker publication blocked: terminal Google series exceed requests")
    summary = snapshot["summary"]
    receipt = {
        "schema_version": "bounty-investment-tracker-release/1",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "tracker_schema_version": snapshot["schema_version"],
        "tracker_snapshot_sha256": sha(snapshot_path),
        "tracker_snapshot_hash_basis": "utf8_text_lf_normalized",
        "methodology": trends["methodology"],
        "trend_data_built_at": trends["built_at"],
        "decision_queue": summary["decision_queue"],
        "trade_ready_now": summary["trade_ready_now"],
        "primary_state_counts": summary["primary_state_counts"],
        "signal_state_counts": summary["signal_state_counts"],
        "google_series": terminal_series,
        "failed_google_series": requested_series - terminal_series,
        "google_preflight": trends["preflight_status"],
        "ghost_conversation_evidence": ghost_evidence,
        "private_data": "Token-gated. This receipt contains aggregate counts and hashes only.",
    }
    receipt["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return receipt


def main() -> None:
    receipt = build_release_receipt()
    OUTPUT.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(OUTPUT), **receipt}, indent=2))


if __name__ == "__main__":
    main()
