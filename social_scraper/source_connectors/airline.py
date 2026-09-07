"""Official free airline demand adapters: ARC, EUROCONTROL and UK CAA."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .execution import payload_sha256
from .models import (
    AuthMode,
    CostProfile,
    NormalizedEvidence,
    PreflightProbe,
    RateLimitPolicy,
    RawConnectorResult,
    RawState,
    SourceCapability,
    SourceExecutionRequest,
    TimeRangeCapability,
)
from .official_http import (
    OfficialFetchError,
    OfficialHttpResponse,
    classify_http_status,
    fetch_public,
)


Fetcher = Callable[[str], Awaitable[OfficialHttpResponse]]

ARC_SALES_PAGE = "https://www2.arccorp.com/articles-trends/sales-statistics/"
ARC_CSV_URL = "https://www2.arccorp.com/globalassets/forms/corpstats.csv"
EUROCONTROL_SERIES_URL = (
    "https://www.eurocontrol.int/publications/series/eurocontrol-european-aviation-overview"
)
EUROCONTROL_ORIGIN = "https://www.eurocontrol.int"
CAA_PUNCTUALITY_INDEX = (
    "https://www.caa.co.uk/data-and-analysis/uk-aviation-market/"
    "flight-punctuality/uk-flight-punctuality-statistics/"
)
CAA_ORIGIN = "https://www.caa.co.uk"

ARC_REQUIRED_FIELDS = ("ID", "DT", "YEAR", "MONTH_NBR", "MONTH", "TOT_SALES_AMT")
ARC_METRIC_DEFINITION = (
    "Total amount (USD) of ARC-settled U.S. travel-agency air travel transactions, "
    "including fares, fees and taxes."
)
EUROCONTROL_WEEK_RE = re.compile(
    r"/publication/eurocontrol-european-aviation-overview-(20\d{2})-week-(\d{1,2})/?",
    re.I,
)
EUROCONTROL_DAILY_FLIGHTS_RE = re.compile(
    r"averaged\s+([0-9][0-9,]*)\s+daily flights",
    re.I,
)
EUROCONTROL_ARRIVAL_OTP_RE = re.compile(
    r"arrival punctuality.{0,80}?(\d+(?:\.\d+)?)\s*%",
    re.I | re.S,
)
EUROCONTROL_DEPARTURE_OTP_RE = re.compile(
    r"departure punctuality.{0,80}?(\d+(?:\.\d+)?)\s*%",
    re.I | re.S,
)
EUROCONTROL_DELAY_RE = re.compile(
    r"ATFM\)?\s*delay per flight was\s+(\d+(?:\.\d+)?)\s*mins?/flight",
    re.I,
)
EUROCONTROL_WEEK_HEAD_RE = re.compile(r"Week\s+(\d{1,2})\b", re.I)
CAA_YEAR_RE = re.compile(r"/uk-flight-punctuality-statistics/(20\d{2})/?$", re.I)
CAA_PERIOD_RE = re.compile(r"\b(20\d{2})(0[1-9]|1[0-2])\b")
CAA_SUMMARY_RE = re.compile(r"punctuality statistics summary analysis", re.I)
CAA_REQUIRED_FIELDS = ("Reporting Period", "Reporting Airport", "Origin Destination")


class SchemaError(ValueError):
    """Published payload is present but no longer matches the frozen schema."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_http_error(status_code: int) -> RawConnectorResult:
    classified = classify_http_status(status_code)
    if classified is None:
        raise ValueError("successful HTTP status is not an error")
    state_name, category = classified
    return RawConnectorResult(
        records=(),
        state=RawState(state_name),
        error_category=category,
    )


def _fetch_error(exc: OfficialFetchError) -> RawConnectorResult:
    return RawConnectorResult(records=(), state=RawState.ERROR, error_category=exc.category)


def _parser_error(category: str) -> RawConnectorResult:
    return RawConnectorResult(records=(), state=RawState.PARSER_ERROR, error_category=category)


def _complete(records: tuple[Any, ...], *, observed_at: str, metadata: Mapping[str, Any]) -> RawConnectorResult:
    if not records:
        return RawConnectorResult(
            records=(),
            state=RawState.EMPTY,
            source_observed_at=observed_at,
            metadata=dict(metadata),
        )
    return RawConnectorResult(
        records=records,
        state=RawState.COMPLETE,
        source_observed_at=observed_at,
        metadata=dict(metadata),
    )


def _query_tokens(query: str) -> str:
    return " ".join(str(query or "").casefold().split())


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int_id(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    return int(text)


# ---------------------------------------------------------------------------
# ARC
# ---------------------------------------------------------------------------


def parse_arc_csv(text: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise SchemaError("arc_csv_missing_header")
    missing = [name for name in ARC_REQUIRED_FIELDS if name not in reader.fieldnames]
    if missing:
        raise SchemaError("arc_csv_changed_schema")
    rows = []
    for raw in reader:
        identifier = _int_id(raw.get("ID"))
        if identifier is None or identifier >= 1000:
            continue
        period = str(raw.get("DT") or "").strip()
        if not period:
            continue
        sales = _number(raw.get("TOT_SALES_AMT"))
        if sales is None:
            raise SchemaError("arc_csv_changed_schema")
        rows.append({
            "id": identifier,
            "period": period,
            "year": str(raw.get("YEAR") or "").strip(),
            "month_number": str(raw.get("MONTH_NBR") or "").strip(),
            "month": str(raw.get("MONTH") or "").strip(),
            "total_sales_usd": sales,
            "total_sales_var": _number(raw.get("TOT_SALES_VAR")),
            "ytd_total_sales_usd": _number(raw.get("YTD_TOT_SALES_AMT")),
            "average_ticket_price_usd": _number(raw.get("AVG_TKT_PRICE")),
            "passenger_trips": _number(raw.get("TOT_FLT_CNT")),
            "raw": {key: raw.get(key) for key in ARC_REQUIRED_FIELDS},
        })
    return rows


def _arc_matches(row: Mapping[str, Any], query: str) -> bool:
    needle = _query_tokens(query)
    if not needle or needle in {"current totals", "current", "totals", "sales"}:
        return True
    haystack = " ".join(
        str(row.get(key) or "")
        for key in ("period", "year", "month", "month_number")
    ).casefold()
    compact = haystack.replace("-", "").replace(" ", "")
    return needle in haystack or needle.replace("-", "").replace(" ", "") in compact


def _arc_record(row: Mapping[str, Any], *, source_url: str, payload_hash: str) -> dict[str, Any]:
    return {
        "kind": "arc_monthly_sales",
        "external_id": f"arc-monthly-{row['period']}",
        "evidence_type": "airline.ticket_sales",
        "title": f"ARC settled agency sales {row['month']} {row['year']}",
        "text": (
            f"ARC settled U.S. travel-agency air sales were {row['total_sales_usd']} USD "
            f"in {row['month']} {row['year']}."
        ),
        "published_at": row["period"],
        "source_date": row["period"],
        "geography": "US",
        "url": ARC_SALES_PAGE,
        "dataset_url": source_url,
        "metric_definition": ARC_METRIC_DEFINITION,
        "raw_payload_hash": payload_hash,
        "total_sales_usd": row["total_sales_usd"],
        "total_sales_var": row["total_sales_var"],
        "ytd_total_sales_usd": row["ytd_total_sales_usd"],
        "average_ticket_price_usd": row["average_ticket_price_usd"],
        "passenger_trips": row["passenger_trips"],
    }


# ---------------------------------------------------------------------------
# EUROCONTROL
# ---------------------------------------------------------------------------


def parse_eurocontrol_listing(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "")
        if "sharer" in href.casefold() or "sharearticle" in href.casefold():
            continue
        match = EUROCONTROL_WEEK_RE.search(href)
        if not match:
            continue
        year, week = match.group(1), f"{int(match.group(2)):02d}"
        url = urljoin(EUROCONTROL_ORIGIN, href.split("?", 1)[0])
        if "/publication/" not in url.casefold():
            continue
        title = anchor.get_text(" ", strip=True) or f"EUROCONTROL European Aviation Overview {year} week {week}"
        found[(year, week)] = {
            "year": year,
            "week": week,
            "week_number": int(week),
            "title": title,
            "url": url,
        }
    if not found:
        raise SchemaError("eurocontrol_listing_changed_schema")
    return sorted(found.values(), key=lambda item: (item["year"], item["week"]), reverse=True)


def parse_eurocontrol_publication(html: str, page_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    body = soup.select_one(".field--name-field-body") or soup.select_one("article") or soup
    text = body.get_text(" ", strip=True) if body else ""
    if not text:
        raise SchemaError("eurocontrol_publication_changed_schema")
    pdf_url = None
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "")
        if href.casefold().endswith(".pdf") and "european-aviation-overview" in href.casefold():
            pdf_url = urljoin(EUROCONTROL_ORIGIN, href)
            break
    flights = EUROCONTROL_DAILY_FLIGHTS_RE.search(text)
    if not flights:
        raise SchemaError("eurocontrol_publication_changed_schema")
    week_match = EUROCONTROL_WEEK_HEAD_RE.search(text)
    arrival = EUROCONTROL_ARRIVAL_OTP_RE.search(text)
    departure = EUROCONTROL_DEPARTURE_OTP_RE.search(text)
    delay = EUROCONTROL_DELAY_RE.search(text)
    return {
        "page_url": page_url,
        "pdf_url": pdf_url,
        "body_text": text,
        "week_number": int(week_match.group(1)) if week_match else None,
        "average_daily_flights": int(flights.group(1).replace(",", "")),
        "arrival_punctuality_pct": _number(arrival.group(1) if arrival else None),
        "departure_punctuality_pct": _number(departure.group(1) if departure else None),
        "atfm_delay_minutes_per_flight": _number(delay.group(1) if delay else None),
    }


def _eurocontrol_matches(item: Mapping[str, Any], query: str) -> bool:
    needle = _query_tokens(query)
    if not needle or "european aviation overview" in needle or needle in {"traffic", "current", "week"}:
        return True
    haystack = " ".join(
        str(item.get(key) or "")
        for key in ("title", "year", "week", "url")
    ).casefold()
    return needle in haystack or needle.replace(" ", "") in haystack.replace(" ", "")


def _eurocontrol_record(
    item: Mapping[str, Any],
    *,
    payload_hash: str,
    metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    year = item["year"]
    week = item["week"]
    source_date = f"{year}-W{week}"
    metrics = dict(metrics or {})
    flights = metrics.get("average_daily_flights")
    text = (
        f"EUROCONTROL network averaged {flights} daily flights in week {int(week)} {year}."
        if flights is not None
        else f"EUROCONTROL European Aviation Overview {year} week {int(week)}."
    )
    return {
        "kind": "eurocontrol_weekly_overview",
        "external_id": f"eurocontrol-week-{year}-{week}",
        "evidence_type": "airline.traffic",
        "title": item.get("title") or f"EUROCONTROL European Aviation Overview {year} week {week}",
        "text": text,
        "published_at": source_date,
        "source_date": source_date,
        "geography": "EUROPE",
        "url": metrics.get("pdf_url") or metrics.get("page_url") or item["url"],
        "listing_url": EUROCONTROL_SERIES_URL,
        "publication_url": item["url"],
        "pdf_url": metrics.get("pdf_url"),
        "metric_definition": (
            "Average daily flights on the EUROCONTROL network, including overflights, "
            "from the weekly European Aviation Overview. Operational traffic is not ticket revenue."
        ),
        "raw_payload_hash": payload_hash,
        "average_daily_flights": flights,
        "arrival_punctuality_pct": metrics.get("arrival_punctuality_pct"),
        "departure_punctuality_pct": metrics.get("departure_punctuality_pct"),
        "atfm_delay_minutes_per_flight": metrics.get("atfm_delay_minutes_per_flight"),
        "week_number": item.get("week_number") or metrics.get("week_number"),
        "year": year,
    }


# ---------------------------------------------------------------------------
# UK CAA
# ---------------------------------------------------------------------------


def parse_caa_index(html: str) -> list[int]:
    soup = BeautifulSoup(html, "lxml")
    years = set()
    for anchor in soup.select("a[href]"):
        match = CAA_YEAR_RE.search(str(anchor.get("href") or ""))
        if match:
            years.add(int(match.group(1)))
    if not years:
        raise SchemaError("caa_index_changed_schema")
    return sorted(years, reverse=True)


def parse_caa_year_documents(html: str, page_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    documents = []
    for anchor in soup.select("a.c-document__link[href], a[href*='/Documents/Download/']"):
        label = anchor.get_text(" ", strip=True)
        href = str(anchor.get("href") or "")
        if not label or not href:
            continue
        period_match = CAA_PERIOD_RE.search(label)
        documents.append({
            "label": label,
            "url": urljoin(CAA_ORIGIN, href),
            "page_url": page_url,
            "period": period_match.group(0) if period_match else "",
            "is_summary_csv": bool(
                CAA_SUMMARY_RE.search(label) and "csv" in label.casefold()
            ),
        })
    if not documents:
        raise SchemaError("caa_year_changed_schema")
    return documents


def parse_caa_summary_csv(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    header_index = None
    for index, line in enumerate(lines[:8]):
        if "Reporting Period" in line and "Reporting Airport" in line:
            header_index = index
            break
    if header_index is None:
        raise SchemaError("caa_csv_changed_schema")
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_index:])))
    fieldnames = [str(name or "").strip() for name in (reader.fieldnames or [])]
    renamed = {name: name.strip() for name in fieldnames}
    missing = [name for name in CAA_REQUIRED_FIELDS if name not in renamed.values()]
    if missing:
        raise SchemaError("caa_csv_changed_schema")
    rows = []
    for raw in reader:
        cleaned = {str(key or "").strip(): (value.strip() if isinstance(value, str) else value) for key, value in raw.items()}
        origin = str(cleaned.get("Origin Destination") or "").strip().casefold()
        if origin != "airport total":
            continue
        period = str(cleaned.get("Reporting Period") or "").strip()
        airport = str(cleaned.get("Reporting Airport") or "").strip()
        if not period or not airport:
            raise SchemaError("caa_csv_changed_schema")
        rows.append({
            "period": period,
            "airport": airport,
            "flights_matched": _number(cleaned.get("Number Flights Matched")),
            "flights_cancelled": _number(cleaned.get("Number Flights Cancelled")),
            "cancelled_percent": _number(cleaned.get("Flights Cancelled Percent")),
            "average_delay_minutes": _number(cleaned.get("Average Delay Minutes")),
            "run_date": str(cleaned.get("Run Date") or "").strip() or None,
        })
    return rows


def _caa_doc_matches(item: Mapping[str, Any], query: str) -> bool:
    needle = _query_tokens(query)
    if not needle or "punctuality" in needle or needle in {"current", "statistics"}:
        return True
    haystack = " ".join(str(item.get(key) or "") for key in ("label", "period")).casefold()
    return needle in haystack


def _caa_catalog_record(item: Mapping[str, Any], *, payload_hash: str) -> dict[str, Any]:
    period = item.get("period") or "unknown"
    return {
        "kind": "caa_punctuality_document",
        "external_id": f"caa-doc-{period}-{payload_sha256(item['url'])[:12]}",
        "evidence_type": "airline.delays",
        "title": item["label"],
        "text": item["label"],
        "published_at": period,
        "source_date": period,
        "geography": "GB",
        "url": item["url"],
        "page_url": item["page_url"],
        "metric_definition": (
            "UK CAA monthly flight punctuality publication covering selected UK airports. "
            "Published with lag and limited to UK-reporting scope."
        ),
        "raw_payload_hash": payload_hash,
        "period": period,
        "is_summary_csv": item.get("is_summary_csv", False),
    }


def _caa_airport_record(row: Mapping[str, Any], *, source_url: str, payload_hash: str) -> dict[str, Any]:
    return {
        "kind": "caa_airport_punctuality",
        "external_id": f"caa-punctuality-{row['period']}-{row['airport'].casefold().replace(' ', '-')}",
        "evidence_type": "airline.delays",
        "title": f"UK CAA punctuality {row['airport']} {row['period']}",
        "text": (
            f"{row['airport']} reported {row['flights_matched']} matched flights "
            f"and {row['cancelled_percent']}% cancellations in {row['period']}."
        ),
        "published_at": row.get("run_date") or row["period"],
        "source_date": row["period"],
        "geography": "GB",
        "url": source_url,
        "metric_definition": (
            "UK CAA punctuality matching summary: flights matched, cancellations and average delay "
            "minutes at selected UK airports. Cancelled flights are non-operation of a previously "
            "planned flight announced less than 24 hours before or after scheduled departure."
        ),
        "raw_payload_hash": payload_hash,
        "airport": row["airport"],
        "period": row["period"],
        "flights_matched": row["flights_matched"],
        "flights_cancelled": row["flights_cancelled"],
        "cancelled_percent": row["cancelled_percent"],
        "average_delay_minutes": row["average_delay_minutes"],
    }


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class OfficialAirlineConnector:
    """One reusable official-source adapter bound to a declared airline capability."""

    def __init__(
        self,
        capability: SourceCapability,
        *,
        fetcher: Fetcher | None = None,
    ):
        self.capability = capability
        self._fetch = fetcher or fetch_public

    async def _get(self, url: str) -> OfficialHttpResponse | RawConnectorResult:
        try:
            response = await self._fetch(url)
        except OfficialFetchError as exc:
            return _fetch_error(exc)
        if classify_http_status(response.status_code) is not None:
            return _as_http_error(response.status_code)
        return response

    async def preflight(self, credentials) -> RawConnectorResult:
        probe = self.capability.preflight
        request = SourceExecutionRequest(
            query=probe.query,
            geography=probe.geography,
            time_filter=probe.time_filter,
            limit=probe.limit,
            options=probe.options,
        )
        result = await self.search(request, credentials)
        if result.state is RawState.EMPTY:
            return RawConnectorResult(
                records=(),
                state=RawState.ERROR,
                error_category="preflight_known_positive_empty",
                source_observed_at=result.source_observed_at,
                metadata=result.metadata,
            )
        return result

    async def search(
        self, request: SourceExecutionRequest, credentials
    ) -> RawConnectorResult:
        return await self._run(request, hydrate=False)

    async def collect(
        self, request: SourceExecutionRequest, credentials
    ) -> RawConnectorResult:
        return await self._run(request, hydrate=True)

    async def _run(self, request: SourceExecutionRequest, *, hydrate: bool) -> RawConnectorResult:
        source_id = self.capability.source_id
        if source_id == "arc_sales":
            return await self._arc(request)
        if source_id == "eurocontrol":
            return await self._eurocontrol(request, hydrate=hydrate)
        if source_id == "uk_caa":
            return await self._caa(request, hydrate=hydrate)
        return _parser_error("unknown_airline_source")

    async def _arc(self, request: SourceExecutionRequest) -> RawConnectorResult:
        response = await self._get(ARC_CSV_URL)
        if isinstance(response, RawConnectorResult):
            return response
        try:
            rows = parse_arc_csv(response.text)
        except SchemaError:
            return _parser_error("arc_csv_changed_schema")
        payload_hash = payload_sha256(response.content.decode("utf-8", errors="replace"))
        matched = [
            _arc_record(row, source_url=response.url, payload_hash=payload_hash)
            for row in rows
            if _arc_matches(row, request.query)
        ]
        matched.sort(key=lambda item: str(item.get("source_date") or ""))
        selected = tuple(matched[-request.limit :])
        return _complete(
            selected,
            observed_at=response.observed_at,
            metadata={"url": response.url, "content_type": response.content_type},
        )

    async def _eurocontrol(
        self, request: SourceExecutionRequest, *, hydrate: bool
    ) -> RawConnectorResult:
        listing = await self._get(EUROCONTROL_SERIES_URL)
        if isinstance(listing, RawConnectorResult):
            return listing
        try:
            items = parse_eurocontrol_listing(listing.text)
        except SchemaError:
            return _parser_error("eurocontrol_listing_changed_schema")
        matched = [item for item in items if _eurocontrol_matches(item, request.query)]
        listing_hash = payload_sha256(listing.text)
        if not hydrate:
            records = tuple(
                _eurocontrol_record(item, payload_hash=listing_hash)
                for item in matched[: request.limit]
            )
            return _complete(
                records,
                observed_at=listing.observed_at,
                metadata={"url": listing.url},
            )
        records = []
        for item in matched[: request.limit]:
            page = await self._get(item["url"])
            if isinstance(page, RawConnectorResult):
                return page
            try:
                metrics = parse_eurocontrol_publication(page.text, page.url)
            except SchemaError:
                return _parser_error("eurocontrol_publication_changed_schema")
            records.append(
                _eurocontrol_record(
                    item,
                    payload_hash=payload_sha256(page.text),
                    metrics=metrics,
                )
            )
        return _complete(
            tuple(records),
            observed_at=listing.observed_at,
            metadata={"url": listing.url},
        )

    async def _caa(
        self, request: SourceExecutionRequest, *, hydrate: bool
    ) -> RawConnectorResult:
        index = await self._get(CAA_PUNCTUALITY_INDEX)
        if isinstance(index, RawConnectorResult):
            return index
        try:
            years = parse_caa_index(index.text)
        except SchemaError:
            return _parser_error("caa_index_changed_schema")
        year_url = urljoin(CAA_PUNCTUALITY_INDEX, f"{years[0]}/")
        year_page = await self._get(year_url)
        if isinstance(year_page, RawConnectorResult):
            return year_page
        try:
            documents = parse_caa_year_documents(year_page.text, year_page.url)
        except SchemaError:
            return _parser_error("caa_year_changed_schema")
        matched = [item for item in documents if _caa_doc_matches(item, request.query)]
        page_hash = payload_sha256(year_page.text)
        if not hydrate:
            records = tuple(
                _caa_catalog_record(item, payload_hash=page_hash)
                for item in matched[: request.limit]
            )
            return _complete(
                records,
                observed_at=year_page.observed_at,
                metadata={"url": year_page.url, "year": years[0]},
            )
        summaries = [item for item in matched if item.get("is_summary_csv")]
        if not summaries:
            return _complete(
                (),
                observed_at=year_page.observed_at,
                metadata={"url": year_page.url, "year": years[0]},
            )
        latest = max(summaries, key=lambda item: item.get("period") or "")
        csv_page = await self._get(latest["url"])
        if isinstance(csv_page, RawConnectorResult):
            return csv_page
        try:
            rows = parse_caa_summary_csv(csv_page.text)
        except SchemaError:
            return _parser_error("caa_csv_changed_schema")
        payload_hash = payload_sha256(csv_page.text)
        records = tuple(
            _caa_airport_record(row, source_url=csv_page.url, payload_hash=payload_hash)
            for row in rows[: request.limit]
        )
        return _complete(
            records,
            observed_at=csv_page.observed_at,
            metadata={"url": csv_page.url, "document": latest["label"]},
        )

    def normalize(self, record: Any, request: SourceExecutionRequest) -> NormalizedEvidence:
        if not isinstance(record, Mapping):
            raise TypeError("airline adapter requires mapping records")
        source_date = str(record.get("source_date") or "")
        url = record.get("url")
        metric_definition = record.get("metric_definition")
        payload_hash = record.get("raw_payload_hash")
        if not source_date or not url or not metric_definition or not payload_hash:
            raise ValueError("normalized airline evidence is missing required provenance")
        attributes = {
            "source_date": source_date,
            "geography": record.get("geography") or request.geography,
            "metric_definition": metric_definition,
            "url": url,
            "raw_payload_hash": payload_hash,
            "kind": record.get("kind"),
        }
        skip = {
            "kind",
            "external_id",
            "evidence_type",
            "title",
            "text",
            "published_at",
            "source_date",
            "geography",
            "url",
            "metric_definition",
            "raw_payload_hash",
        }
        for key, value in record.items():
            if key not in skip:
                attributes[key] = value
        return NormalizedEvidence(
            source_id=self.capability.source_id,
            external_id=str(record["external_id"]),
            evidence_type=str(record["evidence_type"]),
            observed_at=_now(),
            url=str(url),
            title=record.get("title"),
            text=record.get("text"),
            published_at=record.get("published_at"),
            geography=record.get("geography") or request.geography or None,
            attributes=attributes,
        )


def _free_cost() -> CostProfile:
    return CostProfile(
        kind="free",
        currency="USD",
        amount_per_request="0",
        notes="No API credential or per-request provider fee.",
    )


def build_arc_sales_connector(*, fetcher: Fetcher | None = None) -> OfficialAirlineConnector:
    return OfficialAirlineConnector(
        SourceCapability(
            source_id="arc_sales",
            display_name="ARC settled airline sales",
            data_kinds=frozenset({"airline.ticket_sales"}),
            geographies=("US",),
            time_range=TimeRangeCapability(
                notes="Monthly settled U.S. agency-channel sales; history is whatever the public CSV currently contains.",
            ),
            auth_mode=AuthMode.NONE,
            credential_ref=None,
            cost=_free_cost(),
            rate_limit=RateLimitPolicy(
                requests=None,
                window_seconds=None,
                concurrency=1,
                notes="monthly delta",
            ),
            limitations=(
                "Aggregate U.S. agency channel, not complete carrier-specific global bookings.",
            ),
            preflight=PreflightProbe(
                query="current totals",
                geography="US",
                time_filter="month",
                limit=1,
            ),
            priority=40,
        ),
        fetcher=fetcher,
    )


def build_eurocontrol_connector(*, fetcher: Fetcher | None = None) -> OfficialAirlineConnector:
    return OfficialAirlineConnector(
        SourceCapability(
            source_id="eurocontrol",
            display_name="EUROCONTROL aviation operations",
            data_kinds=frozenset({"airline.traffic", "airline.delays", "airline.capacity"}),
            geographies=("EUROPE",),
            time_range=TimeRangeCapability(
                notes="Weekly European Aviation Overview publications.",
            ),
            auth_mode=AuthMode.NONE,
            credential_ref=None,
            cost=_free_cost(),
            rate_limit=RateLimitPolicy(
                requests=None,
                window_seconds=None,
                concurrency=1,
                notes="weekly publication delta",
            ),
            limitations=(
                "Operational traffic is not ticket revenue; issuer cost attribution remains separate.",
            ),
            preflight=PreflightProbe(
                query="European Aviation Overview",
                geography="EUROPE",
                time_filter="week",
                limit=1,
            ),
            priority=41,
        ),
        fetcher=fetcher,
    )


def build_uk_caa_connector(*, fetcher: Fetcher | None = None) -> OfficialAirlineConnector:
    return OfficialAirlineConnector(
        SourceCapability(
            source_id="uk_caa",
            display_name="UK CAA airline statistics",
            data_kinds=frozenset({"airline.traffic", "airline.delays", "airline.capacity"}),
            geographies=("GB",),
            time_range=TimeRangeCapability(
                notes="Monthly punctuality and airline statistics; collect currently hydrates punctuality summaries.",
            ),
            auth_mode=AuthMode.NONE,
            credential_ref=None,
            cost=_free_cost(),
            rate_limit=RateLimitPolicy(
                requests=None,
                window_seconds=None,
                concurrency=1,
                notes="monthly publication delta",
            ),
            limitations=(
                "Published with lag and limited to UK-reporting scope.",
            ),
            preflight=PreflightProbe(
                query="punctuality statistics",
                geography="GB",
                time_filter="month",
                limit=1,
            ),
            priority=42,
        ),
        fetcher=fetcher,
    )


def register_airline_official_sources(catalogue, *, fetcher: Fetcher | None = None) -> tuple[str, ...]:
    """Register the free official airline sources that currently collect without credentials."""

    connectors = (
        build_arc_sales_connector(fetcher=fetcher),
        build_eurocontrol_connector(fetcher=fetcher),
        build_uk_caa_connector(fetcher=fetcher),
    )
    source_ids = []
    for connector in connectors:
        catalogue.register(connector)
        source_ids.append(connector.capability.source_id)
    return tuple(source_ids)
