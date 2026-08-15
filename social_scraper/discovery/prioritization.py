"""Deterministic candidate ordering with inspectable, caller-selected components."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_METRIC_ORDER = (
    "recency", "volume", "growth", "category_match", "already_processed"
)
_ALLOWED_METRICS = frozenset((*DEFAULT_METRIC_ORDER, "search_volume", "growth_pct"))
_CANDIDATE_FIELD_ALIASES = {
    "volume": ("volume", "search_volume"),
    "search_volume": ("search_volume", "volume"),
    "growth": ("growth", "growth_pct"),
    "growth_pct": ("growth_pct", "growth"),
}


@dataclass(frozen=True)
class PrioritizationConfig:
    metric_order: tuple[str, ...] = DEFAULT_METRIC_ORDER

    def __post_init__(self) -> None:
        unknown = set(self.metric_order) - _ALLOWED_METRICS
        if unknown:
            raise ValueError(f"unsupported priority metrics: {sorted(unknown)}")


def candidate_id(candidate: Mapping[str, Any]) -> str:
    value = candidate.get("candidate_id", candidate.get("id", candidate.get("keyword")))
    value = " ".join(str(value or "").strip().casefold().split())
    if not value:
        raise ValueError("candidate requires candidate_id, id, or keyword")
    return value


def _promotion_overrides(candidate: Mapping[str, Any]) -> dict[str, bool]:
    """Prefer an attached promotion evaluation (discovery/promotion.py) over raw flags."""
    promotion = candidate.get("promotion")
    if not isinstance(promotion, Mapping):
        return {}
    return {
        "eligible": bool(promotion.get("eligible", candidate.get("eligible", True))),
        "manual_promoted": promotion.get("promotion_mode") == "manual",
    }


def _number(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def priority_components(
    candidate: Mapping[str, Any], metric_order: Sequence[str] = DEFAULT_METRIC_ORDER
) -> dict[str, Any]:
    unknown = set(metric_order) - _ALLOWED_METRICS
    if unknown:
        raise ValueError(f"unsupported priority metrics: {sorted(unknown)}")
    metrics: dict[str, float | bool] = {}
    for metric in metric_order:
        if metric in {"category_match", "already_processed"}:
            metrics[metric] = bool(candidate.get(metric, False))
        else:
            fields = _CANDIDATE_FIELD_ALIASES.get(metric, (metric,))
            metrics[metric] = _number(next(
                (candidate[field] for field in fields if candidate.get(field) is not None), 0
            ))
    promotion = _promotion_overrides(candidate)
    return {
        "eligible": promotion.get("eligible", bool(candidate.get("eligible", True))),
        "manual_promoted": promotion.get(
            "manual_promoted", bool(candidate.get("manual_promoted", False))),
        "standing_read": bool(candidate.get("standing_read", False)),
        "metrics": metrics,
        "metric_order": list(metric_order),
        "stable_id": candidate_id(candidate),
    }


def priority_tuple(components: Mapping[str, Any]) -> tuple:
    """Ascending sort key; deliberately lexicographic rather than a universal score."""
    values = []
    for name in components["metric_order"]:
        value = components["metrics"][name]
        # Already processed is the sole ascending metric. All others prefer high values.
        values.append(int(value) if name == "already_processed" else -float(value))
    return (
        not components["eligible"],
        not components["manual_promoted"],
        not components["standing_read"],
        *values,
        components["stable_id"],
    )


def prioritize_candidates(
    candidates: Iterable[Mapping[str, Any]],
    metric_order: Sequence[str] = DEFAULT_METRIC_ORDER,
) -> list[dict[str, Any]]:
    result = []
    for raw in candidates:
        candidate = dict(raw)
        components = priority_components(candidate, metric_order)
        candidate["candidate_id"] = components["stable_id"]
        candidate["priority_components"] = components
        result.append(candidate)
    return sorted(result, key=lambda item: priority_tuple(item["priority_components"]))
