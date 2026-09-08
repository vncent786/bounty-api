import json
import sys

import scripts.collect_walmart_native_panel as native
import pytest
from scripts.collect_walmart_native_panel import (
    _classify_missing,
    _classify_product,
    _native_replenished_modes,
    _proxy_city,
    _proxy_username,
)


def _location():
    return {
        "metro": "Seattle/Renton",
        "proxy_city": "renton",
        "store_id": "2516",
        "postal_code": "98057",
    }


def _product(*, pickup="OUT_OF_STOCK", delivery="OUT_OF_STOCK", shipping="NOT_AVAILABLE"):
    return {
        "item_id": "20175615729",
        "location": {"store_id": "2516", "postal_code": "98057"},
        "fulfillment": [
            {"type": "SHIPPING", "availability_status": shipping},
            {"type": "PICKUP", "availability_status": pickup},
            {"type": "DELIVERY", "availability_status": delivery},
        ],
    }


def test_proxy_identity_is_stable_per_market_without_exposing_password():
    location = _location()

    assert _proxy_city(location) == "renton"
    first = _proxy_username("base", location)
    second = _proxy_username("base", location)
    alternate = _proxy_username("base", location, 2)

    assert first == second
    assert first != alternate
    assert first.endswith("-lifetime-10-session-walmartrentonv1")
    assert alternate.endswith("-lifetime-10-session-walmartrentonv2")
    assert "country-US-city-renton" in first


def test_dom_click_fallback_applies_store_when_mouse_events_do_not(monkeypatch):
    location = {
        "metro": "Sacramento",
        "proxy_city": "sacramento",
        "store_id": "3081",
        "postal_code": "95829",
        "store_source_url": "https://www.walmart.com/store/3081-sacramento-ca",
    }

    class FakeClient:
        def __init__(self):
            self.dom_clicked = False
            self.calls = []
            self.navigated = []

        def navigate(self, url, wait_seconds):
            self.navigated.append((url, wait_seconds))

        def evaluate(self, expression):
            if expression == native._page_state_expression():
                return {
                    "title": "Walmart Supercenter #3081",
                    "url": location["store_source_url"],
                    "body_start": "Walmart Supercenter #3081 Make this my store",
                    "challenge": False,
                }
            if expression == native._cookie_store_expression():
                return "3081" if self.dom_clicked else "2468"
            if "getBoundingClientRect" in expression:
                return {"x": 10, "y": 20}
            if "button.click()" in expression:
                self.dom_clicked = True
                return True
            raise AssertionError(f"unexpected expression: {expression}")

        def call(self, method, params):
            self.calls.append((method, params))

    times = iter([0, 16, 20, 21])
    monkeypatch.setattr(native.time, "time", lambda: next(times))
    monkeypatch.setattr(native.time, "sleep", lambda _seconds: None)
    client = FakeClient()

    selected = native._select_store(client, location)

    assert selected["store_id"] == "3081"
    assert selected["postal_code"] == "95829"
    assert client.dom_clicked is True
    assert [call[0] for call in client.calls] == [
        "Input.dispatchMouseEvent",
        "Input.dispatchMouseEvent",
        "Input.dispatchMouseEvent",
    ]


def test_challenge_retry_uses_a_fresh_proxy_session(monkeypatch, tmp_path):
    location = {
        "metro": "Sacramento",
        "proxy_city": "sacramento",
        "store_id": "3081",
        "postal_code": "95829",
        "store_source_url": "https://www.walmart.com/store/3081-sacramento-ca",
    }
    attempts = []

    def collect(_location, _item_id, _brave, _evidence_dir, proxy_attempt):
        attempts.append(proxy_attempt)
        if proxy_attempt == 1:
            return {
                "status": "unavailable_challenge",
                "observed_at": "2026-09-04T00:00:00Z",
            }
        return {"status": "orderable"}

    monkeypatch.setattr(native, "_collect_location", collect)

    record = native._collect_location_with_retry(
        location,
        "20175615729",
        tmp_path / "brave.exe",
        tmp_path / "evidence",
    )

    assert attempts == [1, 2]
    assert record["status"] == "orderable"
    assert record["challenge_retry"] == {
        "attempted": True,
        "first_attempt": {
            "status": "unavailable_challenge",
            "observed_at": "2026-09-04T00:00:00Z",
        },
        "final_attempt": 2,
    }


def test_local_pickup_or_delivery_counts_as_orderable():
    assert _classify_product(
        _product(pickup="IN_STOCK"), _location(), "20175615729"
    ) == "orderable"
    assert _classify_product(
        _product(delivery="IN_STOCK"), _location(), "20175615729"
    ) == "orderable"


def test_verified_local_depletion_is_out_of_stock():
    assert _classify_product(
        _product(), _location(), "20175615729"
    ) == "out_of_stock"


def test_duplicate_fulfillment_modes_fail_closed_as_contradictory():
    product = _product()
    product["fulfillment"].append({
        "type": "PICKUP",
        "availability_status": "IN_STOCK",
    })

    assert _classify_product(
        product, _location(), "20175615729"
    ) == "unavailable_contradictory"


def test_shipping_only_is_not_local_orderability():
    assert _classify_product(
        _product(pickup="", delivery="", shipping="IN_STOCK"),
        _location(), "20175615729"
    ) == "shipping_only_orderable"


def test_wrong_product_or_location_fails_closed():
    wrong_product = _product()
    wrong_product["item_id"] = "wrong"
    wrong_location = _product()
    wrong_location["location"]["store_id"] = "5073"

    assert _classify_product(
        wrong_product, _location(), "20175615729"
    ) == "unavailable_product_unverified"
    assert _classify_product(
        wrong_location, _location(), "20175615729"
    ) == "unavailable_location_unverified"


def test_store_address_zip_is_not_confused_with_delivery_destination_zip():
    product = _product()
    product["location"].update({
        "pickup_store": "2516",
        "delivery_store": "2516",
        "postal_code": "98001",
    })

    assert _classify_product(
        product, _location(), "20175615729"
    ) == "out_of_stock"

    product["location"]["pickup_store"] = "9999"
    assert _classify_product(
        product, _location(), "20175615729"
    ) == "unavailable_location_unverified"


def test_missing_listing_requires_two_route_corroboration():
    verified_zero = {
        "location_verified": True,
        "exact_item_links": [],
        "zero_exact_results": True,
        "no_local_results": False,
        "challenge": False,
    }
    exact_link = {**verified_zero, "exact_item_links": ["https://walmart/item"]}
    unverified = {**verified_zero, "location_verified": False}

    assert _classify_missing(True, verified_zero) == "not_listed_at_store"
    assert _classify_missing(False, verified_zero) == "availability_unknown"
    assert _classify_missing(True, exact_link) == "availability_unknown"
    assert _classify_missing(True, unverified) == "unavailable_location_unverified"


def test_replenishment_requires_same_verified_local_mode():
    base = {
        "record_key": "walmart:2516:98057:20175615729",
        "target_location_verified": True,
        "product_identity_verified": True,
    }
    before = {
        **base,
        "status": "out_of_stock",
        "product": {"fulfillment": [
            {"type": "PICKUP", "availability_status": "OUT_OF_STOCK"},
        ]},
    }
    different_mode_after = {
        **base,
        "status": "orderable",
        "product": {"fulfillment": [
            {"type": "DELIVERY", "availability_status": "IN_STOCK"},
        ]},
    }
    same_mode_after = {
        **base,
        "status": "orderable",
        "product": {"fulfillment": [
            {"type": "PICKUP", "availability_status": "IN_STOCK"},
        ]},
    }

    assert _native_replenished_modes(before, different_mode_after) == []
    assert _native_replenished_modes(before, same_mode_after) == ["PICKUP"]


def _panel_record(index, status):
    local_state = "IN_STOCK" if status == "orderable" else "OUT_OF_STOCK"
    return {
        "record_key": f"walmart:{index}:zip:item",
        "target_location_verified": True,
        "product_identity_verified": True,
        "status": status,
        "product": {"fulfillment": [
            {"type": "PICKUP", "availability_status": local_state},
            {"type": "DELIVERY", "availability_status": local_state},
        ]},
    }


def test_previous_distinct_day_ignores_intraday_retries_and_uses_best_coverage():
    low_coverage = {
        "observed_at": "2026-09-03T01:00:00Z",
        "records": [_panel_record(0, "out_of_stock")],
    }
    full_coverage = {
        "observed_at": "2026-09-03T02:00:00Z",
        "records": [_panel_record(i, "out_of_stock") for i in range(6)],
    }
    same_day = {
        "observed_at": "2026-09-04T01:00:00Z",
        "records": [_panel_record(i, "orderable") for i in range(6)],
    }

    selected = native._previous_distinct_day_snapshot(
        [low_coverage, full_coverage, same_day],
        "2026-09-04T02:00:00Z",
    )

    assert selected is full_coverage


def test_restock_monitor_alerts_on_first_verified_replenishment():
    records = [_panel_record(0, "orderable")] + [
        _panel_record(i, "out_of_stock") for i in range(1, 6)
    ]
    changes = [{
        "record_key": records[0]["record_key"],
        "before": "out_of_stock",
        "after": "orderable",
        "replenishment_candidate": True,
        "replenished_local_modes": ["PICKUP", "DELIVERY"],
    }]

    monitor = native._restock_monitor(
        records, changes, 6, {"broad_restock_orderable_share": 2 / 3},
        {"restock_monitor": {"fingerprint": "prior"}},
    )

    assert monitor["state"] == "replenishment_started"
    assert monitor["newly_orderable_store_count"] == 1
    assert monitor["same_mode_replenishment_store_count"] == 1
    assert monitor["alert_required"] is True
    assert monitor["action"] == "human_thesis_and_exit_review"
    assert monitor["automatic_trade_action"] is False


def test_restock_monitor_distinguishes_broad_and_full_restock():
    broad = native._restock_monitor(
        [_panel_record(i, "orderable" if i < 4 else "out_of_stock") for i in range(6)],
        [], 6, {"broad_restock_orderable_share": 2 / 3}, None,
    )
    full = native._restock_monitor(
        [_panel_record(i, "orderable") for i in range(6)],
        [], 6, {"broad_restock_orderable_share": 2 / 3}, None,
    )

    assert broad["state"] == "broadly_restocked"
    assert broad["orderable"] == 4
    assert full["state"] == "fully_restocked"
    assert full["orderable"] == 6
    assert full["full_restock_requires_orderable_stores"] == 6
    assert full["automatic_trade_action"] is False


def test_restock_monitor_treats_unknown_local_availability_as_source_failure():
    records = [_panel_record(i, "out_of_stock") for i in range(5)]
    records.append(_panel_record(5, "availability_unknown"))

    monitor = native._restock_monitor(records, [], 6, {}, None)

    assert monitor["state"] == "source_failure"
    assert monitor["unavailable"] == 1
    assert monitor["unresolved_statuses"] == ["availability_unknown"]
    assert monitor["automatic_trade_action"] is False


def test_partial_replenishment_alert_is_plain_english():
    records = [
        {
            **_panel_record(0, "orderable"),
            "requested_location": {
                "metro": "Sacramento", "store_id": "3081", "postal_code": "95829",
            },
        },
        {
            **_panel_record(1, "unavailable_location_unverified"),
            "requested_location": {
                "metro": "Dallas", "store_id": "8930", "postal_code": "75248",
            },
        },
        {
            **_panel_record(2, "unavailable_error"),
            "requested_location": {
                "metro": "Atlanta", "store_id": "3709", "postal_code": "30316",
            },
            "error_code": "target_store_cookie_not_applied",
        },
        {
            **_panel_record(3, "out_of_stock"),
            "requested_location": {
                "metro": "Chicago", "store_id": "5402", "postal_code": "60639",
            },
        },
        {
            **_panel_record(4, "out_of_stock"),
            "requested_location": {
                "metro": "Miami", "store_id": "5854", "postal_code": "33155",
            },
        },
        {
            **_panel_record(5, "out_of_stock"),
            "requested_location": {
                "metro": "Seattle/Renton", "store_id": "2516", "postal_code": "98057",
            },
        },
    ]
    changes = [{
        "record_key": records[0]["record_key"],
        "before": "out_of_stock",
        "after": "orderable",
        "replenishment_candidate": True,
        "replenished_local_modes": ["DELIVERY", "PICKUP"],
    }]
    monitor = native._restock_monitor(
        records, changes, 6, {}, {"restock_monitor": {"fingerprint": "prior"}},
    )
    snapshot = {
        "observed_at": "2026-09-05T01:38:23Z",
        "records": records,
        "restock_monitor": monitor,
    }

    message = native._format_restock_alert(snapshot)

    assert "GHOST x A&W Walmart update" in message
    assert "Sacramento became locally available" in message
    assert "1 available, 3 out of stock, 2 unverified" in message
    assert "Dallas: wrong store returned" in message
    assert "Atlanta: collection failed" in message
    assert "Not broad or full restock yet" in message
    assert "No automatic trade" in message
    assert "{" not in message
    assert "record_key" not in message


def test_browser_launch_failure_removes_plaintext_proxy_extension(monkeypatch, tmp_path):
    location = {
        "metro": "Dallas",
        "proxy_city": "dallas",
        "store_id": "8930",
        "postal_code": "75248",
        "store_source_url": "https://www.walmart.com/store/8930-dallas-tx",
    }
    extension = tmp_path / "proxy-extension"
    extension.mkdir()
    (extension / "background.js").write_text("secret-password", encoding="utf-8")
    brave = tmp_path / "brave.exe"
    brave.write_text("", encoding="utf-8")
    monkeypatch.setenv("BOUNTY_PROXY_SERVER", "http://proxy.example:10000")
    monkeypatch.setenv("BOUNTY_PROXY_USERNAME", "user")
    monkeypatch.setenv("BOUNTY_PROXY_PASSWORD", "secret-password")
    monkeypatch.setattr(native, "ROOT", tmp_path)
    monkeypatch.setattr(
        native, "_cleanup_stale_profile_process",
        lambda profile: profile / ".bounty_native_browser.pid",
    )
    monkeypatch.setattr(native, "_write_proxy_extension", lambda *args: extension)
    monkeypatch.setattr(
        native.subprocess, "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("launch failed")),
    )

    with pytest.raises(OSError, match="launch failed"):
        native._collect_location(location, "20175615729", brave, tmp_path / "evidence")

    assert not extension.exists()


def test_partial_run_never_overwrites_latest_or_appends_history(monkeypatch, tmp_path):
    config = {
        "product_identifiers": {"walmart_us_item_id": "20175615729"},
        "walmart_locations": [
            {
                "metro": "Dallas",
                "proxy_city": "dallas",
                "store_id": "8930",
                "postal_code": "75248",
                "store_source_url": "https://www.walmart.com/store/8930-dallas-tx",
            },
            {
                "metro": "Renton",
                "proxy_city": "renton",
                "store_id": "2516",
                "postal_code": "98057",
                "store_source_url": "https://www.walmart.com/store/2516-renton-wa",
            },
        ],
        "rules": {"position_monitor": {}},
    }
    config_path = tmp_path / "config.json"
    latest_path = tmp_path / "latest.json"
    history_path = tmp_path / "history.jsonl"
    evidence_dir = tmp_path / "evidence"
    diagnostic_path = tmp_path / "diagnostic.json"
    brave_path = tmp_path / "brave.exe"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    latest_path.write_text("sentinel-latest", encoding="utf-8")
    history_path.write_text("sentinel-history\n", encoding="utf-8")
    brave_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("BOUNTY_BRAVE_PATH", str(brave_path))
    monkeypatch.setattr(native, "_collect_location_with_retry", lambda location, item_id, brave, evidence: {
        "record_key": f"walmart:{location['store_id']}:{location['postal_code']}:{item_id}",
        "retailer": "Walmart",
        "status": "not_listed_at_store",
        "target_location_verified": True,
        "product_identity_verified": False,
    })
    monkeypatch.setattr(sys, "argv", [
        "collect_walmart_native_panel.py",
        "--config", str(config_path),
        "--latest", str(latest_path),
        "--history", str(history_path),
        "--evidence-dir", str(evidence_dir),
        "--diagnostic-output", str(diagnostic_path),
        "--stores", "8930",
    ])

    assert native.main() == 0
    assert latest_path.read_text(encoding="utf-8") == "sentinel-latest"
    assert history_path.read_text(encoding="utf-8") == "sentinel-history\n"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert diagnostic["coverage_status"] == "partial_diagnostic"
    assert diagnostic["fresh_record_count"] == 1
    assert diagnostic["records"][0]["record_key"].startswith("walmart:8930:")


def test_full_restock_run_emits_human_review_alert(monkeypatch, tmp_path, capsys):
    locations = [
        {
            "metro": f"Store {index}",
            "proxy_city": f"city{index}",
            "store_id": str(index),
            "postal_code": f"0000{index}",
            "store_source_url": f"https://www.walmart.com/store/{index}",
        }
        for index in range(2)
    ]
    config = {
        "product_identifiers": {"walmart_us_item_id": "20175615729"},
        "walmart_locations": locations,
        "rules": {
            "position_monitor": {
                "minimum_verified_walmart_locations": 1,
                "majority_depleted_baseline_required": False,
            },
            "restock_monitor": {
                "minimum_verified_walmart_locations": 1,
                "broad_restock_orderable_share": 2 / 3,
            },
        },
    }
    previous = {
        "schema_version": "walmart-native-panel/3",
        "observed_at": "2026-09-03T00:00:00Z",
        "item_id": "20175615729",
        "coverage_status": "complete",
        "records": [_panel_record(i, "out_of_stock") for i in range(2)],
        "restock_monitor": {"fingerprint": "depleted"},
    }
    config_path = tmp_path / "config.json"
    latest_path = tmp_path / "latest.json"
    history_path = tmp_path / "history.jsonl"
    evidence_dir = tmp_path / "evidence"
    brave_path = tmp_path / "brave.exe"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    latest_path.write_text(json.dumps(previous), encoding="utf-8")
    history_path.write_text(json.dumps(previous) + "\n", encoding="utf-8")
    brave_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("BOUNTY_BRAVE_PATH", str(brave_path))
    monkeypatch.setattr(
        native,
        "_collect_location_with_retry",
        lambda location, item_id, brave, evidence: _panel_record(
            int(location["store_id"]), "orderable"
        ),
    )
    monkeypatch.setattr(native, "_utc_now", lambda: "2026-09-04T00:00:00Z")
    monkeypatch.setattr(sys, "argv", [
        "collect_walmart_native_panel.py",
        "--config", str(config_path),
        "--latest", str(latest_path),
        "--history", str(history_path),
        "--evidence-dir", str(evidence_dir),
        "--workers", "2",
    ])

    assert native.main() == 0

    output = capsys.readouterr().out
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    assert "GHOST x A&W Walmart update" in output
    assert "All 2 monitored stores are now locally available" in output
    assert "Human thesis review. No automatic trade" in output
    assert "{" not in output
    assert "record_key" not in output
    assert latest["restock_monitor"]["state"] == "fully_restocked"
    assert latest["restock_monitor"]["alert_required"] is True
    assert latest["restock_monitor"]["action"] == "human_thesis_and_exit_review"
    assert latest["automatic_trade_action"] is False


def test_challenge_is_never_a_stockout():
    assert _classify_missing(True, {
        "challenge": True,
        "location_verified": True,
        "exact_item_links": [],
        "zero_exact_results": True,
    }) == "unavailable_challenge"
