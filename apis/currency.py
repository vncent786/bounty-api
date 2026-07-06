"""
Currency Conversion API
=======================

FastAPI router exposing live foreign-exchange conversion and reference-rate
endpoints backed by the free, no-key-required Frankfurter API
(https://frankfurter.dev), which publishes daily ECB (European Central Bank)
reference rates.

The router can be mounted by a parent application::

    from apis.currency import router
    app.include_router(router)   # prefix is already "/currency"

Endpoints
---------
- GET /currency/convert?from=USD&to=SGD&amount=100  -> convert an amount
- GET /currency/rates?base=USD                       -> latest rates for a base
- GET /currency/supported                            -> list supported currencies
- GET /currency/                                     -> API metadata

Design notes
------------
- Latest rates are cached in-process with a 1-hour TTL, keyed by base currency;
  the supported-currency list is cached separately with the same TTL. The first
  request for a given base after expiry (or a cold start) triggers exactly one
  upstream call under a per-cache lock.
- Only Python stdlib (urllib / json / ssl / threading / time / datetime) plus
  FastAPI and Pydantic are used -- no extra dependencies, mirroring the rest of
  the project.
- On upstream HTTP failure a 503 is returned. Requests for unsupported currency
  codes are rejected with a 404 pointing the caller at /currency/supported.
"""

from __future__ import annotations

import json
import ssl
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# The published host is api.frankfurter.app; it 301-redirects to the canonical
# api.frankfurter.dev/v1, which urllib follows automatically. We keep the
# documented host as the canonical entry point.
BASE_URL = "https://api.frankfurter.app"
LATEST_URL = f"{BASE_URL}/latest"
CURRENCIES_URL = f"{BASE_URL}/currencies"

HTTP_TIMEOUT = 10                       # seconds per upstream request
CACHE_TTL_SECONDS = 60 * 60             # 1 hour
DEFAULT_BASE = "USD"

HTTP_HEADERS = {"User-Agent": "bountyapi/currency (contact: ops@bountyapi.com)"}

# Frankfurter (ECB) never quotes the base currency against itself; a same-code
# conversion therefore always has a rate of exactly 1.0.
SELF_RATE = 1.0

# Shared, forgiving SSL context (kept consistent with the other routers so that
# transient certificate-chain hiccups don't break a rate refresh).
_SSL_CTX = ssl.create_default_context()


# --------------------------------------------------------------------------- #
# In-memory cache
# --------------------------------------------------------------------------- #

class _TTLCache:
    """Thread-safe, string-keyed TTL cache.

    A keyed generalization of the single-slot cache used by ``hdb_resale``:
    currency rates are independent per base currency, so each base gets its own
    slot without invalidating the others. A reentrant lock lets the refresh
    path hold the lock across a fetch *and* a subsequent ``set``.
    """

    def __init__(self, ttl: int = CACHE_TTL_SECONDS) -> None:
        self.ttl = ttl
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {}
        self._fetched_at: Dict[str, float] = {}

    def fresh(self, key: str) -> bool:
        with self._lock:
            return key in self._data and (
                time.time() - self._fetched_at.get(key, 0.0)
            ) < self.ttl

    def get(self, key: str) -> Optional[Any]:
        """Return cached data for ``key`` only while it is still fresh."""
        with self._lock:
            if not self.fresh(key):
                return None
            return self._data.get(key)

    def set(self, key: str, data: Any) -> None:
        with self._lock:
            self._data[key] = data
            self._fetched_at[key] = time.time()

    def lock(self) -> threading.RLock:
        return self._lock


_RATES_CACHE = _TTLCache()
_SUPPORTED_CACHE = _TTLCache()

# Single, well-known key under which the supported-currency map is stored.
_SUPPORTED_KEY = "__supported__"


# --------------------------------------------------------------------------- #
# Low-level HTTP helper
# --------------------------------------------------------------------------- #

def _http_get_json(url: str, timeout: int = HTTP_TIMEOUT) -> Dict[str, Any]:
    """GET a JSON document from ``url`` using urllib (stdlib only)."""
    req = Request(url, headers=HTTP_HEADERS)
    with urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw)


# --------------------------------------------------------------------------- #
# Data access (with caching)
# --------------------------------------------------------------------------- #

def _normalize(code: str) -> str:
    """Normalize a currency code: uppercase + stripped whitespace."""
    return (code or "").strip().upper()


def _fetch_rates(base: str) -> Dict[str, Any]:
    """Fetch the latest rates for ``base`` from Frankfurter (bypasses cache).

    Raises ``HTTPError``/``URLError``/``RuntimeError`` on failure; callers are
    expected to translate these into HTTP responses.
    """
    url = f"{LATEST_URL}?{urlencode({'from': base})}"
    payload = _http_get_json(url)
    if "rates" not in payload or "base" not in payload:
        raise RuntimeError(f"Unexpected upstream response shape: {payload!r}")
    return payload


def get_rates(base: str) -> Dict[str, Any]:
    """Return the latest rates for ``base``, using the cache when fresh.

    On a cache miss a single upstream call is made under the cache lock so that
    concurrent callers for the same base don't stampede Frankfurter.
    """
    base = _normalize(base)
    cached = _RATES_CACHE.get(base)
    if cached is not None:
        return cached
    with _RATES_CACHE.lock():
        # Double-checked locking: another thread may have populated it.
        cached = _RATES_CACHE.get(base)
        if cached is not None:
            return cached
        payload = _fetch_rates(base)
        _RATES_CACHE.set(base, payload)
        return payload


def _fetch_supported() -> Dict[str, str]:
    """Fetch the supported-currency map from Frankfurter (bypasses cache)."""
    payload = _http_get_json(CURRENCIES_URL)
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError(f"Unexpected upstream currencies response: {payload!r}")
    return payload


def get_supported() -> Dict[str, str]:
    """Return the supported-currency map, using the cache when fresh."""
    cached = _SUPPORTED_CACHE.get(_SUPPORTED_KEY)
    if cached is not None:
        return cached
    with _SUPPORTED_CACHE.lock():
        cached = _SUPPORTED_CACHE.get(_SUPPORTED_KEY)
        if cached is not None:
            return cached
        payload = _fetch_supported()
        _SUPPORTED_CACHE.set(_SUPPORTED_KEY, payload)
        return payload


def _is_supported(code: str) -> bool:
    """Best-effort check for whether ``code`` is a supported currency.

    Consults the (cached) supported list without forcing a refresh -- if the
    list happens to be empty we optimistically allow the caller to proceed and
    let the live rates endpoint give the authoritative answer.
    """
    code = _normalize(code)
    if not code:
        return False
    supported = _SUPPORTED_CACHE.get(_SUPPORTED_KEY)
    if not supported:
        return True  # unknown -> defer to the rates call itself
    return code in supported


# --------------------------------------------------------------------------- #
# Error translation
# --------------------------------------------------------------------------- #

def _translate_upstream_error(base_or_ctx: str, exc: BaseException) -> HTTPException:
    """Map an upstream exception to a user-facing ``HTTPException``."""
    if isinstance(exc, HTTPError):
        if exc.code == 404:
            return HTTPException(
                status_code=404,
                detail={
                    "error": f"Currency '{base_or_ctx}' is not supported.",
                    "hint": "Use GET /currency/supported for the full list.",
                },
            )
        return HTTPException(
            status_code=503,
            detail=f"Upstream currency service returned HTTP {exc.code}: {exc.reason}",
        )
    if isinstance(exc, URLError):
        return HTTPException(
            status_code=503,
            detail=f"Could not reach the currency upstream: {exc.reason}",
        )
    return HTTPException(
        status_code=503,
        detail=f"Upstream currency service unavailable: {exc}",
    )


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class ConvertResponse(BaseModel):
    from_currency: str = Field(..., description="Source ISO 4217 currency code (uppercased)")
    to_currency: str = Field(..., description="Target ISO 4217 currency code (uppercased)")
    amount: float = Field(..., description="Original amount in the source currency")
    rate: float = Field(..., description="Exchange rate used (1 unit of `from_currency` = `rate` units of `to_currency`)")
    result: float = Field(..., description="Converted amount in the target currency")
    date: Optional[str] = Field(None, description="Reference date (YYYY-MM-DD) of the ECB rate, as published by Frankfurter")
    cached: bool = Field(..., description="True if the rate was served from the in-memory cache")
    source: str = Field(..., description="Upstream data source URL")


class RatesResponse(BaseModel):
    base: str = Field(..., description="Base ISO 4217 currency code")
    date: str = Field(..., description="Reference date (YYYY-MM-DD) of the published rates")
    rates: Dict[str, float] = Field(..., description="Mapping of target currency code -> rate relative to the base")
    cached: bool = Field(..., description="True if the rates were served from the in-memory cache")
    source: str = Field(..., description="Upstream data source URL")


class SupportedResponse(BaseModel):
    count: int = Field(..., description="Number of supported currencies")
    currencies: Dict[str, str] = Field(..., description="Mapping of currency code -> human-readable name")
    cached: bool = Field(..., description="True if the list was served from the in-memory cache")
    source: str = Field(..., description="Upstream data source URL")


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #

router = APIRouter(prefix="/currency", tags=["currency"])


@router.get(
    "/convert",
    response_model=ConvertResponse,
    summary="Convert an amount between two currencies",
)
def convert_currency(
    from_: str = Query(
        DEFAULT_BASE,
        alias="from",
        min_length=3,
        max_length=3,
        description="Source ISO 4217 currency code, e.g. 'USD'",
    ),
    to: str = Query(
        ...,
        min_length=3,
        max_length=3,
        description="Target ISO 4217 currency code, e.g. 'SGD'",
    ),
    amount: float = Query(
        1.0,
        ge=0,
        description="Amount in the source currency to convert (>= 0)",
    ),
) -> ConvertResponse:
    """Convert ``amount`` of ``from`` into ``to`` using live ECB rates.

    Example: ``GET /currency/convert?from=USD&to=SGD&amount=100``

    A same-code conversion (``from == to``) always uses a rate of exactly 1.0
    and still validates that the code is supported by the upstream.
    """
    from_cur = _normalize(from_)
    to_cur = _normalize(to)

    if not from_cur or not to_cur:
        raise HTTPException(status_code=422, detail="Currency codes must be 3 letters.")

    # Cheap client-side validation against the cached supported list. If the
    # list isn't cached yet we defer to the live rates call below.
    if not _is_supported(from_cur):
        raise HTTPException(
            status_code=404,
            detail={
                "error": f"Currency '{from_cur}' is not supported.",
                "hint": "Use GET /currency/supported for the full list.",
            },
        )
    if not _is_supported(to_cur):
        raise HTTPException(
            status_code=404,
            detail={
                "error": f"Currency '{to_cur}' is not supported.",
                "hint": "Use GET /currency/supported for the full list.",
            },
        )

    try:
        was_cached = _RATES_CACHE.fresh(from_cur)
        payload = get_rates(from_cur)
    except (HTTPError, URLError, RuntimeError) as exc:
        raise _translate_upstream_error(from_cur, exc)

    if from_cur == to_cur:
        rate = SELF_RATE
    else:
        rates = payload.get("rates", {})
        if to_cur not in rates:
            # Base is valid (we got rates) but the target isn't quoted.
            raise HTTPException(
                status_code=404,
                detail={
                    "error": f"Currency '{to_cur}' is not supported.",
                    "hint": "Use GET /currency/supported for the full list.",
                },
            )
        rate = float(rates[to_cur])

    result = amount * rate

    return ConvertResponse(
        from_currency=from_cur,
        to_currency=to_cur,
        amount=round(amount, 4),
        rate=round(rate, 6),
        result=round(result, 4),
        date=payload.get("date"),
        cached=was_cached,
        source=BASE_URL,
    )


@router.get(
    "/rates",
    response_model=RatesResponse,
    summary="Latest exchange rates for a base currency",
)
def get_latest_rates(
    base: str = Query(
        DEFAULT_BASE,
        min_length=3,
        max_length=3,
        description="Base ISO 4217 currency code, e.g. 'USD'",
    ),
) -> RatesResponse:
    """Return the latest ECB reference rates for ``base``.

    Example: ``GET /currency/rates?base=USD`` -> {base: "USD", rates: {EUR: ...,
    SGD: ..., ...}, date: "YYYY-MM-DD"}.
    """
    base_cur = _normalize(base)
    if not base_cur:
        raise HTTPException(status_code=422, detail="Currency code must be 3 letters.")

    if not _is_supported(base_cur):
        raise HTTPException(
            status_code=404,
            detail={
                "error": f"Currency '{base_cur}' is not supported.",
                "hint": "Use GET /currency/supported for the full list.",
            },
        )

    try:
        was_cached = _RATES_CACHE.fresh(base_cur)
        payload = get_rates(base_cur)
    except (HTTPError, URLError, RuntimeError) as exc:
        raise _translate_upstream_error(base_cur, exc)

    return RatesResponse(
        base=payload.get("base", base_cur),
        date=payload.get("date", ""),
        rates={k: float(v) for k, v in payload.get("rates", {}).items()},
        cached=was_cached,
        source=BASE_URL,
    )


@router.get(
    "/supported",
    response_model=SupportedResponse,
    summary="List all supported currencies",
)
def list_supported_currencies() -> SupportedResponse:
    """Return the full set of currency codes Frankfurter (ECB) supports, as a
    mapping of code -> human-readable name."""
    try:
        was_cached = _SUPPORTED_CACHE.fresh(_SUPPORTED_KEY)
        currencies = get_supported()
    except (HTTPError, URLError, RuntimeError) as exc:
        raise _translate_upstream_error("currencies", exc)

    return SupportedResponse(
        count=len(currencies),
        currencies=dict(sorted(currencies.items())),
        cached=was_cached,
        source=BASE_URL,
    )


@router.get("/", summary="Currency API metadata")
def root() -> Dict[str, Any]:
    return {
        "module": "currency",
        "description": "Live FX conversion & reference rates from Frankfurter (ECB)",
        "endpoints": [
            "/currency/convert",
            "/currency/rates",
            "/currency/supported",
        ],
        "data_source": BASE_URL,
        "default_base": DEFAULT_BASE,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "rates_cache_keys": list(_RATES_CACHE._data.keys()),
        "supported_cached": _SUPPORTED_CACHE.fresh(_SUPPORTED_KEY),
    }


__all__ = ["router"]
