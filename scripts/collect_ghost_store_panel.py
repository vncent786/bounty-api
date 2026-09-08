from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import quote, urlsplit, urlunsplit
import uuid

from bs4 import BeautifulSoup
from curl_cffi import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
for env_path in (ROOT / ".env", ROOT.parent / "bounty-api-fresh" / ".env"):
    if env_path.exists():
        load_dotenv(env_path, override=False)
DEFAULT_CONFIG = ROOT / "references" / "ghost-store-panel-config-2026-09.json"
DEFAULT_LATEST = ROOT / "artifacts" / "dd" / "ghost-kdp" / "store_panel_latest.json"
DEFAULT_HISTORY = ROOT / "artifacts" / "dd" / "ghost-kdp" / "store_panel_history.jsonl"
DEFAULT_WALMART_ITEM_ID = "20175615729"
POSITION_SEMANTICS_VERSION = "local-pickup-delivery/1"
WALMART_ITEM_URL = f"https://www.walmart.com/ip/{DEFAULT_WALMART_ITEM_ID}"
SERPAPI_WALMART_URL = "https://serpapi.com/search.json"
SCRAPINGBEE_WALMART_URL = "https://app.scrapingbee.com/api/v1/walmart/product"
SCRAPINGBEE_GENERIC_URL = "https://app.scrapingbee.com/api/v1/"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _canonical_hash(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return sha256(encoded).hexdigest()


def _walmart_item_url(item_id: str) -> str:
    return f"https://www.walmart.com/ip/{item_id}"


def _walmart_cookie(store_id: str, postal_code: str) -> str:
    acid = str(uuid.uuid4())
    timestamp = int(time.time() * 1000)
    location = {
        "intent": "SHIPPING",
        "storeIntent": "PICKUP",
        "mergeFlag": True,
        "pickup": {"nodeId": store_id, "timestamp": timestamp},
        "postalCode": {"base": postal_code, "timestamp": timestamp},
        "validateKey": f"prod:v2:{acid}",
    }
    encoded = base64.urlsafe_b64encode(
        quote(json.dumps(location, separators=(",", ":")), safe="").encode()
    ).decode()
    return (
        f"ACID={acid}; hasACID=true; hasLocData=1; locDataV3={encoded}; "
        f"assortmentStoreId={store_id}; locGuestData={encoded}"
    )


def _next_data(body: bytes) -> dict:
    soup = BeautifulSoup(body, "html.parser")
    node = soup.find("script", id="__NEXT_DATA__")
    if node is None or not node.string:
        raise ValueError("walmart_next_data_missing")
    return json.loads(node.string)


def _walmart_base_record(
    location: dict, *, observed_at: str, provider: str,
    item_id: str = DEFAULT_WALMART_ITEM_ID,
) -> dict:
    requested_store = str(location["store_id"])
    requested_zip = str(location["postal_code"])
    return {
        "record_key": f"walmart:{requested_store}:{requested_zip}:{item_id}",
        "retailer": "Walmart",
        "provider": provider,
        "listing_id": item_id,
        "requested_location": {
            "metro": location["metro"],
            "store_id": requested_store,
            "postal_code": requested_zip,
            "source_url": location["store_source_url"],
        },
        "observed_at": observed_at,
        "status": "unavailable",
        "limitations": [
            "A stockout remains demand/supply ambiguous.",
            "One point-in-time observation cannot prove replenishment.",
        ],
    }


def _provider_availability_record(
    location: dict, *, observed_at: str, provider: str, route: str,
    payload: dict, source_body: bytes,
    item_id: str = DEFAULT_WALMART_ITEM_ID,
) -> dict:
    record = _walmart_base_record(
        location, observed_at=observed_at, provider=provider, item_id=item_id
    )
    record.update({
        "route": route,
        "http_status": 200,
        "source_bytes": len(source_body),
        "source_sha256": _digest(source_body),
    })
    search_location = payload.get("search_information", {}).get("location", {})
    if not search_location:
        search_location = payload.get("location") or {}
    product = payload.get("product_result") or payload.get("product") or payload
    observed_store = str(
        search_location.get("store_id")
        or search_location.get("storeId")
        or product.get("store_id")
        or product.get("storeId")
        or ""
    )
    observed_zip = str(
        search_location.get("postal_code")
        or search_location.get("zip_code")
        or search_location.get("postalCode")
        or product.get("delivery_zip")
        or ""
    )
    requested_store = str(location["store_id"])
    requested_zip = str(location["postal_code"])
    location_match = (
        observed_store == requested_store and observed_zip == requested_zip
    )
    observed_item_id = str(
        product.get("us_item_id")
        or product.get("usItemId")
        or product.get("product_id")
        or product.get("productId")
        or ""
    )
    item_match = observed_item_id == item_id
    options = []
    for name in ("shipping", "pickup", "delivery"):
        raw = (
            product.get(f"{name}_option")
            or product.get(name)
            or {}
        )
        available = raw.get("available") if isinstance(raw, dict) else None
        options.append({
            "type": name.upper(),
            "available": available if isinstance(available, bool) else None,
            "location_text": raw.get("location") if isinstance(raw, dict) else None,
            "price": raw.get("price") if isinstance(raw, dict) else None,
        })
    record.update({
        "observed_location": {
            "store_id": observed_store or None,
            "postal_code": observed_zip or None,
            "city": search_location.get("city"),
            "state": (
                search_location.get("province_code")
                or search_location.get("state")
            ),
        },
        "target_location_verified": location_match,
        "observed_item_id": observed_item_id or None,
        "product_identity_verified": item_match,
        "fulfillment": options,
        "title": product.get("title") or product.get("name"),
        "price": product.get("price"),
    })
    if not item_match:
        record["status"] = "unavailable_provider_product_unverified"
        record["limitations"].append(
            "Provider response did not preserve the requested Walmart item ID."
        )
        return record
    if not location_match:
        record["status"] = "unavailable_provider_location_unverified"
        record["limitations"].append(
            "Provider response did not resolve to the requested Walmart store ID and ZIP."
        )
        return record
    local_by_type = {
        option["type"]: option["available"] for option in options
        if option["type"] in {"PICKUP", "DELIVERY"}
        and option["available"] is not None
    }
    shipping_available = next(
        (
            option["available"] for option in options
            if option["type"] == "SHIPPING"
        ),
        None,
    )
    if any(value is True for value in local_by_type.values()):
        record["status"] = "orderable"
    elif (
        set(local_by_type) == {"PICKUP", "DELIVERY"}
        and all(value is False for value in local_by_type.values())
    ):
        record["status"] = "out_of_stock"
    elif shipping_available is True:
        record["status"] = "shipping_only_orderable"
    else:
        record["status"] = "availability_unknown"
    return record


def _walmart_direct_record(
    location: dict, *, observed_at: str,
    item_id: str = DEFAULT_WALMART_ITEM_ID,
) -> dict:
    requested_store = str(location["store_id"])
    requested_zip = str(location["postal_code"])
    item_url = _walmart_item_url(item_id)
    record = _walmart_base_record(
        location, observed_at=observed_at,
        provider="direct_html_fallback", item_id=item_id,
    )
    record["route"] = item_url
    try:
        response = requests.get(
            item_url,
            headers={"cookie": _walmart_cookie(requested_store, requested_zip)},
            impersonate="chrome",
            timeout=60,
            allow_redirects=True,
        )
        body = bytes(response.content)
        record.update({
            "http_status": response.status_code,
            "final_url": str(response.url),
            "source_bytes": len(body),
            "source_sha256": _digest(body),
        })
        if response.status_code != 200:
            record["status"] = "unavailable_http"
            return record
        payload = _next_data(body)
        data = payload["props"]["pageProps"]["initialData"]["data"]
        observed = data["contentLayout"]["pageMetadata"]["location"]
        product = data["product"]
        observed_store = str(observed.get("storeId") or "")
        observed_zip = str(observed.get("postalCode") or "")
        location_match = observed_store == requested_store and observed_zip == requested_zip
        fulfillment = [{
            "type": option.get("type"),
            "availability_status": option.get("availabilityStatus"),
            "location_text": option.get("locationText"),
            "available_quantity": option.get("availableQuantity"),
            "inventory_status": option.get("inventoryStatus"),
        } for option in product.get("fulfillmentOptions") or []]
        flags = [{
            "id": flag.get("id"),
            "key": flag.get("key"),
            "text": flag.get("text"),
        } for flag in ((product.get("badges") or {}).get("flags") or [])]
        record.update({
            "observed_location": {
                "store_id": observed_store or None,
                "postal_code": observed_zip or None,
                "city": observed.get("city"),
                "state": observed.get("stateOrProvinceCode"),
                "intent": observed.get("intent"),
                "intent_strength": observed.get("intentStrength"),
            },
            "target_location_verified": location_match,
            "observed_item_id": str(product.get("usItemId") or "") or None,
            "product_identity_verified": (
                str(product.get("usItemId") or "") == record["listing_id"]
            ),
            "product_availability_status": product.get("availabilityStatus"),
            "item_page_availability_status": product.get("itemPageAvailabilityStatus"),
            "show_add_to_cart": product.get("showAtc"),
            "fulfillment": fulfillment,
            "retailer_badges": flags,
        })
        if not location_match:
            record["status"] = "unavailable_location_override_failed"
            record["limitations"].append(
                "Walmart returned a different implicit location; target-store availability is unknown."
            )
            return record
        local_states = {
            str(option.get("availability_status") or "").upper()
            for option in fulfillment
            if option.get("type") in {"PICKUP", "DELIVERY"}
            and option.get("availability_status")
        }
        shipping_states = {
            str(option.get("availability_status") or "").upper()
            for option in fulfillment
            if option.get("type") == "SHIPPING"
            and option.get("availability_status")
        }
        if "IN_STOCK" in local_states:
            record["status"] = "orderable"
        elif local_states and local_states <= {"OUT_OF_STOCK", "NOT_AVAILABLE"}:
            record["status"] = "out_of_stock"
        elif product.get("showAtc") or "IN_STOCK" in shipping_states:
            record["status"] = "shipping_only_orderable"
        else:
            record["status"] = "availability_unknown"
        record["diagnostic_status"] = record["status"]
        record["status"] = "unavailable_direct_diagnostic"
        record["target_location_verified"] = False
        record["limitations"].append(
            "Direct Walmart HTML is not eligible for the position-monitor denominator."
        )
        return record
    except Exception as exc:
        record.update({
            "status": "unavailable_error",
            "error_category": type(exc).__name__,
        })
        return record


def _provider_error_record(
    location: dict, *, observed_at: str, provider: str, route: str,
    status: str, http_status: int | None = None, error_category: str | None = None,
    item_id: str = DEFAULT_WALMART_ITEM_ID,
) -> dict:
    record = _walmart_base_record(
        location, observed_at=observed_at, provider=provider, item_id=item_id
    )
    record.update({"route": route, "status": status})
    if http_status is not None:
        record["http_status"] = http_status
    if error_category:
        record["error_category"] = error_category
    return record


def _walmart_serpapi_record(
    location: dict, *, observed_at: str, api_key: str, fetch=requests.get,
    item_id: str = DEFAULT_WALMART_ITEM_ID,
) -> dict:
    try:
        response = fetch(
            SERPAPI_WALMART_URL,
            params={
                "engine": "walmart_product",
                "product_id": item_id,
                "store_id": str(location["store_id"]),
                "no_cache": "true",
                "api_key": api_key,
            },
            timeout=90,
        )
        body = bytes(response.content)
        if response.status_code != 200:
            return _provider_error_record(
                location,
                observed_at=observed_at,
                provider="serpapi_walmart_product",
                route=SERPAPI_WALMART_URL,
                status="unavailable_provider_http",
                http_status=response.status_code,
                item_id=item_id,
            )
        payload = response.json()
        if payload.get("error"):
            return _provider_error_record(
                location,
                observed_at=observed_at,
                provider="serpapi_walmart_product",
                route=SERPAPI_WALMART_URL,
                status="unavailable_provider_error",
                http_status=response.status_code,
                error_category="provider_error",
                item_id=item_id,
            )
        return _provider_availability_record(
            location,
            observed_at=observed_at,
            provider="serpapi_walmart_product",
            route=SERPAPI_WALMART_URL,
            payload=payload,
            source_body=body,
            item_id=item_id,
        )
    except Exception as exc:
        return _provider_error_record(
            location,
            observed_at=observed_at,
            provider="serpapi_walmart_product",
            route=SERPAPI_WALMART_URL,
            status="unavailable_provider_error",
            error_category=type(exc).__name__,
            item_id=item_id,
        )


def _walmart_scrapingbee_record(
    location: dict, *, observed_at: str, api_key: str, fetch=requests.get,
    item_id: str = DEFAULT_WALMART_ITEM_ID,
) -> dict:
    try:
        response = fetch(
            SCRAPINGBEE_WALMART_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            params={
                "product_id": item_id,
                "store_id": str(location["store_id"]),
                "delivery_zip": str(location["postal_code"]),
            },
            timeout=90,
        )
        body = bytes(response.content)
        if response.status_code != 200:
            return _provider_error_record(
                location,
                observed_at=observed_at,
                provider="scrapingbee_walmart_product",
                route=SCRAPINGBEE_WALMART_URL,
                status="unavailable_provider_http",
                http_status=response.status_code,
                item_id=item_id,
            )
        payload = response.json()
        return _provider_availability_record(
            location,
            observed_at=observed_at,
            provider="scrapingbee_walmart_product",
            route=SCRAPINGBEE_WALMART_URL,
            payload=payload,
            source_body=body,
            item_id=item_id,
        )
    except Exception as exc:
        return _provider_error_record(
            location,
            observed_at=observed_at,
            provider="scrapingbee_walmart_product",
            route=SCRAPINGBEE_WALMART_URL,
            status="unavailable_provider_error",
            error_category=type(exc).__name__,
            item_id=item_id,
        )


def _walmart_record(
    location: dict, *, observed_at: str,
    item_id: str = DEFAULT_WALMART_ITEM_ID,
) -> dict:
    serpapi_key = os.getenv("SERPAPI_API_KEY", "").strip()
    if serpapi_key:
        return _walmart_serpapi_record(
            location, observed_at=observed_at, api_key=serpapi_key,
            item_id=item_id,
        )
    scrapingbee_key = os.getenv("SCRAPINGBEE_API_KEY", "").strip()
    if scrapingbee_key:
        return _walmart_scrapingbee_record(
            location, observed_at=observed_at, api_key=scrapingbee_key,
            item_id=item_id,
        )
    record = _walmart_direct_record(
        location, observed_at=observed_at, item_id=item_id
    )
    record["limitations"].append(
        "No store-scoped provider key is configured. Direct HTML is retained only as a fail-closed fallback."
    )
    return record


def _shopify_product_json_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("shopify_product_url_must_be_public_https")
    path = parsed.path.rstrip("/")
    if not path.endswith(".js"):
        path += ".js"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _shopify_product_record(
    route: dict, *, observed_at: str, fetch=requests.get,
) -> dict:
    retailer = str(route["retailer"])
    url = str(route["url"])
    listing_id = str(route.get("listing_id") or "")
    expected_variants = {
        str(value) for value in route.get("variant_ids") or [] if str(value)
    }
    record = {
        "record_key": f"retailer:{retailer.casefold().replace(' ', '_')}",
        "retailer": retailer,
        "route": url,
        "provider": "self_hosted_shopify_product_json",
        "observed_at": observed_at,
        "location_scoped": False,
        "availability_scope": "online_listing",
        "listing_id": listing_id or None,
        "status": "unavailable",
        "limitations": [
            "This is the retailer's public online-listing state, not local-store shelf inventory.",
            "Orderability does not reveal inventory depth, sell-through or consumer demand.",
        ],
        "provider_attempts": [],
    }
    try:
        json_url = str(route.get("product_json_url") or _shopify_product_json_url(url))
        response = fetch(
            json_url, impersonate="chrome", timeout=45, allow_redirects=True
        )
        body = bytes(response.content)
        record.update({
            "json_route": json_url,
            "http_status": response.status_code,
            "final_url": str(response.url),
            "source_bytes": len(body),
            "source_sha256": _digest(body),
        })
        record["provider_attempts"].append({
            "provider": "self_hosted_shopify_product_json",
            "http_status": response.status_code,
            "blocked": False,
        })
        if response.status_code != 200:
            record["status"] = "unavailable_http"
            return record
        head = body[:4096].decode("utf-8", errors="ignore").casefold()
        if any(marker in head for marker in (
            "robot or human", "px-captcha", "access denied", "verify you are human"
        )):
            record["status"] = "unavailable_blocked"
            record["provider_attempts"][-1]["blocked"] = True
            return record
        payload = response.json()
        observed_listing = str(payload.get("id") or "")
        variants = payload.get("variants") or []
        observed_variant_ids = [
            str(value.get("id"))
            for value in variants
            if isinstance(value, dict) and value.get("id") is not None
        ]
        duplicate_variants = len(observed_variant_ids) != len(set(observed_variant_ids))
        observed_variants = {
            str(value.get("id")): value
            for value in variants
            if isinstance(value, dict) and value.get("id") is not None
        }
        product_match = bool(listing_id) and observed_listing == listing_id
        variants_match = bool(expected_variants) and expected_variants <= set(observed_variants)
        selected = [observed_variants[value] for value in sorted(expected_variants)] if variants_match and not duplicate_variants else []
        record.update({
            "observed_listing_id": observed_listing or None,
            "observed_variant_ids": sorted(observed_variant_ids),
            "product_identity_verified": product_match and variants_match and not duplicate_variants,
            "title": payload.get("title"),
            "selected_variants": [{
                "id": str(value.get("id")),
                "sku": value.get("sku"),
                "available": value.get("available") if isinstance(value.get("available"), bool) else None,
                "price_minor_units": value.get("price"),
            } for value in selected],
        })
        if not product_match:
            record["status"] = "unavailable_product_unverified"
            record["limitations"].append(
                "The public product response did not match the frozen listing ID."
            )
            return record
        if duplicate_variants:
            record["status"] = "unavailable_variant_duplicate"
            record["limitations"].append(
                "The public product response contained duplicate variant IDs."
            )
            return record
        if not variants_match:
            record["status"] = "unavailable_variant_unverified"
            record["limitations"].append(
                "The public product response did not contain every frozen variant ID."
            )
            return record
        availability = [value.get("available") for value in selected]
        if any(value is True for value in availability):
            record["status"] = "orderable"
            fulfillment_status = "IN_STOCK"
        elif availability and all(value is False for value in availability):
            record["status"] = "out_of_stock"
            fulfillment_status = "OUT_OF_STOCK"
        else:
            record["status"] = "availability_unknown"
            fulfillment_status = "UNKNOWN"
        record["fulfillment"] = [{
            "type": "SHIPPING",
            "availability_status": fulfillment_status,
        }]
        return record
    except Exception as exc:
        record.update({
            "status": "unavailable_error",
            "error_category": type(exc).__name__,
        })
        return record


def _retailer_page_fields(body: bytes) -> tuple[str, str, list[str]]:
    soup = BeautifulSoup(body, "html.parser")
    text = " ".join(soup.get_text(" ", strip=True).split())
    title = " ".join(
        (soup.title.get_text(" ", strip=True) if soup.title else "").split()
    )
    markers = []
    patterns = (
        r"\$\s?\d+(?:\.\d{2})?",
        r"(?i)in stock",
        r"(?i)out of stock",
        r"(?i)sold out",
        r"(?i)not available",
        r"(?i)add to cart",
        r"(?i)limited(?:-time| time| edition)",
    )
    for pattern in patterns:
        markers.extend(str(value) for value in re.findall(pattern, text)[:5])
    return title[:500], text, list(dict.fromkeys(markers))[:30]


def _retailer_record(
    route: dict, *, observed_at: str, fetch=requests.get,
) -> dict:
    if route.get("adapter") == "shopify_product_json":
        return _shopify_product_record(
            route, observed_at=observed_at, fetch=fetch
        )
    retailer = str(route["retailer"])
    url = str(route["url"])
    record = {
        "record_key": f"retailer:{retailer.casefold().replace(' ', '_')}",
        "retailer": retailer,
        "route": url,
        "provider": "direct_html",
        "observed_at": observed_at,
        "location_scoped": bool(route.get("location_scoped")),
        "availability_scope": "unscoped_page",
        "product_identity_verified": False,
        "status": "unavailable",
        "limitations": [
            "No ZIP/store context was established for this route.",
            "Catalog presence is not proof of checkout, inventory depth or sell-through.",
        ],
        "provider_attempts": [],
    }
    try:
        response = fetch(
            url, impersonate="chrome", timeout=45, allow_redirects=True
        )
        body = bytes(response.content)
        title, text, markers = _retailer_page_fields(body)
        blocked = (
            response.status_code != 200
            or "access to this page has been denied" in title.casefold()
        )
        record["provider_attempts"].append({
            "provider": "direct_html",
            "http_status": response.status_code,
            "blocked": blocked,
        })
        scrapingbee_key = os.getenv("SCRAPINGBEE_API_KEY", "").strip()
        if blocked and scrapingbee_key:
            managed = fetch(
                SCRAPINGBEE_GENERIC_URL,
                params={
                    "api_key": scrapingbee_key,
                    "url": url,
                    "render_js": "true",
                    "premium_proxy": "true",
                    "country_code": "us",
                },
                timeout=120,
            )
            managed_body = bytes(managed.content)
            managed_title, managed_text, managed_markers = _retailer_page_fields(
                managed_body
            )
            managed_blocked = (
                managed.status_code != 200
                or "access to this page has been denied" in managed_title.casefold()
            )
            record["provider_attempts"].append({
                "provider": "scrapingbee_managed_page",
                "http_status": managed.status_code,
                "blocked": managed_blocked,
            })
            if not managed_blocked:
                response = managed
                body = managed_body
                title = managed_title
                text = managed_text
                markers = managed_markers
                blocked = False
                record["provider"] = "scrapingbee_managed_page"
                record["limitations"].append(
                    "Managed rendering recovered the public page but did not establish store-level location context."
                )
        record.update({
            "http_status": response.status_code,
            "final_url": url if record["provider"] != "direct_html" else str(response.url),
            "source_bytes": len(body),
            "source_sha256": _digest(body),
            "title": title,
            "markers": markers,
        })
        lower = {value.casefold() for value in markers}
        unavailable = any(
            phrase in value
            for value in lower
            for phrase in ("out of stock", "sold out", "not available")
        )
        available = any(
            phrase in value
            for value in lower
            for phrase in ("in stock", "add to cart")
        )
        if blocked:
            record["status"] = "unavailable_blocked"
        elif unavailable and available:
            record["status"] = "unavailable_contradictory"
        elif unavailable:
            record["status"] = "unavailable_unscoped_marker"
        elif available:
            record["status"] = "catalog_available_unscoped"
        else:
            record["status"] = "catalog_reachable_orderability_unknown"
        return record
    except Exception as exc:
        record.update({
            "status": "unavailable_error",
            "error_category": type(exc).__name__,
        })
        return record


def _material_signature(record: dict) -> dict:
    signature = {
        "record_key": record.get("record_key"),
        "provider": record.get("provider"),
        "status": record.get("status"),
        "target_location_verified": record.get("target_location_verified"),
        "product_identity_verified": record.get("product_identity_verified"),
        "listing_id": record.get("listing_id"),
        "observed_item_id": record.get("observed_item_id"),
    }
    if record.get("status") in {
        "unavailable_location_override_failed",
        "unavailable_provider_location_unverified",
    }:
        signature["observed_location"] = record.get("observed_location")
        return signature
    signature.update({
        "product_availability_status": record.get("product_availability_status"),
        "item_page_availability_status": record.get("item_page_availability_status"),
        "show_add_to_cart": record.get("show_add_to_cart"),
        "fulfillment": record.get("fulfillment"),
        "markers": record.get("markers"),
    })
    return signature


def _trusted_walmart_observation(record: dict) -> bool:
    listing_id = str(record.get("listing_id") or "")
    observed_item_id = str(record.get("observed_item_id") or "")
    return bool(
        record.get("retailer") == "Walmart"
        and record.get("target_location_verified") is True
        and record.get("product_identity_verified") is True
        and listing_id
        and observed_item_id == listing_id
    )


def _local_fulfillment_states(record: dict) -> dict[str, bool]:
    output = {}
    for option in record.get("fulfillment") or []:
        name = str(option.get("type") or "").upper()
        if name not in {"PICKUP", "DELIVERY"}:
            continue
        available = option.get("available")
        if isinstance(available, bool):
            output[name] = available
            continue
        status = str(option.get("availability_status") or "").upper()
        if status == "IN_STOCK":
            output[name] = True
        elif status in {"OUT_OF_STOCK", "NOT_AVAILABLE"}:
            output[name] = False
    return output


def _replenished_local_modes(before: dict, after: dict) -> list[str]:
    if not (
        _trusted_walmart_observation(before)
        and _trusted_walmart_observation(after)
        and before.get("record_key") == after.get("record_key")
    ):
        return []
    old_states = _local_fulfillment_states(before)
    new_states = _local_fulfillment_states(after)
    return sorted(
        name for name, old_value in old_states.items()
        if old_value is False and new_states.get(name) is True
    )


def _changes(previous: dict | None, current_records: list[dict]) -> list[dict]:
    if not previous:
        return []
    prior = {
        record["record_key"]: record
        for record in previous.get("records") or []
    }
    output = []
    for record in current_records:
        old = prior.get(record["record_key"])
        if old is None:
            output.append({"record_key": record["record_key"], "change": "new_route"})
            continue
        before = _material_signature(old)
        after = _material_signature(record)
        if before != after:
            replenished_modes = _replenished_local_modes(old, record)
            output.append({
                "record_key": record["record_key"],
                "change": "material_state_changed",
                "before": before,
                "after": after,
                "replenishment_candidate": bool(
                    old.get("status") == "out_of_stock"
                    and record.get("status") == "orderable"
                    and replenished_modes
                ),
                "replenished_local_modes": replenished_modes,
            })
    return output


def _walmart_point(snapshot: dict) -> dict:
    records = [
        record for record in snapshot.get("records") or []
        if _trusted_walmart_observation(record)
    ]
    verified = len(records)
    orderable = sum(record.get("status") == "orderable" for record in records)
    depleted = sum(record.get("status") == "out_of_stock" for record in records)
    return {
        "observed_at": snapshot.get("observed_at"),
        "verified": verified,
        "orderable": orderable,
        "depleted": depleted,
        "orderable_share": orderable / verified if verified else None,
        "depleted_share": depleted / verified if verified else None,
    }


def _observation_utc_day(snapshot: dict) -> str | None:
    observed_at = str(snapshot.get("observed_at") or "").strip()
    if not observed_at:
        return None
    try:
        parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).date().isoformat()


def _daily_walmart_points(snapshots: list[dict]) -> list[tuple[str, dict]]:
    by_day: dict[str, dict] = {}
    for snapshot in snapshots:
        day = _observation_utc_day(snapshot)
        if day is None:
            continue
        point = _walmart_point(snapshot)
        prior = by_day.get(day)
        if prior is None or point["verified"] >= prior["verified"]:
            by_day[day] = point
    return sorted(by_day.items())


def _position_monitor(
    history: list[dict], current: dict, policy: dict,
) -> dict:
    minimum = int(policy.get("minimum_verified_walmart_locations") or 4)
    threshold = float(policy.get("majority_threshold") or (2 / 3))
    required_streak = int(
        policy.get("majority_orderable_streak_observations") or 3
    )
    current_day = _observation_utc_day(current)
    daily_points = _daily_walmart_points([*history, current])
    latest = _walmart_point(current)
    had_majority_depleted = any(
        day != current_day
        and point["verified"] >= minimum
        and point["depleted_share"] is not None
        and point["depleted_share"] >= threshold
        for day, point in daily_points
    )
    orderable_streak = 0
    previous_day = None
    for day, point in reversed(daily_points):
        parsed_day = datetime.fromisoformat(day).date()
        if previous_day is not None and (previous_day - parsed_day).days != 1:
            break
        if (
            point["verified"] >= minimum
            and point["orderable_share"] is not None
            and point["orderable_share"] >= threshold
        ):
            orderable_streak += 1
            previous_day = parsed_day
        else:
            break
    if latest["verified"] < minimum:
        state = "insufficient_coverage"
        reason = (
            f"Only {latest['verified']} Walmart locations were verified; "
            f"at least {minimum} are required."
        )
    elif current_day is None:
        state = "monitor"
        reason = "The current observation timestamp is invalid; no daily streak can be credited."
    elif (
        orderable_streak >= required_streak
        and (
            had_majority_depleted
            or not policy.get("majority_depleted_baseline_required", True)
        )
    ):
        state = "exit_review_availability_normalized"
        reason = (
            f"At least {threshold:.0%} of verified Walmart locations were "
            f"orderable for {orderable_streak} consecutive UTC observation days after "
            "a majority-depleted baseline."
        )
    else:
        state = "monitor"
        reason = "Availability evidence has not met the bounded exit-review trigger."
    return {
        "state": state,
        "action": (
            "human_exit_review"
            if state == "exit_review_availability_normalized"
            else "no_automatic_trade_action"
        ),
        "reason": reason,
        "latest": latest,
        "minimum_verified_locations": minimum,
        "majority_threshold": threshold,
        "required_orderable_streak": required_streak,
        "current_orderable_streak": orderable_streak,
        "streak_unit": "distinct_consecutive_utc_days",
        "distinct_observation_days": len(daily_points),
        "majority_depleted_baseline_seen": had_majority_depleted,
        "automatic_trade_action": False,
        "interpretation_warning": (
            "Availability normalization can reflect improved supply rather than weaker demand. "
            "This trigger requests a human review; it never executes or mandates an exit."
        ),
    }


def _panel_id(config: dict) -> str:
    return _canonical_hash({
        "candidate": config["candidate"],
        "product_identifiers": config.get("product_identifiers") or {},
        "locations": config["walmart_locations"],
        "routes": config["retailer_routes"],
    })


def _previous_snapshot_relation(
    previous: dict, *, config_hash: str, panel_id: str,
    supersedes: list[str],
) -> str:
    if (
        previous.get("config_hash") == config_hash
        or previous.get("panel_id") == panel_id
    ):
        return "same"
    if previous.get("panel_id") in {str(value) for value in supersedes}:
        return "superseded"
    return "rejected"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--latest", type=Path, default=DEFAULT_LATEST)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--report-all", action="store_true")
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    config_hash = _canonical_hash(config)
    panel_id = _panel_id(config)
    walmart_item_id = str(
        (config.get("product_identifiers") or {}).get("walmart_us_item_id") or ""
    )
    if not re.fullmatch(r"\d+", walmart_item_id):
        raise RuntimeError("walmart_item_id_missing_or_invalid")
    observed_at = _utc_now()
    previous = None
    superseded_panel_id = None
    if args.latest.exists():
        previous = json.loads(args.latest.read_text(encoding="utf-8"))
        relation = _previous_snapshot_relation(
            previous,
            config_hash=config_hash,
            panel_id=panel_id,
            supersedes=config.get("supersedes_panel_ids") or [],
        )
        if relation == "rejected":
            raise RuntimeError("store_panel_definition_changed_after_freeze")
        if relation == "superseded":
            superseded_panel_id = previous.get("panel_id")
            previous = None
    history_snapshots = []
    if args.history.exists():
        for line in args.history.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if value.get("panel_id") == panel_id:
                history_snapshots.append(value)

    records = [
        _walmart_record(
            location, observed_at=observed_at, item_id=walmart_item_id
        )
        for location in config["walmart_locations"]
    ]
    records.extend(
        _retailer_record(route, observed_at=observed_at)
        for route in config["retailer_routes"]
    )
    changes = _changes(previous, records)
    status_counts = {}
    for record in records:
        status = record["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    verified_walmart = [
        record for record in records
        if _trusted_walmart_observation(record)
    ]
    walmart_provider_counts = {}
    for record in records:
        if record.get("retailer") != "Walmart":
            continue
        provider = str(record.get("provider") or "unknown")
        walmart_provider_counts[provider] = walmart_provider_counts.get(provider, 0) + 1
    snapshot = {
        "schema_version": "ghost-store-panel-snapshot/1",
        "panel_id": panel_id,
        "superseded_panel_id": superseded_panel_id,
        "config_hash": config_hash,
        "observed_at": observed_at,
        "records": records,
        "summary": {
            "records": len(records),
            "status_counts": status_counts,
            "walmart_locations_attempted": len(config["walmart_locations"]),
            "walmart_provider_counts": walmart_provider_counts,
            "walmart_store_scoped_provider_ready": bool(
                os.getenv("SERPAPI_API_KEY", "").strip()
                or os.getenv("SCRAPINGBEE_API_KEY", "").strip()
            ),
            "walmart_locations_verified": len(verified_walmart),
            "walmart_orderable": sum(
                record["status"] == "orderable" for record in verified_walmart
            ),
            "walmart_out_of_stock": sum(
                record["status"] == "out_of_stock" for record in verified_walmart
            ),
            "changes_from_prior": len(changes),
            "replenishment_candidates": sum(
                bool(change.get("replenishment_candidate")) for change in changes
            ),
        },
        "changes": changes,
        "limitations": [
            "Walmart target rows count only when the returned store ID and ZIP match the frozen request.",
            "Location override failures remain unavailable, not stockouts.",
            "Unscoped retailer pages do not enter the Walmart store-level denominator.",
            "A first snapshot establishes a baseline; replenishment requires a later state change.",
        ],
    }
    snapshot["position_monitor"] = _position_monitor(
        history_snapshots,
        snapshot,
        config.get("rules", {}).get("position_monitor", {}),
    )
    args.latest.parent.mkdir(parents=True, exist_ok=True)
    args.latest.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    args.history.parent.mkdir(parents=True, exist_ok=True)
    with args.history.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")

    if args.report_all:
        print(json.dumps({
            **snapshot["summary"],
            "position_monitor": snapshot["position_monitor"],
        }, indent=2))
    else:
        prior_monitor_state = (
            (previous or {}).get("position_monitor", {}).get("state")
        )
        monitor_changed = (
            previous is not None
            and prior_monitor_state != snapshot["position_monitor"]["state"]
        )
        if previous and (changes or monitor_changed):
            print(
                "GHOST store panel changed: "
                + json.dumps({
                    "observed_at": observed_at,
                    "changes": len(changes),
                    "replenishment_candidates": snapshot["summary"]["replenishment_candidates"],
                    "status_counts": status_counts,
                    "position_monitor": snapshot["position_monitor"],
                }, separators=(",", ":"))
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
