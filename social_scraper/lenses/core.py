"""Versioned, user-configurable research lenses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


REGISTERED_FEATURES = {
    "company_exposure",
    "fx_relevance",
    "event_uncertainty",
    "independent_voices",
    "inventory_change",
    "behavior_evidence",
    "novelty",
    "durability",
    "consumer_adoption",
    "switching",
    "rejection",
    "pain_point",
    "adoption",
    "unmet_need",
    "question",
    "desire",
    "desired_outcome",
    "workaround",
    "objection",
    "request",
    "purchase_trigger",
    "behavior_change",
    "comparison",
    "catalyst",
    "risk",
    "public_awareness",
    "materiality",
}
_ALLOWED_MODES = {"filter", "score", "display"}
_ALLOWED_MISSING = {"keep_unknown", "score_zero", "exclude"}


@dataclass(frozen=True)
class LensCriterion:
    criterion_id: str
    label: str
    feature_key: str
    mode: str = "score"
    weight: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    missing_policy: str = "keep_unknown"
    description: str = ""


@dataclass(frozen=True)
class ResearchLensSpec:
    lens_id: str
    name: str
    version: str
    objective: str
    criteria: tuple[LensCriterion, ...]
    preferred_geographies: tuple[str, ...] = ()
    preferred_categories: tuple[str, ...] = ()
    required_enrichers: tuple[str, ...] = ()


@dataclass(frozen=True)
class CriterionResult:
    criterion_id: str
    feature_key: str
    value: float | None
    passed: bool | None
    contribution: float | None
    missing_reason: str | None = None


@dataclass(frozen=True)
class LensEvaluation:
    candidate_id: str
    lens_id: str
    lens_version: str
    status: str
    score: float | None
    score_coverage: float
    criterion_results: tuple[CriterionResult, ...]
    limitations: tuple[str, ...] = ()
    evaluated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _validate(spec: ResearchLensSpec) -> None:
    if not spec.lens_id or not spec.version:
        raise ValueError("lens id and version are required")
    seen: set[str] = set()
    for criterion in spec.criteria:
        if criterion.criterion_id in seen:
            raise ValueError(f"duplicate criterion: {criterion.criterion_id}")
        seen.add(criterion.criterion_id)
        if criterion.feature_key not in REGISTERED_FEATURES:
            raise ValueError(f"unregistered feature: {criterion.feature_key}")
        if criterion.mode not in _ALLOWED_MODES:
            raise ValueError(f"invalid criterion mode: {criterion.mode}")
        if criterion.missing_policy not in _ALLOWED_MISSING:
            raise ValueError(f"invalid missing policy: {criterion.missing_policy}")
        if criterion.weight < 0:
            raise ValueError("criterion weight must be non-negative")


def evaluate_lens(candidate: dict, spec: ResearchLensSpec) -> LensEvaluation:
    """Evaluate one immutable candidate under one lens; never mutate evidence."""
    _validate(spec)
    features = candidate.get("features") or {}
    results = []
    excluded = False
    known = 0
    active_weight = 0.0
    configured_weight = sum(c.weight for c in spec.criteria if c.mode == "score")
    weighted_sum = 0.0
    limitations = []

    for criterion in spec.criteria:
        raw_value = features.get(criterion.feature_key)
        if raw_value is None:
            if criterion.missing_policy == "exclude":
                excluded = True
                passed = False
            else:
                passed = None
            if criterion.missing_policy == "score_zero" and criterion.mode == "score":
                value = 0.0
                contribution = 0.0
                active_weight += criterion.weight
                known += 1
            else:
                value = None
                contribution = None
            results.append(CriterionResult(
                criterion_id=criterion.criterion_id,
                feature_key=criterion.feature_key,
                value=value,
                passed=passed,
                contribution=contribution,
                missing_reason="feature unavailable",
            ))
            limitations.append(f"{criterion.label}: feature unavailable")
            continue

        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"feature {criterion.feature_key} must be numeric") from exc
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"feature {criterion.feature_key} must be between 0 and 1")
        known += 1
        passed = True
        if criterion.minimum is not None and value < criterion.minimum:
            passed = False
        if criterion.maximum is not None and value > criterion.maximum:
            passed = False
        if criterion.mode == "filter" and not passed:
            excluded = True
        contribution = None
        if criterion.mode == "score":
            contribution = criterion.weight * value
            weighted_sum += contribution
            active_weight += criterion.weight
        results.append(CriterionResult(
            criterion_id=criterion.criterion_id,
            feature_key=criterion.feature_key,
            value=value,
            passed=passed,
            contribution=contribution,
        ))

    score = weighted_sum / active_weight if active_weight else None
    coverage = active_weight / configured_weight if configured_weight else (1.0 if known else 0.0)
    status = "excluded" if excluded else "included" if known else "insufficient_evidence"
    return LensEvaluation(
        candidate_id=str(candidate.get("candidate_id") or ""),
        lens_id=spec.lens_id,
        lens_version=spec.version,
        status=status,
        score=score,
        score_coverage=coverage,
        criterion_results=tuple(results),
        limitations=tuple(limitations),
    )
