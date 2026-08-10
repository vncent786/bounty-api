"""Compile lens feature requirements into the minimum Discovery pipeline depth."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Iterable, Mapping


# A registered score is not evidence that the current pipeline already produces it.
# Keep provenance explicit so adding a criterion cannot silently under-plan collection.
FEATURE_SOURCE_MAP: dict[str, str] = {
    "novelty": "candidate",
    "public_awareness": "candidate",
    "independent_voices": "root_probe",
    "behavior_evidence": "horizontal_analysis",
    "durability": "horizontal_analysis",
    "pain_point": "horizontal_analysis",
    "adoption": "horizontal_analysis",
    "unmet_need": "horizontal_analysis",
    "question": "horizontal_analysis",
    "desire": "horizontal_analysis",
    "desired_outcome": "horizontal_analysis",
    "workaround": "horizontal_analysis",
    "objection": "horizontal_analysis",
    "request": "horizontal_analysis",
    "purchase_trigger": "horizontal_analysis",
    "behavior_change": "horizontal_analysis",
    "switching": "horizontal_analysis",
    "rejection": "horizontal_analysis",
    "comparison": "horizontal_analysis",
    "catalyst": "horizontal_analysis",
    "risk": "horizontal_analysis",
    "company_exposure": "optional_enrichment",
    "fx_relevance": "optional_enrichment",
    "event_uncertainty": "optional_enrichment",
    "inventory_change": "optional_enrichment",
    "consumer_adoption": "optional_enrichment",
    "materiality": "optional_enrichment",
}

_STAGE_DEPTH = {
    "candidate": 0,
    "root_probe": 1,
    "deep_read": 2,
    "horizontal_analysis": 3,
    "custom_extraction": 4,
    "optional_enrichment": 4,
}


class LensCompileError(ValueError):
    pass


def _mapping(value: Any) -> Mapping[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return value
    raise LensCompileError("lens spec must be an object")


def compile_lens(
    spec: Mapping[str, Any] | Any,
    custom_fields: Iterable[Mapping[str, Any] | Any] = (),
) -> dict[str, Any]:
    """Return deterministic collection requirements without running any pipeline stage."""
    source_map = dict(FEATURE_SOURCE_MAP)
    for raw_field in custom_fields:
        field = _mapping(raw_field)
        key = str(field.get("key") or "")
        stage = str(field.get("source_stage") or "")
        if not key or stage not in _STAGE_DEPTH:
            raise LensCompileError(f"invalid custom field source: {key or '<missing>'}")
        source_map[key] = stage

    raw_spec = _mapping(spec)
    criteria = raw_spec.get("criteria")
    if not isinstance(criteria, (list, tuple)) or not criteria:
        raise LensCompileError("lens must contain at least one criterion")

    used: dict[str, str] = {}
    for raw_criterion in criteria:
        criterion = _mapping(raw_criterion)
        feature_key = str(criterion.get("feature_key") or "")
        if feature_key not in source_map:
            raise LensCompileError(f"unregistered feature: {feature_key}")
        used[feature_key] = source_map[feature_key]

    required_depth = max(
        used.values(), key=lambda stage: (_STAGE_DEPTH[stage], stage == "custom_extraction")
    )
    # Deep reading is an explicit prerequisite, not an implementation detail of
    # horizontal/custom/enrichment work. This keeps plans auditable and budgetable.
    required_stages = [
        stage for stage in ("candidate", "root_probe", "deep_read", "horizontal_analysis")
        if _STAGE_DEPTH[stage] <= _STAGE_DEPTH[required_depth]
    ]
    # The two deepest stages are sibling branches; include each branch actually used.
    for stage in ("custom_extraction", "optional_enrichment"):
        if stage in used.values():
            required_stages.append(stage)
    return {
        "required_depth": required_depth,
        "required_stages": required_stages,
        "feature_sources": dict(sorted(used.items())),
    }
