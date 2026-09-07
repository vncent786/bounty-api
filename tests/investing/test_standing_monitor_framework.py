"""Contract tests for the reusable standing-monitor sensor framework."""

from __future__ import annotations

import json
from pathlib import Path

from social_scraper.investing.standing_monitor import (
    build_public_parity_observation,
    load_sensor_framework,
    sensor_plan_for,
)


ROOT = Path(__file__).parents[2]


def test_framework_requires_common_sensors_and_explicit_consumer_industrial_routes():
    framework = load_sensor_framework(ROOT)

    assert framework["schema_version"] == "bounty-standing-monitor-sensors/1"
    assert set(framework["common_sensors"]) == {
        "google_search_attention",
        "public_information_parity",
    }
    parity = framework["common_sensors"]["public_information_parity"]
    assert set(parity["required_lanes"]) == {
        "official_ir",
        "regulator_filings",
        "earnings_calls",
        "official_product_context",
        "qualifying_business_news",
        "sell_side_public_mentions",
    }
    assert parity["paywalled_research"] == "not_observable_not_checked"

    consumer = framework["business_types"]["consumer_product"]
    assert {
        "retailer_availability",
        "independent_user_behavior",
        "repeat_purchase",
    } <= set(consumer["thesis_specific_sensors"])
    industrial = framework["business_types"]["industrial_and_data_center"]
    assert {
        "contracts_orders_backlog",
        "capacity_reservations_and_use",
        "permits_grid_and_construction",
    } <= set(industrial["thesis_specific_sensors"])
    assert framework["evidence_strength_order"] == [
        "money_committed",
        "capacity_committed_or_used",
        "purchase_preparation",
        "attention_only",
    ]


def test_sensor_plan_keeps_common_sensors_and_selects_by_economic_mechanism():
    framework = load_sensor_framework(ROOT)

    consumer = sensor_plan_for(framework, "consumer_product")
    industrial = sensor_plan_for(framework, "industrial_and_data_center")

    assert consumer["common_sensors"] == [
        "google_search_attention",
        "public_information_parity",
    ]
    assert consumer["selection_basis"] == "retail_sell_through_and_repeat_purchase"
    assert industrial["selection_basis"] == "orders_capacity_and_project_execution"
    assert "social_conversation" not in industrial["required_operational_sensors"]


def test_public_parity_observation_keeps_every_lane_and_does_not_claim_paywalls_checked():
    framework = load_sensor_framework(ROOT)
    config = {
        "monitor_id": "standing::fixture",
        "candidate": "Fixture Co",
        "exact_implication": "A named product changes issuer economics.",
        "window_days": 14,
    }
    receipts = {
        "official_ir": {
            "state": "complete",
            "health_state": "healthy",
            "evidence": [{
                "url": "https://issuer.example/ir",
                "title": "Issuer release",
                "published_at": "2026-09-06",
                "attributes": {"exact_implication_match": False},
            }],
        },
        "regulator_filings": {
            "state": "complete",
            "health_state": "healthy",
            "evidence": [{
                "url": "https://regulator.example/filing",
                "title": "Current filing",
                "published_at": "2026-09-06",
                "attributes": {"exact_implication_match": False},
            }],
        },
        "earnings_calls": {
            "state": "complete",
            "health_state": "healthy",
            "evidence": [{
                "url": "https://issuer.example/earnings-call",
                "title": "Q2 2026 earnings call",
                "published_at": "2026-08-06",
                "attributes": {
                    "event_date": "2026-08-06",
                    "retrieved": True,
                    "exact_implication_match": False,
                },
            }],
        },
        "official_product_context": {
            "state": "complete",
            "health_state": "healthy",
            "evidence": [{
                "url": "https://brand.example/product",
                "title": "Product page",
                "attributes": {"exact_implication_match": False},
            }],
        },
        "qualifying_business_news": {
            "state": "complete",
            "health_state": "healthy",
            "evidence": [{
                "url": "https://news.example/discovery-only",
                "title": "Exact implication in a search result",
                "attributes": {
                    "exact_implication_match": True,
                    "direct_article_verified": False,
                    "outlet": "Example Finance",
                },
            }],
        },
        "sell_side_public_mentions": {
            "state": "complete_empty",
            "health_state": "healthy",
            "evidence": [],
        },
    }

    observation = build_public_parity_observation(
        framework,
        config,
        receipts,
        observed_at="2026-09-06T13:00:00Z",
    )

    assert observation["parity_state"] == "NICHE_ONLY"
    assert set(observation["lanes"]) == set(
        framework["common_sensors"]["public_information_parity"]["required_lanes"]
    )
    assert observation["lanes"]["earnings_calls"]["retrieved_count"] == 1
    assert observation["lanes"]["earnings_calls"]["events"][0]["event_date"] == "2026-08-06"
    assert observation["lanes"]["qualifying_business_news"]["checked_count"] == 1
    assert observation["lanes"]["qualifying_business_news"]["retrieved_count"] == 0
    assert observation["lanes"]["qualifying_business_news"]["qualifying_count"] == 0
    assert observation["paywalled_research"] == {
        "status": "not_observable_not_checked",
        "checked": False,
        "note": "Paywalled or private research is outside this public monitor and is not represented as checked.",
    }
    assert observation["source_health"]["all_required_public_lanes_healthy"] is True


def test_framework_file_is_machine_readable_and_contains_no_monitor_specific_secret_values():
    path = ROOT / "references" / "standing-monitor-sensor-framework-v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload).casefold()

    assert "password" not in serialized
    assert "api_key" not in serialized
    assert "private position" not in serialized
