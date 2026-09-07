"""Contract checks for success-led standing-monitor presentation."""

import json
from pathlib import Path


def test_reusable_ghost_contract_freezes_success_led_monitoring_rules():
    root = Path(__file__).parents[2]
    contract = json.loads(
        (root / "references" / "ghost-thesis-realization-monitor-contract-v1.json").read_text(
            encoding="utf-8"
        )
    )

    conversation = contract["evidence_axes"]["conversation_volume"]
    assert "every deduplicated exact original post" in conversation["headline_measurement"]
    assert "independently qualifying" in conversation["investing_quality_measurement"]
    assert contract["evidence_axes"]["sentiment_context"]["required_buckets"] == [
        "positive",
        "negative",
        "neutral",
        "mixed",
        "unclassified",
    ]
    dashboard = contract["dashboard_contract"]
    assert "daily rolling seven-day percentage change" in dashboard["charts"]
    assert "last successful observation remains visible" in dashboard["last_verified_rule"]
    assert "closed audit detail" in dashboard["audit"]
    assert "clickable persisted evidence" in dashboard["evidence_links"]
    parity = contract["evidence_axes"]["news_and_management_coverage"]
    assert parity["required_public_lanes"] == [
        "official_ir",
        "regulator_filings",
        "earnings_calls",
        "official_product_context",
        "qualifying_business_news",
        "sell_side_public_mentions",
    ]
    assert "not_observable_not_checked" in parity["paywalled_research_rule"]
