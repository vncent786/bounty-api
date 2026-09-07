"""Reusable standing-investment-monitor contracts and public parity projection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


FRAMEWORK_PATH = Path("references/standing-monitor-sensor-framework-v1.json")
_TERMINAL_STATES = {"complete", "complete_empty", "bounded_partial"}


def load_sensor_framework(root: str | Path) -> dict[str, Any]:
    path = Path(root) / FRAMEWORK_PATH
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "bounty-standing-monitor-sensors/1":
        raise ValueError("unsupported standing-monitor sensor framework")
    return value


def sensor_plan_for(framework: Mapping[str, Any], business_type: str) -> dict[str, Any]:
    business = (framework.get("business_types") or {}).get(business_type)
    if not isinstance(business, Mapping):
        raise ValueError(f"unknown standing-monitor business type: {business_type}")
    return {
        "business_type": business_type,
        "common_sensors": list((framework.get("common_sensors") or {}).keys()),
        "selection_basis": business.get("selection_basis"),
        "economic_mechanism": business.get("economic_mechanism"),
        "required_operational_sensors": list(business.get("minimum_operational_set") or ()),
        "available_operational_sensors": dict(business.get("thesis_specific_sensors") or {}),
        "evidence_strength_order": list(framework.get("evidence_strength_order") or ()),
    }


def _receipt_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    return dict(value) if isinstance(value, Mapping) else {}


def _event(evidence: Mapping[str, Any], lane: str) -> dict[str, Any]:
    attributes = evidence.get("attributes") if isinstance(evidence.get("attributes"), Mapping) else {}
    exact_match = attributes.get("exact_implication_match") is True
    direct_article_verified = attributes.get("direct_article_verified") is True
    media_lane = lane in {"qualifying_business_news", "sell_side_public_mentions"}
    return {
        "url": evidence.get("url"),
        "title": evidence.get("title"),
        "published_at": evidence.get("published_at"),
        "event_date": attributes.get("event_date"),
        "passage": attributes.get("passage") or evidence.get("text"),
        "outlet": attributes.get("outlet"),
        "direct_article_verified": direct_article_verified,
        "exact_implication_match": exact_match,
        "management_attribution": (
            exact_match
            if lane in {"official_ir", "regulator_filings", "earnings_calls"}
            else attributes.get("management_attribution") is True
        ),
        "qualifying": (
            exact_match and direct_article_verified
            if media_lane
            else attributes.get("qualifying") is True
        ),
    }


def build_public_parity_observation(
    framework: Mapping[str, Any],
    config: Mapping[str, Any],
    receipts: Mapping[str, Any],
    *,
    observed_at: str,
) -> dict[str, Any]:
    """Build one fail-closed public-information-parity observation.

    Public sell-side summaries are a separate lane. Paywalled/private research is
    deliberately outside scope and is never represented as checked.
    """

    parity = (framework.get("common_sensors") or {}).get("public_information_parity") or {}
    required = list(parity.get("required_lanes") or ())
    lanes: dict[str, Any] = {}
    all_healthy = True
    qualifying: list[dict[str, Any]] = []
    management: list[dict[str, Any]] = []

    for lane in required:
        receipt = _receipt_dict(receipts.get(lane))
        state = str(receipt.get("state") or "source_unavailable").lower()
        health = str(receipt.get("health_state") or "unknown").lower()
        evidence = [item for item in receipt.get("evidence") or () if isinstance(item, Mapping)]
        events = [_event(item, lane) for item in evidence]
        request = receipt.get("request") if isinstance(receipt.get("request"), Mapping) else {}
        options = request.get("options") if isinstance(request.get("options"), Mapping) else {}
        requested_count = len(options.get("sources") or options.get("rss_queries") or ())
        lane_healthy = state in _TERMINAL_STATES and health == "healthy"
        directly_read = [
            event for event in events
            if lane not in {"qualifying_business_news", "sell_side_public_mentions"}
            or event["direct_article_verified"]
        ]
        qualifying_events = [event for event in directly_read if event["qualifying"]]
        all_healthy = all_healthy and lane_healthy
        lanes[lane] = {
            "status": "complete" if lane_healthy else "source_gap",
            "state": state,
            "receipt_state": state,
            "health_state": health,
            "requested_count": requested_count,
            "checked_count": len(evidence),
            "retrieved_count": len(directly_read),
            "exact_implication_match_count": sum(event["exact_implication_match"] for event in directly_read),
            "qualifying_count": len(qualifying_events),
            "events": events,
            "evidence": events,
            "error_category": receipt.get("error_category"),
        }
        if lane in {"qualifying_business_news", "sell_side_public_mentions"}:
            qualifying.extend(
                {**event, "lane": lane}
                for event in qualifying_events
            )
        if lane in {"official_ir", "regulator_filings", "earnings_calls"}:
            management.extend(
                {**event, "lane": lane}
                for event in events
                if event["exact_implication_match"] and event["management_attribution"]
            )

    if not all_healthy:
        state = "SOURCE_FAILURE"
    elif management or len(qualifying) >= 3:
        state = "EXTENSIVE_COVERAGE_REVIEW"
    elif qualifying:
        state = "EARLY_BUSINESS_COVERAGE"
    else:
        state = "NICHE_ONLY"

    return {
        "schema_version": "bounty-public-information-parity/1",
        "monitor_id": config.get("monitor_id"),
        "candidate": config.get("candidate"),
        "exact_implication": config.get("exact_implication"),
        "observed_at": observed_at,
        "window": {"type": "latest_calendar_days", "days": int(config.get("window_days") or 14)},
        "parity_state": state,
        "qualifying_independent_business_financial_outlet_count": len(qualifying),
        "qualifying_independent_outlets": qualifying,
        "management_acknowledges_a_and_w_economics": bool(management),
        "management_acknowledgments": management,
        "lanes": lanes,
        "sell_side_public_mentions": lanes.get("sell_side_public_mentions", {}),
        "paywalled_research": {
            "status": "not_observable_not_checked",
            "checked": False,
            "note": "Paywalled or private research is outside this public monitor and is not represented as checked.",
        },
        "source_health": {
            "all_required_public_lanes_healthy": all_healthy,
            "required_lane_count": len(required),
            "healthy_lane_count": sum(
                row["state"] in _TERMINAL_STATES and row["health_state"] == "healthy"
                for row in lanes.values()
            ),
        },
        "automatic_trade_action": False,
    }
