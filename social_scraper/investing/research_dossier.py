"""Source-grounded investment dossier primitives and deterministic calculations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Iterable


VERIFIED_FACT_STATES = {"verified_primary", "verified_secondary"}
ASSUMPTION_KINDS = {"assumption", "analyst_assumption"}


@dataclass(frozen=True)
class ReportedFact:
    fact_id: str
    issuer_id: str
    metric: str
    value: Decimal
    unit: str
    currency: str | None
    period_start: str | None
    period_end: str | None
    scope_type: str
    scope_name: str | None
    source_id: str
    source_locator: str
    exact_source_text: str
    verification_status: str


@dataclass(frozen=True)
class RatioResult:
    numerator_fact_id: str
    denominator_fact_id: str
    formula: str
    value: Decimal


@dataclass(frozen=True)
class MaterialityAssessment:
    status: str
    mechanism: str
    computed_value: Decimal | None
    unit: str | None
    formula: str | None
    numerator_fact_ids: tuple[str, ...]
    denominator_fact_ids: tuple[str, ...]
    missing_reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class BridgeTerm:
    name: str
    sign: int
    value: Decimal | None
    provenance_kind: str
    source_ref: str | None = None
    rationale: str | None = None


@dataclass(frozen=True)
class BridgeResult:
    total: Decimal | None
    missing_terms: tuple[str, ...]
    assumption_terms: tuple[str, ...]
    terms: tuple[BridgeTerm, ...]


@dataclass(frozen=True)
class BridgeRangeTerm:
    name: str
    sign: int
    low: Decimal | None
    base: Decimal | None
    high: Decimal | None
    provenance_kind: str
    source_ref: str | None = None
    rationale: str | None = None


@dataclass(frozen=True)
class ScenarioBridgeResult:
    low_total: Decimal | None
    base_total: Decimal | None
    high_total: Decimal | None
    missing_terms: tuple[str, ...]
    assumption_terms: tuple[str, ...]
    terms: tuple[BridgeRangeTerm, ...]


def _scopes_are_compatible(numerator: ReportedFact, denominator: ReportedFact) -> bool:
    if (
        numerator.scope_type == denominator.scope_type
        and numerator.scope_name == denominator.scope_name
    ):
        return True
    return (
        numerator.scope_type == "consolidated_line_item"
        and denominator.scope_type == "consolidated"
        and numerator.scope_name == denominator.scope_name
    )


def _facts_are_comparable(numerator: ReportedFact, denominator: ReportedFact) -> bool:
    return all((
        numerator.verification_status in VERIFIED_FACT_STATES,
        denominator.verification_status in VERIFIED_FACT_STATES,
        numerator.issuer_id == denominator.issuer_id,
        numerator.period_start == denominator.period_start,
        numerator.period_end == denominator.period_end,
        numerator.currency == denominator.currency,
        numerator.unit == denominator.unit,
        _scopes_are_compatible(numerator, denominator),
        denominator.value != 0,
    ))


def reported_ratio(numerator: ReportedFact, denominator: ReportedFact) -> RatioResult:
    """Calculate only from verified, comparable reported facts."""
    if not _facts_are_comparable(numerator, denominator):
        raise ValueError("reported facts are not comparable")
    return RatioResult(
        numerator_fact_id=numerator.fact_id,
        denominator_fact_id=denominator.fact_id,
        formula=f"{numerator.metric} / {denominator.metric}",
        value=numerator.value / denominator.value,
    )


def assess_materiality(
    *,
    mechanism: str,
    numerator: ReportedFact | None,
    denominator: ReportedFact | None,
    missing_reason_codes: Iterable[str] = (),
) -> MaterialityAssessment:
    """Return an exact ratio only when both financial facts support it."""
    missing = tuple(dict.fromkeys(str(value) for value in missing_reason_codes if value))
    if numerator is None or denominator is None:
        if not missing:
            missing = (
                "affected_numerator_missing" if numerator is None else "denominator_missing",
            )
        return MaterialityAssessment(
            status="not_estimable",
            mechanism=mechanism,
            computed_value=None,
            unit=None,
            formula=None,
            numerator_fact_ids=() if numerator is None else (numerator.fact_id,),
            denominator_fact_ids=() if denominator is None else (denominator.fact_id,),
            missing_reason_codes=missing,
        )
    ratio = reported_ratio(numerator, denominator)
    return MaterialityAssessment(
        status="exactly_quantified",
        mechanism=mechanism,
        computed_value=ratio.value,
        unit="share",
        formula=ratio.formula,
        numerator_fact_ids=(numerator.fact_id,),
        denominator_fact_ids=(denominator.fact_id,),
        missing_reason_codes=missing,
    )


def evaluate_bridge(terms: Iterable[BridgeTerm]) -> BridgeResult:
    """Evaluate a transparent economics bridge only when every term is explicit."""
    normalized = tuple(terms)
    if not normalized:
        raise ValueError("at least one bridge term is required")
    for term in normalized:
        if term.sign not in {-1, 1}:
            raise ValueError("bridge term sign must be -1 or 1")
    missing = tuple(term.name for term in normalized if term.value is None)
    assumptions = tuple(
        term.name for term in normalized if term.provenance_kind in ASSUMPTION_KINDS
    )
    total = None
    if not missing:
        total = sum(
            (Decimal(term.sign) * term.value for term in normalized if term.value is not None),
            start=Decimal("0"),
        )
    return BridgeResult(
        total=total,
        missing_terms=missing,
        assumption_terms=assumptions,
        terms=normalized,
    )


def evaluate_scenario_bridge(
    terms: Iterable[BridgeRangeTerm],
) -> ScenarioBridgeResult:
    """Calculate low/base/high only when every range input is explicit.

    Positive drivers use low/base/high in normal order. Costs use high cost in
    the low economic case and low cost in the high economic case.
    """
    normalized = tuple(terms)
    if not normalized:
        raise ValueError("at least one scenario bridge term is required")
    for term in normalized:
        if term.sign not in {-1, 1}:
            raise ValueError("bridge range term sign must be -1 or 1")
        values = (term.low, term.base, term.high)
        if all(value is not None for value in values):
            assert term.low is not None and term.base is not None and term.high is not None
            if not term.low <= term.base <= term.high:
                raise ValueError("bridge range must satisfy low <= base <= high")
    missing = tuple(
        term.name
        for term in normalized
        if any(value is None for value in (term.low, term.base, term.high))
    )
    assumptions = tuple(
        term.name for term in normalized if term.provenance_kind in ASSUMPTION_KINDS
    )
    low_total = base_total = high_total = None
    if not missing:
        low_total = sum(
            (
                term.low if term.sign == 1 else -term.high
                for term in normalized
                if term.low is not None and term.high is not None
            ),
            start=Decimal("0"),
        )
        base_total = sum(
            (
                Decimal(term.sign) * term.base
                for term in normalized
                if term.base is not None
            ),
            start=Decimal("0"),
        )
        high_total = sum(
            (
                term.high if term.sign == 1 else -term.low
                for term in normalized
                if term.low is not None and term.high is not None
            ),
            start=Decimal("0"),
        )
    return ScenarioBridgeResult(
        low_total=low_total,
        base_total=base_total,
        high_total=high_total,
        missing_terms=missing,
        assumption_terms=assumptions,
        terms=normalized,
    )


def to_jsonable(value: Any) -> Any:
    """Convert dossier dataclasses and Decimals without losing exact decimal text."""
    if hasattr(value, "__dataclass_fields__"):
        return to_jsonable(asdict(value))
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_jsonable(item) for item in value]
    return value
