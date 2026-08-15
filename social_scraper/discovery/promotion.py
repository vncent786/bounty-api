"""Configurable, versioned promotion contracts for discovery candidates (Task 4.1).

Boolean model, exactly as planned::

    eligible = all(universal gates)
    automatically_promoted = eligible and any(route.passed for automatic routes)

Rules enforced here:
- Passing components from different routes never combine into an implicit pass.
- Missing or weak evidence never passes a component; gaps stay gaps with an
  explicit ``unknown`` status (never zero-filled, never fabricated).
- Repost clusters never count as independent corroboration or breadth: routes
  consume the dedup-adjusted root summary (Task 2.2) and supported engagement
  baselines (Task 2.3; weak/unavailable baselines cannot pass outlier or depth
  routes).
- A manual promotion within the user's explicit budget is honoured as a manual
  mode and is never attributed to automatic promotion.
- Exploration samples only eligible, non-promoted candidates under a fixed
  per-run cap with category/region stratification.

Every default below is an explicit calibration candidate, not a tuned optimum.
This module is pure computation: no LLM, storage, network or API access.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Mapping

POLICY_VERSION = "2026-08-15.1"

AUTOMATIC_ROUTES = (
    "daily_search_persistence",
    "search_trajectory_expansion",
    "cross_platform_breadth",
    "age_adjusted_engagement_breakout",
    "creator_breadth_expansion",
    "conversation_depth_trigger",
    "personal_radar_recurrence",
)
MANUAL_ROUTES = ("manual_promotion",)
EXPLORATION_ROUTES = ("exploration_allocation",)
ROUTE_NAMES = AUTOMATIC_ROUTES + MANUAL_ROUTES + EXPLORATION_ROUTES

DEFAULT_PROMOTION_CONFIG: dict[str, Any] = {
    "eligibility": {
        "minimum_unique_roots": 3,
        "minimum_independent_authors": 2,
        "require_usable_text": True,
        "require_source_health": True,
        "reject_duplicate_only_support": True,
    },
    # Applied only when a candidate matches a saved personal radar. Source
    # limitations remain visible; only the minimum floors may be relaxed.
    "radar_floor_overrides": {
        "minimum_unique_roots": 1,
        "minimum_independent_authors": 1,
    },
    "routes": {
        "daily_search_persistence": {
            "required_snapshots": 2, "comparable_window": 3,
            "min_roots_after_sweep": 1, "min_independent_authors_after_sweep": 1,
        },
        "search_trajectory_expansion": {
            "required_snapshots": 2, "comparable_window": 3,
        },
        "cross_platform_breadth": {
            "min_healthy_platforms": 2, "min_independent_authors": 3,
            "min_distinct_roots": 3,
        },
        "age_adjusted_engagement_breakout": {
            "percentile_threshold": 95.0, "extreme_percentile_threshold": 99.0,
        },
        "creator_breadth_expansion": {
            "min_independent_creators": 3, "material_increase_multiple": 1.5,
        },
        "conversation_depth_trigger": {
            "comment_percentile_threshold": 90.0, "min_active_discussion_roots": 2,
        },
        "personal_radar_recurrence": {},
        "manual_promotion": {},
        "exploration_allocation": {"per_run_cap": 3},
    },
}

_OVERRIDABLE_FLOORS = ("minimum_unique_roots", "minimum_independent_authors")

# route -> {key: (kind, low, high)} validation table; kind: int|number|percentile
_ROUTE_SPEC: dict[str, dict[str, tuple[str, float | None, float | None]]] = {
    "daily_search_persistence": {
        "required_snapshots": ("int", 1, None), "comparable_window": ("int", 1, None),
        "min_roots_after_sweep": ("int", 0, None),
        "min_independent_authors_after_sweep": ("int", 0, None),
    },
    "search_trajectory_expansion": {
        "required_snapshots": ("int", 1, None), "comparable_window": ("int", 1, None),
    },
    "cross_platform_breadth": {
        "min_healthy_platforms": ("int", 0, None),
        "min_independent_authors": ("int", 0, None),
        "min_distinct_roots": ("int", 0, None),
    },
    "age_adjusted_engagement_breakout": {
        "percentile_threshold": ("percentile", 0, 100),
        "extreme_percentile_threshold": ("percentile", 0, 100),
    },
    "creator_breadth_expansion": {
        "min_independent_creators": ("int", 0, None),
        "material_increase_multiple": ("number", None, None),
    },
    "conversation_depth_trigger": {
        "comment_percentile_threshold": ("percentile", 0, 100),
        "min_active_discussion_roots": ("int", 0, None),
    },
    "personal_radar_recurrence": {},
    "manual_promotion": {},
    "exploration_allocation": {"per_run_cap": ("int", 0, None)},
}


def _component(name, passed, observed, required, status=None, **extra):
    if status is None:
        status = "pass" if passed else "fail"
    result = {"name": name, "passed": bool(passed), "observed": observed,
              "required": required, "status": status}
    result.update(extra)
    return result


def _int_or_none(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else None


def _number_or_none(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _validate_route_values(route: str, values: Mapping[str, Any]) -> None:
    spec = _ROUTE_SPEC[route]
    unknown = set(values) - set(spec)
    if unknown:
        raise ValueError(f"unknown keys for route {route}: {sorted(unknown)}")
    for key, (kind, low, high) in spec.items():
        if key not in values:
            continue
        value = values[key]
        if kind == "int" and (_int_or_none(value) is None or isinstance(value, bool)):
            raise ValueError(f"{route}.{key} must be an integer, got {value!r}")
        if kind in {"number", "percentile"} and (
            _number_or_none(value) is None or isinstance(value, bool)
        ):
            raise ValueError(f"{route}.{key} must be numeric, got {value!r}")
        number = float(value)
        if low is not None and number < low:
            raise ValueError(f"{route}.{key} must be >= {low}, got {value!r}")
        if high is not None and number > high:
            raise ValueError(f"{route}.{key} must be <= {high}, got {value!r}")
    if route == "age_adjusted_engagement_breakout":
        regular = float(values.get("percentile_threshold",
                                   DEFAULT_PROMOTION_CONFIG["routes"][route]["percentile_threshold"]))
        extreme = float(values.get("extreme_percentile_threshold",
                                   DEFAULT_PROMOTION_CONFIG["routes"][route]["extreme_percentile_threshold"]))
        if extreme < regular:
            raise ValueError("extreme_percentile_threshold must be >= percentile_threshold")
    if route in {"daily_search_persistence", "search_trajectory_expansion"}:
        required = _int_or_none(values.get("required_snapshots",
                                           DEFAULT_PROMOTION_CONFIG["routes"][route]["required_snapshots"]))
        window = _int_or_none(values.get("comparable_window",
                                         DEFAULT_PROMOTION_CONFIG["routes"][route]["comparable_window"]))
        if required is not None and window is not None and required > window:
            raise ValueError(f"{route}: required_snapshots cannot exceed comparable_window")
    if route == "creator_breadth_expansion":
        multiple = _number_or_none(values.get("material_increase_multiple",
                                              DEFAULT_PROMOTION_CONFIG["routes"][route]["material_increase_multiple"]))
        if multiple is not None and multiple <= 1.0:
            raise ValueError("creator_breadth_expansion.material_increase_multiple must be > 1.0")


@dataclass(frozen=True)
class PromotionPolicy:
    """Immutable, versioned promotion configuration; partial configs merge over defaults."""

    config: Mapping[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULT_PROMOTION_CONFIG))
    version: str = POLICY_VERSION

    def __post_init__(self) -> None:
        supplied = self.config if isinstance(self.config, Mapping) else {}
        unknown_sections = set(supplied) - {"eligibility", "radar_floor_overrides", "routes"}
        if unknown_sections:
            raise ValueError(f"unknown config sections: {sorted(unknown_sections)}")

        merged = copy.deepcopy(DEFAULT_PROMOTION_CONFIG)

        eligibility = supplied.get("eligibility", {})
        unknown = set(eligibility) - set(DEFAULT_PROMOTION_CONFIG["eligibility"])
        if unknown:
            raise ValueError(f"unknown eligibility keys: {sorted(unknown)}")
        for key, value in eligibility.items():
            if key.startswith("minimum_"):
                if _int_or_none(value) is None or value < 0:
                    raise ValueError(f"eligibility.{key} must be a non-negative integer")
            elif not isinstance(value, bool):
                raise ValueError(f"eligibility.{key} must be a boolean")
            merged["eligibility"][key] = value

        overrides = supplied.get("radar_floor_overrides", {})
        unknown = set(overrides) - set(_OVERRIDABLE_FLOORS)
        if unknown:
            raise ValueError(f"radar_floor_overrides may only relax: {list(_OVERRIDABLE_FLOORS)}")
        for key, value in overrides.items():
            if _int_or_none(value) is None or value < 0:
                raise ValueError(f"radar_floor_overrides.{key} must be a non-negative integer")
            if value > merged["eligibility"][key]:
                raise ValueError(f"radar_floor_overrides.{key} may only lower the floor")
            merged["radar_floor_overrides"][key] = value

        routes = supplied.get("routes", {})
        unknown = set(routes) - set(ROUTE_NAMES)
        if unknown:
            raise ValueError(f"unknown routes: {sorted(unknown)}")
        for route, values in routes.items():
            if not isinstance(values, Mapping):
                raise ValueError(f"route config for {route} must be a mapping")
            _validate_route_values(route, values)
            merged["routes"][route].update(values)

        object.__setattr__(self, "config", merged)

    def route_config(self, route: str) -> dict[str, Any]:
        if route not in ROUTE_NAMES:
            raise ValueError(f"unknown route: {route}")
        return self.config["routes"][route]

    def config_snapshot(self) -> dict[str, Any]:
        return {"policy_version": self.version, **copy.deepcopy(self.config)}


def _root_summary(evidence: Mapping[str, Any]) -> dict[str, Any]:
    summary = evidence.get("root_summary")
    return summary if isinstance(summary, Mapping) else {}


def _persistence_component(evidence, cfg):
    windows = evidence.get("snapshot_windows")
    window = int(cfg["comparable_window"])
    required = int(cfg["required_snapshots"])
    recent = [w for w in (windows or [])[-window:] if isinstance(w, Mapping)]
    if not recent:
        return _component(
            "snapshot_presence", False,
            {"present": None, "absent": None, "gap": None, "observed_snapshots": 0},
            {"required_present": required, "comparable_window": window}, "unknown")
    present = sum(1 for w in recent if w.get("status") == "present")
    absent = sum(1 for w in recent if w.get("status") == "absent")
    gap = len(recent) - present - absent  # unknown/missing statuses stay gaps
    return _component(
        "snapshot_presence", present >= required,
        {"present": present, "absent": absent, "gap": gap,
         "observed_snapshots": len(recent)},
        {"required_present": required, "comparable_window": window})


def _route_daily(evidence, policy):
    cfg = policy.route_config("daily_search_persistence")
    presence = _persistence_component(evidence, cfg)
    summary = _root_summary(evidence)
    unique = _int_or_none(summary.get("unique_root_count"))
    authors = _int_or_none(summary.get("independent_author_count"))
    required = {"min_unique_roots": cfg["min_roots_after_sweep"],
                "min_independent_authors": cfg["min_independent_authors_after_sweep"]}
    if unique is None or authors is None:
        sweep = _component("root_evidence_after_weekly_sweep", False,
                           {"unique_root_count": unique, "independent_author_count": authors},
                           required, "unknown")
    else:
        sweep = _component(
            "root_evidence_after_weekly_sweep",
            unique >= required["min_unique_roots"] and authors >= required["min_independent_authors"],
            {"unique_root_count": unique, "independent_author_count": authors}, required)
    return {"route": "daily_search_persistence", "mode": "automatic",
            "passed": presence["passed"] and sweep["passed"],
            "components": [presence, sweep]}


def _increase(series) -> bool | None:
    if not isinstance(series, Mapping):
        return None
    previous = _number_or_none(series.get("previous"))
    current = _number_or_none(series.get("current"))
    if previous is None or current is None:
        return None
    return current > previous  # missing values can never pass this component


def _route_trajectory(evidence, policy):
    cfg = policy.route_config("search_trajectory_expansion")
    presence = _persistence_component(evidence, cfg)
    trajectory = evidence.get("trajectory") if isinstance(evidence.get("trajectory"), Mapping) else {}
    volume = _increase(trajectory.get("volume"))
    growth = _increase(trajectory.get("growth"))
    related = _increase(trajectory.get("related_query_breadth"))
    regional = _increase(trajectory.get("regional_breadth"))
    components = [
        presence,
        _component("own_series_volume_or_growth_increase",
                   volume is True or growth is True,
                   {"volume": volume, "growth": growth},
                   {"at_least_one_observed_increase": True}),
        _component("related_query_breadth_increase", related is True, related,
                   {"observed_strict_increase": True}),
        _component("regional_breadth_increase", regional is True, regional,
                   {"observed_strict_increase": True}),
    ]
    expanded = components[1]["passed"] or components[2]["passed"] or components[3]["passed"]
    return {"route": "search_trajectory_expansion", "mode": "automatic",
            "passed": presence["passed"] and expanded, "components": components}


def _route_cross_platform(evidence, policy):
    cfg = policy.route_config("cross_platform_breadth")
    hits = evidence.get("platform_hits") if isinstance(evidence.get("platform_hits"), Mapping) else {}
    health = evidence.get("source_health") if isinstance(evidence.get("source_health"), Mapping) else {}
    healthy = sorted(
        platform for platform, count in hits.items()
        if (_int_or_none(count) or 0) >= 1 and health.get(platform) == "healthy"
    )
    summary = _root_summary(evidence)
    authors = _int_or_none(summary.get("independent_author_count"))
    roots = _int_or_none(summary.get("unique_root_count"))
    author_component = (
        _component("independent_authors", False,
                   {"independent_author_count": None, "repost_cluster_count": None,
                    "raw_counts_excluded": True},
                   {"min_independent_authors": cfg["min_independent_authors"]}, "unknown")
        if authors is None else
        _component("independent_authors", authors >= cfg["min_independent_authors"],
                   {"independent_author_count": authors,
                    "repost_cluster_count": summary.get("repost_cluster_count"),
                    "raw_counts_excluded": True},
                   {"min_independent_authors": cfg["min_independent_authors"]})
    )
    root_component = (
        _component("distinct_roots", False, None,
                   {"min_distinct_roots": cfg["min_distinct_roots"]}, "unknown")
        if roots is None else
        _component("distinct_roots", roots >= cfg["min_distinct_roots"], roots,
                   {"min_distinct_roots": cfg["min_distinct_roots"]})
    )
    components = [
        _component("healthy_platforms_with_hits", len(healthy) >= cfg["min_healthy_platforms"],
                   {"count": len(healthy), "platforms": healthy},
                   {"min_healthy_platforms": cfg["min_healthy_platforms"]}),
        author_component, root_component,
    ]
    return {"route": "cross_platform_breadth", "mode": "automatic",
            "passed": all(c["passed"] for c in components), "components": components}


def _route_engagement(evidence, policy):
    cfg = policy.route_config("age_adjusted_engagement_breakout")
    roots = [r for r in (evidence.get("engagement_roots") or []) if isinstance(r, Mapping)]
    coverage = {"supported": 0, "weak": 0, "unavailable": 0}
    for root in roots:
        status_value = root.get("baseline_status")
        if status_value in coverage:
            coverage[status_value] += 1

    def percentile(root) -> float | None:
        value = _number_or_none(root.get("engagement_percentile"))
        return value

    # Repost copies never count as independent corroboration or outliers.
    independent = [r for r in roots
                   if r.get("baseline_status") == "supported"
                   and percentile(r) is not None
                   and not r.get("repost_cluster_id")]
    outliers = [r for r in independent if percentile(r) >= cfg["percentile_threshold"]]
    extremes = [r for r in independent if percentile(r) >= cfg["extreme_percentile_threshold"]]
    outlier_authors = {r.get("author_id") for r in outliers}
    two_outlier = len(outlier_authors) >= 2
    extreme_path = False
    corroborating = []
    if extremes:
        extreme_authors = {r.get("author_id") for r in extremes}
        extreme_ids = {r.get("root_id") for r in extremes}
        corroborating = [r for r in independent
                         if r.get("author_id") not in extreme_authors
                         and r.get("root_id") not in extreme_ids]
        extreme_path = bool(corroborating)
    components = [
        _component("two_supported_outliers", two_outlier,
                   {"distinct_outlier_authors": len(outlier_authors)},
                   {"distinct_authors_above_percentile": 2,
                    "percentile_threshold": cfg["percentile_threshold"]},
                   "unknown" if not roots else None),
        _component("extreme_outlier_plus_corroboration", extreme_path,
                   {"extreme_root_count": len(extremes),
                    "corroborating_independent_roots": len(corroborating)},
                   {"extreme_percentile_threshold": cfg["extreme_percentile_threshold"],
                    "corroborating_independent_roots": 1}),
        _component("supported_baseline_coverage", coverage["supported"] >= 1, coverage,
                   {"min_supported_roots": 1},
                   "unknown" if not roots else None),
    ]
    return {"route": "age_adjusted_engagement_breakout", "mode": "automatic",
            "passed": (two_outlier or extreme_path) and coverage["supported"] >= 1,
            "components": components}


def _route_creator(evidence, policy):
    cfg = policy.route_config("creator_breadth_expansion")
    summary = evidence.get("creator_summary") if isinstance(evidence.get("creator_summary"), Mapping) else {}
    current = _int_or_none(summary.get("independent_creator_count"))
    previous = _int_or_none(summary.get("previous_independent_creator_count"))
    floor = (
        _component("independent_creators", False, None,
                   {"min_independent_creators": cfg["min_independent_creators"]}, "unknown")
        if current is None else
        _component("independent_creators", current >= cfg["min_independent_creators"],
                   {"independent_creator_count": current, "repost_clusters_excluded": True},
                   {"min_independent_creators": cfg["min_independent_creators"]})
    )
    if current is None or previous is None:
        material = _component("material_increase_vs_previous", False,
                              {"current": current, "previous": previous},
                              {"material_increase_multiple": cfg["material_increase_multiple"],
                               "strictly_greater": True}, "unknown")
    else:
        material = _component(
            "material_increase_vs_previous",
            current > previous and current >= previous * cfg["material_increase_multiple"],
            {"current": current, "previous": previous},
            {"material_increase_multiple": cfg["material_increase_multiple"],
             "strictly_greater": True})
    components = [floor, material]
    return {"route": "creator_breadth_expansion", "mode": "automatic",
            "passed": all(c["passed"] for c in components), "components": components}


def _route_depth(evidence, policy):
    cfg = policy.route_config("conversation_depth_trigger")
    roots = [r for r in (evidence.get("depth_roots") or []) if isinstance(r, Mapping)]
    above = [r for r in roots
             if r.get("baseline_status") == "supported"
             and (_number_or_none(r.get("comment_percentile")) or -1.0)
             >= cfg["comment_percentile_threshold"]]
    weak = sum(1 for r in roots if r.get("baseline_status") in {"weak", "unavailable"})
    percentile_component = _component(
        "comment_activity_above_baseline", bool(above),
        {"roots_above_threshold": [r.get("root_id") for r in above],
         "weak_or_unavailable_baselines": weak},
        {"comment_percentile_threshold": cfg["comment_percentile_threshold"]},
        "unknown" if not roots else None)
    active = _int_or_none(evidence.get("active_discussion_roots"))
    discussions_component = (
        _component("several_independent_active_discussions", False, None,
                   {"min_active_discussion_roots": cfg["min_active_discussion_roots"]}, "unknown")
        if active is None else
        _component("several_independent_active_discussions",
                   active >= cfg["min_active_discussion_roots"],
                   {"independent_active_discussion_roots": active},
                   {"min_active_discussion_roots": cfg["min_active_discussion_roots"]})
    )
    components = [percentile_component, discussions_component]
    return {"route": "conversation_depth_trigger", "mode": "automatic",
            "passed": percentile_component["passed"] or discussions_component["passed"],
            "components": components}


def _route_radar(evidence, policy, eligibility):
    radar = evidence.get("radar_match") if isinstance(evidence.get("radar_match"), Mapping) else {}
    matched = bool(radar.get("matched"))
    radar_ids = [str(i) for i in (radar.get("radar_ids") or [])]
    applied = eligibility.get("floor_overrides_applied") or {}
    components = [
        _component("saved_radar_match", matched, radar_ids, {"matched_saved_radar": True},
                   "unknown" if "radar_match" not in evidence else None),
        _component("floor_overrides_applied", True, applied,
                   {"overridable_floors": list(_OVERRIDABLE_FLOORS)}),
    ]
    return {"route": "personal_radar_recurrence", "mode": "automatic",
            "passed": all(c["passed"] for c in components), "components": components}


def _route_manual(evidence, policy):
    request = evidence.get("manual_request") if isinstance(evidence.get("manual_request"), Mapping) else {}
    components = [
        _component("explicit_request", bool(request.get("requested")),
                   bool(request.get("requested")), {"requested": True}),
        _component("within_explicit_budget", bool(request.get("within_budget")),
                   bool(request.get("within_budget")), {"within_budget": True}),
    ]
    return {"route": "manual_promotion", "mode": "manual",
            "passed": all(c["passed"] for c in components), "components": components}


def _route_exploration_pending(evidence, policy, eligible, mode):
    already = mode in {"automatic", "manual"}
    component = _component(
        "eligible_non_promoted", eligible and not already,
        {"eligible": eligible, "already_promoted": already},
        {"cohort_selection": "pending"}, "pending_cohort_selection")
    return {"route": "exploration_allocation", "mode": "exploration",
            "passed": False, "components": [component]}


def _evaluate_eligibility(evidence, policy) -> dict[str, Any]:
    cfg = policy.config["eligibility"]
    radar = evidence.get("radar_match") if isinstance(evidence.get("radar_match"), Mapping) else {}
    radar_matched = bool(radar.get("matched"))
    overrides = policy.config["radar_floor_overrides"] if radar_matched else {}
    effective = {**cfg, **overrides}

    summary = _root_summary(evidence)
    gates = []

    unique = _int_or_none(summary.get("unique_root_count"))
    gates.append(_component(
        "minimum_unique_roots",
        unique is not None and unique >= effective["minimum_unique_roots"], unique,
        effective["minimum_unique_roots"],
        None if unique is not None else "unknown",
        applied_override=bool(overrides.get("minimum_unique_roots"))))

    authors = _int_or_none(summary.get("independent_author_count"))
    gates.append(_component(
        "minimum_independent_authors",
        authors is not None and authors >= effective["minimum_independent_authors"], authors,
        effective["minimum_independent_authors"],
        None if authors is not None else "unknown",
        applied_override=bool(overrides.get("minimum_independent_authors"))))

    if not cfg["require_usable_text"]:
        gates.append(_component("require_usable_text", True, "disabled",
                                {"enabled": False}, "disabled"))
    else:
        usable = _int_or_none(evidence.get("usable_text_root_count"))
        gates.append(_component(
            "require_usable_text",
            usable is not None and usable >= 1, usable, {"min_usable_text_roots": 1},
            None if usable is not None else "unknown"))

    if not cfg["require_source_health"]:
        gates.append(_component("require_source_health", True, "disabled",
                                {"enabled": False}, "disabled"))
    else:
        hits = evidence.get("platform_hits") if isinstance(evidence.get("platform_hits"), Mapping) else {}
        health = evidence.get("source_health") if isinstance(evidence.get("source_health"), Mapping) else {}
        hit_platforms = {p: health.get(p) for p, n in hits.items() if (_int_or_none(n) or 0) >= 1}
        states = set(hit_platforms.values())
        known = states <= {"healthy", "degraded", "unavailable"}
        passed = bool(hit_platforms) and known and "healthy" in states and "unavailable" not in states
        status = None if (hit_platforms and known) else "unknown"
        gates.append(_component(
            "require_source_health", passed, hit_platforms,
            {"at_least_one_healthy_hit_platform": True, "no_hit_platform_unavailable": True},
            status))

    duplicate_only = evidence.get("duplicate_only_support")
    if not cfg["reject_duplicate_only_support"]:
        gates.append(_component("reject_duplicate_only_support", True, "disabled",
                                {"enabled": False}, "disabled"))
    elif duplicate_only is None:
        # Rejection gate: absence of propagation evidence is not duplicate-only
        # support; it stays explicitly unverified.
        gates.append(_component("reject_duplicate_only_support", True, None,
                                {"duplicate_only_support": False}, "unverified"))
    else:
        gates.append(_component("reject_duplicate_only_support", not bool(duplicate_only),
                                bool(duplicate_only), {"duplicate_only_support": False}))

    return {"radar_matched": radar_matched,
            "floor_overrides_applied": dict(overrides) if radar_matched else {},
            "gates": gates}


def _limitations(evidence, eligibility, routes) -> list[str]:
    limitations = []
    for gate in eligibility["gates"]:
        if gate["status"] == "unknown":
            limitations.append(f"eligibility gate {gate['name']}: evidence gap (unknown)")
        elif gate["status"] == "unverified":
            limitations.append(
                f"eligibility gate {gate['name']}: unverified (no repost propagation evidence)")
    for evaluated in routes:
        if evaluated["route"] == "daily_search_persistence":
            presence = evaluated["components"][0]
            observed = presence["observed"]
            if isinstance(observed, Mapping) and observed.get("gap"):
                limitations.append(
                    f"daily snapshots: {observed['gap']} gap(s) in the comparable window "
                    "remain gaps (not counted as present or absent)")
        if evaluated["route"] == "age_adjusted_engagement_breakout":
            coverage = evaluated["components"][2]["observed"]
            if coverage["weak"] or coverage["unavailable"]:
                limitations.append(
                    f"engagement baselines weak/unavailable for {coverage['weak'] + coverage['unavailable']} "
                    "root(s); these cannot pass an automatic outlier route")
        if evaluated["route"] == "creator_breadth_expansion":
            material = evaluated["components"][1]
            if material["status"] == "unknown":
                limitations.append("previous comparable creator observation missing; "
                                   "material increase cannot be verified")
        if evaluated["route"] == "conversation_depth_trigger":
            for component in evaluated["components"]:
                if component["status"] == "unknown":
                    limitations.append(f"conversation depth: {component['name']} has no observed evidence")
    if eligibility["radar_matched"]:
        unknown_gates = [g["name"] for g in eligibility["gates"] if g["status"] == "unknown"]
        if unknown_gates:
            limitations.append(
                "personal radar match relaxed floors but source limitations remain: "
                + ", ".join(unknown_gates))
    return limitations


def evaluate_promotion(evidence: Mapping[str, Any], policy: PromotionPolicy | None = None) -> dict[str, Any]:
    """Evaluate one candidate against universal gates and all nine routes."""
    policy = policy or PromotionPolicy()
    eligibility = _evaluate_eligibility(evidence, policy)
    eligible = all(gate["passed"] for gate in eligibility["gates"])

    routes = [
        _route_daily(evidence, policy),
        _route_trajectory(evidence, policy),
        _route_cross_platform(evidence, policy),
        _route_engagement(evidence, policy),
        _route_creator(evidence, policy),
        _route_depth(evidence, policy),
        _route_radar(evidence, policy, eligibility),
        _route_manual(evidence, policy),
    ]
    automatic_passed = [r["route"] for r in routes if r["mode"] == "automatic" and r["passed"]]
    manual_passed = routes[-1]["passed"]

    if manual_passed:
        # The user's explicit in-budget decision claims the promotion; it is
        # never attributed to automation. Passing routes stay visible for audit.
        automatically_promoted = False
        mode = "manual"
    else:
        automatically_promoted = eligible and bool(automatic_passed)
        mode = "automatic" if automatically_promoted else "none"

    routes.append(_route_exploration_pending(evidence, policy, eligible, mode))

    reasons = []
    for gate in eligibility["gates"]:
        if not gate["passed"]:
            reasons.append(f"gate {gate['name']} failed ({gate['status']})")
    if not eligible:
        reasons.append("not eligible: universal gates incomplete")
    for evaluated in routes:
        if evaluated["mode"] == "automatic" and not evaluated["passed"]:
            failed = [c["name"] for c in evaluated["components"] if not c["passed"]]
            reasons.append(f"route {evaluated['route']} failed: {', '.join(failed) or 'no passing path'}")
        elif evaluated["passed"] and evaluated["route"] != "exploration_allocation":
            reasons.append(f"route {evaluated['route']} passed ({evaluated['mode']})")
    if automatically_promoted:
        reasons.append("automatically promoted via: " + ", ".join(automatic_passed))
    if mode == "manual":
        reasons.append("manual promotion requested within explicit budget; not automatic")

    stratum = evidence.get("stratum") if isinstance(evidence.get("stratum"), Mapping) else {}
    return {
        "candidate_id": str(evidence.get("candidate_id", "candidate")),
        "policy_version": policy.version,
        "config": policy.config_snapshot(),
        "eligible": eligible,
        "eligibility": eligibility,
        "routes": routes,
        "automatic_routes_passed": automatic_passed,
        "automatically_promoted": automatically_promoted,
        "promotion_mode": mode,
        "reasons": reasons,
        "limitations": _limitations(evidence, eligibility, routes),
        "stratum": {"category": stratum.get("category"), "region": stratum.get("region")},
    }


def select_exploration_sample(
    evaluations: list[Mapping[str, Any]], policy: PromotionPolicy | None = None
) -> list[dict[str, Any]]:
    """Deterministically sample eligible, non-promoted candidates under the cap.

    Stratifies by (category, region), round-robins across sorted strata, and
    orders within a stratum by candidate_id. Input evaluations are not mutated.
    """
    policy = policy or PromotionPolicy()
    cap = int(policy.route_config("exploration_allocation")["per_run_cap"])
    pool = [e for e in evaluations
            if e.get("eligible")
            and not e.get("automatically_promoted")
            and e.get("promotion_mode") != "manual"]
    strata: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for evaluation in pool:
        stratum = evaluation.get("stratum") or {}
        key = (str(stratum.get("category") or "uncategorized"),
               str(stratum.get("region") or "unspecified"))
        strata.setdefault(key, []).append(evaluation)
    queues = [(key, sorted(entries, key=lambda e: str(e.get("candidate_id"))))
              for key, entries in sorted(strata.items())]

    selected: list[tuple[tuple[str, str], Mapping[str, Any]]] = []
    round_index = 0
    while len(selected) < cap:
        took = False
        for key, queue in queues:
            if round_index < len(queue):
                selected.append((key, queue[round_index]))
                took = True
                if len(selected) >= cap:
                    break
        if not took:
            break
        round_index += 1

    results = []
    for position, (key, evaluation) in enumerate(selected, start=1):
        updated = copy.deepcopy(evaluation)
        exploration = next(r for r in updated["routes"] if r["route"] == "exploration_allocation")
        exploration["passed"] = True
        pending = next(c for c in exploration["components"] if c["name"] == "eligible_non_promoted")
        pending["passed"] = True
        pending["status"] = "pass"
        exploration["components"].append(_component(
            "exploration_selection", True,
            {"stratum": {"category": key[0], "region": key[1]},
             "selection_index": position, "per_run_cap": cap},
            {"per_run_cap": cap}))
        updated["promotion_mode"] = "exploration"
        updated["reasons"] = list(updated.get("reasons") or []) + [
            f"exploration_allocation selected position {position} within per-run cap {cap}"
        ]
        results.append(updated)
    return results
