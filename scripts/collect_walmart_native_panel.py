from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
import requests
import websocket

try:
    from scripts.collect_ghost_store_panel import _position_monitor
except ModuleNotFoundError:  # Direct `python scripts/...py` execution.
    from collect_ghost_store_panel import _position_monitor

ROOT = Path(__file__).resolve().parents[1]
for env_path in (ROOT / ".env", ROOT.parent / "bounty-api-fresh" / ".env"):
    if env_path.exists():
        load_dotenv(env_path, override=False)
DEFAULT_CONFIG = ROOT / "references" / "ghost-store-panel-config-2026-09.json"
DEFAULT_LATEST = ROOT / "artifacts" / "dd" / "ghost-kdp" / "walmart_native_latest.json"
DEFAULT_HISTORY = ROOT / "artifacts" / "dd" / "ghost-kdp" / "walmart_native_history.jsonl"
DEFAULT_EVIDENCE_DIR = ROOT / "artifacts" / "dd" / "ghost-kdp" / "retailer-evidence"
DEFAULT_BRAVE = Path(r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe")
SEARCH_URL = "https://www.walmart.com/search?q=GHOST%20Energy%20A%26W%20Root%20Beer"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError("walmart_native_panel_already_running") from exc
    try:
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def _atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _profile_process_pids(profile: Path) -> list[int]:
    if os.name != "nt":
        return []
    lookup = subprocess.run(
        [
            "wmic.exe", "process", "where", "name='brave.exe'",
            "get", "CommandLine,ProcessId", "/format:csv",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if lookup.returncode != 0:
        raise RuntimeError("brave_process_inventory_failed")
    profile_text = str(profile).casefold()
    output = []
    for row in csv.DictReader(
        line for line in lookup.stdout.splitlines() if line.strip()
    ):
        command_line = str(row.get("CommandLine") or "").casefold()
        process_id = str(row.get("ProcessId") or "").strip()
        if profile_text in command_line and process_id.isdigit():
            output.append(int(process_id))
    return sorted(set(output))


def _kill_profile_processes(profile: Path):
    if os.name != "nt":
        return
    for pid in _profile_process_pids(profile):
        subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            timeout=15,
        )
    remaining = _profile_process_pids(profile)
    if remaining:
        raise RuntimeError("owned_brave_process_cleanup_failed")


def _kill_process_tree(process: subprocess.Popen | None, profile: Path):
    if process is not None and process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                timeout=15,
            )
        else:
            process.kill()
    _kill_profile_processes(profile)
    if process is not None and process.poll() is None:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _cleanup_stale_profile_process(profile: Path) -> Path:
    pid_path = profile / ".bounty_native_browser.pid"
    if not pid_path.exists():
        if _profile_process_pids(profile):
            raise RuntimeError("untracked_owned_brave_process_present")
        return pid_path
    try:
        int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        raise RuntimeError("owned_brave_pid_marker_invalid") from exc
    _kill_profile_processes(profile)
    pid_path.unlink(missing_ok=False)
    return pid_path


def _proxy_city(location: dict) -> str:
    configured = str(location.get("proxy_city") or "").strip().lower()
    if configured:
        return configured
    return re.sub(r"[^a-z0-9]+", "", str(location["metro"]).split("/")[-1].lower())


def _proxy_username(base: str, location: dict, attempt: int = 1) -> str:
    city = _proxy_city(location)
    return (
        f"{base}-country-US-city-{city}"
        f"-lifetime-10-session-walmart{city}v{attempt}"
    )


def _classify_product(product: dict, location: dict, item_id: str) -> str:
    observed_item = str(product.get("item_id") or "")
    observed_location = product.get("location") or {}
    requested_store = str(location["store_id"])
    observed_store = str(observed_location.get("store_id") or "")
    observed_pickup_store = str(observed_location.get("pickup_store") or "")
    observed_delivery_store = str(observed_location.get("delivery_store") or "")
    if observed_item != item_id:
        return "unavailable_product_unverified"
    # The configured postal code identifies the selected store address. Walmart's
    # product payload separately reports a delivery destination, which can be a
    # generic city ZIP. Store identity comes from the verified store page/cookie
    # plus the product payload's store IDs, not that delivery-destination ZIP.
    if observed_store != requested_store:
        return "unavailable_location_unverified"
    if observed_pickup_store and observed_pickup_store != requested_store:
        return "unavailable_location_unverified"
    if observed_delivery_store and observed_delivery_store != requested_store:
        return "unavailable_location_unverified"
    states = {}
    for value in product.get("fulfillment") or []:
        mode = str(value.get("type") or "").upper()
        state = str(value.get("availability_status") or "").upper()
        if mode in states:
            return "unavailable_contradictory"
        states[mode] = state
    local = [states.get("PICKUP"), states.get("DELIVERY")]
    if any(value == "IN_STOCK" for value in local):
        return "orderable"
    if all(value in {"OUT_OF_STOCK", "NOT_AVAILABLE"} for value in local):
        return "out_of_stock"
    if states.get("SHIPPING") == "IN_STOCK":
        return "shipping_only_orderable"
    return "availability_unknown"


def _classify_missing(product_page_missing: bool, search: dict) -> str:
    if search.get("challenge"):
        return "unavailable_challenge"
    if search.get("location_verified") is not True:
        return "unavailable_location_unverified"
    if search.get("exact_item_links"):
        return "availability_unknown"
    if product_page_missing and (
        search.get("zero_exact_results")
        or search.get("no_local_results")
    ):
        return "not_listed_at_store"
    return "availability_unknown"


def _record_local_states(record: dict) -> dict[str, str]:
    return {
        str(value.get("type") or "").upper(): str(
            value.get("availability_status") or ""
        ).upper()
        for value in (record.get("product") or {}).get("fulfillment") or []
        if str(value.get("type") or "").upper() in {"PICKUP", "DELIVERY"}
    }


def _native_replenished_modes(before: dict, after: dict) -> list[str]:
    if not (
        before.get("record_key") == after.get("record_key")
        and before.get("status") == "out_of_stock"
        and after.get("status") == "orderable"
        and before.get("target_location_verified") is True
        and after.get("target_location_verified") is True
        and before.get("product_identity_verified") is True
        and after.get("product_identity_verified") is True
    ):
        return []
    old_states = _record_local_states(before)
    new_states = _record_local_states(after)
    return sorted(
        name for name, old_value in old_states.items()
        if old_value in {"OUT_OF_STOCK", "NOT_AVAILABLE"}
        and new_states.get(name) == "IN_STOCK"
    )


class _CDP:
    def __init__(self, websocket_url: str):
        self.ws = websocket.create_connection(
            websocket_url, timeout=25, suppress_origin=True
        )
        self.request_id = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.request_id += 1
        request_id = self.request_id
        self.ws.send(json.dumps({
            "id": request_id,
            "method": method,
            "params": params or {},
        }))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") == request_id:
                if message.get("error"):
                    raise RuntimeError(
                        f"cdp_{method}_failed:{message['error'].get('message')}"
                    )
                return message.get("result") or {}

    def evaluate(self, expression: str):
        result = self.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        return result.get("result", {}).get("value")

    def navigate(self, url: str, wait_seconds: float = 12.0):
        self.call("Page.navigate", {"url": url})
        time.sleep(wait_seconds)

    def close(self):
        self.ws.close()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _browser_target(port: int, deadline_seconds: int = 30) -> dict:
    deadline = time.time() + deadline_seconds
    while time.time() < deadline:
        try:
            targets = requests.get(
                f"http://127.0.0.1:{port}/json/list", timeout=1
            ).json()
            pages = [value for value in targets if value.get("type") == "page"]
            if pages:
                return pages[0]
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("native_brave_cdp_unavailable")


def _write_proxy_extension(server: str, username: str, password: str) -> Path:
    parsed = urlsplit(server)
    if not parsed.hostname or not parsed.port:
        raise RuntimeError("geonode_proxy_server_invalid")
    path = Path(tempfile.mkdtemp(prefix="bounty-walmart-proxy-"))
    manifest = {
        "manifest_version": 3,
        "name": "Bounty GeoNode Session",
        "version": "1.0.0",
        "permissions": ["proxy", "storage", "webRequest", "webRequestAuthProvider"],
        "host_permissions": ["<all_urls>"],
        "background": {"service_worker": "background.js"},
    }
    background = f"""
const config = {{mode: 'fixed_servers', rules: {{
  singleProxy: {{scheme: {json.dumps(parsed.scheme or 'http')}, host: {json.dumps(parsed.hostname)}, port: {parsed.port}}},
  bypassList: ['127.0.0.1', 'localhost']
}}}};
chrome.proxy.settings.set({{value: config, scope: 'regular'}});
chrome.webRequest.onAuthRequired.addListener(
  (_details, callback) => callback({{authCredentials: {{username: {json.dumps(username)}, password: {json.dumps(password)}}}}}),
  {{urls: ['<all_urls>']}}, ['asyncBlocking']
);
""".strip()
    try:
        (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (path / "background.js").write_text(background, encoding="utf-8")
        return path
    except Exception:
        shutil.rmtree(path, ignore_errors=False)
        raise


def _cookie_store_expression() -> str:
    return """Object.fromEntries(document.cookie.split('; ').filter(Boolean).map(value => {const i=value.indexOf('=');return [value.slice(0,i),value.slice(i+1)]})).assortmentStoreId || null"""


def _page_state_expression() -> str:
    return r'''(() => {
      const text = document.body?.innerText || '';
      return {
        title: document.title,
        url: location.href,
        body_start: text.slice(0, 1200),
        webdriver: navigator.webdriver,
        challenge: /robot or human|press & hold|verify you are human/i.test(text) || location.pathname === '/blocked',
        technical_error: /We're having technical issues/i.test(text),
        missing_page: /We couldn[’']t find this page/i.test(text)
      };
    })()'''


def _product_expression() -> str:
    return r'''(() => {
      const node = document.querySelector('#__NEXT_DATA__');
      if (!node) return null;
      const json = JSON.parse(node.textContent);
      const data = json?.props?.pageProps?.initialData?.data || {};
      const product = data.product || {};
      const location = data?.contentLayout?.pageMetadata?.location || {};
      return {
        item_id: String(product.usItemId || ''),
        title: product.name || null,
        availability_status: product.availabilityStatus || null,
        item_page_availability_status: product.itemPageAvailabilityStatus || null,
        show_add_to_cart: product.showAtc,
        price: product.priceInfo?.currentPrice || null,
        location: {
          store_id: String(location.storeId || ''),
          pickup_store: String(location.pickupStore || ''),
          delivery_store: String(location.deliveryStore || ''),
          postal_code: String(location.postalCode || ''),
          city: location.city || null,
          state: location.stateOrProvinceCode || null,
          intent: location.intent || null,
          intent_strength: location.intentStrength || null
        },
        fulfillment: (product.fulfillmentOptions || []).map(value => ({
          type: value.type,
          availability_status: value.availabilityStatus,
          location_text: value.locationText,
          available_quantity: value.availableQuantity ?? null,
          inventory_status: value.inventoryStatus ?? null
        })),
        badges: (product.badges?.flags || []).map(value => ({key: value.key, text: value.text}))
      };
    })()'''


def _search_expression(item_id: str, location: dict) -> str:
    return r'''(() => {
      const text = document.body?.innerText || '';
      const cookies = Object.fromEntries(document.cookie.split('; ').filter(Boolean).map(value => {
        const index = value.indexOf('=');
        return [value.slice(0, index), value.slice(index + 1)];
      }));
      return {
        title: document.title,
        url: location.href,
        challenge: /robot or human|press & hold|verify you are human/i.test(text) || location.pathname === '/blocked',
        location_verified: cookies.assortmentStoreId === STORE,
        exact_item_links: [...document.querySelectorAll('a[href]')]
          .filter(value => value.href.includes(ITEM))
          .map(value => value.href).slice(0, 5),
        zero_exact_results: /0 results for ["“]GHOST Energy A&W Root Beer["”]/i.test(text),
        no_local_results: /No (pickup|delivery) results found for ["“]GHOST Energy A&W Root Beer["”]/i.test(text),
        evidence_text: text.slice(0, 2200)
      };
    })()'''.replace("STORE", json.dumps(str(location["store_id"]))).replace(
        "ITEM", json.dumps(item_id)
    )


def _select_store(client: _CDP, location: dict) -> dict:
    client.navigate(str(location["store_source_url"]), wait_seconds=12)
    page = client.evaluate(_page_state_expression()) or {}
    if page.get("challenge"):
        raise RuntimeError("walmart_store_page_challenge")
    body = str(page.get("body_start") or "")
    store_id = str(location["store_id"])
    if f"#{store_id}" not in body:
        raise RuntimeError("walmart_store_page_identity_unverified")
    current = str(client.evaluate(_cookie_store_expression()) or "")
    if current != store_id:
        point = client.evaluate(r'''(() => {
          const button = [...document.querySelectorAll('button')]
            .find(value => (value.innerText || '').trim() === 'Make this my store');
          if (!button) return null;
          button.scrollIntoView({block: 'center'});
          const rect = button.getBoundingClientRect();
          return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
        })()''')
        if not point:
            raise RuntimeError("make_this_my_store_button_missing")
        for event_type, button in (
            ("mouseMoved", "none"),
            ("mousePressed", "left"),
            ("mouseReleased", "left"),
        ):
            params = {
                "type": event_type,
                "x": point["x"],
                "y": point["y"],
                "button": button,
            }
            if event_type != "mouseMoved":
                params["clickCount"] = 1
            client.call("Input.dispatchMouseEvent", params)
        deadline = time.time() + 15
        while time.time() < deadline:
            current = str(client.evaluate(_cookie_store_expression()) or "")
            if current == store_id:
                break
            time.sleep(1)
        if current != store_id:
            clicked = client.evaluate(r'''(() => {
              const button = [...document.querySelectorAll('button')]
                .find(value => (value.innerText || '').trim() === 'Make this my store');
              if (!button) return false;
              button.scrollIntoView({block: 'center'});
              button.click();
              return true;
            })()''')
            if clicked:
                deadline = time.time() + 15
                while time.time() < deadline:
                    current = str(client.evaluate(_cookie_store_expression()) or "")
                    if current == store_id:
                        break
                    time.sleep(1)
    if current != store_id:
        raise RuntimeError("target_store_cookie_not_applied")
    return {
        "store_id": current,
        "postal_code": str(location["postal_code"]),
        "store_page_title": page.get("title"),
        "store_page_url": page.get("url"),
    }


def _observe_store(
    client: _CDP, location: dict, item_id: str, evidence_dir: Path,
) -> dict:
    observed_at = _utc_now()
    item_url = f"https://www.walmart.com/ip/{item_id}"
    client.navigate(item_url, wait_seconds=15)
    page = client.evaluate(_page_state_expression()) or {}
    if page.get("technical_error"):
        client.call("Page.reload", {"ignoreCache": True})
        time.sleep(12)
        page = client.evaluate(_page_state_expression()) or {}
    product = client.evaluate(_product_expression()) or {}
    if page.get("challenge"):
        status = "unavailable_challenge"
    elif product.get("item_id"):
        status = _classify_product(product, location, item_id)
    else:
        client.navigate(SEARCH_URL, wait_seconds=15)
        search = client.evaluate(_search_expression(item_id, location)) or {}
        status = _classify_missing(bool(page.get("missing_page")), search)
        page = client.evaluate(_page_state_expression()) or page
        product = {"search": search}
    screenshot = client.call("Page.captureScreenshot", {
        "format": "png",
        "captureBeyondViewport": False,
    })
    html = str(client.evaluate("document.documentElement?.outerHTML || ''") or "")
    stem = f"{_proxy_city(location)}-{location['store_id']}-walmart-native"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = evidence_dir / f"{stem}.png"
    screenshot_path.write_bytes(base64.b64decode(screenshot["data"]))
    return {
        "record_key": f"walmart:{location['store_id']}:{location['postal_code']}:{item_id}",
        "retailer": "Walmart",
        "provider": "self_hosted_native_brave_geonode",
        "observed_at": observed_at,
        "requested_location": {
            "metro": location["metro"],
            "store_id": str(location["store_id"]),
            "postal_code": str(location["postal_code"]),
            "source_url": location["store_source_url"],
        },
        "listing_id": item_id,
        "status": status,
        "target_location_verified": (
            str(client.evaluate(_cookie_store_expression()) or "")
            == str(location["store_id"])
        ),
        "product_identity_verified": (
            str(product.get("item_id") or "") == item_id
        ),
        "requested_product_identity_bound": bool(
            status == "not_listed_at_store"
            or str(product.get("item_id") or "") == item_id
        ),
        "observed_item_id": str(product.get("item_id") or "") or None,
        "observed_location": product.get("location"),
        "product": product,
        "page": page,
        "source_html_bytes": len(html.encode("utf-8")),
        "source_html_sha256": sha256(html.encode("utf-8")).hexdigest(),
        "screenshot_path": str(screenshot_path),
        "limitations": [
            "Digital orderability is not physical shelf inventory.",
            "Stockouts can reflect demand, allocation, distribution, or stale retailer data.",
            "A missing listing can reflect assortment or distribution rather than demand.",
            "One observation cannot establish sell-through or replenishment.",
        ],
    }


def _unavailable_record(location: dict, item_id: str, exc: Exception) -> dict:
    return {
        "record_key": f"walmart:{location['store_id']}:{location['postal_code']}:{item_id}",
        "retailer": "Walmart",
        "provider": "self_hosted_native_brave_geonode",
        "observed_at": _utc_now(),
        "requested_location": {
            "metro": location["metro"],
            "store_id": str(location["store_id"]),
            "postal_code": str(location["postal_code"]),
            "source_url": location["store_source_url"],
        },
        "listing_id": item_id,
        "status": "unavailable_error",
        "target_location_verified": False,
        "product_identity_verified": False,
        "error_category": type(exc).__name__,
        "error_code": str(exc).split(":", 1)[0][:120],
    }


def _collect_location(
    location: dict, item_id: str, brave: Path, evidence_dir: Path,
    proxy_attempt: int = 1,
) -> dict:
    proxy_server = os.getenv("BOUNTY_PROXY_SERVER", "").strip()
    proxy_base_user = os.getenv("BOUNTY_PROXY_USERNAME", "").strip()
    proxy_password = os.getenv("BOUNTY_PROXY_PASSWORD", "").strip()
    if not all((proxy_server, proxy_base_user, proxy_password)):
        raise RuntimeError("geonode_proxy_credentials_missing")
    port = _free_port()
    city = _proxy_city(location)
    profile = ROOT / ".browser_profiles" / f"walmart_native_{city}"
    profile.mkdir(parents=True, exist_ok=True)
    pid_path = _cleanup_stale_profile_process(profile)
    extension = None
    process = None
    client = None
    try:
        extension = _write_proxy_extension(
            proxy_server,
            _proxy_username(proxy_base_user, location, proxy_attempt),
            proxy_password,
        )
        process = subprocess.Popen([
            str(brave),
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={port}",
            f"--disable-extensions-except={extension}",
            f"--load-extension={extension}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            "about:blank",
        ])
        _atomic_write(pid_path, str(process.pid))
        target = _browser_target(port)
        client = _CDP(target["webSocketDebuggerUrl"])
        if client.evaluate("navigator.webdriver") is not False:
            raise RuntimeError("native_browser_automation_flag_present")
        store = _select_store(client, location)
        record = _observe_store(client, location, item_id, evidence_dir)
        record["store_selection"] = store
        return record
    finally:
        if process is not None:
            try:
                version = requests.get(
                    f"http://127.0.0.1:{port}/json/version", timeout=1
                ).json()
                browser = _CDP(version["webSocketDebuggerUrl"])
                try:
                    browser.call("Browser.close")
                finally:
                    browser.close()
            except Exception:
                _kill_process_tree(process, profile)
            else:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    _kill_process_tree(process, profile)
            if _profile_process_pids(profile):
                _kill_profile_processes(profile)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        pid_path.unlink(missing_ok=True)
        if extension is not None:
            shutil.rmtree(extension, ignore_errors=False)
            if extension.exists():
                raise RuntimeError("proxy_extension_cleanup_failed")


def _collect_location_with_retry(
    location: dict, item_id: str, brave: Path, evidence_dir: Path,
) -> dict:
    try:
        record = _collect_location(
            location, item_id, brave, evidence_dir,
            proxy_attempt=1,
        )
        retry_kind = None
        if record.get("status") == "unavailable_challenge":
            retry_kind = "challenge_retry"
        elif (
            record.get("status") == "unavailable_error"
            and record.get("error_code") == "target_store_cookie_not_applied"
        ):
            retry_kind = "transient_retry"
        if retry_kind is None:
            return record
        first_attempt = {
            "status": record.get("status"),
            "observed_at": record.get("observed_at"),
        }
        if record.get("error_code"):
            first_attempt["error_code"] = record.get("error_code")
        try:
            retry_record = _collect_location(
                location, item_id, brave, evidence_dir,
                proxy_attempt=2,
            )
        except Exception as retry_exc:
            record[retry_kind] = {
                "attempted": True,
                "first_attempt": first_attempt,
                "final_attempt": 2,
                "retry_error_category": type(retry_exc).__name__,
                "retry_error_code": str(retry_exc).split(":", 1)[0][:120],
            }
            return record
        retry_record[retry_kind] = {
            "attempted": True,
            "first_attempt": first_attempt,
            "final_attempt": 2,
        }
        return retry_record
    except Exception as exc:
        return _unavailable_record(location, item_id, exc)


def _snapshot_utc_day(snapshot: dict) -> str | None:
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


def _classifiable_local_count(snapshot: dict) -> int:
    return sum(
        record.get("target_location_verified") is True
        and record.get("product_identity_verified") is True
        and record.get("status") in {"orderable", "out_of_stock"}
        for record in snapshot.get("records") or []
    )


def _previous_distinct_day_snapshot(
    history: list[dict], current_observed_at: str,
) -> dict | None:
    current_day = _snapshot_utc_day({"observed_at": current_observed_at})
    if current_day is None:
        return None
    by_day: dict[str, tuple[tuple[int, str], dict]] = {}
    for snapshot in history:
        day = _snapshot_utc_day(snapshot)
        if day is None or day >= current_day:
            continue
        score = (
            _classifiable_local_count(snapshot),
            str(snapshot.get("observed_at") or ""),
        )
        if day not in by_day or score >= by_day[day][0]:
            by_day[day] = (score, snapshot)
    if not by_day:
        return None
    return by_day[sorted(by_day)[-1]][1]


def _day_over_day_changes(previous: dict | None, records: list[dict]) -> list[dict]:
    if previous is None:
        return []
    prior = {
        value["record_key"]: value
        for value in previous.get("records") or []
    }
    changes = []
    for record in records:
        old = prior.get(record["record_key"])
        if old is None or old.get("status") == record.get("status"):
            continue
        replenished_modes = _native_replenished_modes(old, record)
        changes.append({
            "record_key": record["record_key"],
            "before": old.get("status"),
            "after": record.get("status"),
            "replenishment_candidate": bool(replenished_modes),
            "replenished_local_modes": replenished_modes,
        })
    return changes


def _restock_monitor(
    records: list[dict], day_changes: list[dict], target_count: int,
    policy: dict, previous: dict | None,
) -> dict:
    minimum = int(policy.get("minimum_verified_walmart_locations") or 4)
    broad_threshold = float(policy.get("broad_restock_orderable_share") or (2 / 3))
    trusted = [
        record for record in records
        if record.get("target_location_verified") is True
        and record.get("product_identity_verified") is True
        and record.get("status") in {"orderable", "out_of_stock"}
    ]
    verified = len(trusted)
    orderable = sum(record.get("status") == "orderable" for record in trusted)
    depleted = sum(record.get("status") == "out_of_stock" for record in trusted)
    unresolved_statuses = [
        str(record.get("status") or "unknown")
        for record in records
        if record.get("status") not in {
            "orderable", "out_of_stock", "not_listed_at_store",
        }
    ]
    unavailable = len(unresolved_statuses)
    orderable_share = orderable / verified if verified else None
    newly_orderable = [
        value for value in day_changes
        if value.get("before") in {"out_of_stock", "not_listed_at_store"}
        and value.get("after") == "orderable"
    ]
    replenished = [
        value for value in newly_orderable
        if value.get("replenishment_candidate") is True
    ]
    if unavailable:
        status_text = ", ".join(sorted(set(unresolved_statuses)))
        state = "source_failure"
        reason = (
            f"{unavailable} of {target_count} frozen store rows lacked a terminal "
            f"local-availability state ({status_text})."
        )
    elif verified < minimum:
        state = "insufficient_coverage"
        reason = f"Only {verified} classifiable store rows were verified; {minimum} are required."
    elif verified == target_count and orderable == target_count:
        state = "fully_restocked"
        reason = f"All {target_count} frozen stores are locally orderable for the exact SKU."
    elif orderable_share is not None and orderable_share >= broad_threshold:
        state = "broadly_restocked"
        reason = (
            f"{orderable} of {verified} verified stores are locally orderable, "
            f"meeting the {broad_threshold:.0%} broad-restock threshold."
        )
    elif newly_orderable:
        state = "replenishment_started"
        reason = f"{len(newly_orderable)} frozen store rows became locally orderable."
    elif verified == target_count and depleted == target_count:
        state = "fully_depleted"
        reason = f"All {target_count} frozen stores are locally out of stock for the exact SKU."
    else:
        state = "mixed_availability"
        reason = f"{orderable} of {verified} verified stores are locally orderable."
    operational_state = "partial" if unavailable else "healthy"
    if unavailable:
        availability_state = (
            "replenishment_started" if newly_orderable else "incomplete"
        )
    else:
        availability_state = state
    fingerprint_payload = {
        "state": state,
        "availability_state": availability_state,
        "operational_state": operational_state,
        "verified": verified,
        "orderable": orderable,
        "depleted": depleted,
        "unavailable": unavailable,
        "newly_orderable": sorted(value["record_key"] for value in newly_orderable),
    }
    fingerprint = sha256(json.dumps(
        fingerprint_payload, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    prior_fingerprint = (
        (previous or {}).get("restock_monitor", {}).get("fingerprint")
    )
    return {
        "state": state,
        "availability_state": availability_state,
        "operational_state": operational_state,
        "reason": reason,
        "verified": verified,
        "target_count": target_count,
        "orderable": orderable,
        "depleted": depleted,
        "unavailable": unavailable,
        "unresolved_statuses": sorted(unresolved_statuses),
        "orderable_share": orderable_share,
        "newly_orderable_store_count": len(newly_orderable),
        "same_mode_replenishment_store_count": len(replenished),
        "newly_orderable_changes": newly_orderable,
        "broad_restock_orderable_share": broad_threshold,
        "full_restock_requires_orderable_stores": target_count,
        "fingerprint": fingerprint,
        "alert_required": previous is not None and fingerprint != prior_fingerprint,
        "action": (
            "human_thesis_and_exit_review"
            if availability_state in {
                "replenishment_started", "broadly_restocked", "fully_restocked",
            }
            else "continue_monitoring"
        ),
        "automatic_trade_action": False,
        "interpretation_warning": (
            "Digital orderability is not physical shelf inventory or verified sales. "
            "Restocking can reflect supply normalization rather than weakening demand."
        ),
    }


def _format_observed_at_sgt(observed_at: str) -> str:
    try:
        parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        local = parsed.astimezone(ZoneInfo("Asia/Singapore"))
    except (TypeError, ValueError):
        return "time unavailable"
    clock = local.strftime("%I:%M%p").lstrip("0").lower()
    return f"{local.day} {local.strftime('%b %Y')}, {clock} SGT"


def _unverified_store_reason(record: dict) -> str:
    metro = str((record.get("requested_location") or {}).get("metro") or "Store")
    status = str(record.get("status") or "")
    if status == "unavailable_location_unverified":
        reason = "wrong store returned"
    elif status == "unavailable_challenge":
        reason = "Walmart verification challenge"
    elif status == "availability_unknown":
        reason = "availability unclear"
    elif status == "unavailable_error":
        reason = "collection failed"
    else:
        reason = "not verified"
    return f"{metro}: {reason}"


def _format_restock_alert(snapshot: dict) -> str:
    monitor = snapshot["restock_monitor"]
    records = snapshot.get("records") or []
    by_key = {record.get("record_key"): record for record in records}
    newly_orderable = monitor.get("newly_orderable_changes") or []
    availability_state = monitor.get("availability_state")
    if not availability_state and newly_orderable:
        availability_state = "replenishment_started"
    availability_state = str(availability_state or monitor.get("state") or "unknown")

    if availability_state == "fully_restocked":
        change_text = (
            f"All {monitor['target_count']} monitored stores are now locally available."
        )
        meaning = "Full restock signal."
    elif availability_state == "broadly_restocked":
        change_text = (
            f"Broad restock: {monitor['orderable']} of {monitor['target_count']} "
            "monitored stores are locally available."
        )
        meaning = "Broad restock signal, but not full restock yet."
    elif availability_state == "replenishment_started":
        labels = []
        for change in newly_orderable:
            record = by_key.get(change.get("record_key")) or {}
            metro = str(
                (record.get("requested_location") or {}).get("metro") or "One store"
            )
            modes = [
                str(value).lower()
                for value in change.get("replenished_local_modes") or []
            ]
            if modes == ["delivery", "pickup"] or modes == ["pickup", "delivery"]:
                mode_text = " for pickup and delivery"
            elif modes:
                mode_text = " for " + " and ".join(modes)
            else:
                mode_text = ""
            labels.append(f"{metro} became locally available{mode_text}")
        change_text = "; ".join(labels) + " after being out of stock."
        meaning = "First replenishment signal. Not broad or full restock yet."
    elif availability_state == "fully_depleted":
        change_text = (
            f"All {monitor['target_count']} monitored stores are locally out of stock."
        )
        meaning = "Full local depletion, but this does not prove whether demand or supply caused it."
    else:
        change_text = "No verified broad or full restock signal."
        meaning = "The panel is incomplete, so no panel-wide availability conclusion is valid."

    unresolved = [
        record for record in records
        if record.get("status") not in {
            "orderable", "out_of_stock", "not_listed_at_store",
        }
    ]
    lines = [
        "GHOST x A&W Walmart update | "
        + _format_observed_at_sgt(str(snapshot.get("observed_at") or "")),
        "",
        "What changed: " + change_text,
        (
            f"Current check: {monitor['orderable']} available, "
            f"{monitor['depleted']} out of stock, {len(unresolved)} unverified."
        ),
    ]
    if unresolved:
        lines.append(
            "Data gap: "
            + "; ".join(_unverified_store_reason(record) for record in unresolved)
            + ". These rows are unknown, not out of stock."
        )
    needs_human_review = availability_state in {
        "replenishment_started", "broadly_restocked", "fully_restocked",
    }
    lines.extend([
        "Meaning: " + meaning,
        (
            "Action: Human thesis review. No automatic trade."
            if needs_human_review
            else "Action: Continue monitoring. No automatic trade."
        ),
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--latest", type=Path, default=DEFAULT_LATEST)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    parser.add_argument("--diagnostic-output", type=Path)
    parser.add_argument("--stores", nargs="*", default=[])
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("BOUNTY_WALMART_PANEL_WORKERS", "6")),
    )
    parser.add_argument("--report-all", action="store_true")
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    item_id = str(config["product_identifiers"]["walmart_us_item_id"])
    brave = Path(os.getenv("BOUNTY_BRAVE_PATH", "") or DEFAULT_BRAVE)
    if not brave.exists():
        raise RuntimeError("brave_executable_missing")
    all_locations = list(config["walmart_locations"])
    selected = {str(value) for value in args.stores}
    known_store_ids = {str(value["store_id"]) for value in all_locations}
    if selected - known_store_ids:
        raise RuntimeError("unknown_store_id_requested")
    locations = [
        value for value in all_locations
        if not selected or str(value["store_id"]) in selected
    ]
    partial_run = bool(selected)
    lock_path = args.latest.parent / "walmart_native_panel.lock"
    with _exclusive_lock(lock_path):
        previous = None
        history_snapshots = []
        if not partial_run and args.latest.exists():
            previous = json.loads(args.latest.read_text(encoding="utf-8"))
        if not partial_run and args.history.exists():
            for line in args.history.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if value.get("item_id") == item_id and value.get("coverage_status") == "complete":
                    history_snapshots.append(value)
        worker_count = max(1, min(args.workers, len(locations)))
        if worker_count == 1:
            records = [
                _collect_location_with_retry(
                    location, item_id, brave, args.evidence_dir,
                )
                for location in locations
            ]
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                records = list(executor.map(
                    lambda location: _collect_location_with_retry(
                        location, item_id, brave, args.evidence_dir,
                    ),
                    locations,
                ))
        changes = []
        prior = {
            value["record_key"]: value
            for value in (previous or {}).get("records") or []
        }
        if not partial_run:
            for record in records:
                old = prior.get(record["record_key"])
                if old and old.get("status") != record.get("status"):
                    replenished_modes = _native_replenished_modes(old, record)
                    changes.append({
                        "record_key": record["record_key"],
                        "before": old.get("status"),
                        "after": record.get("status"),
                        "replenishment_candidate": bool(replenished_modes),
                        "replenished_local_modes": replenished_modes,
                    })
        counts = {}
        for record in records:
            counts[record["status"]] = counts.get(record["status"], 0) + 1
        locally_terminal_statuses = {
            "orderable", "out_of_stock", "not_listed_at_store",
        }
        unavailable = sum(
            value.get("status") not in locally_terminal_statuses
            for value in records
        )
        observed_at = _utc_now()
        prior_daily = (
            None
            if partial_run
            else _previous_distinct_day_snapshot(history_snapshots, observed_at)
        )
        day_over_day_changes = (
            [] if partial_run else _day_over_day_changes(prior_daily, records)
        )
        snapshot = {
            "schema_version": "walmart-native-panel/3",
            "observed_at": observed_at,
            "item_id": item_id,
            "coverage_status": (
                "partial_diagnostic"
                if partial_run
                else ("complete" if unavailable == 0 else "partial")
            ),
            "target_count": len(all_locations),
            "fresh_record_count": len(records),
            "collection_workers": worker_count,
            "records": records,
            "day_over_day_previous_observed_at": (
                (prior_daily or {}).get("observed_at")
            ),
            "day_over_day_changes": day_over_day_changes,
            "summary": {
                "attempted": len(locations),
                "status_counts": counts,
                "location_verified": sum(
                    value.get("target_location_verified") is True for value in records
                ),
                "orderable": sum(value.get("status") == "orderable" for value in records),
                "out_of_stock": sum(value.get("status") == "out_of_stock" for value in records),
                "not_listed_at_store": sum(
                    value.get("status") == "not_listed_at_store" for value in records
                ),
                "unavailable": unavailable,
                "changes": len(changes),
                "day_over_day_changes": len(day_over_day_changes),
            },
            "changes": changes,
            "automatic_trade_action": False,
        }
        if partial_run:
            snapshot["position_monitor"] = {
                "state": "partial_diagnostic_not_eligible",
                "action": "no_automatic_trade_action",
                "automatic_trade_action": False,
            }
            snapshot["restock_monitor"] = {
                "state": "partial_diagnostic_not_eligible",
                "alert_required": False,
                "action": "continue_monitoring",
                "automatic_trade_action": False,
            }
        else:
            snapshot["position_monitor"] = _position_monitor(
                history_snapshots,
                snapshot,
                config.get("rules", {}).get("position_monitor", {}),
            )
            snapshot["position_monitor"]["latest"]["not_listed_at_store"] = sum(
                value.get("status") == "not_listed_at_store" for value in records
            )
            if snapshot["position_monitor"]["state"] == "insufficient_coverage":
                classifiable = sum(
                    value.get("status") in {"orderable", "out_of_stock"}
                    for value in records
                )
                required = snapshot["position_monitor"]["minimum_verified_locations"]
                snapshot["position_monitor"]["reason"] = (
                    f"Only {classifiable} stores returned classifiable local product "
                    f"availability; {snapshot['summary']['not_listed_at_store']} returned "
                    f"an exact-location not-listed state, and at least {required} "
                    "orderable/out-of-stock observations are required."
                )
            snapshot["restock_monitor"] = _restock_monitor(
                records,
                day_over_day_changes,
                len(all_locations),
                config.get("rules", {}).get("restock_monitor", {}),
                previous,
            )
            latest_content = json.dumps(snapshot, indent=2) + "\n"
            _atomic_write(args.latest, latest_content)
            existing_history = (
                args.history.read_text(encoding="utf-8")
                if args.history.exists()
                else ""
            )
            if existing_history and not existing_history.endswith("\n"):
                existing_history += "\n"
            existing_history += json.dumps(snapshot, separators=(",", ":")) + "\n"
            _atomic_write(args.history, existing_history)
        if args.diagnostic_output:
            _atomic_write(
                args.diagnostic_output,
                json.dumps(snapshot, indent=2) + "\n",
            )
        if args.report_all:
            print(json.dumps({
                **snapshot["summary"],
                "coverage_status": snapshot["coverage_status"],
                "position_monitor": snapshot["position_monitor"],
                "restock_monitor": snapshot["restock_monitor"],
                "changes_detail": changes,
                "day_over_day_changes_detail": day_over_day_changes,
            }, indent=2))
        elif snapshot["restock_monitor"].get("alert_required"):
            print(_format_restock_alert(snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
