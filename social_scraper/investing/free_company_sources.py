"""Zero-key company, filing, instrument, news and transcript research helpers."""

from __future__ import annotations

import hashlib
import re
import warnings
from decimal import Decimal
from typing import Any, Iterable
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from apis.news_search import GOOGLE_NEWS_RSS, parse_google_news_rss
from social_scraper.investing.research_dossier import (
    VERIFIED_FACT_STATES,
    ReportedFact,
)


SEC_USER_AGENT = "Bounty investment research https://bountyapi.com"


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
        return parse_gleif_resolution(response.json())
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


async def fetch_text_document(
    url: str,
    *,
    source_type: str,
    client: httpx.AsyncClient | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    owned = client is None
    client = client or httpx.AsyncClient(timeout=45, follow_redirects=True)
    try:
        response = await client.get(
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


async def fetch_transcript_findings(
    url: str,
    *,
    source_name: str,
    source_type: str,
    keywords: Iterable[str],
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    owned = client is None
    client = client or httpx.AsyncClient(timeout=45, follow_redirects=True)
    try:
        response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
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
