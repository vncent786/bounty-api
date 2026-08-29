"""Zero-key company, filing, instrument, news and transcript research helpers."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import socket
import warnings
from datetime import date
from decimal import Decimal
from typing import Any, Iterable
from urllib.parse import quote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from apis.news_search import GOOGLE_NEWS_RSS, parse_google_news_rss
from social_scraper.investing.research_dossier import (
    VERIFIED_FACT_STATES,
    ReportedFact,
)


SEC_USER_AGENT = "Bounty investment research https://bountyapi.com"


def validate_public_https_url_syntax(url: str) -> str:
    text = str(url or "").strip()
    parsed = urlparse(text)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("source URL must use public HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("source URL credentials are not allowed")
    hostname = parsed.hostname.rstrip(".").casefold()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("private source host is not allowed")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("private or reserved source address is not allowed")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("invalid source URL port") from exc
    return text


async def _resolve_public_host(hostname: str, port: int) -> None:
    records = await asyncio.to_thread(
        socket.getaddrinfo,
        hostname,
        port,
        type=socket.SOCK_STREAM,
    )
    addresses = {
        str(record[4][0]).split("%", 1)[0]
        for record in records
        if record and record[4]
    }
    if not addresses:
        raise ValueError("source host did not resolve")
    for value in addresses:
        if not ipaddress.ip_address(value).is_global:
            raise ValueError("source host resolved to a private or reserved address")


async def _safe_public_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    max_redirects: int = 5,
) -> httpx.Response:
    current = validate_public_https_url_syntax(url)
    for _attempt in range(max(0, int(max_redirects)) + 1):
        parsed = urlparse(current)
        await _resolve_public_host(parsed.hostname or "", parsed.port or 443)
        response = await client.get(current, headers=headers, follow_redirects=False)
        if not response.is_redirect:
            return response
        location = response.headers.get("location")
        if not location:
            raise ValueError("redirect response omitted its destination")
        current = validate_public_https_url_syntax(urljoin(current, location))
    raise ValueError("source URL exceeded the redirect limit")


def free_provider_registry() -> list[dict[str, Any]]:
    """Describe the honest zero-cost federation; none is a complete global database."""
    return [
        {
            "provider": "GLEIF",
            "cost": "free",
            "role": "global legal-entity and reported parent relationship lookup",
            "coverage": "global LEI registrants; not every company has an LEI",
            "complete_global_filings": False,
            "url": "https://api.gleif.org/api/v1",
        },
        {
            "provider": "OpenFIGI",
            "cost": "free",
            "role": "global listed-instrument identifier mapping",
            "coverage": "broad instrument mapping; not company filings or fundamentals",
            "complete_global_filings": False,
            "url": "https://api.openfigi.com/v3",
        },
        {
            "provider": "SEC EDGAR",
            "cost": "free",
            "role": "US issuer and foreign-private-issuer filings",
            "coverage": "SEC registrants only",
            "complete_global_filings": False,
            "url": "https://www.sec.gov/edgar",
        },
        {
            "provider": "filings.xbrl.org",
            "cost": "free",
            "role": "ESEF filing index and API",
            "coverage": "participating European ESEF repositories; known country gaps",
            "complete_global_filings": False,
            "url": "https://filings.xbrl.org/api/filings",
        },
        {
            "provider": "Issuer investor relations",
            "cost": "free",
            "role": "issuer releases, presentations, webcasts and occasional transcripts",
            "coverage": "issuer-specific and inconsistent",
            "complete_global_filings": False,
            "url": None,
        },
        {
            "provider": "Finnhub free account (optional)",
            "cost": "free",
            "role": "single convenience API for global profiles, fundamentals and some transcripts",
            "coverage": "availability and endpoint entitlements vary; never authoritative alone",
            "complete_global_filings": False,
            "url": "https://finnhub.io/docs/api",
        },
    ]


def parse_gleif_resolution(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for item in payload.get("data") or []:
        attributes = item.get("attributes") or {}
        entity = attributes.get("entity") or {}
        name = (entity.get("legalName") or {}).get("name")
        if not item.get("id") or not name:
            continue
        results.append({
            "lei": str(item["id"]),
            "legal_name": str(name),
            "jurisdiction": entity.get("jurisdiction"),
            "entity_status": entity.get("status"),
        })
    return results


def parse_openfigi_resolution(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for job in payload or []:
        for item in job.get("data") or []:
            if not item.get("figi"):
                continue
            results.append({
                "figi": str(item["figi"]),
                "share_class_figi": item.get("shareClassFIGI"),
                "name": item.get("name"),
                "ticker": item.get("ticker"),
                "exchange_code": item.get("exchCode"),
                "security_type": item.get("securityType2") or item.get("securityType"),
            })
    return results


def select_sec_ticker(payload: dict[str, Any], ticker: str) -> dict[str, str] | None:
    normalized = str(ticker or "").strip().upper()
    if not normalized:
        return None
    matches = [
        value for value in payload.values()
        if str((value or {}).get("ticker") or "").strip().upper() == normalized
    ]
    if len(matches) != 1:
        return None
    value = matches[0]
    cik_number = int(value["cik_str"])
    return {
        "cik": f"{cik_number:010d}",
        "archive_cik": str(cik_number),
        "ticker": normalized,
        "title": str(value.get("title") or ""),
    }


def build_sec_filing_rows(submissions: dict[str, Any]) -> list[dict[str, Any]]:
    recent = ((submissions.get("filings") or {}).get("recent") or {})
    accessions = list(recent.get("accessionNumber") or [])
    rows = []
    fields = {
        "filing_date": "filingDate",
        "report_date": "reportDate",
        "acceptance_datetime": "acceptanceDateTime",
        "form": "form",
        "primary_document": "primaryDocument",
        "is_xbrl": "isXBRL",
        "is_inline_xbrl": "isInlineXBRL",
    }
    for index, accession in enumerate(accessions):
        row: dict[str, Any] = {"accession_number": accession}
        for output_name, source_name in fields.items():
            values = list(recent.get(source_name) or [])
            row[output_name] = values[index] if index < len(values) else None
        rows.append(row)
    return rows


def select_latest_sec_filing(
    rows: Iterable[dict[str, Any]],
    forms: Iterable[str],
    *,
    as_of: str,
) -> dict[str, Any] | None:
    allowed = {str(value).upper() for value in forms}
    cutoff = str(as_of)[:10]
    matches = [
        dict(row) for row in rows
        if str(row.get("form") or "").upper() in allowed
        and str(row.get("filing_date") or "")[:10] <= cutoff
        and row.get("accession_number")
        and row.get("primary_document")
    ]
    if not matches:
        return None
    return max(matches, key=lambda row: (
        str(row.get("report_date") or ""),
        str(row.get("acceptance_datetime") or ""),
        str(row.get("filing_date") or ""),
        str(row.get("accession_number") or ""),
    ))


def sec_filing_url(cik: str, filing: dict[str, Any]) -> str:
    archive_cik = str(int(str(cik)))
    accession = str(filing.get("accession_number") or "")
    primary_document = str(filing.get("primary_document") or "")
    if not accession or not primary_document:
        raise ValueError("SEC filing accession and primary document are required")
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{archive_cik}/{accession.replace('-', '')}/{primary_document}"
    )


def _duration_days(observation: dict[str, Any]) -> int:
    try:
        return (
            date.fromisoformat(str(observation.get("end")))
            - date.fromisoformat(str(observation.get("start")))
        ).days
    except (TypeError, ValueError):
        return -1


def extract_sec_revenue_fact(
    payload: dict[str, Any],
    filing: dict[str, Any],
    *,
    issuer_id: str,
    as_of: str,
    source_url: str,
) -> ReportedFact | None:
    all_facts = payload.get("facts") or {}
    accession = str(filing.get("accession_number") or "")
    report_date = str(filing.get("report_date") or "")
    form = str(filing.get("form") or "")
    cutoff = str(as_of)[:10]
    concept_sets = (
        ("us-gaap", (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        )),
        ("ifrs-full", (
            "Revenue",
            "RevenueFromContractsWithCustomers",
        )),
    )
    for namespace, concept_names in concept_sets:
        concepts = all_facts.get(namespace) or {}
        for concept_name in concept_names:
            concept = concepts.get(concept_name)
            if not isinstance(concept, dict):
                continue
            units = concept.get("units") or {}
            unit_names = sorted(units, key=lambda value: (value != "USD", value))
            for unit_name in unit_names:
                observations = [
                    dict(value) for value in (units.get(unit_name) or [])
                    if str(value.get("accn") or "") == accession
                    and str(value.get("filed") or "")[:10] <= cutoff
                    and str(value.get("form") or "") == form
                    and str(value.get("end") or "") == report_date
                    and value.get("val") is not None
                ]
                if not observations:
                    continue
                selected = max(observations, key=lambda value: (
                    str(value.get("end") or "") == report_date,
                    _duration_days(value),
                    str(value.get("filed") or ""),
                ))
                raw_value = str(selected["val"])
                digest = hashlib.sha256(
                    f"{issuer_id}|{namespace}|{concept_name}|{accession}|{raw_value}".encode("utf-8")
                ).hexdigest()[:24]
                label = str(concept.get("label") or "Revenue")
                description = str(concept.get("description") or "").strip()
                exact = (
                    f"{label}; {description}; accession {accession}; "
                    f"period {selected.get('start')} to {selected.get('end')}; "
                    f"value {raw_value} {unit_name}; source {source_url}"
                )
                return ReportedFact(
                    fact_id=f"fact:{digest}",
                    issuer_id=issuer_id,
                    metric="consolidated_revenue",
                    value=Decimal(raw_value),
                    unit=unit_name,
                    currency=unit_name,
                    period_start=selected.get("start"),
                    period_end=selected.get("end"),
                    scope_type="consolidated",
                    scope_name=None,
                    source_id=f"sec:{accession}",
                    source_locator=f"SEC CompanyFacts {namespace}:{concept_name}",
                    exact_source_text=exact,
                    verification_status="verified_primary",
                )
    return None


def select_exact_gleif(
    records: Iterable[dict[str, Any]], legal_name: str,
) -> list[dict[str, Any]]:
    normalize = lambda value: re.sub(r"[^a-z0-9]+", "", str(value).casefold())
    expected = normalize(legal_name)
    if not expected:
        return []
    return [
        dict(value) for value in records
        if normalize(value.get("legal_name")) == expected
    ]


async def resolve_gleif(
    legal_name: str, *, client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    owned = client is None
    client = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
    try:
        response = await client.get(
            "https://api.gleif.org/api/v1/lei-records",
            params={"filter[entity.legalName]": legal_name},
        )
        response.raise_for_status()
        return select_exact_gleif(parse_gleif_resolution(response.json()), legal_name)
    finally:
        if owned:
            await client.aclose()


async def resolve_openfigi(
    ticker: str,
    exchange_code: str,
    *,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    owned = client is None
    client = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key
    try:
        response = await client.post(
            "https://api.openfigi.com/v3/mapping",
            headers=headers,
            json=[{"idType": "TICKER", "idValue": ticker, "exchCode": exchange_code}],
        )
        response.raise_for_status()
        return parse_openfigi_resolution(response.json())
    finally:
        if owned:
            await client.aclose()


def html_to_text(html: str) -> str:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "lxml")
    for node in soup.select("script,style,noscript,nav,header,footer"):
        node.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def extract_transcript_text(html: str, *, source_domain: str = "") -> str:
    soup = BeautifulSoup(html, "lxml")
    for node in soup.select("script,style,noscript,nav,header,footer"):
        node.decompose()
    selectors = []
    if "fool.com" in source_domain.casefold():
        selectors.append(".article-body.transcript-content")
    selectors.extend(("[data-testid='article-content']", ".transcript-content", "main"))
    for selector in selectors:
        node = soup.select_one(selector)
        if node is not None:
            text = " ".join(node.get_text(" ", strip=True).split())
            if text:
                return text
    return " ".join(soup.get_text(" ", strip=True).split())


def extract_passages(
    text: str, keywords: Iterable[str], *, radius: int = 280,
) -> list[str]:
    normalized = " ".join(str(text).split())
    folded = normalized.casefold()
    passages: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        key = " ".join(str(keyword).split())
        if not key:
            continue
        position = folded.find(key.casefold())
        if position < 0:
            continue
        raw_start = max(0, position - radius)
        raw_end = min(len(normalized), position + len(key) + radius)
        sentence_start = normalized.rfind(". ", raw_start, position)
        start = sentence_start + 2 if sentence_start >= raw_start else raw_start
        sentence_end = normalized.find(". ", position + len(key), raw_end)
        end = sentence_end + 1 if sentence_end >= 0 else raw_end
        passage = normalized[start:end].strip()
        dedupe_key = passage.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        passages.append(passage)
    return passages


def extract_speaker_findings(
    text: str, keywords: Iterable[str], *, radius: int = 280,
) -> list[dict[str, str | None]]:
    """Attach the nearest preceding transcript speaker to bounded passages."""
    normalized = " ".join(str(text).split())
    folded = normalized.casefold()
    speaker_matches = list(re.finditer(
        r"(?:^|[.!?]\s)([A-Z][A-Za-z .'-]{1,60}):\s",
        normalized,
    ))
    results: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for keyword in keywords:
        key = " ".join(str(keyword).split())
        position = folded.find(key.casefold()) if key else -1
        if position < 0:
            continue
        passages = extract_passages(normalized, (key,), radius=radius)
        if not passages:
            continue
        passage = passages[0]
        speaker = None
        for match in speaker_matches:
            if match.end() > position:
                break
            if position - match.end() <= 5000:
                speaker = match.group(1).strip()
        if speaker and passage.casefold().startswith(f"{speaker}:".casefold()):
            passage = passage[len(speaker) + 1:].strip()
        dedupe_key = passage.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        results.append({"keyword": key, "speaker": speaker, "text": passage})
    return results


def extract_regex_fact(
    text: str,
    *,
    pattern: str,
    metric: str,
    issuer_id: str,
    source_id: str,
    period_start: str | None,
    period_end: str | None,
    currency: str | None,
    unit: str,
    scope_type: str,
    source_locator: str,
    verification_status: str,
    scope_name: str | None = None,
    flags: int = re.IGNORECASE,
) -> ReportedFact:
    if verification_status not in VERIFIED_FACT_STATES:
        raise ValueError("reported fact requires a verified source status")
    match = re.search(pattern, text, flags)
    if match is None:
        raise ValueError(f"reported fact not found: {metric}")
    raw_value = match.group(1).replace(",", "").strip()
    value = Decimal(raw_value)
    start = max(0, match.start() - 120)
    end = min(len(text), match.end() + 220)
    exact = " ".join(text[start:end].split())
    digest = hashlib.sha256(
        f"{issuer_id}|{metric}|{period_end}|{source_id}|{raw_value}".encode("utf-8")
    ).hexdigest()[:24]
    return ReportedFact(
        fact_id=f"fact:{digest}",
        issuer_id=issuer_id,
        metric=metric,
        value=value,
        unit=unit,
        currency=currency,
        period_start=period_start,
        period_end=period_end,
        scope_type=scope_type,
        scope_name=scope_name,
        source_id=source_id,
        source_locator=source_locator,
        exact_source_text=exact,
        verification_status=verification_status,
    )


async def fetch_sec_company_sources(
    ticker: str,
    *,
    as_of: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Discover official SEC filings and an accession-matched revenue baseline."""
    normalized = str(ticker or "").strip().upper()
    if not normalized:
        return {
            "status": "not_applicable",
            "ticker": None,
            "sources": [],
            "documents": [],
            "reported_facts": [],
            "limitations": ["No ticker was supplied for SEC discovery."],
        }
    owned = client is None
    client = client or httpx.AsyncClient(timeout=45, follow_redirects=True)
    try:
        headers = {"User-Agent": SEC_USER_AGENT}
        tickers_response = await client.get(
            "https://www.sec.gov/files/company_tickers.json", headers=headers
        )
        tickers_response.raise_for_status()
        company = select_sec_ticker(tickers_response.json(), normalized)
        if company is None:
            return {
                "status": "not_found",
                "ticker": normalized,
                "sources": [],
                "documents": [],
                "reported_facts": [],
                "limitations": ["Ticker was not found in the SEC ticker map."],
            }
        cik = company["cik"]
        submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        submissions_response, facts_response = await asyncio.gather(
            client.get(submissions_url, headers=headers),
            client.get(facts_url, headers=headers),
        )
        submissions_response.raise_for_status()
        facts_response.raise_for_status()
        submissions = submissions_response.json()
        if normalized not in {
            str(value).upper() for value in submissions.get("tickers") or []
        }:
            return {
                "status": "conflict",
                "ticker": normalized,
                "company": company,
                "sources": [],
                "documents": [],
                "reported_facts": [],
                "limitations": ["SEC submissions did not confirm the requested ticker."],
            }
        rows = build_sec_filing_rows(submissions)
        annual = select_latest_sec_filing(rows, ("10-K", "20-F"), as_of=as_of)
        interim = select_latest_sec_filing(rows, ("10-Q", "6-K"), as_of=as_of)
        filings = [value for value in (annual, interim) if value is not None]
        documents = []
        sources = [{
            "source_type": "sec_submissions",
            "status": "complete",
            "url": submissions_url,
            "ticker": normalized,
            "cik": cik,
        }, {
            "source_type": "sec_companyfacts",
            "status": "complete",
            "url": facts_url,
            "ticker": normalized,
            "cik": cik,
        }]
        for filing in filings:
            url = sec_filing_url(cik, filing)
            document = await fetch_text_document(
                url,
                source_type="regulator_filing",
                client=client,
                user_agent=SEC_USER_AGENT,
            )
            document["filing"] = filing
            documents.append(document)
            sources.append({
                key: value for key, value in document.items()
                if key != "text"
            })
        revenue_fact = None
        if annual is not None:
            revenue_fact = extract_sec_revenue_fact(
                facts_response.json(),
                annual,
                issuer_id=f"sec-cik:{cik}",
                as_of=as_of,
                source_url=sec_filing_url(cik, annual),
            )
        limitations = []
        if annual is None:
            limitations.append("No 10-K or 20-F was available by the research cutoff.")
        if revenue_fact is None:
            limitations.append("Accession-matched consolidated revenue was unavailable.")
        if interim and interim.get("form") == "6-K":
            limitations.append("The latest 6-K is not assumed to be a standardized quarterly report.")
        if any(document.get("status") != "complete" for document in documents):
            limitations.append("At least one discovered SEC filing could not be retrieved.")
        annual_document_complete = bool(
            annual
            and any(
                document.get("status") == "complete"
                and (document.get("filing") or {}).get("accession_number")
                    == annual.get("accession_number")
                for document in documents
            )
        )
        if not annual_document_complete:
            limitations.append("The primary annual SEC filing document was unavailable.")
        return {
            "status": "complete" if annual_document_complete else "partial",
            "ticker": normalized,
            "company": company,
            "annual_filing": annual,
            "interim_filing": interim,
            "sources": sources,
            "documents": documents,
            "reported_facts": [revenue_fact] if revenue_fact is not None else [],
            "limitations": limitations,
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "ticker": normalized,
            "sources": [],
            "documents": [],
            "reported_facts": [],
            "error_category": type(exc).__name__,
            "limitations": ["SEC discovery failed; failure is not absence of disclosure."],
        }
    finally:
        if owned:
            await client.aclose()


async def fetch_text_document(
    url: str,
    *,
    source_type: str,
    client: httpx.AsyncClient | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    owned = client is None
    client = client or httpx.AsyncClient(timeout=45, follow_redirects=False)
    try:
        response = await _safe_public_get(
            client,
            url,
            headers={"User-Agent": user_agent or SEC_USER_AGENT},
        )
        response.raise_for_status()
        raw = response.content
        content_type = response.headers.get("content-type", "")
        text = html_to_text(response.text) if "html" in content_type or b"<html" in raw[:500].lower() else response.text
        return {
            "url": str(response.url),
            "requested_url": url,
            "source_type": source_type,
            "status": "complete",
            "http_status": response.status_code,
            "retrieved_at": response.headers.get("date"),
            "content_sha256": hashlib.sha256(raw).hexdigest(),
            "text": text,
        }
    except Exception as exc:
        return {
            "url": url,
            "requested_url": url,
            "source_type": source_type,
            "status": "unavailable",
            "http_status": getattr(getattr(exc, "response", None), "status_code", None),
            "error_category": type(exc).__name__,
            "text": "",
        }
    finally:
        if owned:
            await client.aclose()


async def fetch_finnhub_transcript_findings(
    ticker: str,
    *,
    api_key: str | None,
    keywords: Iterable[str],
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Use a user-supplied Finnhub free-account key when the endpoint is entitled."""
    if not api_key:
        return {
            "status": "unavailable",
            "source_type": "finnhub_transcript",
            "url": None,
            "passages": [],
            "findings": [],
            "error_category": "api_key_not_supplied",
            "limitations": ["Finnhub transcript access requires a user-supplied API key."],
        }
    owned = client is None
    client = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
    try:
        list_response = await client.get(
            "https://finnhub.io/api/v1/stock/transcripts/list",
            params={"symbol": str(ticker).upper(), "token": api_key},
        )
        list_response.raise_for_status()
        body = list_response.json()
        rows = list(body.get("transcripts") or []) if isinstance(body, dict) else []
        rows = [value for value in rows if value.get("id")]
        if not rows:
            return {
                "status": "empty",
                "source_type": "finnhub_transcript",
                "url": "https://finnhub.io/api/v1/stock/transcripts/list",
                "passages": [],
                "findings": [],
                "limitations": ["Finnhub returned no transcript metadata for this symbol."],
            }
        selected = max(rows, key=lambda value: (
            int(value.get("year") or 0),
            int(value.get("quarter") or 0),
            str(value.get("time") or ""),
        ))
        transcript_response = await client.get(
            "https://finnhub.io/api/v1/stock/transcripts",
            params={"id": selected["id"], "token": api_key},
        )
        transcript_response.raise_for_status()
        transcript_payload = transcript_response.json()
        segments = (
            list(transcript_payload.get("transcript") or [])
            if isinstance(transcript_payload, dict)
            else []
        )
        text = " ".join(
            f"{value.get('name') or value.get('speaker') or 'Speaker'}: "
            f"{value.get('speech') or value.get('text') or ''}"
            for value in segments
            if value.get("speech") or value.get("text")
        )
        keyword_values = tuple(keywords)
        return {
            "status": "complete" if text else "partial",
            "source_type": "finnhub_transcript",
            "source_name": "Finnhub earnings-call transcript",
            "url": "https://finnhub.io/api/v1/stock/transcripts",
            "transcript_id": selected["id"],
            "passages": extract_passages(text, keyword_values),
            "findings": extract_speaker_findings(text, keyword_values),
            "limitations": [
                "Finnhub is a convenience transcript source; critical claims require issuer verification."
            ],
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "source_type": "finnhub_transcript",
            "url": "https://finnhub.io/docs/api/earnings-call-transcripts-api",
            "passages": [],
            "findings": [],
            "error_category": type(exc).__name__,
            "limitations": [
                "Finnhub transcript access failed or was not included in the supplied free account."
            ],
        }
    finally:
        if owned:
            await client.aclose()


async def fetch_transcript_findings(
    url: str,
    *,
    source_name: str,
    source_type: str,
    keywords: Iterable[str],
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    owned = client is None
    client = client or httpx.AsyncClient(timeout=45, follow_redirects=False)
    try:
        response = await _safe_public_get(
            client,
            url,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        raw = response.content
        domain = response.url.host or ""
        text = extract_transcript_text(response.text, source_domain=domain)
        keyword_values = tuple(keywords)
        passages = extract_passages(text, keyword_values)
        findings = extract_speaker_findings(text, keyword_values)
        return {
            "source_name": source_name,
            "source_type": source_type,
            "url": str(response.url),
            "status": "complete" if text else "partial",
            "content_sha256": hashlib.sha256(raw).hexdigest(),
            "passages": passages,
            "findings": findings,
            "limitations": [
                "Secondary transcript text must be reverified against official audio before relying on a critical quote."
            ] if source_type != "official_transcript" else [],
        }
    except Exception as exc:
        return {
            "source_name": source_name,
            "source_type": source_type,
            "url": url,
            "status": "unavailable",
            "error_category": type(exc).__name__,
            "passages": [],
            "findings": [],
            "limitations": ["Transcript source could not be retrieved."],
        }
    finally:
        if owned:
            await client.aclose()


async def check_public_news_implication(
    implication: str,
    *,
    client: httpx.AsyncClient | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    owned = client is None
    client = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
    url = GOOGLE_NEWS_RSS.format(query=quote(implication))
    try:
        response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        articles = parse_google_news_rss(response.text)[: max(1, int(limit))]
        return {
            "implication": implication,
            "status": "complete",
            "checked_source": "Google News RSS",
            "coverage_class": "sampled_public_news",
            "articles": [{
                "title": item.title,
                "url": item.link,
                "source": item.source,
                "published": item.published,
            } for item in articles],
            "limitations": [
                "Keyword/news coverage is not complete sell-side or consensus coverage.",
                "A topic mention does not prove that the financial implication was discussed.",
            ],
        }
    except Exception as exc:
        return {
            "implication": implication,
            "status": "unavailable",
            "checked_source": "Google News RSS",
            "coverage_class": "unavailable",
            "articles": [],
            "error_category": type(exc).__name__,
            "limitations": ["News-source failure cannot be interpreted as silence."],
        }
    finally:
        if owned:
            await client.aclose()
