"""Deterministic Camillo-style qualification over already collected evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import date
from typing import Any, Mapping, Sequence


GENERIC_TOPICS = {
    "ai", "artificial intelligence", "ai software", "artificial intelligence software",
    "inflation", "fitness", "news", "weather", "technology", "tech", "software",
    "crypto", "stocks", "shopping", "viral",
}
GENERIC_TOKENS = {"ai", "artificial", "intelligence", "software", "technology", "tech", "app", "apps"}
BROAD_PANEL_TERMS = {
    "apparel", "beauty", "cleaning", "coffee", "device", "drink", "food",
    "grocery", "haircare", "headphones", "household", "makeup", "pet",
    "restaurant", "shoes", "skincare", "smartwatch", "snack", "wearable",
}
UNSUPPORTED_CLAIM = re.compile(
    r"\b(guaranteed|confirmed|ceo|revenue|profit|earnings|stock|shares?|ticker|"
    r"million|billion|trillion|ten[- ]?bagger|\d+x|\d+(?:\.\d+)?%)\b",
    re.IGNORECASE,
)
ALLOWED_BEHAVIOURS = {
    "purchase", "adoption", "switching", "shortage", "rejection",
    "pain_point", "price_change", "workaround",
}
BEHAVIOUR_PHRASES = {
    "purchase": ("i bought", "we bought", "purchased", "ordered", "buying", "repurchased"),
    "adoption": ("started using", "now use", "installed", "adopted", "trying it", "new use"),
    "switching": ("switched to", "switching to", "moved from", "replaced my", "instead of"),
    "shortage": ("sold out", "out of stock", "can't find", "cannot find", "shortage", "restock"),
    "rejection": ("stopped using", "stopped buying", "returned it", "cancelled", "canceled", "boycott"),
    "pain_point": ("problem", "issue", "doesn't work", "not working", "pain", "struggling"),
    "price_change": ("price increase", "price hike", "more expensive", "cheaper", "discount"),
    "workaround": ("workaround", "hack", "temporary fix", "instead of"),
}


def _norm(value: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).split())


def is_specific_anchor(value: Any) -> bool:
    normalized = _norm(value)
    if not normalized or normalized in GENERIC_TOPICS or normalized in BROAD_PANEL_TERMS:
        return False
    tokens = normalized.split()
    return not (len(tokens) == 1 and tokens[0] in GENERIC_TOKENS)


def _behavior_applies_to_anchor(
    text: Any,
    anchors: Sequence[str],
    phrases: Sequence[str],
    behavior: str,
) -> bool:
    """Require the behavior phrase and specific anchor to form one local claim."""
    directional = behavior in {"purchase", "adoption", "switching", "rejection"}
    object_bridge = (
        r"(?:\s+(?:a|an|the|my|our|this|that|these|those|another))?"
    )
    state_bridge = (
        r"(?:\s+(?:is|are|was|were|keep|keeps|remain|remains|always|often|"
        r"frequently|still|now|has|have|really|constantly|repeatedly)){0,2}"
    )
    clauses = re.split(r"[,.!?;:\n\r\u2026]+", str(text or ""))
    for raw_clause in clauses:
        clause = _norm(raw_clause)
        if not clause:
            continue
        for phrase in (_norm(value) for value in phrases):
            if not phrase:
                continue
            phrase_pattern = re.escape(phrase)
            for anchor in anchors:
                if not anchor:
                    continue
                anchor_pattern = rf"{re.escape(anchor)}s?"
                forward = (
                    rf"(?:^|\s){phrase_pattern}{object_bridge}\s+"
                    rf"{anchor_pattern}$"
                )
                if re.search(forward, clause):
                    return True
                if directional:
                    continue
                reverse = (
                    rf"(?:^|\s){anchor_pattern}{state_bridge}\s+"
                    rf"{phrase_pattern}$"
                )
                if re.search(reverse, clause):
                    return True
    return False


def _gate(state: str, reason: str, metrics: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "state": state,
        "passed": True if state == "pass" else False if state == "fail" else None,
        "reason": reason,
        "metrics": dict(metrics or {}),
    }


def _candidate_id(label: str, anchors: Sequence[str]) -> str:
    material = json.dumps(
        ["private-radar-candidate/1", _norm(label), sorted(_norm(anchor) for anchor in anchors)],
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def qualify_candidate(
    proposal: Mapping[str, Any],
    *,
    evidence: Sequence[Mapping[str, Any]],
    windows: Sequence[Mapping[str, Any]],
    parity: Mapping[str, Any],
) -> dict[str, Any]:
    """Return an inspectable fail-closed qualification decision."""
    label = str(proposal.get("label") or "").strip()
    behaviour = str(proposal.get("behaviour_type") or "").strip()
    anchors = [str(value).strip() for value in proposal.get("anchor_terms") or [] if str(value).strip()]
    evidence_by_id = {str(item.get("id")): dict(item) for item in evidence if item.get("id")}
    requested_ids = list(dict.fromkeys(str(value) for value in proposal.get("evidence_ids") or []))
    cited = [evidence_by_id[eid] for eid in requested_ids if eid in evidence_by_id]

    normalized_label = _norm(label)
    normalized_anchors = [_norm(anchor) for anchor in anchors if _norm(anchor)]
    anchor_supported = bool(normalized_anchors) and all(
        any(anchor in _norm(item.get("text")) for item in cited)
        for anchor in normalized_anchors
    )
    specificity_ok = (
        len(normalized_label.split()) >= 3
        and normalized_label not in GENERIC_TOPICS
        and all(is_specific_anchor(anchor) for anchor in anchors)
        and anchor_supported
    )
    specificity = _gate(
        "pass" if specificity_ok else "fail",
        "Specific product/behaviour anchor is supported by cited evidence."
        if specificity_ok else "Topic is generic or its anchor terms are unsupported.",
        {"anchor_terms": anchors},
    )

    phrases = BEHAVIOUR_PHRASES.get(behaviour, ())
    behavior_records = [
        item for item in cited
        if _behavior_applies_to_anchor(
            item.get("text"), normalized_anchors, phrases, behaviour
        )
    ]
    behavior_authors = {
        f"{item.get('platform')}:{item.get('author')}"
        for item in behavior_records if item.get("author")
    }
    behavior_ok = behaviour in ALLOWED_BEHAVIOURS and len(behavior_records) >= 2 and len(behavior_authors) >= 2
    behavior_gate = _gate(
        "pass" if behavior_ok else "fail",
        "Concrete behavior appears in at least two independent cited voices."
        if behavior_ok else "Concrete behavior is not independently supported.",
        {"behaviour_type": behaviour, "records": len(behavior_records), "authors": len(behavior_authors)},
    )

    authors = {
        f"{item.get('platform')}:{item.get('author')}"
        for item in cited if item.get("author")
    }
    platforms = {str(item.get("platform")) for item in cited}
    roots = {
        f"{item.get('platform')}:{item.get('external_id') or item.get('url') or item.get('id')}"
        for item in cited
    }
    text_counts = Counter(_norm(item.get("text")) for item in cited if _norm(item.get("text")))
    largest_copy_group = max(text_counts.values(), default=0)
    breadth_ok = (
        len(authors) >= 2 and len(roots) >= 2 and len(platforms) >= 2
        and largest_copy_group < max(2, len(cited))
    )
    breadth = _gate(
        "pass" if breadth_ok else "fail",
        "Evidence contains independent roots and voices after copy checks."
        if breadth_ok else "Evidence is too concentrated in one voice, root, or copied text.",
        {"authors": len(authors), "roots": len(roots), "platforms": len(platforms), "largest_copy_group": largest_copy_group},
    )

    window_rows = [dict(row) for row in windows]
    expected_keys = {"current", "prior_1", "prior_2", "prior_3"}
    keys = [str(row.get("window_key") or "") for row in window_rows]
    anchor_queries = {str(row.get("anchor_query") or "") for row in window_rows}
    intervals = []
    valid_intervals = True
    try:
        for row in window_rows:
            start = date.fromisoformat(str(row.get("start_date") or ""))
            end = date.fromisoformat(str(row.get("end_date") or ""))
            if (end - start).days != 7:
                valid_intervals = False
            intervals.append((start, end))
    except ValueError:
        valid_intervals = False
    non_overlapping = valid_intervals and all(
        left[1] <= right[0] or right[1] <= left[0]
        for index, left in enumerate(intervals)
        for right in intervals[index + 1:]
    )
    comparable = (
        len(window_rows) == 4
        and set(keys) == expected_keys
        and len(keys) == len(set(keys))
        and len(anchor_queries) == 1
        and "" not in anchor_queries
        and valid_intervals
        and non_overlapping
        and all(row.get("status") == "complete" and not row.get("capped") for row in window_rows)
    )
    current = next((row for row in window_rows if row.get("window_key") == "current"), {})
    prior = [row for row in window_rows if row.get("window_key") != "current"]
    current_count = int(current.get("result_count") or 0)
    current_authors = int(current.get("unique_authors") or 0)
    prior_counts = [int(row.get("result_count") or 0) for row in prior]
    if not comparable:
        anomaly = _gate(
            "unknown", "Comparable uncapped historical windows are unavailable.",
            {"current_count": current_count, "prior_counts": prior_counts},
        )
    else:
        anomaly_ok = current_count >= 3 and current_authors >= 2 and current_count > max(prior_counts, default=0)
        anomaly = _gate(
            "pass" if anomaly_ok else "fail",
            "Current publication count exceeds every comparable prior window."
            if anomaly_ok else "Current publication count is not a supported retrospective anomaly.",
            {"current_count": current_count, "current_authors": current_authors, "prior_counts": prior_counts},
        )

    parity_level = str(parity.get("level") or "unknown")
    if parity_level in {"L0", "L1", "L2"}:
        parity_gate = _gate("pass", "Checked coverage remains below repeated financial consensus.", {"level": parity_level})
    elif parity_level == "unknown":
        parity_gate = _gate("unknown", "Information parity could not be checked.", {"level": parity_level})
    else:
        parity_gate = _gate("fail", "The checked implication is already financially mainstreamed.", {"level": parity_level})

    required_fields = (
        "summary", "economic_mechanism", "why_investigate", "contradiction", "invalidation"
    )
    hypothesis_texts = [str(proposal.get(field) or "").strip() for field in required_fields]
    claims_safe = not any(UNSUPPORTED_CLAIM.search(text) for text in hypothesis_texts)
    summary_norm = _norm(proposal.get("summary"))
    summary_anchored = any(anchor in summary_norm for anchor in normalized_anchors)
    investable_ok = all(hypothesis_texts) and claims_safe and summary_anchored
    investigability = _gate(
        "pass" if investable_ok else "fail",
        "Possible mechanism, diligence question, counterevidence, and invalidation are explicit and contain no unsupported financial claim."
        if investable_ok else "The proposal is unanchored, incomplete, or contains an unsupported financial claim.",
    )

    gates = {
        "specificity": specificity,
        "behavior": behavior_gate,
        "anomaly": anomaly,
        "breadth": breadth,
        "parity": parity_gate,
        "investigability": investigability,
    }
    states = {gate["state"] for gate in gates.values()}
    if "fail" in states:
        status = "not_qualified"
    elif "unknown" in states:
        status = "unknown_due_to_coverage"
    else:
        status = "qualified"

    return {
        "candidate_id": _candidate_id(label, anchors),
        "qualification_status": status,
        "label": label,
        "behaviour_type": behaviour,
        "anchor_terms": anchors,
        "summary": str(proposal.get("summary") or "").strip(),
        "economic_mechanism": str(proposal.get("economic_mechanism") or "").strip(),
        "why_investigate": str(proposal.get("why_investigate") or "").strip(),
        "contradiction": str(proposal.get("contradiction") or "").strip(),
        "invalidation": str(proposal.get("invalidation") or "").strip(),
        "evidence_ids": [item["id"] for item in cited],
        "voice_count": len(authors),
        "platforms": sorted({str(item.get("platform")) for item in cited}),
        "gates": gates,
        "parity": dict(parity),
        "windows": window_rows,
    }
