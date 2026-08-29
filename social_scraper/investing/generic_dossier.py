"""Generic candidate handoff and source-grounded investment dossier assembly."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from social_scraper.investing.research_dossier import (
    RangeAssumption,
    ReportedFact,
    evaluate_materiality_assumptions,
    to_jsonable,
)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def build_candidate_handoff(
    candidate: Mapping[str, Any],
    *,
    source_scan_id: str,
    selection_mode: str,
    created_at: str,
) -> dict[str, Any]:
    if selection_mode not in {"qualified", "research_only"}:
        raise ValueError("selection_mode must be qualified or research_only")
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    label = str(candidate.get("label") or "").strip()
    evidence = [dict(item) for item in candidate.get("evidence") or []]
    if not candidate_id or not label or not source_scan_id:
        raise ValueError("candidate id, label and source scan id are required")
    if not evidence or any(
        not str(item.get("id") or "").strip()
        or not str(item.get("url") or "").startswith(("http://", "https://"))
        for item in evidence
    ):
        raise ValueError("candidate handoff requires openable cited evidence")
    qualification_status = str(candidate.get("qualification_status") or "unknown")
    if selection_mode == "qualified" and qualification_status != "qualified":
        raise ValueError("candidate is not qualified")
    decision_fields = (
        "label", "qualification_status", "review_status", "behaviour_type",
        "summary", "economic_mechanism", "why_investigate", "contradiction",
        "invalidation", "anchor_terms", "gates", "parity", "windows",
        "movement_bundle", "trajectory", "blocking_reasons", "caveats",
    )
    decision = {
        field: candidate.get(field)
        for field in decision_fields
        if field in candidate
    }
    candidate_hash = hashlib.sha256(
        _canonical({
            "source_scan_id": source_scan_id,
            "candidate_id": candidate_id,
            "decision": decision,
            "evidence": evidence,
        }).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": "candidate-handoff/1",
        "handoff_id": f"handoff:{candidate_hash[:24]}",
        "source_scan_id": str(source_scan_id),
        "candidate_id": candidate_id,
        "selection_mode": selection_mode,
        "qualification_status": qualification_status,
        "decision": decision,
        "evidence": evidence,
        "candidate_hash": candidate_hash,
        "created_at": created_at,
    }


def _sanitize_source(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in source.items()
        if key not in {"text", "raw", "payload"}
    }


def build_generic_dossier(
    *,
    run_id: str,
    handoff: Mapping[str, Any],
    target: Mapping[str, Any],
    entities: Sequence[Mapping[str, Any]],
    instruments: Sequence[Mapping[str, Any]],
    sources: Sequence[Mapping[str, Any]],
    reported_facts: Sequence[ReportedFact],
    filing_passages: Sequence[Mapping[str, Any]],
    transcript: Mapping[str, Any],
    news_checks: Sequence[Mapping[str, Any]],
    assumptions: Mapping[str, RangeAssumption],
    created_at: str,
    limitations: Sequence[str],
) -> dict[str, Any]:
    candidate_id = str(handoff.get("candidate_id") or "")
    candidate_hash = str(handoff.get("candidate_hash") or "")
    dossier_material = _canonical({
        "run_id": run_id,
        "candidate_hash": candidate_hash,
        "target": target,
        "created_at": created_at,
    })
    dossier_id = "investment-dossier:" + hashlib.sha256(
        dossier_material.encode("utf-8")
    ).hexdigest()[:24]
    scenario = evaluate_materiality_assumptions(dict(assumptions))
    scenario_complete = not scenario.missing_assumptions
    revenue_fact = next(
        (fact for fact in reported_facts if fact.metric == "consolidated_revenue"),
        None,
    )
    completed_news = [
        dict(check) for check in news_checks if check.get("status") == "complete"
    ]
    parity_status = (
        "unknown_for_analyst_coverage"
        if completed_news
        else "unknown_news_unavailable"
    )
    public_article_count = sum(len(check.get("articles") or []) for check in completed_news)
    common_stock_eligible = any(
        str(item.get("security_type") or "").casefold() == "common stock"
        for item in instruments
    )
    transcript_available = (
        transcript.get("status") == "complete"
        and bool(transcript.get("findings") or transcript.get("passages"))
    )
    decision = dict(handoff.get("decision") or {})
    materiality_status = "assumption_scenario" if scenario_complete else "not_estimable"
    materiality_missing = list(scenario.missing_assumptions)
    if revenue_fact is None:
        materiality_missing.append("consolidated_revenue_baseline_missing")
    payload = {
        "schema_version": "investment-dossier/1",
        "dossier_id": dossier_id,
        "run_id": run_id,
        "case_id": f"private-radar:{handoff.get('source_scan_id')}:{candidate_id}",
        "created_at": created_at,
        "status": "research_only",
        "title": str(decision.get("label") or target.get("company_name") or "Investment research"),
        "candidate": {
            "source_scan_id": handoff.get("source_scan_id"),
            "candidate_id": candidate_id,
            "selection_mode": handoff.get("selection_mode"),
            "qualification_status": handoff.get("qualification_status"),
            "candidate_hash": candidate_hash,
            "decision": decision,
            "evidence": [dict(item) for item in handoff.get("evidence") or []],
        },
        "target": dict(target),
        "entities": [dict(item) for item in entities],
        "instruments": [dict(item) for item in instruments],
        "sources": [_sanitize_source(item) for item in sources],
        "reported_facts": [to_jsonable(fact) for fact in reported_facts],
        "filing_passages": [dict(item) for item in filing_passages],
        "transcript_research": {
            **_sanitize_source(transcript),
            "finding_status": (
                "secondary_findings_available"
                if transcript_available
                else "transcript_findings_unavailable"
            ),
            "critical_quote_policy": (
                "Reverify critical transcript claims against official text or audio."
            ),
        },
        "materiality": {
            "status": materiality_status,
            "reported_revenue_baseline_fact_id": (
                revenue_fact.fact_id if revenue_fact is not None else None
            ),
            "scenario": to_jsonable(scenario),
            "missing_reason_codes": list(dict.fromkeys(materiality_missing)),
            "limitation": (
                "Scenario values are explicit analyst assumptions, not company-reported impact."
                if scenario_complete
                else "No financial impact is calculated while required assumptions are missing."
            ),
        },
        "information_parity": {
            "status": parity_status,
            "parity_level": "unknown",
            "sampled_public_article_count": public_article_count,
            "checks": [dict(item) for item in news_checks],
            "conclusion": (
                "Public news was sampled, but complete analyst and point-in-time consensus "
                "coverage was unavailable."
                if completed_news
                else "Public news could not be sampled; source failure is not silence."
            ),
        },
        "direction": {
            "company_direction": "uncertain",
            "possible_mechanism": decision.get("economic_mechanism"),
            "counterevidence": decision.get("contradiction"),
            "invalidation": decision.get("invalidation"),
        },
        "instrument_implementation": {
            "common_stock_eligible": common_stock_eligible,
            "options_required": False,
            "options_status": "not_checked",
            "decision": "No position conclusion; common stock is valid if later diligence passes.",
        },
        "limitations": list(dict.fromkeys(str(value) for value in limitations if value)),
        "bottom_line": (
            "The candidate has a saved source-grounded research record. Company direction, "
            "materiality and information parity remain explicit rather than inferred from social volume."
        ),
    }
    return payload
