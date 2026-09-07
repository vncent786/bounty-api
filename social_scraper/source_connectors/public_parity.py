"""Shared public-source connector for issuer/news information-parity checks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import email.utils
import hashlib
import html
import io
import ipaddress
import json
import re
import socket
from typing import Any, Awaitable, Callable, Mapping, Sequence
from urllib.parse import quote, urljoin, urlparse
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

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


FetchPublic = Callable[..., Awaitable[OfficialHttpResponse]]
HostResolver = Callable[[str], Sequence[str]]
_ALLOWED_LANES = {
    "official_ir",
    "regulator_filings",
    "earnings_calls",
    "official_product_context",
    "qualifying_business_news",
    "sell_side_public_mentions",
}


def _system_resolver(host: str) -> Sequence[str]:
    return tuple({
        str(row[4][0])
        for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    })


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not any((
        address.is_private,
        address.is_loopback,
        address.is_link_local,
        address.is_multicast,
        address.is_reserved,
        address.is_unspecified,
    ))


def _safe_https_url(value: Any, resolver: HostResolver) -> str | None:
    text = str(value or "").strip()
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    host = parsed.hostname.casefold().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return None
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = list(resolver(host))
        except OSError:
            return None
    if not addresses or any(not _is_public_address(address) for address in addresses):
        return None
    return text


def _parse_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return email.utils.parsedate_to_datetime(text).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(
            timezone.utc
        ).isoformat()
    except ValueError:
        return text[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text[:10]) else None


def _extract_text(response: OfficialHttpResponse) -> tuple[str, str | None, str | None]:
    if "pdf" in response.content_type or response.url.casefold().endswith(".pdf"):
        try:
            from pypdf import PdfReader

            text = "\n".join(
                page.extract_text() or "" for page in PdfReader(io.BytesIO(response.content)).pages
            )
            return text, None, None
        except Exception as exc:  # PDF stays retrieved but its text gap is explicit.
            return "", None, f"pdf_text_extraction_{type(exc).__name__.casefold()}"
    soup = BeautifulSoup(response.content, "html.parser")
    for node in soup(("script", "style", "noscript", "template")):
        node.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else None
    return soup.get_text(" ", strip=True), title, None


def _matching_attributes(
    text: str,
    identity_terms: list[str],
    economic_terms: list[str],
) -> dict[str, Any]:
    unescaped = html.unescape(text)
    folded = unescaped.casefold()
    identity_matches = [term for term in identity_terms if term.casefold() in folded]
    economic_matches = [term for term in economic_terms if term.casefold() in folded]
    exact_snippets = []
    if identity_terms and len(identity_matches) == len(identity_terms) and economic_matches:
        anchor = identity_terms[0].casefold()
        passages = re.split(r"(?<=[.!?])\s+|\n+", unescaped)
        for passage in passages:
            passage_folded = passage.casefold()
            start = 0
            while len(exact_snippets) < 5:
                at = passage_folded.find(anchor, start)
                if at < 0:
                    break
                window_start = max(0, at - 400)
                window_end = min(len(passage_folded), at + len(anchor) + 400)
                window = passage_folded[window_start:window_end]
                if (
                    all(term.casefold() in window for term in identity_terms)
                    and any(term.casefold() in window for term in economic_terms)
                ):
                    exact_snippets.append(" ".join(passage[window_start:window_end].split()))
                start = at + len(anchor)
            if len(exact_snippets) >= 5:
                break
    return {
        "identity_terms_required": identity_terms,
        "identity_terms_matched": identity_matches,
        "economic_terms_matched": economic_matches,
        "exact_implication_match": bool(exact_snippets),
        "exact_implication_snippets": exact_snippets,
    }


class PublicParityConnector:
    """Fetch public IR, regulator, call, news and sell-side-discovery lanes.

    It deliberately has no paid or private research route. Search titles are
    discovery context until a direct article is fetched and reviewed.
    """

    def __init__(
        self,
        *,
        canary_url: str,
        fetcher: FetchPublic = fetch_public,
        resolver: HostResolver | None = None,
    ) -> None:
        self._resolver = resolver or (
            _system_resolver
            if fetcher is fetch_public
            else lambda _host: ("93.184.216.34",)
        )
        safe_canary = _safe_https_url(canary_url, self._resolver)
        if not safe_canary:
            raise ValueError("public parity canary must be a safe HTTPS URL")
        self.canary_url = safe_canary
        self._fetch = fetcher
        self.capability = SourceCapability(
            source_id="public_information_parity",
            display_name="Public issuer, filing, call, news and sell-side checks",
            data_kinds=frozenset({"public_information_parity"}),
            geographies=("*",),
            time_range=TimeRangeCapability(
                max_lookback_days=3650,
                notes="Public pages and feeds only; source-specific history applies.",
            ),
            auth_mode=AuthMode.NONE,
            credential_ref=None,
            cost=CostProfile(
                kind="free",
                currency="USD",
                amount_per_request="0",
                notes="Public HTTPS only.",
            ),
            rate_limit=RateLimitPolicy(
                requests=None,
                window_seconds=None,
                concurrency=1,
                notes="Bounded serial public checks with shared retries and receipts.",
            ),
            limitations=(
                "Paywalled and private research is not fetched or represented as checked.",
                "News-feed titles remain discovery context until a direct article is verified.",
            ),
            preflight=PreflightProbe(
                query=self.canary_url,
                geography="",
                time_filter="current",
                limit=1,
            ),
        )

    async def _one(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> OfficialHttpResponse | RawConnectorResult:
        safe = _safe_https_url(url, self._resolver)
        if not safe:
            return RawConnectorResult(
                records=(), state=RawState.ERROR, error_category="unsafe_source_url"
            )
        for _redirect_count in range(6):
            try:
                response = await self._fetch(
                    safe,
                    timeout=45.0,
                    headers=headers,
                    follow_redirects=False,
                )
            except OfficialFetchError as exc:
                return RawConnectorResult(
                    records=(), state=RawState.ERROR, error_category=exc.category
                )
            if 300 <= response.status_code < 400:
                location = response.headers.get("location")
                if not location:
                    return RawConnectorResult(
                        records=(),
                        state=RawState.ERROR,
                        error_category="redirect_without_location",
                    )
                redirected = _safe_https_url(
                    urljoin(response.url, str(location)), self._resolver
                )
                if not redirected:
                    return RawConnectorResult(
                        records=(),
                        state=RawState.ERROR,
                        error_category="unsafe_redirect_url",
                    )
                safe = redirected
                continue
            break
        else:
            return RawConnectorResult(
                records=(), state=RawState.ERROR, error_category="too_many_redirects"
            )
        classified = classify_http_status(response.status_code)
        if classified:
            raw_state, category = classified
            return RawConnectorResult(
                records=(), state=RawState(raw_state), error_category=category
            )
        return response

    async def preflight(self, credentials: Any) -> RawConnectorResult:
        result = await self._one(self.canary_url)
        if isinstance(result, RawConnectorResult):
            return result
        return RawConnectorResult(
            records=({
                "external_id": "public-parity-canary",
                "lane": "preflight",
                "url": result.url,
                "title": "Public information parity canary",
                "observed_at": result.observed_at,
                "retrieved": True,
                "exact_implication_match": False,
            },),
            state=RawState.COMPLETE,
            source_observed_at=result.observed_at,
        )

    async def _document_records(
        self,
        request: SourceExecutionRequest,
    ) -> RawConnectorResult:
        lane = str(request.options.get("lane") or "")
        sources = request.options.get("sources")
        if lane not in _ALLOWED_LANES or not isinstance(sources, list):
            return RawConnectorResult(
                records=(), state=RawState.ERROR, error_category="invalid_lane_request"
            )
        identity_terms = [str(value) for value in request.options.get("identity_terms") or []]
        economic_terms = [str(value) for value in request.options.get("economic_terms") or []]
        records = []
        failures = []
        for source in sources:
            if not isinstance(source, Mapping):
                failures.append("invalid_source_definition")
                continue
            url = _safe_https_url(source.get("url"), self._resolver)
            if not url:
                return RawConnectorResult(
                    records=(), state=RawState.ERROR, error_category="unsafe_source_url"
                )
            result = await self._one(url)
            if isinstance(result, RawConnectorResult):
                failures.append(result.error_category or result.state.value)
                continue
            text, title, extraction_error = _extract_text(result)
            matches = _matching_attributes(text, identity_terms, economic_terms)
            excerpt = re.sub(r"\s+", " ", text).strip()[:600] or None
            records.append({
                "external_id": str(source.get("key") or hashlib.sha256(url.encode()).hexdigest()),
                "lane": lane,
                "url": result.url,
                "title": source.get("event_name") or title or source.get("key"),
                "source_class": source.get("source_class") or lane,
                "observed_at": result.observed_at,
                "published_at": _parse_date(source.get("event_date")),
                "event_date": source.get("event_date"),
                "event_name": source.get("event_name"),
                "retrieved": True,
                "http_status": result.status_code,
                "content_length": len(result.content),
                "content_sha256": hashlib.sha256(result.content).hexdigest(),
                "text_excerpt": excerpt,
                "text_extraction_error": extraction_error,
                **matches,
            })
        if records and failures:
            return RawConnectorResult(
                records=tuple(records),
                state=RawState.BOUNDED_PARTIAL,
                error_category="partial_source_failure",
                metadata={"failures": failures},
            )
        if records:
            return RawConnectorResult(records=tuple(records), state=RawState.COMPLETE)
        return RawConnectorResult(
            records=(),
            state=RawState.ERROR,
            error_category=failures[0] if failures else "source_set_empty",
        )

    async def _sec_records(self, request: SourceExecutionRequest) -> RawConnectorResult:
        url = _safe_https_url(request.options.get("submissions_url"), self._resolver)
        cik = re.sub(r"\D", "", str(request.options.get("sec_cik") or ""))
        if not url or not cik:
            return RawConnectorResult(
                records=(), state=RawState.ERROR, error_category="invalid_sec_request"
            )
        sec_headers = {
            "User-Agent": "Bounty Research https://bountyapi.com",
            "Accept": "application/json, text/html, */*",
        }
        result = await self._one(url, headers=sec_headers)
        if isinstance(result, RawConnectorResult):
            return result
        try:
            payload = json.loads(result.text)
            recent = payload.get("filings", {}).get("recent", {})
        except (json.JSONDecodeError, AttributeError):
            return RawConnectorResult(
                records=(), state=RawState.PARSER_ERROR, error_category="sec_json_parse"
            )
        identity_terms = [str(value) for value in request.options.get("identity_terms") or []]
        economic_terms = [str(value) for value in request.options.get("economic_terms") or []]
        allowed_forms = {str(value) for value in request.options.get("recent_forms") or []}
        lookback = int(request.options.get("window_days") or 14)
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=max(1, lookback) + 2)
        sources = [{
            "key": "sec_submissions_current",
            "url": result.url,
            "source_class": "sec_official_metadata",
            "event_date": None,
            "payload": result,
            "form": "SUBMISSIONS",
            "accession_number": None,
        }]
        for filing_date, form, accession, primary in zip(
            recent.get("filingDate", []),
            recent.get("form", []),
            recent.get("accessionNumber", []),
            recent.get("primaryDocument", []),
        ):
            if allowed_forms and form not in allowed_forms:
                continue
            try:
                if datetime.fromisoformat(str(filing_date)).date() < cutoff:
                    continue
            except ValueError:
                continue
            compact = str(accession).replace("-", "")
            filing_url = _safe_https_url(
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{compact}/{primary}",
                self._resolver,
            )
            if filing_url:
                sources.append({
                    "key": f"sec_{accession}_{primary}",
                    "url": filing_url,
                    "source_class": "sec_official_filing",
                    "event_date": filing_date,
                    "form": form,
                    "accession_number": accession,
                })
        records = []
        failures = []
        for source in sources:
            response = source.pop("payload", None)
            if response is None:
                response = await self._one(source["url"], headers=sec_headers)
            if isinstance(response, RawConnectorResult):
                failures.append(response.error_category or response.state.value)
                continue
            text, title, extraction_error = _extract_text(response)
            matches = _matching_attributes(text, identity_terms, economic_terms)
            records.append({
                "external_id": source["key"],
                "lane": "regulator_filings",
                "url": response.url,
                "title": title or source.get("form"),
                "source_class": source["source_class"],
                "observed_at": response.observed_at,
                "published_at": _parse_date(source.get("event_date")),
                "event_date": source.get("event_date"),
                "retrieved": True,
                "http_status": response.status_code,
                "content_length": len(response.content),
                "content_sha256": hashlib.sha256(response.content).hexdigest(),
                "text_excerpt": re.sub(r"\s+", " ", text).strip()[:600] or None,
                "text_extraction_error": extraction_error,
                "form": source.get("form"),
                "accession_number": source.get("accession_number"),
                **matches,
            })
        if records and failures:
            return RawConnectorResult(
                records=tuple(records),
                state=RawState.BOUNDED_PARTIAL,
                error_category="partial_source_failure",
                metadata={"failures": failures},
            )
        if records:
            return RawConnectorResult(records=tuple(records), state=RawState.COMPLETE)
        return RawConnectorResult(
            records=(), state=RawState.ERROR, error_category=failures[0] if failures else "sec_empty"
        )

    def _rss_records(
        self,
        response: OfficialHttpResponse,
        query: str,
        lane: str,
    ) -> list[dict[str, Any]]:
        root = ET.fromstring(response.content)
        records = []
        for item in root.findall(".//item"):
            def value(name: str) -> str | None:
                node = item.find(name)
                return (node.text or "").strip() if node is not None and node.text else None

            source = item.find("source")
            outlet = (source.text or "").strip() if source is not None and source.text else None
            url = _safe_https_url(value("link"), self._resolver)
            title = value("title")
            if not url:
                continue
            records.append({
                "external_id": hashlib.sha256(f"{lane}|{url}|{title}".encode()).hexdigest(),
                "lane": lane,
                "url": url,
                "title": title,
                "source_class": "public_news_discovery" if lane == "qualifying_business_news" else "public_sell_side_discovery",
                "observed_at": response.observed_at,
                "published_at": _parse_date(value("pubDate")),
                "outlet": outlet,
                "retrieved": True,
                "discovery_query": query,
                "direct_article_verified": False,
                "exact_implication_match": False,
                "review_status": "requires_direct_article_review",
            })
        return records

    async def _rss_search(self, request: SourceExecutionRequest) -> RawConnectorResult:
        lane = str(request.options.get("lane") or "")
        if lane not in {"qualifying_business_news", "sell_side_public_mentions"}:
            return RawConnectorResult(
                records=(), state=RawState.ERROR, error_category="invalid_news_lane"
            )
        queries = request.options.get("rss_queries")
        if not isinstance(queries, list) or not queries:
            queries = [request.query] if request.query else []
        fixed_url = _safe_https_url(request.options.get("rss_url"), self._resolver)
        records = []
        failures = []
        for query in queries:
            url = fixed_url or (
                "https://news.google.com/rss/search?q="
                + quote(str(query))
                + "&hl=en-US&gl=US&ceid=US:en"
            )
            result = await self._one(url)
            if isinstance(result, RawConnectorResult):
                failures.append(result.error_category or result.state.value)
                continue
            try:
                records.extend(self._rss_records(result, str(query), lane))
            except ET.ParseError:
                failures.append("rss_parse_error")
        unique = {
            (row["url"], row.get("title")): row for row in records
        }
        records = list(unique.values())
        if records and failures:
            return RawConnectorResult(
                records=tuple(records),
                state=RawState.BOUNDED_PARTIAL,
                error_category="partial_source_failure",
                metadata={"failures": failures},
            )
        if records:
            return RawConnectorResult(records=tuple(records), state=RawState.COMPLETE)
        if failures:
            return RawConnectorResult(
                records=(), state=RawState.ERROR, error_category=failures[0]
            )
        return RawConnectorResult(records=(), state=RawState.EMPTY)

    async def search(
        self, request: SourceExecutionRequest, credentials: Any
    ) -> RawConnectorResult:
        return await self._rss_search(request)

    async def collect(
        self, request: SourceExecutionRequest, credentials: Any
    ) -> RawConnectorResult:
        lane = str(request.options.get("lane") or "")
        if lane == "regulator_filings":
            return await self._sec_records(request)
        return await self._document_records(request)

    def normalize(
        self, record: Any, request: SourceExecutionRequest
    ) -> NormalizedEvidence:
        if not isinstance(record, Mapping):
            raise TypeError("public parity records must be mappings")
        attributes = {
            str(key): value
            for key, value in record.items()
            if key not in {
                "external_id", "url", "title", "text_excerpt", "observed_at", "published_at"
            }
        }
        return NormalizedEvidence(
            source_id=self.capability.source_id,
            external_id=str(record.get("external_id") or ""),
            evidence_type="public_information_parity",
            observed_at=str(record.get("observed_at") or ""),
            url=record.get("url"),
            title=record.get("title"),
            text=record.get("text_excerpt"),
            published_at=record.get("published_at"),
            geography=request.geography or None,
            attributes=attributes,
        )
