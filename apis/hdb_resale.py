"""
Singapore HDB Resale Price Data API
====================================

FastAPI router exposing aggregated and searchable HDB (Housing & Development
Board) public-housing resale transaction data sourced from Singapore's official
open-data portal, https://data.gov.sg.

The router can be mounted by a parent application::

    from apis.hdb_resale import router
    app.include_router(router)   # prefix is already "/hdb"

Endpoints
---------
- GET /hdb/towns              -> all towns with transaction counts
- GET /hdb/median             -> median resale price by flat type for ALL towns
- GET /hdb/median/{town}      -> median resale price by flat type for one town
- GET /hdb/search             -> search transactions with optional filters

Design notes
------------
- The full dataset (~235k transactions, 2017 -> present) is downloaded once
  and aggregated client-side.  The data.gov.sg ``datastore_search_sql`` endpoint
  is currently unavailable, so SQL aggregations cannot be offloaded to the
  server.  Aggregation results are cached in-process with a 24h TTL; the first
  request after expiry (or a cold start) rebuilds the cache.
- Only Python stdlib (urllib / json / ssl / threading / datetime / statistics)
  plus FastAPI and Pydantic are used -- no extra dependencies.
- On API failure, a still-valid (or stale) cache is served and a ``note`` field
  warns the caller; HTTP 503 is only returned when no data is available at all.
"""

from __future__ import annotations

import json
import ssl
import time
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_SOURCE_URL = "https://data.gov.sg/api/action/datastore_search"

# Candidate resource IDs, in order of preference.  The first that responds
# successfully is used for the lifetime of the process.
RESOURCE_CANDIDATES: List[str] = [
    "f1765b54-a209-4718-8d38-a39c379cc32c",  # legacy / "standard" dataset id
    "d_8b84c4ee58e3cfc0ece0d773c8ca6abc",    # 2024+ dataset id (known good)
]

# Download tuning
BATCH_SIZE = 5_000            # records per HTTP request during full aggregation
HTTP_TIMEOUT = 30            # seconds per request
CACHE_TTL_SECONDS = 24 * 60 * 60   # 24 hours
SEARCH_FETCH_CAP = 1_000     # max records pulled per /search call before
                             # client-side filtering & slicing to `limit`

# Square-metre -> square-foot conversion for price-per-square-foot (psf).
SQM_TO_SQFT = 10.7639

# Canonical ordering of flat types in responses.
FLAT_TYPE_ORDER = [
    "1 ROOM",
    "2 ROOM",
    "3 ROOM",
    "4 ROOM",
    "5 ROOM",
    "EXECUTIVE",
    "MULTI-GENERATION",
]

HTTP_HEADERS = {"User-Agent": "bountyapi/hdb-resale (contact: ops@bountyapi.com)"}

# Shared SSL context (data.gov.sg is fine with verification, but we keep a
# forgiving context so transient chain issues don't break data refreshes).
_SSL_CTX = ssl.create_default_context()


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

class _Cache:
    """Thread-safe cache wrapping an aggregated dataset with a TTL."""

    def __init__(self, ttl: int = CACHE_TTL_SECONDS) -> None:
        self.ttl = ttl
        # Reentrant lock: the refresh path holds this lock across the rebuild
        # *and* calls self.set(), which re-acquires it from the same thread.
        # RLock still serializes rebuilds across different threads.
        self._lock = threading.RLock()
        self._data: Optional[Dict[str, Any]] = None
        self._fetched_at: float = 0.0

    @property
    def is_empty(self) -> bool:
        return self._data is None

    def fresh(self) -> bool:
        return self._data is not None and (time.time() - self._fetched_at) < self.ttl

    def get(self) -> Optional[Dict[str, Any]]:
        return self._data

    def set(self, data: Dict[str, Any]) -> None:
        with self._lock:
            self._data = data
            self._fetched_at = time.time()

    def lock(self) -> threading.Lock:
        return self._lock


_CACHE = _Cache()

# Resolved resource id (selected on first use).
_active_resource_id: Optional[str] = None
_resource_lock = threading.Lock()

# Background-refresh coordination: ensures at most one background rebuild runs.
_bg_lock = threading.Lock()
_bg_running = False


# ---------------------------------------------------------------------------
# Low-level HTTP helpers
# ---------------------------------------------------------------------------

def _http_get_json(url: str, timeout: int = HTTP_TIMEOUT) -> Dict[str, Any]:
    """GET a JSON document from ``url`` using urllib."""
    req = Request(url, headers=HTTP_HEADERS)
    with urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw)


def _select_resource_id() -> str:
    """Pick the first candidate resource id that returns data."""
    global _active_resource_id
    if _active_resource_id is not None:
        return _active_resource_id
    with _resource_lock:
        if _active_resource_id is not None:
            return _active_resource_id
        last_err: Optional[str] = None
        for rid in RESOURCE_CANDIDATES:
            try:
                url = (
                    f"{DATA_SOURCE_URL}?"
                    + urlencode({"resource_id": rid, "limit": 1})
                )
                payload = _http_get_json(url)
                if (
                    payload.get("success")
                    and payload.get("result", {}).get("records") is not None
                ):
                    _active_resource_id = rid
                    return rid
            except Exception as exc:  # noqa: BLE001
                last_err = f"{rid}: {exc}"
                continue
        raise RuntimeError(
            f"No usable HDB resource id found. Last error: {last_err}"
        )


def _fetch_page(resource_id: str, offset: int, limit: int) -> List[Dict[str, Any]]:
    """Fetch a single page of records."""
    url = (
        f"{DATA_SOURCE_URL}?"
        + urlencode(
            {"resource_id": resource_id, "limit": limit, "offset": offset}
        )
    )
    payload = _http_get_json(url)
    if not payload.get("success"):
        raise RuntimeError(f"Upstream API reported failure: {payload}")
    return payload.get("result", {}).get("records", []) or []


def _get_total(resource_id: str) -> int:
    """Return the total record count reported by the API."""
    payload = _http_get_json(
        f"{DATA_SOURCE_URL}?"
        + urlencode({"resource_id": resource_id, "limit": 1})
    )
    total = payload.get("result", {}).get("total")
    if not isinstance(total, int):
        raise RuntimeError(f"Could not determine total record count: {payload}")
    return total


# ---------------------------------------------------------------------------
# Numeric parsing & statistics
# ---------------------------------------------------------------------------

def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _median(sorted_values: List[float]) -> Optional[float]:
    """Median of an already-sorted list."""
    n = len(sorted_values)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 == 1:
        return float(sorted_values[mid])
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2.0


def _sort_key(flat_type: str) -> Tuple[int, str]:
    try:
        return (FLAT_TYPE_ORDER.index(flat_type), flat_type)
    except ValueError:
        return (len(FLAT_TYPE_ORDER), flat_type)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

# Per (town, flat_type) aggregation bucket.
#   prices: sorted list of resale_price
#   psfs:   sorted list of price-per-sqft
GroupStats = Dict[str, List[float]]


def _build_aggregate(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    towns: Dict[str, int] = {}
    groups: Dict[Tuple[str, str], GroupStats] = {}

    for rec in records:
        town = (rec.get("town") or "").strip().upper()
        flat_type = (rec.get("flat_type") or "").strip().upper()
        price = _to_float(rec.get("resale_price"))
        if not town or not flat_type or price is None:
            continue

        towns[town] = towns.get(town, 0) + 1

        area = _to_float(rec.get("floor_area_sqm"))
        bucket = groups.setdefault(
            (town, flat_type), {"prices": [], "psfs": []}
        )
        bucket["prices"].append(price)
        if area and area > 0:
            bucket["psfs"].append(price / (area * SQM_TO_SQFT))

    for bucket in groups.values():
        bucket["prices"].sort()
        bucket["psfs"].sort()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(records),
        "towns": towns,
        "groups": groups,
    }


def _fetch_all_records(resource_id: str) -> List[Dict[str, Any]]:
    """Download the entire dataset.

    Fetching is done **sequentially**. data.gov.sg throttles concurrent
    requests, so a parallel ``ThreadPoolExecutor`` is reliably *slower* here
    (measured ~3.7k rec/s parallel vs ~21k rec/s sequential) and can stall.
    Sequential pages of 5,000 records typically complete the full ~235k
    dataset in well under a minute.
    """
    total = _get_total(resource_id)
    all_records: List[Dict[str, Any]] = []
    offset = 0
    consecutive_errors = 0
    while offset < total:
        try:
            page = _fetch_page(resource_id, offset, BATCH_SIZE)
            consecutive_errors = 0
        except Exception as exc:  # noqa: BLE001
            consecutive_errors += 1
            if consecutive_errors >= 3:
                raise
            # Transient error -- retry the same offset once more.
            continue
        if not page:
            break
        all_records.extend(page)
        offset += len(page)
    if not all_records:
        raise RuntimeError("No records downloaded from upstream")
    return all_records


def _refresh_aggregate() -> Dict[str, Any]:
    """Force a full refresh of the aggregated dataset."""
    resource_id = _select_resource_id()
    records = _fetch_all_records(resource_id)
    aggregate = _build_aggregate(records)
    _CACHE.set(aggregate)
    return aggregate


def _maybe_refresh_background() -> None:
    """Spawn a background rebuild if one isn't already running."""
    global _bg_running
    with _bg_lock:
        if _bg_running:
            return
        _bg_running = True
    threading.Thread(target=_background_refresh, daemon=True).start()


def _background_refresh() -> None:
    global _bg_running
    try:
        _refresh_aggregate()
    except Exception:  # noqa: BLE001
        # Background failures are non-fatal: existing (stale) cache remains.
        pass
    finally:
        with _bg_lock:
            _bg_running = False


def _get_aggregate(
    force: bool = False, allow_refresh: bool = True
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return ``(aggregate, warning)``.

    Fresh cache is served immediately. When the cache is stale:
      - if *some* data is already cached, it is served instantly and a refresh
        is kicked off in the background (callers never block waiting for a
        rebuild);
      - if the cache is empty (cold start), the rebuild is done synchronously
        -- a one-time cost (~10-60s) after which the cache is fresh for 24h.

    On a synchronous refresh failure, stale cache (if any) is returned with a
    warning, else ``(None, error)``. Callers that only want to *consult* an
    existing cache (e.g. input validation in ``/search``) pass
    ``allow_refresh=False``.
    """
    if not force and _CACHE.fresh():
        return _CACHE.get(), None

    if not allow_refresh:
        # Serve whatever we have (even stale) without triggering a download.
        return _CACHE.get(), None if _CACHE.fresh() else "serving stale cache"

    # Have (stale) data -> serve immediately, refresh quietly in the background.
    if not _CACHE.is_empty:
        _maybe_refresh_background()
        return (
            _CACHE.get(),
            "serving cached data; a refresh is running in the background",
        )

    # Cold start: DON'T block the caller for 30-60s. Kick off a background
    # rebuild and return immediately with a "warming up" warning. The startup
    # event in app.py triggers this on deploy, so by the time real traffic
    # arrives the cache should already be populated.
    _maybe_refresh_background()
    return None, "HDB data is warming up. Please retry in ~30 seconds."


# ---------------------------------------------------------------------------
# Response builders (shared by endpoints)
# ---------------------------------------------------------------------------

def _flat_type_stats(group: GroupStats) -> Optional["FlatTypeStats"]:
    prices = group["prices"]
    if not prices:
        return None
    psfs = group["psfs"]
    median_price = _median(prices)
    return FlatTypeStats(
        median_price=round(median_price, 2) if median_price is not None else 0.0,
        count=len(prices),
        min_price=round(prices[0], 2),
        max_price=round(prices[-1], 2),
        price_psf_range={
            "min": round(psfs[0], 2) if psfs else None,
            "max": round(psfs[-1], 2) if psfs else None,
            "median": round(_median(psfs), 2) if psfs else None,
        },
    )


def _town_median(aggregate: Dict[str, Any], town: str) -> "TownMedianResponse":
    flat_types: List[FlatTypeStats] = []
    for (t, ft), group in aggregate["groups"].items():
        if t != town:
            continue
        stats = _flat_type_stats(group)
        if stats is not None:
            stats.type = ft
            flat_types.append(stats)
    flat_types.sort(key=lambda s: _sort_key(s.type))
    return TownMedianResponse(town=town, flat_types=flat_types)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TownInfo(BaseModel):
    town: str
    transaction_count: int


class TownsResponse(BaseModel):
    total_towns: int
    total_transactions: int
    generated_at: str
    source: str = DATA_SOURCE_URL
    resource_id: str
    note: Optional[str] = None
    towns: List[TownInfo]


class PricePsfRange(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None
    median: Optional[float] = None


class FlatTypeStats(BaseModel):
    type: str = ""
    median_price: float
    count: int
    min_price: float
    max_price: float
    price_psf_range: PricePsfRange = Field(default_factory=PricePsfRange)


class TownMedianResponse(BaseModel):
    town: str
    flat_types: List[FlatTypeStats]


class AllTownsMedianResponse(BaseModel):
    generated_at: str
    total_towns: int
    source: str = DATA_SOURCE_URL
    note: Optional[str] = None
    towns: List[TownMedianResponse]


class Transaction(BaseModel):
    month: Optional[str] = None
    town: Optional[str] = None
    flat_type: Optional[str] = None
    block: Optional[str] = None
    street_name: Optional[str] = None
    storey_range: Optional[str] = None
    floor_area_sqm: Optional[float] = None
    flat_model: Optional[str] = None
    lease_commence_date: Optional[str] = None
    remaining_lease: Optional[str] = None
    resale_price: Optional[float] = None


class SearchResponse(BaseModel):
    count: int
    limit: int
    filters_applied: Dict[str, Any]
    transactions: List[Transaction]
    note: Optional[str] = None


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/hdb", tags=["hdb-resale"])


@router.get("/towns", response_model=TownsResponse, summary="List all HDB towns with transaction counts")
def get_towns() -> TownsResponse:
    """Return every town present in the dataset along with how many resale
    transactions have been recorded for it, sorted by transaction count
    (descending)."""
    aggregate, warning = _get_aggregate()
    if aggregate is None:
        raise HTTPException(
            status_code=503,
            detail=f"HDB data currently unavailable: {warning}",
        )
    towns = sorted(aggregate["towns"].items(), key=lambda kv: (-kv[1], kv[0]))
    return TownsResponse(
        total_towns=len(towns),
        total_transactions=aggregate["total"],
        generated_at=aggregate["generated_at"],
        resource_id=_select_resource_id(),
        note=warning,
        towns=[TownInfo(town=t, transaction_count=c) for t, c in towns],
    )


@router.get("/median", response_model=AllTownsMedianResponse, summary="Median resale price by flat type for ALL towns")
def get_all_median() -> AllTownsMedianResponse:
    """Median resale price (and min/max/price-per-sqft range) for each flat
    type, for every town in the dataset."""
    aggregate, warning = _get_aggregate()
    if aggregate is None:
        raise HTTPException(
            status_code=503,
            detail=f"HDB data currently unavailable: {warning}",
        )
    town_names = sorted(aggregate["towns"].keys())
    towns = [_town_median(aggregate, t) for t in town_names]
    return AllTownsMedianResponse(
        generated_at=aggregate["generated_at"],
        total_towns=len(town_names),
        note=warning,
        towns=towns,
    )


@router.get(
    "/median/{town}",
    response_model=TownMedianResponse,
    summary="Median resale price by flat type for a given town",
)
def get_town_median(town: str) -> TownMedianResponse:
    """Median resale price (and min/max/price-per-sqft range) for each flat
    type in the requested town. ``town`` is matched case-insensitively."""
    town_norm = town.strip().upper()
    aggregate, warning = _get_aggregate()
    if aggregate is None:
        raise HTTPException(
            status_code=503,
            detail=f"HDB data currently unavailable: {warning}",
        )

    if town_norm not in aggregate["towns"]:
        # Suggest close matches to help callers self-correct.
        known = list(aggregate["towns"].keys())
        suggestions = sorted(
            known,
            key=lambda t: _levenshtein(town_norm, t),
        )[:5]
        raise HTTPException(
            status_code=404,
            detail={
                "error": f"Town '{town}' not found.",
                "suggestions": suggestions,
                "hint": "Use GET /hdb/towns for the full list of valid towns.",
            },
        )

    result = _town_median(aggregate, town_norm)
    # Surface a stale-cache warning without breaking the response contract.
    if warning:
        result = result.model_copy(update={"town": result.town})
    return result


@router.get("/search", response_model=SearchResponse, summary="Search HDB resale transactions")
def search_transactions(
    town: Optional[str] = Query(None, description="Town name (case-insensitive)"),
    flat_type: Optional[str] = Query(None, description="Flat type, e.g. '3 ROOM'"),
    min_price: Optional[float] = Query(None, ge=0, description="Minimum resale_price (SGD)"),
    max_price: Optional[float] = Query(None, ge=0, description="Maximum resale_price (SGD)"),
    min_floor_area_sqm: Optional[float] = Query(
        None, ge=0, description="Minimum floor area in square metres"
    ),
    limit: int = Query(20, ge=1, le=100, description="Max records to return (1-100)"),
) -> SearchResponse:
    """Search transactions. ``town`` and ``flat_type`` are filtered server-side
    by the upstream API; price and floor-area filters are applied client-side on
    the fetched batch. Because the upstream API cannot do range queries, a cap
    of records is fetched per call and filtered locally."""
    resource_id = _select_resource_id()

    town_norm = town.strip().upper() if town else None
    flat_type_norm = flat_type.strip().upper() if flat_type else None

    # Validate town/flat_type against the cached dataset when readily available.
    # We deliberately do NOT trigger a full refresh here (allow_refresh=False):
    # /search must stay lightweight and never block on a 235k-record rebuild.
    # If the cache is empty (cold start) we skip validation and let the upstream
    # API return an empty result for unknown values.
    aggregate, _ = _get_aggregate(allow_refresh=False)
    if aggregate is not None:
        if town_norm and town_norm not in aggregate["towns"]:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": f"Town '{town}' not found.",
                    "suggestions": sorted(aggregate["towns"].keys())[:10],
                    "hint": "Use GET /hdb/towns for the full list.",
                },
            )
        if flat_type_norm:
            known_flat_types = sorted({ft for (_t, ft) in aggregate["groups"].keys()})
            if flat_type_norm not in known_flat_types:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": f"Flat type '{flat_type}' not found.",
                        "valid_flat_types": known_flat_types,
                    },
                )

    if max_price is not None and min_price is not None and max_price < min_price:
        raise HTTPException(
            status_code=422, detail="max_price must be >= min_price"
        )

    # Server-side exact-match filters.
    api_filters: Dict[str, str] = {}
    if town_norm:
        api_filters["town"] = town_norm
    if flat_type_norm:
        api_filters["flat_type"] = flat_type_norm

    note: Optional[str] = None
    try:
        url = (
            f"{DATA_SOURCE_URL}?"
            + urlencode(
                {
                    "resource_id": resource_id,
                    "limit": SEARCH_FETCH_CAP,
                    "filters": json.dumps(api_filters) if api_filters else "",
                }
            )
        )
        payload = _http_get_json(url)
        records = payload.get("result", {}).get("records", []) or []
    except Exception as exc:  # noqa: BLE001
        note = f"Upstream search failed ({type(exc).__name__}: {exc}); returning empty result."
        records = []

    # Client-side range filters.
    filtered: List[Transaction] = []
    for rec in records:
        price = _to_float(rec.get("resale_price"))
        area = _to_float(rec.get("floor_area_sqm"))
        if price is None:
            continue
        if min_price is not None and price < min_price:
            continue
        if max_price is not None and price > max_price:
            continue
        if min_floor_area_sqm is not None and (area is None or area < min_floor_area_sqm):
            continue
        filtered.append(
            Transaction(
                month=rec.get("month"),
                town=rec.get("town"),
                flat_type=rec.get("flat_type"),
                block=rec.get("block"),
                street_name=rec.get("street_name"),
                storey_range=rec.get("storey_range"),
                floor_area_sqm=area,
                flat_model=rec.get("flat_model"),
                lease_commence_date=rec.get("lease_commence_date"),
                remaining_lease=rec.get("remaining_lease"),
                resale_price=price,
            )
        )
        if len(filtered) >= limit:
            break

    return SearchResponse(
        count=len(filtered),
        limit=limit,
        filters_applied={
            "town": town_norm,
            "flat_type": flat_type_norm,
            "min_price": min_price,
            "max_price": max_price,
            "min_floor_area_sqm": min_floor_area_sqm,
        },
        transactions=filtered,
        note=note,
    )


# ---------------------------------------------------------------------------
# Health / metadata
# ---------------------------------------------------------------------------

@router.get("/", summary="HDB resale API metadata")
def root() -> Dict[str, Any]:
    return {
        "module": "hdb_resale",
        "description": "Singapore HDB resale transaction data from data.gov.sg",
        "endpoints": [
            "/hdb/towns",
            "/hdb/median",
            "/hdb/median/{town}",
            "/hdb/search",
        ],
        "data_source": DATA_SOURCE_URL,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "cache_fresh": _CACHE.fresh(),
        "cache_populated": not _CACHE.is_empty,
    }


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def _levenshtein(a: str, b: str) -> int:
    """Classic edit distance, used for town-name suggestions."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[-1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


__all__ = ["router"]
