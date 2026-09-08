import json

from scripts.collect_ghost_store_panel import (
    _changes,
    _material_signature,
    _panel_id,
    _position_monitor,
    _previous_snapshot_relation,
    _provider_availability_record,
    _retailer_record,
    _shopify_product_record,
    _walmart_scrapingbee_record,
    _walmart_serpapi_record,
)


def test_location_override_failure_ignores_unverified_product_payload_changes():
    before = {
        "record_key": "walmart:8930:75248:item",
        "status": "unavailable_location_override_failed",
        "target_location_verified": False,
        "observed_location": {"store_id": "3081", "postal_code": "95829"},
        "retailer_badges": [{"text": "2K+ bought since yesterday"}],
        "fulfillment": [{"type": "PICKUP", "availability_status": "OUT_OF_STOCK"}],
    }
    after = {
        **before,
        "retailer_badges": [],
        "fulfillment": [{"type": "PICKUP", "availability_status": "IN_STOCK"}],
    }

    assert _material_signature(before) == _material_signature(after)
    assert _changes({"records": [before]}, [after]) == []


def test_intermittent_retailer_badge_does_not_create_inventory_alert():
    before = {
        "record_key": "walmart:3081:95829:item",
        "status": "out_of_stock",
        "target_location_verified": True,
        "product_availability_status": "OUT_OF_STOCK",
        "fulfillment": [{"type": "PICKUP", "availability_status": "OUT_OF_STOCK"}],
        "retailer_badges": [],
    }
    after = {
        **before,
        "retailer_badges": [{"text": "2K+ bought since yesterday"}],
    }

    assert _changes({"records": [before]}, [after]) == []


def test_verified_out_of_stock_to_orderable_is_replenishment_candidate():
    before = {
        "record_key": "walmart:3081:95829:item",
        "retailer": "Walmart",
        "listing_id": "item",
        "observed_item_id": "item",
        "product_identity_verified": True,
        "target_location_verified": True,
        "status": "out_of_stock",
        "product_availability_status": "OUT_OF_STOCK",
        "fulfillment": [{"type": "PICKUP", "availability_status": "OUT_OF_STOCK"}],
    }
    after = {
        "record_key": "walmart:3081:95829:item",
        "retailer": "Walmart",
        "listing_id": "item",
        "observed_item_id": "item",
        "product_identity_verified": True,
        "target_location_verified": True,
        "status": "orderable",
        "product_availability_status": "IN_STOCK",
        "fulfillment": [{"type": "PICKUP", "availability_status": "IN_STOCK"}],
    }

    changes = _changes({"records": [before]}, [after])

    assert len(changes) == 1
    assert changes[0]["replenishment_candidate"] is True


def test_replenishment_requires_verified_product_location_and_same_local_mode():
    trusted = {
        "record_key": "walmart:3081:95829:20175615729",
        "retailer": "Walmart",
        "listing_id": "20175615729",
        "observed_item_id": "20175615729",
        "product_identity_verified": True,
        "target_location_verified": True,
    }
    unverified_before = {
        **trusted,
        "product_identity_verified": False,
        "status": "out_of_stock",
        "fulfillment": [{"type": "PICKUP", "available": False}],
    }
    current = {
        **trusted,
        "status": "orderable",
        "fulfillment": [{"type": "PICKUP", "available": True}],
    }
    different_mode_before = {
        **trusted,
        "status": "out_of_stock",
        "fulfillment": [{"type": "PICKUP", "available": False}],
    }
    different_mode_after = {
        **trusted,
        "status": "orderable",
        "fulfillment": [{"type": "DELIVERY", "available": True}],
    }

    assert _changes({"records": [unverified_before]}, [current])[0]["replenishment_candidate"] is False
    assert _changes({"records": [different_mode_before]}, [different_mode_after])[0]["replenishment_candidate"] is False


def test_unverified_location_becoming_verified_is_a_material_change_not_replenishment():
    before = {
        "record_key": "walmart:8930:75248:item",
        "status": "unavailable_location_override_failed",
        "target_location_verified": False,
        "observed_location": {"store_id": "3081", "postal_code": "95829"},
    }
    after = {
        "record_key": "walmart:8930:75248:item",
        "status": "orderable",
        "target_location_verified": True,
        "product_availability_status": "IN_STOCK",
        "fulfillment": [{"type": "PICKUP", "availability_status": "IN_STOCK"}],
    }

    changes = _changes({"records": [before]}, [after])

    assert len(changes) == 1
    assert changes[0]["replenishment_candidate"] is False


def _location(store_id="8930", postal_code="75248"):
    return {
        "metro": "Dallas",
        "store_id": store_id,
        "postal_code": postal_code,
        "store_source_url": f"https://www.walmart.com/store/{store_id}-dallas-tx",
    }


def _snapshot(statuses, observed_at="2026-09-02T00:00:00Z"):
    return {
        "observed_at": observed_at,
        "records": [{
            "record_key": f"walmart:{index}",
            "retailer": "Walmart",
            "target_location_verified": True,
            "product_identity_verified": True,
            "listing_id": "20175615729",
            "observed_item_id": "20175615729",
            "status": status,
        } for index, status in enumerate(statuses)],
    }


def test_serpapi_payload_requires_exact_store_and_preserves_fulfillment():
    payload = {
        "search_information": {
            "location": {
                "store_id": "8930",
                "postal_code": "75248",
                "city": "Dallas",
                "province_code": "TX",
            }
        },
        "product_result": {
            "us_item_id": "20175615729",
            "title": "GHOST Energy A&W Root Beer",
            "price": 2.68,
            "shipping_option": {"available": False, "location": "Dallas, 75248"},
            "pickup_option": {"available": True, "location": "Dallas Supercenter"},
            "delivery_option": {"available": False, "location": "Dallas, 75248"},
        },
    }
    body = json.dumps(payload).encode()

    record = _provider_availability_record(
        _location(), observed_at="2026-09-02T00:00:00Z",
        provider="serpapi_walmart_product", route="https://serpapi.com/search.json",
        payload=payload, source_body=body,
    )

    assert record["target_location_verified"] is True
    assert record["status"] == "orderable"
    assert [item["available"] for item in record["fulfillment"]] == [False, True, False]


def test_shipping_only_does_not_count_as_local_store_orderability():
    payload = {
        "search_information": {"location": {"store_id": "8930", "postal_code": "75248"}},
        "product_result": {
            "us_item_id": "20175615729",
            "shipping_option": {"available": True},
            "pickup_option": {},
            "delivery_option": {},
        },
    }

    record = _provider_availability_record(
        _location(), observed_at="2026-09-02T00:00:00Z",
        provider="serpapi_walmart_product", route="https://serpapi.com/search.json",
        payload=payload, source_body=json.dumps(payload).encode(),
    )

    assert record["status"] == "shipping_only_orderable"


def test_provider_payload_with_wrong_store_is_unavailable_not_stockout():
    payload = {
        "search_information": {"location": {"store_id": "3081", "postal_code": "95829"}},
        "product_result": {
            "us_item_id": "20175615729",
            "shipping_option": {"available": False},
            "pickup_option": {"available": False},
            "delivery_option": {"available": False},
        },
    }

    record = _provider_availability_record(
        _location(), observed_at="2026-09-02T00:00:00Z",
        provider="serpapi_walmart_product", route="https://serpapi.com/search.json",
        payload=payload, source_body=json.dumps(payload).encode(),
    )

    assert record["target_location_verified"] is False
    assert record["status"] == "unavailable_provider_location_unverified"


def test_provider_payload_with_wrong_product_is_unavailable():
    payload = {
        "search_information": {"location": {"store_id": "8930", "postal_code": "75248"}},
        "product_result": {
            "us_item_id": "wrong-item",
            "pickup_option": {"available": True},
        },
    }

    record = _provider_availability_record(
        _location(), observed_at="2026-09-02T00:00:00Z",
        provider="serpapi_walmart_product", route="https://serpapi.com/search.json",
        payload=payload, source_body=json.dumps(payload).encode(),
    )

    assert record["product_identity_verified"] is False
    assert record["status"] == "unavailable_provider_product_unverified"


def test_serpapi_request_uses_configured_item_and_store_without_leaking_key():
    calls = []
    payload = {
        "search_information": {"location": {"store_id": "8930", "postal_code": "75248"}},
        "product_result": {
            "us_item_id": "custom-item",
            "pickup_option": {"available": True},
        },
    }

    class Response:
        status_code = 200
        content = json.dumps(payload).encode()

        @staticmethod
        def json():
            return payload

    def fetch(url, *, params, timeout):
        calls.append((url, params, timeout))
        return Response()

    record = _walmart_serpapi_record(
        _location(), observed_at="2026-09-02T00:00:00Z",
        api_key="secret-test-key", fetch=fetch, item_id="custom-item",
    )

    assert calls[0][1]["product_id"] == "custom-item"
    assert calls[0][1]["store_id"] == "8930"
    assert calls[0][1]["no_cache"] == "true"
    assert "secret-test-key" not in json.dumps(record)
    assert record["status"] == "orderable"


def test_scrapingbee_request_uses_configured_item_store_and_zip_without_leaking_key():
    calls = []
    payload = {
        "location": {"store_id": "8930", "zip_code": "75248"},
        "product": {
            "product_id": "custom-item",
            "pickup": {"available": False},
            "delivery": {"available": False},
            "shipping": {"available": False},
        },
    }

    class Response:
        status_code = 200
        content = json.dumps(payload).encode()

        @staticmethod
        def json():
            return payload

    def fetch(url, *, headers, params, timeout):
        calls.append((url, headers, params, timeout))
        return Response()

    record = _walmart_scrapingbee_record(
        _location(), observed_at="2026-09-02T00:00:00Z",
        api_key="secret-bee-key", fetch=fetch, item_id="custom-item",
    )

    assert calls[0][2]["product_id"] == "custom-item"
    assert calls[0][2]["store_id"] == "8930"
    assert calls[0][2]["delivery_zip"] == "75248"
    assert "secret-bee-key" not in json.dumps(record)
    assert record["status"] == "out_of_stock"


def test_shopify_product_json_verifies_frozen_ids_and_online_orderability():
    route = {
        "retailer": "GHOST direct",
        "url": "https://shop.example/products/ghost",
        "adapter": "shopify_product_json",
        "listing_id": "10030392410425",
        "variant_ids": ["51321206702393"],
    }
    payload = {
        "id": 10030392410425,
        "title": "GHOST Energy x A&W Root Beer",
        "available": True,
        "variants": [{
            "id": 51321206702393,
            "available": True,
            "price": 299,
            "sku": "GHOST-AW-16OZ",
        }],
    }

    class Response:
        status_code = 200
        content = json.dumps(payload).encode()
        url = "https://shop.example/products/ghost.js"

        @staticmethod
        def json():
            return payload

    record = _shopify_product_record(
        route, observed_at="2026-09-02T00:00:00Z", fetch=lambda *args, **kwargs: Response()
    )

    assert record["product_identity_verified"] is True
    assert record["status"] == "orderable"
    assert record["location_scoped"] is False
    assert record["availability_scope"] == "online_listing"
    assert record["fulfillment"] == [{
        "type": "SHIPPING",
        "availability_status": "IN_STOCK",
    }]


def test_shopify_product_json_rejects_wrong_product_identity():
    route = {
        "retailer": "GHOST direct",
        "url": "https://shop.example/products/ghost",
        "adapter": "shopify_product_json",
        "listing_id": "10030392410425",
        "variant_ids": ["51321206702393"],
    }
    payload = {
        "id": 999,
        "title": "Wrong product",
        "available": False,
        "variants": [{"id": 51321206702393, "available": False}],
    }

    class Response:
        status_code = 200
        content = json.dumps(payload).encode()
        url = "https://shop.example/products/ghost.js"

        @staticmethod
        def json():
            return payload

    record = _shopify_product_record(
        route, observed_at="2026-09-02T00:00:00Z", fetch=lambda *args, **kwargs: Response()
    )

    assert record["product_identity_verified"] is False
    assert record["status"] == "unavailable_product_unverified"


def test_shopify_product_json_requires_every_frozen_variant_to_be_present():
    route = {
        "retailer": "EnergyDrinkCity",
        "url": "https://shop.example/products/ghost",
        "adapter": "shopify_product_json",
        "listing_id": "15618749300809",
        "variant_ids": ["55829710340169", "missing-variant"],
    }
    payload = {
        "id": 15618749300809,
        "title": "Ghost A&W Root Beer LTO",
        "available": True,
        "variants": [{"id": 55829710340169, "available": True}],
    }

    class Response:
        status_code = 200
        content = json.dumps(payload).encode()
        url = "https://shop.example/products/ghost.js"

        @staticmethod
        def json():
            return payload

    record = _shopify_product_record(
        route, observed_at="2026-09-02T00:00:00Z", fetch=lambda *args, **kwargs: Response()
    )

    assert record["product_identity_verified"] is False
    assert record["status"] == "unavailable_variant_unverified"


def test_shopify_product_json_rejects_duplicate_variant_ids():
    route = {
        "retailer": "EnergyDrinkCity",
        "url": "https://shop.example/products/ghost",
        "adapter": "shopify_product_json",
        "listing_id": "15618749300809",
        "variant_ids": ["55829710340169"],
    }
    payload = {
        "id": 15618749300809,
        "title": "Ghost A&W Root Beer LTO",
        "available": True,
        "variants": [
            {"id": 55829710340169, "available": False},
            {"id": 55829710340169, "available": True},
        ],
    }

    class Response:
        status_code = 200
        content = json.dumps(payload).encode()
        url = "https://shop.example/products/ghost.js"

        @staticmethod
        def json():
            return payload

    record = _shopify_product_record(
        route, observed_at="2026-09-02T00:00:00Z", fetch=lambda *args, **kwargs: Response()
    )

    assert record["product_identity_verified"] is False
    assert record["status"] == "unavailable_variant_duplicate"


def test_shopify_product_json_marks_verified_sold_out_listing():
    route = {
        "retailer": "EnergyDrinkCity",
        "url": "https://shop.example/products/ghost",
        "adapter": "shopify_product_json",
        "listing_id": "15618749300809",
        "variant_ids": ["55829710340169"],
    }
    payload = {
        "id": 15618749300809,
        "title": "Ghost A&W Root Beer LTO",
        "available": False,
        "variants": [{"id": 55829710340169, "available": False}],
    }

    class Response:
        status_code = 200
        content = json.dumps(payload).encode()
        url = "https://shop.example/products/ghost.js"

        @staticmethod
        def json():
            return payload

    record = _shopify_product_record(
        route, observed_at="2026-09-02T00:00:00Z", fetch=lambda *args, **kwargs: Response()
    )

    assert record["product_identity_verified"] is True
    assert record["status"] == "out_of_stock"


def test_retailer_dispatches_frozen_shopify_adapter(monkeypatch):
    route = {
        "retailer": "GHOST direct",
        "url": "https://shop.example/products/ghost",
        "adapter": "shopify_product_json",
        "listing_id": "10030392410425",
        "variant_ids": ["51321206702393"],
    }
    payload = {
        "id": 10030392410425,
        "available": True,
        "variants": [{"id": 51321206702393, "available": True}],
    }

    class Response:
        status_code = 200
        content = json.dumps(payload).encode()
        url = "https://shop.example/products/ghost.js"

        @staticmethod
        def json():
            return payload

    record = _retailer_record(
        route,
        observed_at="2026-09-02T00:00:00Z",
        fetch=lambda *args, **kwargs: Response(),
    )

    assert record["provider"] == "self_hosted_shopify_product_json"
    assert record["status"] == "orderable"


def test_blocked_retailer_page_uses_managed_fallback_without_leaking_key(monkeypatch):
    monkeypatch.setenv("SCRAPINGBEE_API_KEY", "secret-managed-key")
    calls = []

    class Response:
        def __init__(self, status_code, body, url):
            self.status_code = status_code
            self.content = body.encode()
            self.url = url

    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        if url == "https://retailer.example/product":
            return Response(403, "<title>Access to this page has been denied</title>", url)
        return Response(
            200,
            "<title>GHOST A&W</title><body>In stock Add to cart</body>",
            url + "?api_key=secret-managed-key",
        )

    record = _retailer_record(
        {"retailer": "GNC", "url": "https://retailer.example/product"},
        observed_at="2026-09-02T00:00:00Z", fetch=fetch,
    )

    assert record["provider"] == "scrapingbee_managed_page"
    assert record["status"] == "catalog_available_unscoped"
    assert record["final_url"] == "https://retailer.example/product"
    assert "secret-managed-key" not in json.dumps(record)
    assert calls[1][1]["params"]["url"] == "https://retailer.example/product"


def test_previous_snapshot_relation_accepts_only_explicit_panel_migration():
    previous = {"panel_id": "old-panel", "config_hash": "old-config"}

    assert _previous_snapshot_relation(
        previous, config_hash="new-config", panel_id="new-panel",
        supersedes=["old-panel"],
    ) == "superseded"
    assert _previous_snapshot_relation(
        previous, config_hash="old-config", panel_id="new-panel",
        supersedes=[],
    ) == "same"
    assert _previous_snapshot_relation(
        previous, config_hash="new-config", panel_id="old-panel",
        supersedes=[],
    ) == "same"
    assert _previous_snapshot_relation(
        previous, config_hash="new-config", panel_id="new-panel",
        supersedes=[],
    ) == "rejected"


def test_panel_id_changes_when_frozen_product_identifier_changes():
    base = {
        "candidate": "GHOST A&W",
        "product_identifiers": {"walmart_us_item_id": "20175615729"},
        "walmart_locations": [_location()],
        "retailer_routes": [],
    }
    changed = {
        **base,
        "product_identifiers": {"walmart_us_item_id": "different-item"},
    }

    assert _panel_id(base) != _panel_id(changed)


def test_position_monitor_excludes_wrong_product_from_verified_denominator():
    depleted = _snapshot(["out_of_stock"] * 4)
    current = _snapshot(["orderable"] * 4)
    current["records"][-1].update({
        "product_identity_verified": False,
        "observed_item_id": "wrong-item",
    })

    result = _position_monitor(
        [depleted, _snapshot(["orderable"] * 4), _snapshot(["orderable"] * 4)],
        current,
        {
            "minimum_verified_walmart_locations": 4,
            "majority_threshold": 2 / 3,
            "majority_orderable_streak_observations": 3,
            "majority_depleted_baseline_required": True,
        },
    )

    assert result["latest"]["verified"] == 3
    assert result["state"] == "insufficient_coverage"
    assert result["action"] == "no_automatic_trade_action"


def test_position_monitor_requires_coverage_and_only_requests_human_exit_review():
    policy = {
        "minimum_verified_walmart_locations": 4,
        "majority_threshold": 2 / 3,
        "majority_orderable_streak_observations": 3,
        "majority_depleted_baseline_required": True,
    }
    depleted = _snapshot(["out_of_stock"] * 4, "2026-09-01T00:00:00Z")
    orderable_day_1 = _snapshot(["orderable"] * 4, "2026-09-02T00:00:00Z")
    orderable_day_2 = _snapshot(["orderable"] * 4, "2026-09-03T00:00:00Z")
    orderable_day_3 = _snapshot(["orderable"] * 4, "2026-09-04T00:00:00Z")

    result = _position_monitor(
        [depleted, orderable_day_1, orderable_day_2], orderable_day_3, policy
    )

    assert result["state"] == "exit_review_availability_normalized"
    assert result["action"] == "human_exit_review"
    assert result["current_orderable_streak"] == 3
    assert result["streak_unit"] == "distinct_consecutive_utc_days"
    assert result["automatic_trade_action"] is False


def test_position_monitor_does_not_count_intraday_reruns_as_daily_streak():
    policy = {
        "minimum_verified_walmart_locations": 4,
        "majority_threshold": 2 / 3,
        "majority_orderable_streak_observations": 3,
        "majority_depleted_baseline_required": True,
    }
    depleted = _snapshot(["out_of_stock"] * 4, "2026-09-01T23:00:00Z")
    orderable_1 = _snapshot(["orderable"] * 4, "2026-09-02T01:00:00Z")
    orderable_2 = _snapshot(["orderable"] * 4, "2026-09-02T02:00:00Z")
    orderable_3 = _snapshot(["orderable"] * 4, "2026-09-02T03:00:00Z")

    result = _position_monitor(
        [depleted, orderable_1, orderable_2], orderable_3, policy
    )

    assert result["state"] == "monitor"
    assert result["current_orderable_streak"] == 1
    assert result["distinct_observation_days"] == 2
    assert result["majority_depleted_baseline_seen"] is True
    assert result["automatic_trade_action"] is False


def test_position_monitor_refuses_signal_with_insufficient_verified_locations():
    result = _position_monitor(
        [], _snapshot(["out_of_stock"]),
        {"minimum_verified_walmart_locations": 4},
    )

    assert result["state"] == "insufficient_coverage"
    assert result["action"] == "no_automatic_trade_action"
