"""Optional investing interpretation over canonical horizontal evidence."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable


_MECHANISMS = {"revenue", "price_mix", "margin", "retention", "inventory", "reputation", "unknown"}
_MATERIALITY = {"plausible", "weak", "not_established"}
_DIRECTION = {"potential_long", "potential_short", "mixed", "no_directional_inference"}


@dataclass
class InvestingInterpretation:
    status: str
    companies: list[dict] = field(default_factory=list)
    materiality: str = "not_established"
    signal_direction: str = "no_directional_inference"
    signal_freshness: str = "unknown"
    invalidating_evidence: list[str] = field(default_factory=list)
    required_next_diligence: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    error: str = ""
    schema_version: str = "investing-lens/1"

    def to_dict(self) -> dict:
        return asdict(self)


_SYSTEM = """Interpret the supplied candidate and conversation evidence as an investment research hypothesis, never as a trade recommendation. Return only JSON with companies, materiality, signal_direction, invalidating_evidence, required_next_diligence, and limitations. Each company needs company_name, ticker (or null), mapping_confidence from 0 to 1, mechanism, rationale, and evidence_ids. Allowed mechanisms: revenue, price_mix, margin, retention, inventory, reputation, unknown. Allowed materiality: plausible, weak, not_established. Allowed signal_direction: potential_long, potential_short, mixed, no_directional_inference. Entity or ticker matching alone never establishes economic exposure or materiality. Cite only supplied evidence IDs. Admit unknowns and include disconfirming work."""


def _parse(raw: str) -> dict:
    value = raw.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("investing lens response must be an object")
    return parsed


async def analyze_investing_lens(
    candidate: dict,
    conversation: dict,
    *,
    llm_call_fn: Callable[[str, str], Awaitable[str]] | None = None,
) -> InvestingInterpretation:
    evidence = conversation.get("evidence") or []
    valid_ids = {item.get("id") for item in evidence if item.get("id")}
    if conversation.get("status") != "supported" or not valid_ids:
        return InvestingInterpretation(
            status="insufficient_evidence",
            limitations=["No citation-backed horizontal conversation is available."],
        )
    if llm_call_fn is None:
        from social_scraper.llm_client import call_llm

        async def llm_call_fn(system: str, user: str) -> str:
            return await call_llm(
                system,
                user,
                max_tokens=1800,
                temperature=0.0,
                task_class="dossier",
            )

    payload = {
        "candidate": candidate,
        "horizontal_analysis": {
            key: conversation.get(key)
            for key in (
                "behavior_type", "direction", "novelty", "signals", "entities",
                "durability_evidence", "limitations",
            )
        },
        "evidence": evidence,
    }
    try:
        parsed = _parse(await llm_call_fn(
            _SYSTEM, json.dumps(payload, ensure_ascii=False)
        ))
    except Exception as exc:
        return InvestingInterpretation(
            status="error",
            limitations=["Investment interpretation failed; horizontal evidence remains valid."],
            error=str(exc)[:200],
        )

    companies = []
    unsupported = False
    for raw in parsed.get("companies", []):
        if not isinstance(raw, dict):
            continue
        ids = raw.get("evidence_ids")
        if not isinstance(ids, list) or not ids or any(item not in valid_ids for item in ids):
            unsupported = True
            continue
        mechanism = raw.get("mechanism")
        confidence = raw.get("mapping_confidence")
        if mechanism not in _MECHANISMS or not isinstance(confidence, (int, float)):
            continue
        if not 0 <= float(confidence) <= 1:
            continue
        name = raw.get("company_name")
        if not isinstance(name, str) or not name.strip():
            continue
        companies.append({
            "company_name": name[:200],
            "ticker": raw.get("ticker") if isinstance(raw.get("ticker"), str) else None,
            "mapping_confidence": float(confidence),
            "mechanism": mechanism,
            "rationale": str(raw.get("rationale") or "")[:500],
            "evidence_ids": list(dict.fromkeys(ids)),
        })

    materiality = parsed.get("materiality")
    if materiality not in _MATERIALITY:
        materiality = "not_established"
    direction = parsed.get("signal_direction")
    if direction not in _DIRECTION:
        direction = "no_directional_inference"
    limitations = [
        str(item)[:300] for item in parsed.get("limitations", [])
        if isinstance(item, str)
    ]

    # Enforced outside the model: a named entity with no economic mechanism is
    # a mapping lead, not materiality or direction.
    established_mechanisms = [
        item for item in companies if item["mechanism"] != "unknown"
    ]
    if not established_mechanisms:
        materiality = "not_established"
        direction = "no_directional_inference"
        limitations.append(
            "Entity matching alone does not establish economic exposure or materiality."
        )
    if unsupported:
        limitations.append("Unsupported company mappings were rejected.")

    return InvestingInterpretation(
        status="complete",
        companies=companies,
        materiality=materiality,
        signal_direction=direction,
        signal_freshness=str(candidate.get("source_started_at") or "unknown"),
        invalidating_evidence=[
            str(item)[:300] for item in parsed.get("invalidating_evidence", [])
            if isinstance(item, str)
        ],
        required_next_diligence=[
            str(item)[:300] for item in parsed.get("required_next_diligence", [])
            if isinstance(item, str)
        ],
        limitations=list(dict.fromkeys(limitations)),
    )
