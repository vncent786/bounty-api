"""Persisted shadow evaluation for promotion calibration.

The runner consumes already-collected evidence only. It never collects,
invokes a model, or executes a promotion action. Undated or stale evidence
fails closed before an evaluation is written.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from .promotion import PromotionPolicy, evaluate_promotion, select_exploration_sample

MAX_EVIDENCE_AGE = timedelta(days=90)


def _as_utc(value: Any, *, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    else:
        raise ValueError(f"{label} is required")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def run_shadow_mode(
    store: Any,
    evidence_rows: Sequence[Mapping[str, Any]],
    *,
    workspace_id: str = "default",
    policy: PromotionPolicy | None = None,
    evaluated_at: Any = None,
) -> dict:
    active_policy = policy or PromotionPolicy()
    evaluation_time = _as_utc(
        evaluated_at or datetime.now(timezone.utc), label="evaluated_at"
    )
    for row in evidence_rows:
        observed_at = _as_utc(row.get("observed_at"), label="evidence observed_at")
        if observed_at > evaluation_time:
            raise ValueError("evidence observed_at cannot be in the future")
        if evaluation_time - observed_at > MAX_EVIDENCE_AGE:
            raise ValueError("evidence is stale and cannot be promoted")

    evaluations = [evaluate_promotion(row, active_policy) for row in evidence_rows]
    exploration = select_exploration_sample(evaluations, active_policy)
    exploration_by_id = {item["candidate_id"]: item for item in exploration}
    persisted = []
    for evidence, evaluation in zip(evidence_rows, evaluations):
        final = exploration_by_id.get(evaluation["candidate_id"], evaluation)
        persisted.append(store.record_promotion_evaluation(
            workspace_id=workspace_id,
            candidate_id=final["candidate_id"],
            family_id=evidence.get("family_id"),
            policy_version=final["policy_version"],
            evaluation=final,
            evidence=evidence,
            shadow=True,
            evaluated_at=evaluation_time,
        ))
    return {
        "mode": "shadow",
        "collection_performed": False,
        "executed_actions": 0,
        "evaluations": persisted,
        "funnel": store.summarize_promotion_funnel(workspace_id=workspace_id),
    }
