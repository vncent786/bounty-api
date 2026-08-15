"""Pure, evidence-preserving projection for the Global Explore interface.

This module performs no collection, persistence, or model calls.  It turns
already-recorded family, promotion, and evidence records into a stable UI
contract while keeping missing evidence explicit.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any, Mapping

from social_scraper.lenses.core import (
    LensCriterion,
    ResearchLensSpec,
    evaluate_lens,
)


DEFAULT_STAGE_POLICY: dict[str, Any] = {
    "version": "explore-stage-v1",
    "rules": [
        "explicit_event_evidence",
        "comparable_decline",
        "durable_broad_evidence",
        "repeated_independent_evidence",
        "promoted_with_multiple_roots",
        "observed_once",
        "insufficient_evidence",
    ],
    "established": {
        "comparable_snapshots": 3,
        "independent_author_count": 5,
        "healthy_platform_count": 2,
    },
    "confirming": {"comparable_snapshots": 2, "independent_author_count": 2},
    "emerging": {"unique_root_count": 3},
}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _at_least(value: Any, minimum: float) -> bool:
    number = _number(value)
    return number is not None and number >= minimum


def classify_stage(
    signals: Mapping[str, Any] | None,
    *,
    policy: Mapping[str, Any] = DEFAULT_STAGE_POLICY,
) -> dict[str, Any]:
    """Classify a family using explicit, ordered, versioned rules.

    Missing values never become zero and cannot satisfy a threshold.
    """

    values = dict(signals or {})
    version = str(policy.get("version") or "unknown")
    established = dict(policy.get("established") or {})
    confirming = dict(policy.get("confirming") or {})
    emerging = dict(policy.get("emerging") or {})

    if values.get("one_off_event") is True:
        stage, rule = "event_spike", "explicit_event_evidence"
    elif (
        values.get("direction") == "declining"
        and values.get("trajectory_comparable") is True
    ):
        stage, rule = "cooling", "comparable_decline"
    elif all((
        _at_least(values.get("comparable_snapshots"), established.get("comparable_snapshots", 3)),
        _at_least(values.get("independent_author_count"), established.get("independent_author_count", 5)),
        _at_least(values.get("healthy_platform_count"), established.get("healthy_platform_count", 2)),
    )):
        stage, rule = "established", "durable_broad_evidence"
    elif all((
        _at_least(values.get("comparable_snapshots"), confirming.get("comparable_snapshots", 2)),
        _at_least(values.get("independent_author_count"), confirming.get("independent_author_count", 2)),
    )):
        stage, rule = "confirming", "repeated_independent_evidence"
    elif (
        values.get("promoted") is True
        and _at_least(values.get("unique_root_count"), emerging.get("unique_root_count", 3))
    ):
        stage, rule = "emerging", "promoted_with_multiple_roots"
    elif (
        _at_least(values.get("comparable_snapshots"), 1)
        or _at_least(values.get("unique_root_count"), 1)
    ):
        stage, rule = "observed", "observed_once"
    else:
        stage, rule = "unclear", "insufficient_evidence"

    return {
        "stage": stage,
        "policy_version": version,
        "passed_rule": rule,
        "inputs": deepcopy(values),
    }


def _member_terms(family: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for member in family.get("members") or []:
        if not isinstance(member, Mapping):
            continue
        term = str(
            member.get("normalized_keyword")
            or member.get("keyword")
            or member.get("term")
            or ""
        ).strip()
        if not term:
            continue
        result.append({
            "term": term,
            "relationship": member.get("relationship") or member.get("relationship_type") or "same_topic",
        })
    return result


def _supported_explanation(
    evidence: Mapping[str, Any], limitations: list[str]
) -> dict[str, Any]:
    explanation = evidence.get("what_it_is") or {}
    if not isinstance(explanation, Mapping):
        explanation = {}
    text = str(explanation.get("text") or "").strip()
    support = explanation.get("support")
    if text and isinstance(support, list) and support:
        return {"text": text, "status": "supported", "support": deepcopy(support)}
    limitations.append("Topic explanation lacks cited related-query or root context.")
    return {"text": "Not enough context yet.", "status": "unclear"}


def _trajectory(evidence: Mapping[str, Any], limitations: list[str]) -> dict[str, Any]:
    source = evidence.get("trajectory") or {}
    if not isinstance(source, Mapping):
        source = {}
    result = {key: deepcopy(value) for key, value in source.items() if key != "reported_volume"}
    result.setdefault("status", "supported" if source else "unknown")
    if source.get("reported_volume") is not None:
        if source.get("source") and source.get("period"):
            result["reported_volume"] = source["reported_volume"]
        else:
            limitations.append("Search volume is hidden because its source or period is missing.")
    return result


def _lens_spec(value: Mapping[str, Any]) -> ResearchLensSpec:
    criteria = tuple(
        LensCriterion(
            criterion_id=str(item.get("criterion_id") or item.get("feature_key") or ""),
            label=str(item.get("label") or item.get("feature_key") or "Criterion"),
            feature_key=str(item.get("feature_key") or ""),
            mode=str(item.get("mode") or "score"),
            weight=float(item.get("weight") or 0.0),
            minimum=item.get("minimum"),
            maximum=item.get("maximum"),
            missing_policy=str(item.get("missing_policy") or "keep_unknown"),
            description=str(item.get("description") or ""),
        )
        for item in (value.get("criteria") or [])
        if isinstance(item, Mapping)
    )
    return ResearchLensSpec(
        lens_id=str(value.get("lens_id") or value.get("id") or ""),
        name=str(value.get("name") or "Perspective"),
        version=str(value.get("version") or "1"),
        objective=str(value.get("objective") or ""),
        criteria=criteria,
        preferred_geographies=tuple(value.get("preferred_geographies") or ()),
        preferred_categories=tuple(value.get("preferred_categories") or ()),
        required_enrichers=tuple(value.get("required_enrichers") or ()),
    )


def apply_perspective(
    families: list[Mapping[str, Any]], lens: Mapping[str, Any]
) -> dict[str, Any]:
    """Filter and order existing family projections without collecting data."""

    spec = _lens_spec(lens)
    included: list[dict[str, Any]] = []
    excluded: list[str] = []
    for raw in families:
        item = deepcopy(dict(raw))
        family_id = str(item.get("family_id") or "")
        evaluation = evaluate_lens(
            {"candidate_id": family_id, "features": item.get("features") or {}}, spec
        )
        if evaluation.status == "excluded":
            excluded.append(family_id)
            continue
        item["perspective"] = asdict(evaluation)
        included.append(item)

    included.sort(key=lambda item: (
        item["perspective"]["score"] is None,
        -(item["perspective"]["score"] or 0.0),
        str(item.get("label") or "").casefold(),
        str(item.get("family_id") or ""),
    ))
    return {
        "perspective": {"lens_id": spec.lens_id, "version": spec.version, "name": spec.name},
        "collection_performed": False,
        "items": included,
        "excluded_family_ids": excluded,
    }


def build_explore_read_model(
    family: Mapping[str, Any],
    *,
    evidence: Mapping[str, Any] | None = None,
    promotion: Mapping[str, Any] | None = None,
    stage_policy: Mapping[str, Any] = DEFAULT_STAGE_POLICY,
) -> dict[str, Any]:
    """Build the generic family card/detail contract consumed by Explore."""

    evidence = dict(evidence or {})
    promotion = dict(promotion or {})
    limitations = [str(item) for item in (evidence.get("limitations") or []) if str(item).strip()]
    limitations.extend(
        str(item) for item in (promotion.get("limitations") or []) if str(item).strip()
    )

    trajectory = _trajectory(evidence, limitations)
    corroboration = deepcopy(evidence.get("corroboration") or {})
    propagation = deepcopy(evidence.get("propagation") or {})
    conversation_depth = deepcopy(evidence.get("conversation_depth") or {})
    coverage = deepcopy(evidence.get("coverage") or {})

    routes = [deepcopy(route) for route in (promotion.get("routes") or []) if isinstance(route, Mapping)]
    passed_routes = [route for route in routes if route.get("passed") is True]
    promoted = promotion.get("automatically_promoted") is True or bool(passed_routes)
    stage_inputs = {
        "one_off_event": evidence.get("one_off_event") is True,
        "direction": trajectory.get("direction"),
        "trajectory_comparable": trajectory.get("comparable") is True
        or _at_least(trajectory.get("comparable_snapshots"), 2),
        "comparable_snapshots": trajectory.get("comparable_snapshots"),
        "unique_root_count": corroboration.get("unique_root_count"),
        "independent_author_count": corroboration.get("independent_author_count"),
        "healthy_platform_count": corroboration.get("healthy_platform_count"),
        "promoted": promoted,
    }
    stage = classify_stage(stage_inputs, policy=stage_policy)

    if str(family.get("status") or "").casefold() == "rejected":
        stage = {
            "stage": "unclear",
            "policy_version": str(stage_policy.get("version") or "unknown"),
            "passed_rule": "rejected_family",
            "inputs": stage_inputs,
        }

    # Preserve order while de-duplicating limitations.
    limitations = list(dict.fromkeys(limitations))
    return {
        "family_id": str(family.get("id") or family.get("family_id") or ""),
        "label": str(family.get("canonical_label") or family.get("label") or "Unnamed topic"),
        "geo": family.get("geo"),
        "category": family.get("category"),
        "member_terms": _member_terms(family),
        "what_it_is": _supported_explanation(evidence, limitations),
        "stage": stage["stage"],
        "stage_evaluation": stage,
        "why_surfaced": passed_routes,
        "trajectory": trajectory,
        "resonance": deepcopy(evidence.get("resonance") or {}),
        "corroboration": corroboration,
        "propagation": propagation,
        "conversation_depth": conversation_depth,
        "coverage": coverage,
        "features": deepcopy(evidence.get("features") or {}),
        "limitations": limitations,
        "available_actions": ["investigate", "monitor", "dismiss"],
    }
