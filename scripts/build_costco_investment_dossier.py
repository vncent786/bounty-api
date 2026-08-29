"""Build a source-grounded Costco Executive-membership investment dossier.

This is the first vertical slice of the Candidate -> Exposure -> Materiality ->
Information Parity -> Investment Memo workflow. It uses only free/public sources,
produces no trade recommendation, and refuses to estimate undisclosed Executive
standalone economics.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from social_scraper.investing.free_company_sources import (
    check_public_news_implication,
    extract_regex_fact,
    fetch_text_document,
    fetch_transcript_findings,
    free_provider_registry,
    resolve_gleif,
    resolve_openfigi,
)
from social_scraper.investing.research_dossier import (
    BridgeRangeTerm,
    BridgeTerm,
    ReportedFact,
    assess_materiality,
    evaluate_bridge,
    evaluate_scenario_bridge,
    reported_ratio,
    to_jsonable,
)
from social_scraper.investing.research_store import InvestmentResearchStore


CASE_ID = "costco-executive"
SCHEMA_VERSION = "investment-dossier/1"
TEN_K_URL = "https://www.sec.gov/Archives/edgar/data/909832/000090983225000101/cost-20250831.htm"
TEN_Q_URL = "https://www.sec.gov/Archives/edgar/data/909832/000090983226000051/cost-20260510.htm"
OFFICIAL_WEBCAST_URL = (
    "https://investor.costco.com/events-and-presentations/events/event-details/"
    "2026/Q3-2026-Earnings-Results-2026-2TUZRPTsc9/default.aspx"
)
SECONDARY_TRANSCRIPT_URL = (
    "https://www.fool.com/earnings/call-transcripts/2026/05/28/"
    "costco-cost-q3-2026-earnings-transcript/"
)
DEFAULT_DB = ROOT / "data" / "investment_research.db"
DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "investment-dossiers"


def _public_source(source: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in source.items()
        if key not in {"text"} and value not in (None, "")
    }


def _fact_dict(fact: ReportedFact) -> dict[str, Any]:
    return to_jsonable(fact)


def _try_fact(
    facts: list[ReportedFact],
    gaps: list[str],
    text: str,
    **kwargs: Any,
) -> ReportedFact | None:
    """Extract a fact only after the caller validates the primary document."""
    kwargs["verification_status"] = "verified_primary"
    try:
        fact = extract_regex_fact(text, **kwargs)
    except ValueError:
        gaps.append(f"reported_fact_not_found:{kwargs['metric']}")
        return None
    facts.append(fact)
    return fact


def _dossier_id(created_at: str, sources: list[dict[str, Any]]) -> str:
    material = json.dumps(
        {
            "case_id": CASE_ID,
            "created_at": created_at,
            "source_hashes": [source.get("content_sha256") for source in sources],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "costco-executive-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def build_dossier_from_sources(
    *,
    created_at: str,
    gleif: list[dict[str, Any]],
    instruments: list[dict[str, Any]],
    ten_k: dict[str, Any],
    ten_q: dict[str, Any],
    official_webcast: dict[str, Any],
    transcript: dict[str, Any],
    news_checks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the deterministic dossier from already collected source payloads."""
    for label, document in (("10-K", ten_k), ("10-Q", ten_q)):
        if (
            document.get("status") != "complete"
            or document.get("source_type") != "regulator_filing"
            or not document.get("content_sha256")
            or not str(document.get("text") or "").strip()
        ):
            raise ValueError(f"{label} must be a complete primary filing")
    transcript_public = _public_source(transcript)
    if transcript.get("status") != "complete":
        transcript_public["passages"] = []
        transcript_public["findings"] = []
    sources = [
        _public_source(ten_k),
        _public_source(ten_q),
        _public_source(official_webcast),
        transcript_public,
    ]
    dossier_id = _dossier_id(created_at, sources)
    facts: list[ReportedFact] = []
    gaps: list[str] = []
    ten_k_text = str(ten_k.get("text") or "")
    ten_q_text = str(ten_q.get("text") or "")
    issuer_id = "lei:29DX7H14B9S6O3FD6V18"

    membership_fees = _try_fact(
        facts,
        gaps,
        ten_k_text,
        pattern=r"Membership Fees\s+2025\s+2024\s+2023\s+Membership fees\s*\$\s*([\d,]+)",
        metric="membership_fee_revenue",
        issuer_id=issuer_id,
        source_id="sec:cost-20250831",
        period_start="2024-09-02",
        period_end="2025-08-31",
        currency="USD",
        unit="USD millions",
        scope_type="consolidated_line_item",
        source_locator="Membership Fees",
    )
    total_revenue = _try_fact(
        facts,
        gaps,
        ten_k_text,
        pattern=r"Total revenue\s+([\d,]+)\s+[\d,]+\s+[\d,]+",
        metric="total_revenue",
        issuer_id=issuer_id,
        source_id="sec:cost-20250831",
        period_start="2024-09-02",
        period_end="2025-08-31",
        currency="USD",
        unit="USD millions",
        scope_type="consolidated",
        source_locator="Consolidated Statements of Income",
    )
    executive_sales_penetration = _try_fact(
        facts,
        gaps,
        ten_k_text,
        pattern=(
            r"sales penetration of Executive members represented approximately\s*"
            r"([\d.]+)% of worldwide net sales in 2025"
        ),
        metric="executive_member_sales_penetration",
        issuer_id=issuer_id,
        source_id="sec:cost-20250831",
        period_start="2024-09-02",
        period_end="2025-08-31",
        currency=None,
        unit="percent",
        scope_type="worldwide_sales_association",
        source_locator="Membership",
    )
    executive_members = _try_fact(
        facts,
        gaps,
        ten_k_text,
        pattern=r"Executive members represented\s*([\d,]+),\s*[\d,]+,\s*and\s*[\d,]+",
        metric="executive_paid_memberships",
        issuer_id=issuer_id,
        source_id="sec:cost-20250831",
        period_start=None,
        period_end="2025-08-31",
        currency=None,
        unit="thousands of memberships",
        scope_type="worldwide_memberships",
        source_locator="Membership footnote",
    )
    paid_members = _try_fact(
        facts,
        gaps,
        ten_k_text,
        pattern=r"Total paid members\s*(?:1\s*)?([\d,]+)\s+[\d,]+\s+[\d,]+",
        metric="total_paid_memberships",
        issuer_id=issuer_id,
        source_id="sec:cost-20250831",
        period_start=None,
        period_end="2025-08-31",
        currency=None,
        unit="thousands of memberships",
        scope_type="worldwide_memberships",
        source_locator="Membership table",
    )
    upgrade_fee = _try_fact(
        facts,
        gaps,
        ten_k_text,
        pattern=r"additional annual fee of\s*\$([\d,]+)",
        metric="executive_upgrade_fee_us",
        issuer_id=issuer_id,
        source_id="sec:cost-20250831",
        period_start=None,
        period_end="2025-08-31",
        currency="USD",
        unit="USD per US membership per year",
        scope_type="US_membership_terms",
        source_locator="Membership",
    )
    reward_rate = _try_fact(
        facts,
        gaps,
        ten_k_text,
        pattern=r"Executive members earn a\s*([\d.]+)% reward",
        metric="executive_reward_rate",
        issuer_id=issuer_id,
        source_id="sec:cost-20250831",
        period_start=None,
        period_end="2025-08-31",
        currency=None,
        unit="percent of qualified purchases",
        scope_type="membership_terms",
        source_locator="Membership",
    )
    _try_fact(
        facts,
        gaps,
        ten_q_text,
        pattern=(
            r"Membership Fees\s+12 Weeks Ended\s+36 Weeks Ended.*?"
            r"Membership fees\s*\$\s*([\d,]+)"
        ),
        metric="membership_fee_revenue_q3_2026",
        issuer_id=issuer_id,
        source_id="sec:cost-20260510",
        period_start="2026-02-16",
        period_end="2026-05-10",
        currency="USD",
        unit="USD millions",
        scope_type="consolidated_line_item",
        source_locator="Membership Fees",
        flags=0,
    )
    _try_fact(
        facts,
        gaps,
        ten_q_text,
        pattern=(
            r"Membership Fees\s+12 Weeks Ended\s+36 Weeks Ended.*?"
            r"Membership fees\s*\$\s*[\d,]+\s*\$\s*[\d,]+\s*\$\s*([\d,]+)"
        ),
        metric="membership_fee_revenue_ytd_q3_2026",
        issuer_id=issuer_id,
        source_id="sec:cost-20260510",
        period_start="2025-09-01",
        period_end="2026-05-10",
        currency="USD",
        unit="USD millions",
        scope_type="consolidated_line_item",
        source_locator="Membership Fees",
        flags=0,
    )

    fee_share = assess_materiality(
        mechanism="Membership-fee revenue as a share of Costco consolidated revenue.",
        numerator=membership_fees,
        denominator=total_revenue,
        missing_reason_codes=(),
    )
    executive_materiality = assess_materiality(
        mechanism=(
            "Executive upgrades may affect membership fees, retention, shopping behavior, "
            "merchandise contribution, reward cost and benefit cost."
        ),
        numerator=None,
        denominator=total_revenue,
        missing_reason_codes=(
            "executive_fee_revenue_not_disclosed",
            "aggregate_executive_reward_cost_not_disclosed",
            "executive_specific_renewal_not_disclosed",
            "incremental_spend_caused_by_upgrade_not_disclosed",
            "executive_contribution_margin_not_disclosed",
        ),
    )

    derived = []
    if upgrade_fee is not None and reward_rate is not None and reward_rate.value != 0:
        consumer_break_even = upgrade_fee.value / (reward_rate.value / Decimal("100"))
        derived.append({
            "name": "us_consumer_reward_break_even_spend",
            "value": format(consumer_break_even, "f"),
            "unit": "USD qualified purchases per year",
            "formula": "reported US Executive upgrade fee / reported reward rate",
            "derived_from_fact_ids": [upgrade_fee.fact_id, reward_rate.fact_id],
            "limitation": (
                "Consumer reward arithmetic only. It excludes purchase exclusions, other "
                "benefits and Costco's incremental merchandise economics."
            ),
        })

    bridge = evaluate_bridge((
        BridgeTerm(
            "incremental_upgrade_fee",
            1,
            upgrade_fee.value if upgrade_fee is not None else None,
            "reported" if upgrade_fee is not None else "not_disclosed",
            upgrade_fee.source_id if upgrade_fee is not None else None,
            "US upgrade fee only; international fees vary.",
        ),
        BridgeTerm("incremental_merchandise_contribution", 1, None, "assumption"),
        BridgeTerm("incremental_retention_value", 1, None, "not_disclosed"),
        BridgeTerm("reward_cost", -1, None, "not_disclosed"),
        BridgeTerm("benefit_and_service_cost", -1, None, "not_disclosed"),
    ))
    scenario_bridge = evaluate_scenario_bridge((
        BridgeRangeTerm(
            "incremental_upgrade_fee",
            1,
            upgrade_fee.value if upgrade_fee is not None else None,
            upgrade_fee.value if upgrade_fee is not None else None,
            upgrade_fee.value if upgrade_fee is not None else None,
            "reported" if upgrade_fee is not None else "not_disclosed",
            upgrade_fee.source_id if upgrade_fee is not None else None,
            "US upgrade fee only; international fees vary.",
        ),
        BridgeRangeTerm(
            "incremental_merchandise_contribution", 1,
            None, None, None, "analyst_assumption",
        ),
        BridgeRangeTerm(
            "incremental_retention_value", 1,
            None, None, None, "analyst_assumption",
        ),
        BridgeRangeTerm(
            "reward_cost", -1, None, None, None, "analyst_assumption",
        ),
        BridgeRangeTerm(
            "benefit_and_service_cost", -1,
            None, None, None, "analyst_assumption",
        ),
    ))

    completed_news_checks = [
        check for check in news_checks if check.get("status") == "complete"
    ]
    public_article_count = sum(
        len(check.get("articles") or []) for check in completed_news_checks
    )
    parity_status = (
        "unknown_for_analyst_coverage"
        if completed_news_checks
        else "unknown_news_unavailable"
    )
    parity_conclusion = (
        "Public-news coverage was sampled, but the exact causal incremental-profit "
        "implication and point-in-time analyst consensus were not comprehensively checked."
        if completed_news_checks
        else "Public-news coverage could not be sampled because every configured news check "
        "was unavailable; source failure cannot be interpreted as silence."
    )
    transcript_has_findings = (
        transcript.get("status") == "complete"
        and bool(transcript_public.get("passages") or transcript_public.get("findings"))
    )
    eligible_common_stock = any(
        str(item.get("security_type") or "").casefold() == "common stock"
        for item in instruments
    )
    facts_by_metric = {fact.metric: fact for fact in facts}
    payload = {
        "schema_version": SCHEMA_VERSION,
        "dossier_id": dossier_id,
        "case_id": CASE_ID,
        "created_at": created_at,
        "as_of": "2026-08-29",
        "status": "research_only",
        "title": "Costco Executive membership economics",
        "observation": (
            "Some members describe the 2% Executive reward as making the upgrade "
            "self-funding or worthwhile."
        ),
        "single_complete_global_database": False,
        "free_provider_strategy": free_provider_registry(),
        "entities": gleif,
        "instruments": instruments,
        "instrument_implementation": {
            "common_stock_eligible": eligible_common_stock,
            "options_required": False,
            "options_status": "not_checked",
            "decision": "No position conclusion. Common stock is valid if later diligence passes.",
        },
        "sources": sources,
        "reported_facts": [_fact_dict(fact) for fact in facts],
        "extraction_gaps": gaps,
        "association_warning": (
            "Executive-member sales penetration is an observed customer-mix association, "
            "not evidence that upgrading caused the spending."
        ),
        "derived_calculations": derived,
        "materiality": [
            {
                "assessment_id": "membership_fee_share_of_total_revenue",
                **to_jsonable(fee_share),
                "limitation": "This is all membership fees, not Executive-only economics.",
            },
            {
                "assessment_id": "executive_incremental_economics",
                **to_jsonable(executive_materiality),
                "limitation": (
                    "Public disclosure does not provide the counterfactual cohort economics "
                    "needed to determine causal incremental profit."
                ),
            },
        ],
        "economic_bridge": to_jsonable(bridge),
        "assumption_scenario_bridge": to_jsonable(scenario_bridge),
        "assumption_policy": {
            "educated_assumptions_allowed": True,
            "requirements": [
                "Every assumption must have units, a dated rationale and an author.",
                "Use low/base/high ranges and show sensitivity to each assumption.",
                "Never relabel an assumption as a reported fact.",
                "Do not calculate a scenario total while a required term is missing.",
            ],
        },
        "direction": {
            "company_direction": "uncertain",
            "positive_case": (
                "Upgrades cause profitable incremental fees, retention or shopping contribution "
                "that exceeds rewards and benefit costs."
            ),
            "neutral_case": (
                "Existing heavy shoppers self-select into Executive and the fee/reward mix "
                "changes without much incremental contribution."
            ),
            "negative_case": (
                "Rewards and benefits subsidize spending that would have occurred anyway or "
                "create downgrade/cancellation risk."
            ),
            "evidence_needed": [
                "Executive-specific renewal, downgrade and cancellation cohorts",
                "Aggregate Executive reward generation and redemption cost",
                "Matched pre/post-upgrade shopping and contribution behavior",
                "Benefit utilization and servicing cost",
            ],
        },
        "transcript_research": {
            "official_webcast": _public_source(official_webcast),
            "secondary_transcript": transcript_public,
            "finding_status": (
                "secondary_findings_available"
                if transcript_has_findings
                else "transcript_findings_unavailable"
            ),
            "critical_quote_policy": (
                "Reverify critical numeric or causal transcript claims against official audio."
            ),
        },
        "information_parity": {
            "status": parity_status,
            "parity_level": "unknown",
            "implications_tested": [check.get("implication") for check in news_checks],
            "sampled_public_article_count": public_article_count,
            "checks": news_checks,
            "conclusion": parity_conclusion,
        },
        "bottom_line": (
            "Executive membership is strategically important, but public disclosure does not "
            "support a standalone Executive-profit or causal incremental-spend estimate. "
            "The observation remains worth researching and is not a trade conclusion."
        ),
        "review_questions": [
            "Does Executive conversion change renewal after controlling for prior spend?",
            "How much reward cost is incremental versus attached to spending that would occur anyway?",
            "Has financial coverage connected Executive cohort economics to earnings estimates?",
            "What evidence would falsify the positive, neutral and negative cases?",
        ],
        "calibration_facts": {
            "executive_sales_penetration_fact_id": (
                executive_sales_penetration.fact_id if executive_sales_penetration else None
            ),
            "executive_members_fact_id": executive_members.fact_id if executive_members else None,
            "paid_members_fact_id": paid_members.fact_id if paid_members else None,
            "membership_fee_ratio": (
                str(reported_ratio(membership_fees, total_revenue).value)
                if membership_fees is not None and total_revenue is not None
                else None
            ),
            "q3_membership_upgrade_statement_primary": (
                "upgrades to executive membership" in ten_q_text.casefold()
            ),
            "reward_reduces_net_sales_primary": (
                "reward associated with executive membership reduces net sales"
                in ten_k_text.casefold()
            ),
        },
    }
    return payload


async def collect_live_sources() -> dict[str, Any]:
    timeout = httpx.Timeout(60, connect=15)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        tasks = [
            resolve_gleif("Costco Wholesale Corporation", client=client),
            resolve_openfigi(
                "COST",
                "US",
                client=client,
                api_key=os.environ.get("OPENFIGI_API_KEY"),
            ),
            fetch_text_document(TEN_K_URL, source_type="regulator_filing", client=client),
            fetch_text_document(TEN_Q_URL, source_type="regulator_filing", client=client),
            fetch_text_document(
                OFFICIAL_WEBCAST_URL,
                source_type="official_webcast_page",
                client=client,
                user_agent="Mozilla/5.0",
            ),
            fetch_transcript_findings(
                SECONDARY_TRANSCRIPT_URL,
                source_name="The Motley Fool earnings-call transcript",
                source_type="secondary_public_transcript",
                keywords=(
                    "upgrades to executive memberships",
                    "At Q3 end, we had 41.2 million paid executive memberships",
                    "renewal rate was 92.2%",
                ),
                client=client,
            ),
            check_public_news_implication(
                '"Costco Executive membership" incremental profit', client=client
            ),
            check_public_news_implication(
                '"Costco Executive membership" reward cost', client=client
            ),
            check_public_news_implication(
                '"Costco Executive membership" renewal upgrades', client=client
            ),
        ]
        (
            gleif,
            instruments,
            ten_k,
            ten_q,
            official_webcast,
            transcript,
            *news_checks,
        ) = await asyncio.gather(*tasks)
    return {
        "gleif": gleif,
        "instruments": instruments,
        "ten_k": ten_k,
        "ten_q": ten_q,
        "official_webcast": official_webcast,
        "transcript": transcript,
        "news_checks": news_checks,
    }


async def build_live_dossier(*, created_at: str | None = None) -> dict[str, Any]:
    sources = await collect_live_sources()
    timestamp = created_at or datetime.now(timezone.utc).isoformat()
    if sources["ten_k"].get("status") != "complete":
        raise RuntimeError("Costco primary 10-K could not be retrieved")
    if sources["ten_q"].get("status") != "complete":
        raise RuntimeError("Costco primary Q3 10-Q could not be retrieved")
    return build_dossier_from_sources(created_at=timestamp, **sources)


def _fact_value(payload: dict[str, Any], metric: str) -> str:
    fact = next(
        (item for item in payload.get("reported_facts") or [] if item.get("metric") == metric),
        None,
    )
    if fact is None:
        return "Not available"
    value = Decimal(str(fact.get("value")))
    unit = str(fact.get("unit") or "")
    if unit == "percent":
        return f"{format(value, 'f')}%"
    if unit == "thousands of memberships":
        return f"{value / Decimal('1000'):,.1f} million paid memberships"
    if unit == "USD millions":
        return f"US${value / Decimal('1000'):,.3f} billion"
    return f"{format(value, 'f')} {unit}".strip()


def _display_share(value: Any) -> str:
    if value in (None, ""):
        return "Not available"
    percentage = Decimal(str(value)) * Decimal("100")
    return f"{percentage.quantize(Decimal('0.01'))}%"


def render_markdown(payload: dict[str, Any]) -> str:
    materiality = {
        item["assessment_id"]: item for item in payload.get("materiality") or []
    }
    executive = materiality.get("executive_incremental_economics") or {}
    parity = payload.get("information_parity") or {}
    transcript = payload.get("transcript_research") or {}
    secondary_transcript = transcript.get("secondary_transcript") or {}
    passages = secondary_transcript.get("passages") or []
    findings = secondary_transcript.get("findings") or []
    sources = [
        item for item in payload.get("sources") or [] if item.get("url") or item.get("requested_url")
    ]
    source_lines = "\n".join(
        f"- [{item.get('source_type', 'source')}]({item.get('url') or item.get('requested_url')}) "
        f"({item.get('status', 'unknown')})"
        for item in sources
    )
    if findings:
        transcript_lines = "\n".join(
            f"- **{item.get('speaker') or 'Speaker unavailable'}:** {item.get('text')}"
            for item in findings
        )
    elif passages:
        transcript_lines = "\n".join(f"- {value}" for value in passages)
    else:
        transcript_lines = "- No retrievable transcript findings."
    fee_share_display = _display_share(
        (materiality.get("membership_fee_share_of_total_revenue") or {}).get(
            "computed_value"
        )
    )
    break_even = next(
        (
            item.get("value")
            for item in payload.get("derived_calculations") or []
            if item.get("name") == "us_consumer_reward_break_even_spend"
        ),
        None,
    )
    break_even_display = (
        "Not available"
        if break_even in (None, "")
        else f"US${Decimal(str(break_even)):,.0f}"
    )
    scenario = payload.get("assumption_scenario_bridge") or {}
    scenario_values = (
        scenario.get("low_total"),
        scenario.get("base_total"),
        scenario.get("high_total"),
    )
    scenario_display = (
        "Not calculated because required inputs are missing"
        if any(value is None for value in scenario_values)
        else " / ".join(str(value) for value in scenario_values)
    )
    return f"""# Costco Executive Membership Investment Dossier

**As of:** {payload.get('as_of')}
**Status:** Research only
Company direction: **{payload.get('direction', {}).get('company_direction')}**
Options required: **No**

## Observation

{payload.get('observation')}

## What the filings establish

- Executive-member sales penetration: {_fact_value(payload, 'executive_member_sales_penetration')}
- Executive paid memberships: {_fact_value(payload, 'executive_paid_memberships')}
- Total paid memberships: {_fact_value(payload, 'total_paid_memberships')}
- FY2025 membership-fee revenue: {_fact_value(payload, 'membership_fee_revenue')}
- FY2025 total revenue: {_fact_value(payload, 'total_revenue')}

These figures establish scale and association. They do not establish that upgrading caused the spending.

## Materiality

- Membership-fee share of total revenue: **{fee_share_display}**. This includes all membership fees, not Executive-only economics.
- Executive incremental economics: **{'Not estimable' if executive.get('status') == 'not_estimable' else executive.get('status')}**.
- Missing inputs: {', '.join(executive.get('missing_reason_codes') or [])}
- Consumer reward break-even in the US: **{break_even_display}** of qualified annual purchases. This is member arithmetic, not Costco profit.

## Educated-assumption bridge

The system permits explicit low/base/high assumptions, but it will not calculate a total while a required term is absent.

- Missing bridge terms: {', '.join((payload.get('economic_bridge') or {}).get('missing_terms') or [])}
- Current bridge total: {(payload.get('economic_bridge') or {}).get('total')}
- Low/base/high scenario totals: {scenario_display}

## Direction cases

- **Positive:** {payload.get('direction', {}).get('positive_case')}
- **Neutral:** {payload.get('direction', {}).get('neutral_case')}
- **Negative:** {payload.get('direction', {}).get('negative_case')}

## Transcript findings

Source status: **{transcript.get('finding_status')}**. Secondary transcript passages require verification against official audio before relying on critical quotes.

{transcript_lines}

## Information parity

Status: **{parity.get('status')}**
Sampled public-news articles: **{parity.get('sampled_public_article_count')}**

{parity.get('conclusion')}

## Free global-data answer

**No single free database provides complete global legal entities, instruments, filings, fundamentals and transcripts.** Bounty therefore uses a zero-cost federation: GLEIF for entities, OpenFIGI for instruments, official filing repositories, filings.xbrl.org for available ESEF filings, and issuer IR pages. Finnhub can be added as an optional free convenience layer, never the sole authority.

## Bottom line

{payload.get('bottom_line')}

## Sources

{source_lines}
"""


def write_artifacts(
    payload: dict[str, Any], *, output_dir: Path, db_path: Path,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{payload['dossier_id']}.json"
    markdown_path = output_dir / f"{payload['dossier_id']}.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(payload), encoding="utf-8")
    store = InvestmentResearchStore(db_path)
    stored = store.append_dossier(payload)
    if not store.verify_dossier(payload["dossier_id"]):
        raise RuntimeError("persisted dossier hash verification failed")
    return {
        "json_path": str(json_path.resolve()),
        "markdown_path": str(markdown_path.resolve()),
        "database_path": str(db_path.resolve()),
        "payload_sha256": str(stored["payload_sha256"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    payload = asyncio.run(build_live_dossier())
    artifacts = write_artifacts(payload, output_dir=args.output_dir, db_path=args.db)
    print(json.dumps({
        "dossier_id": payload["dossier_id"],
        "status": payload["status"],
        "company_direction": payload["direction"]["company_direction"],
        "executive_materiality": next(
            item["status"] for item in payload["materiality"]
            if item["assessment_id"] == "executive_incremental_economics"
        ),
        "artifacts": artifacts,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
