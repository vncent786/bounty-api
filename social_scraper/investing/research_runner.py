"""Generic free-source investment research runner."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Mapping

import httpx

from social_scraper.investing.free_company_sources import (
    check_public_news_implication,
    extract_passages,
    fetch_sec_company_sources,
    fetch_finnhub_transcript_findings,
    fetch_text_document,
    fetch_transcript_findings,
    resolve_gleif,
    resolve_openfigi,
)
from social_scraper.investing.generic_dossier import build_generic_dossier
from social_scraper.investing.research_dossier import RangeAssumption
from social_scraper.investing.research_store import InvestmentResearchStore


SourceCollector = Callable[
    [Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]],
    Awaitable[dict[str, Any]],
]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal assumption: {value}") from exc


def parse_assumptions(payload: Mapping[str, Any] | None) -> dict[str, RangeAssumption]:
    results: dict[str, RangeAssumption] = {}
    for name, value in dict(payload or {}).items():
        if not isinstance(value, Mapping):
            continue
        results[str(name)] = RangeAssumption(
            name=str(name),
            low=_decimal_or_none(value.get("low")),
            base=_decimal_or_none(value.get("base")),
            high=_decimal_or_none(value.get("high")),
            unit=str(value.get("unit") or ""),
            provenance_kind="analyst_assumption",
            rationale=str(value.get("rationale") or "User-supplied explicit assumption"),
            source_ref=str(value.get("source_ref") or "") or None,
        )
    return results


async def collect_generic_sources(
    target: Mapping[str, Any],
    handoff: Mapping[str, Any],
    options: Mapping[str, Any],
) -> dict[str, Any]:
    company_name = str(target.get("company_name") or "").strip()
    ticker = str(target.get("ticker") or "").strip().upper()
    exchange_code = str(target.get("exchange_code") or "US").strip().upper()
    as_of = str(options.get("as_of") or datetime.now(timezone.utc).date().isoformat())
    decision = dict(handoff.get("decision") or {})
    company_key = "".join(character for character in company_name.casefold() if character.isalnum())
    anchor_terms = []
    for value in decision.get("anchor_terms") or []:
        text = str(value).strip()
        anchor_key = "".join(character for character in text.casefold() if character.isalnum())
        if not text or not anchor_key:
            continue
        if company_key.startswith(anchor_key):
            continue
        anchor_terms.append(text)
    search_terms = list(dict.fromkeys(anchor_terms))[:8]
    if not search_terms:
        search_terms = [company_name]
    implications = [
        str(value).strip()
        for value in (
            decision.get("economic_mechanism"),
            decision.get("why_investigate"),
            decision.get("contradiction"),
        )
        if str(value or "").strip()
    ][:3]
    if not implications:
        implications = [f'"{company_name}" {decision.get("label") or "investment implication"}']

    timeout = httpx.Timeout(60, connect=15)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async def entity_lookup():
            try:
                values = await resolve_gleif(company_name, client=client)
                return values, {
                    "source_type": "gleif_entity_resolution",
                    "status": "complete" if values else "empty",
                    "url": "https://api.gleif.org/api/v1/lei-records",
                }, None
            except Exception as exc:
                return [], {
                    "source_type": "gleif_entity_resolution",
                    "status": "unavailable",
                    "url": "https://api.gleif.org/api/v1/lei-records",
                    "error_category": type(exc).__name__,
                }, "GLEIF entity resolution was unavailable; failure is not no company."

        async def instrument_lookup():
            if not ticker:
                return [], {
                    "source_type": "openfigi_instrument_resolution",
                    "status": "not_applicable",
                    "url": "https://api.openfigi.com/v3/mapping",
                }, None
            try:
                values = await resolve_openfigi(
                    ticker,
                    exchange_code,
                    client=client,
                    api_key=os.environ.get("OPENFIGI_API_KEY"),
                )
                return values, {
                    "source_type": "openfigi_instrument_resolution",
                    "status": "complete" if values else "empty",
                    "url": "https://api.openfigi.com/v3/mapping",
                }, None
            except Exception as exc:
                return [], {
                    "source_type": "openfigi_instrument_resolution",
                    "status": "unavailable",
                    "url": "https://api.openfigi.com/v3/mapping",
                    "error_category": type(exc).__name__,
                }, "OpenFIGI instrument resolution was unavailable; failure is not no listing."

        entity_task = entity_lookup()
        instrument_task = instrument_lookup()
        sec_task = (
            fetch_sec_company_sources(ticker, as_of=as_of, client=client)
            if ticker
            else asyncio.sleep(0, result={
                "status": "not_applicable",
                "sources": [],
                "documents": [],
                "reported_facts": [],
                "limitations": ["No ticker was supplied for automatic SEC discovery."],
            })
        )
        manual_urls = [
            str(value).strip() for value in target.get("primary_document_urls") or []
            if str(value).startswith("https://")
        ][:4]
        manual_tasks = [
            fetch_text_document(
                url,
                source_type="user_supplied_primary_candidate",
                client=client,
                user_agent="Mozilla/5.0",
            )
            for url in manual_urls
        ]
        transcript_url = str(target.get("transcript_url") or "").strip()
        if transcript_url.startswith("https://"):
            transcript_task = fetch_transcript_findings(
                transcript_url,
                source_name="User-supplied transcript source",
                source_type="user_supplied_transcript",
                keywords=search_terms,
                client=client,
            )
        elif ticker and os.environ.get("FINNHUB_API_KEY"):
            transcript_task = fetch_finnhub_transcript_findings(
                ticker,
                api_key=os.environ.get("FINNHUB_API_KEY"),
                keywords=search_terms,
                client=client,
            )
        else:
            transcript_task = asyncio.sleep(0, result={
                "status": "unavailable",
                "source_type": "transcript_not_supplied",
                "url": None,
                "passages": [],
                "findings": [],
                "limitations": [
                    "No transcript URL or user-supplied Finnhub API key was available."
                ],
            })
        news_tasks = [
            check_public_news_implication(value, client=client)
            for value in implications
        ]
        entity_result, instrument_result, sec_result, manual_documents, transcript, news_checks = (
            await asyncio.gather(
                entity_task,
                instrument_task,
                sec_task,
                asyncio.gather(*manual_tasks),
                transcript_task,
                asyncio.gather(*news_tasks),
            )
        )
        entities, entity_receipt, entity_limitation = entity_result
        instruments, instrument_receipt, instrument_limitation = instrument_result

    documents = list(sec_result.get("documents") or []) + list(manual_documents)
    filing_passages = []
    for document in documents:
        if document.get("status") != "complete":
            continue
        filing = document.get("filing") if isinstance(document.get("filing"), Mapping) else {}
        source_label = (
            f"SEC {filing.get('form')} · report {filing.get('report_date')} · "
            f"accession {filing.get('accession_number')}"
            if filing
            else "User-supplied report"
        )
        for passage in extract_passages(str(document.get("text") or ""), search_terms):
            filing_passages.append({
                "source_url": document.get("url") or document.get("requested_url"),
                "source_type": document.get("source_type"),
                "source_label": source_label,
                "text": passage,
            })
    sources = [entity_receipt, instrument_receipt] + list(sec_result.get("sources") or []) + [
        {key: value for key, value in document.items() if key != "text"}
        for document in manual_documents
    ]
    limitations = list(sec_result.get("limitations") or [])
    if entity_limitation:
        limitations.append(entity_limitation)
    if instrument_limitation:
        limitations.append(instrument_limitation)
    if not entities:
        limitations.append("GLEIF did not return an exact legal-entity match; company mapping requires review.")
    if ticker and not instruments:
        limitations.append("OpenFIGI did not return an instrument match; ticker mapping requires review.")
    if transcript.get("status") != "complete":
        limitations.extend(transcript.get("limitations") or [])
    if any(check.get("status") != "complete" for check in news_checks):
        limitations.append("At least one public-news implication check was unavailable.")
    if manual_documents:
        limitations.append(
            "User-supplied primary-document candidates were retained as passages but were not used for automatic numeric facts."
        )
    critical_partial = (
        sec_result.get("status") not in {"complete", "not_applicable"}
        or not entities
        or (ticker and not instruments)
        or entity_receipt.get("status") == "unavailable"
        or instrument_receipt.get("status") == "unavailable"
        or any(check.get("status") != "complete" for check in news_checks)
    )
    return {
        "entities": entities,
        "instruments": instruments,
        "sources": sources,
        "reported_facts": list(sec_result.get("reported_facts") or []),
        "filing_passages": filing_passages[:20],
        "transcript": transcript,
        "news_checks": news_checks,
        "limitations": list(dict.fromkeys(str(value) for value in limitations if value)),
        "coverage_status": "partial" if critical_partial else "complete",
    }


class GenericInvestmentResearchRunner:
    def __init__(
        self,
        store: InvestmentResearchStore,
        *,
        source_collector: SourceCollector | None = None,
    ):
        self.store = store
        self.source_collector = source_collector or collect_generic_sources

    async def run(self, run_id: str, claim_token: str) -> dict[str, Any]:
        run = self.store.get_research_run(run_id)
        if run is None:
            raise ValueError("investment research run was not found")
        handoff = dict(run.get("handoff") or {})
        target = dict(run.get("target") or {})
        options = dict(run.get("options") or {})
        try:
            self.store.update_research_run(
                run_id,
                claim_token=claim_token,
                stage="candidate_handoff",
                progress=5,
                result={"message": "Candidate evidence preserved"},
            )
            self.store.renew_research_claim(
                run_id, claim_token=claim_token, lease_seconds=300
            )
            self.store.update_research_run(
                run_id,
                claim_token=claim_token,
                stage="company_research",
                progress=20,
                result={"message": "Checking free company sources"},
            )
            collected = await self.source_collector(target, handoff, options)
            self.store.renew_research_claim(
                run_id, claim_token=claim_token, lease_seconds=300
            )
            self.store.update_research_run(
                run_id,
                claim_token=claim_token,
                stage="materiality",
                progress=70,
                result={
                    "message": "Separating reported facts from explicit assumptions",
                    "source_count": len(collected.get("sources") or []),
                },
            )
            assumptions = parse_assumptions(options.get("assumptions"))
            payload = build_generic_dossier(
                run_id=run_id,
                handoff=handoff,
                target=target,
                entities=collected.get("entities") or [],
                instruments=collected.get("instruments") or [],
                sources=collected.get("sources") or [],
                reported_facts=collected.get("reported_facts") or [],
                filing_passages=collected.get("filing_passages") or [],
                transcript=collected.get("transcript") or {
                    "status": "unavailable", "passages": [], "findings": []
                },
                news_checks=collected.get("news_checks") or [],
                assumptions=assumptions,
                created_at=_utc_iso(),
                limitations=collected.get("limitations") or [],
            )
            terminal_status = (
                "partial" if collected.get("coverage_status") == "partial" else "complete"
            )
            result = {
                "message": "Dossier saved",
                "coverage_status": collected.get("coverage_status"),
                "source_count": len(collected.get("sources") or []),
                "limitation_count": len(collected.get("limitations") or []),
            }
            self.store.finalize_research_run_with_dossier(
                run_id,
                claim_token=claim_token,
                status=terminal_status,
                payload=payload,
                result=result,
            )
            return payload
        except Exception as exc:
            current = self.store.get_research_run(run_id)
            if current and current.get("status") == "running":
                try:
                    self.store.complete_research_run(
                        run_id,
                        claim_token=claim_token,
                        status="error",
                        dossier_id=None,
                        result={"message": "Research failed without producing a dossier"},
                        error_category=type(exc).__name__,
                    )
                except ValueError:
                    pass
            raise
