"""Read-only assembly of persisted topic families for Global Explore."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import urlparse

from .explore_read_model import apply_perspective, build_explore_read_model

MAX_TRAJECTORY_AGE = timedelta(days=90)


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _trajectory(store: Any, members: list[Mapping[str, Any]]) -> tuple[dict, list[str]]:
    limitations: list[str] = []
    if not members:
        return {}, ["No member series are linked to this family."]
    member = members[0]
    history = store.get_candidate_history(member["geo"], member["normalized_keyword"])
    observations = [
        row for row in (history.get("observations") or [])
        if row.get("run_status") == "complete"
        and bool(row.get("run_comparable"))
        and _parse_timestamp(row.get("observed_at")) is not None
    ]
    if len(observations) < 2:
        return {}, ["Fewer than two comparable member snapshots are available."]
    previous, current = observations[-2:]
    current_at = _parse_timestamp(current.get("observed_at"))
    if current_at is None or datetime.now(timezone.utc) - current_at > MAX_TRAJECTORY_AGE:
        return {}, ["The latest comparable snapshot is stale; trajectory is withheld."]
    before = previous.get("search_volume")
    after = current.get("search_volume")
    direction = "unknown"
    if before is not None and after is not None:
        direction = "rising" if after > before else "falling" if after < before else "flat"
    if history.get("gaps"):
        limitations.append("The member series contains explicit coverage gaps; no values were interpolated.")
    return {
        "direction": direction,
        "reported_volume": after,
        "comparison": {"previous": before, "current": after},
        "source": "Google Trends feed",
        "period": {
            "start": previous.get("observed_at"),
            "end": current.get("observed_at"),
        },
        "comparable_snapshots": len(observations),
        "gap_count": len(history.get("gaps") or []),
    }, limitations


def _valid_citation(value: Any) -> bool:
    url = value.get("url") if isinstance(value, Mapping) else value
    candidate = str(url or "").strip()
    if not candidate or any(character.isspace() for character in candidate):
        return False
    try:
        parsed = urlparse(candidate)
        hostname = parsed.hostname
    except (TypeError, ValueError):
        return False
    return parsed.scheme in {"http", "https"} and bool(hostname)


def _membership_evidence(members: list[Mapping[str, Any]]) -> dict:
    support: list[Any] = []
    explanations: list[str] = []
    features: dict[str, Any] = {}
    for member in members:
        evidence = member.get("evidence") or {}
        if evidence.get("explanation"):
            explanations.append(str(evidence["explanation"]))
        values = evidence.get("support") or []
        values = values if isinstance(values, list) else [values]
        support.extend(value for value in values if _valid_citation(value))
        if isinstance(evidence.get("features"), Mapping):
            features.update(evidence["features"])
    return {
        "what_it_is": {
            "text": explanations[0] if explanations else None,
            "support": support,
        },
        "features": features,
    }


def build_persisted_explore(
    store: Any,
    *,
    workspace_id: str = "default",
    perspective: Mapping[str, Any] | None = None,
) -> dict:
    """Project persisted data only; no connector or LLM entry point is reachable."""
    evaluations = store.list_promotion_evaluations(workspace_id=workspace_id)
    latest_by_family: dict[str, dict] = {}
    for row in evaluations:
        if row.get("family_id"):
            latest_by_family[str(row["family_id"])] = row
    dismissed_family_ids = {
        str(row["family_id"])
        for row in store.list_promotion_labels(workspace_id=workspace_id)
        if row.get("family_id") and row.get("action_type") == "dismiss"
    }

    items: list[dict] = []
    for family in store.list_topic_families():
        members = family.get("memberships") or []
        trajectory, trajectory_limits = _trajectory(store, members)
        evidence = _membership_evidence(members)
        promoted = latest_by_family.get(str(family["id"]))
        promotion_evidence = deepcopy((promoted or {}).get("evidence") or {})
        evaluation = (promoted or {}).get("evaluation") or {}
        evidence.update({
            "trajectory": trajectory,
            "corroboration": promotion_evidence.get("root_summary") or {},
            "propagation": promotion_evidence.get("propagation") or {},
            "coverage": {"source_health": promotion_evidence.get("source_health") or {}},
            "conversation_depth": promotion_evidence.get("conversation_depth") or {},
            "limitations": trajectory_limits,
        })
        geos = sorted({str(member.get("geo") or "") for member in members if member.get("geo")})
        rejected = family["status"] == "retired" or str(family["id"]) in dismissed_family_ids
        family_record = {
            "id": family["id"],
            "canonical_label": family["canonical_label"],
            "geo": geos[0] if len(geos) == 1 else "Global",
            "status": "rejected" if rejected else "active",
            "members": [{"term": member["normalized_keyword"]} for member in members],
        }
        model = build_explore_read_model(
            family_record, evidence=evidence, promotion=evaluation
        )
        model["status"] = "rejected" if rejected else "active"
        model["family_status"] = family["status"]
        items.append(model)

    result = apply_perspective(items, perspective) if perspective else {
        "perspective": None,
        "collection_performed": False,
        "items": items,
        "excluded_family_ids": [],
    }
    result["workspace_id"] = workspace_id
    result["funnel"] = store.summarize_promotion_funnel(workspace_id=workspace_id)
    return result
