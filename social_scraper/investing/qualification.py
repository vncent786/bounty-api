"""Deterministic Camillo-style qualification over already collected evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import date, datetime, timezone
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
    "purchase": ("i bought", "we bought", "bought", "purchased", "ordered", "buying", "repurchased"),
    "adoption": ("started using", "now use", "now have", "installed", "adopted", "trying it", "new use"),
    "switching": (
        "switched to", "switching to", "moved from", "replaced my", "replacing",
        "replaced", "instead of", "cancelling", "canceling", "cancelled", "canceled",
        "trying to cancel", "now have",
    ),
    "shortage": ("sold out", "out of stock", "can't find", "cannot find", "shortage", "restock"),
    "rejection": ("stopped using", "stopped buying", "returned it", "cancelled", "canceled", "boycott"),
    "pain_point": (
        "problem", "issue", "doesn't work", "not working", "pain", "struggling",
        "can't find", "cannot find", "hard to find", "wouldn't open", "won't open",
        "safety issue", "trapped",
    ),
    "price_change": ("price increase", "price hike", "more expensive", "cheaper", "discount"),
    "workaround": ("workaround", "hack", "temporary fix", "instead of"),
}

_SUMMARY_STOPWORDS = {
    "a", "an", "and", "as", "at", "for", "from", "in", "into", "of", "on",
    "or", "the", "their", "this", "to", "with",
}
_NEWS_REPORTING = re.compile(
    r"(?:^|\b)(breaking|news roundup|daily news|newsletter|according to|regulator(?:s)?|"
    r"ordered by|recall(?:ed)?|reported by|news report|issued its|million vehicles)(?:\b|:)",
    re.IGNORECASE,
)
_FIRSTHAND_VOICE = re.compile(
    r"\b(i|i ve|ive|i m|im|my|me|we|we ve|our|ours)\b",
    re.IGNORECASE,
)
_CREATOR_ACTION = re.compile(
    r"^(?:part\s+\d+\s+of\s+)?(?:replacing|cancelling|canceling|switching|"
    r"trying|started|stopped|bought|ordered)\b",
    re.IGNORECASE,
)


def _norm(value: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).split())


def is_specific_anchor(value: Any) -> bool:
    normalized = _norm(value)
    if not normalized or normalized in GENERIC_TOPICS or normalized in BROAD_PANEL_TERMS:
        return False
    tokens = normalized.split()
    return not (len(tokens) == 1 and tokens[0] in GENERIC_TOKENS)


def _phrase_is_negated(clause: str, phrase: str) -> bool:
    phrase_tokens = phrase.split()
    if not phrase_tokens or phrase_tokens[0] in {"not", "no", "never"}:
        return False
    tokens = clause.split()
    starts = [
        index for index in range(len(tokens) - len(phrase_tokens) + 1)
        if tokens[index:index + len(phrase_tokens)] == phrase_tokens
    ]
    if not starts:
        return False
    negations = {
        "not", "no", "never", "without", "isn", "wasn", "aren", "weren",
        "doesn", "didn", "don", "won",
    }
    return all(
        any(token in negations for token in tokens[max(0, index - 3):index])
        for index in starts
    )


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
        if behavior == "switching" and re.search(
            r"\b(?:haven t|have not) been\s+(?:to|back to|inside|at)\b.+\bever since\b",
            clause,
        ):
            if any(anchor and anchor in clause for anchor in anchors):
                return True
        for phrase in (_norm(value) for value in phrases):
            if not phrase or _phrase_is_negated(clause, phrase):
                continue
            phrase_pattern = re.escape(phrase)
            for anchor in anchors:
                if not anchor:
                    continue
                if anchor in clause and phrase in anchor:
                    return True
                anchor_pattern = rf"{re.escape(anchor)}s?"
                forward = (
                    rf"(?:^|\s){phrase_pattern}{object_bridge}\s+"
                    rf"{anchor_pattern}$"
                )
                if re.search(forward, clause):
                    return True
                if directional:
                    continue
                if anchor in clause and phrase in clause:
                    return True
                reverse = (
                    rf"(?:^|\s){anchor_pattern}{state_bridge}\s+"
                    rf"{phrase_pattern}$"
                )
                if re.search(reverse, clause):
                    return True
    return False


def _anchor_matches(text: Any, anchors: Sequence[str]) -> list[str]:
    normalized_text = _norm(text)
    return [anchor for anchor in anchors if anchor and anchor in normalized_text]


def _evidence_is_anchor_relevant(text: Any, anchors: Sequence[str]) -> bool:
    """Reject weak single-anchor collisions when a proposal supplies richer anchors."""
    matches = _anchor_matches(text, anchors)
    if not matches:
        return False
    if len(anchors) == 1 or any(len(match.split()) >= 3 for match in matches):
        return True
    return len(matches) >= 2


def _independent_root_representatives(
    items: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Collapse known propagation so copies cannot become independent voices."""
    representatives: list[dict[str, Any]] = []
    seen_clusters: set[str] = set()
    dropped = 0
    for source in items:
        item = dict(source)
        copy_cluster = _norm(item.get("copy_cluster_id"))
        normalized_text = _norm(item.get("text"))
        if copy_cluster:
            cluster_key = f"copy:{copy_cluster}"
        elif normalized_text:
            cluster_key = f"text:{normalized_text}"
        else:
            cluster_key = (
                "root:"
                f"{item.get('platform')}:{item.get('root_post_external_id') or item.get('external_id') or item.get('url') or item.get('id')}"
            )
        if cluster_key in seen_clusters:
            dropped += 1
            continue
        seen_clusters.add(cluster_key)
        representatives.append(item)
    return representatives, dropped


def _summary_supports_anchor(summary: Any, anchors: Sequence[str]) -> bool:
    normalized_summary = _norm(summary)
    for anchor in anchors:
        if anchor in normalized_summary:
            return True
        tokens = anchor.split()
        if len(tokens) < 2:
            continue
        for index in range(len(tokens) - 1):
            phrase = f"{tokens[index]} {tokens[index + 1]}"
            if (
                tokens[index] not in _SUMMARY_STOPWORDS
                and tokens[index + 1] not in _SUMMARY_STOPWORDS
                and phrase in normalized_summary
            ):
                return True
    return False


def _safe_metric(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _created_at(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _engagement_state(item: Mapping[str, Any]) -> tuple[bool, bool, dict[str, int | None]]:
    raw = item.get("engagement") if isinstance(item.get("engagement"), Mapping) else {}
    metrics = {
        name: _safe_metric(raw.get(name))
        for name in (
            "views", "likes", "comments", "shares", "collects", "upvotes",
            "replies", "reposts", "bookmarks",
        )
    }
    known = any(value is not None for value in metrics.values())
    interactions = sum(
        metrics[name] or 0
        for name in ("likes", "comments", "shares", "collects", "upvotes", "replies", "bookmarks")
    )
    engaged = (
        (metrics["comments"] or 0) + (metrics["replies"] or 0) >= 2
        or interactions >= 10
        or (metrics["views"] or 0) >= 1000
    )
    return known, engaged, metrics


def _evidence_kind(item: Mapping[str, Any], behaviour: str) -> str:
    text = str(item.get("text") or "").strip()
    normalized = _norm(text)
    if _NEWS_REPORTING.search(text):
        return "reportage"
    if _FIRSTHAND_VOICE.search(normalized):
        return "firsthand"
    if behaviour in {"purchase", "adoption", "switching", "rejection"} and _CREATOR_ACTION.search(normalized):
        return "firsthand"
    return "observation"


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
    requested_cited = [
        evidence_by_id[eid] for eid in requested_ids if eid in evidence_by_id
    ]
    eligible_roots = [
        item for item in requested_cited
        if str(item.get("record_type") or "root") == "root"
        and item.get("is_repost") is not True
    ]
    non_root_or_repost_dropped = len(requested_cited) - len(eligible_roots)

    normalized_label = _norm(label)
    normalized_anchors = [_norm(anchor) for anchor in anchors if _norm(anchor)]
    anchor_relevant_roots = [
        item for item in eligible_roots
        if _evidence_is_anchor_relevant(item.get("text"), normalized_anchors)
    ]
    cited, propagation_dropped = _independent_root_representatives(
        anchor_relevant_roots
    )
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
        _norm(item.get("author"))
        for item in behavior_records if _norm(item.get("author"))
    }
    behavior_ok = behaviour in ALLOWED_BEHAVIOURS and len(behavior_records) >= 2 and len(behavior_authors) >= 2
    behavior_gate = _gate(
        "pass" if behavior_ok else "fail",
        "Concrete behavior appears in at least two independent cited voices."
        if behavior_ok else "Concrete behavior is not independently supported.",
        {"behaviour_type": behaviour, "records": len(behavior_records), "authors": len(behavior_authors)},
    )

    evidence_kinds = {
        str(item["id"]): _evidence_kind(item, behaviour)
        for item in cited
    }
    firsthand_records = [
        item for item in behavior_records
        if evidence_kinds.get(str(item.get("id"))) == "firsthand"
    ]
    firsthand_authors = {
        _norm(item.get("author"))
        for item in firsthand_records if _norm(item.get("author"))
    }
    firsthand_platforms = {
        str(item.get("platform") or "unknown") for item in firsthand_records
    }
    engagement_by_id = {}
    known_engagement_records = 0
    engaged_records = 0
    for item in firsthand_records:
        known, engaged, metrics = _engagement_state(item)
        engagement_by_id[str(item["id"])] = metrics
        known_engagement_records += int(known)
        engaged_records += int(engaged)
    reportage_records = sum(kind == "reportage" for kind in evidence_kinds.values())
    quality_ok = (
        len(firsthand_authors) >= 3
        and len(firsthand_records) >= 3
        and known_engagement_records >= 2
        and engaged_records >= 1
        and (len(firsthand_platforms) >= 2 or engaged_records >= 2)
    )
    if quality_ok:
        quality_reason = "At least three independent firsthand voices have usable engagement or cross-platform support."
    elif reportage_records >= max(1, len(firsthand_records)):
        quality_reason = "Matched citations are dominated by reporting or commentary, not firsthand behavior."
    elif len(firsthand_authors) < 3:
        quality_reason = "Fewer than three independent firsthand voices support this behavior."
    elif known_engagement_records < 2:
        quality_reason = "Engagement was not captured for enough firsthand sources to judge whether the behavior is spreading."
    else:
        quality_reason = "The firsthand observations lack cross-platform breadth or visible engagement."
    evidence_quality = _gate(
        "pass" if quality_ok else "fail",
        quality_reason,
        {
            "firsthand_records": len(firsthand_records),
            "firsthand_authors": len(firsthand_authors),
            "firsthand_platforms": len(firsthand_platforms),
            "known_engagement_records": known_engagement_records,
            "engaged_records": engaged_records,
            "reportage_records": reportage_records,
            "proof_evidence_ids": [str(item["id"]) for item in firsthand_records],
        },
    )

    dated_firsthand = [
        (item, _created_at(item.get("created_at")))
        for item in firsthand_records
    ]
    dated_firsthand = [
        (item, timestamp) for item, timestamp in dated_firsthand
        if timestamp is not None
    ]
    timestamps = [timestamp for _item, timestamp in dated_firsthand]
    active_weeks = {
        (timestamp.isocalendar().year, timestamp.isocalendar().week)
        for timestamp in timestamps
    }
    day_counts = Counter(timestamp.date().isoformat() for timestamp in timestamps)
    max_day_records = max(day_counts.values(), default=0)
    span_days = (
        (max(timestamps).date() - min(timestamps).date()).days
        if timestamps else 0
    )
    one_day_cluster = (
        bool(timestamps)
        and span_days <= 1
        and max_day_records / len(timestamps) >= 0.7
    )
    persistence_ok = (
        len(timestamps) >= 3
        and len(active_weeks) >= 2
        and span_days >= 7
        and not one_day_cluster
    )
    if persistence_ok:
        persistence_reason = "Firsthand behavior persists across at least two weeks."
        social_pattern = "ongoing"
    elif one_day_cluster:
        persistence_reason = "The firsthand cluster is concentrated in one day and may be event-driven."
        social_pattern = "one_day_cluster"
    else:
        persistence_reason = "The checked firsthand evidence does not span enough time to establish persistence."
        social_pattern = "insufficient_history"
    persistence = _gate(
        "pass" if persistence_ok else "fail",
        persistence_reason,
        {
            "dated_firsthand_records": len(timestamps),
            "active_weeks": len(active_weeks),
            "span_days": span_days,
            "max_single_day_records": max_day_records,
        },
    )

    authors = {
        _norm(item.get("author"))
        for item in cited if _norm(item.get("author"))
    }
    platforms = {str(item.get("platform")) for item in cited}
    roots = {
        f"{item.get('platform')}:{item.get('external_id') or item.get('url') or item.get('id')}"
        for item in cited
    }
    text_counts = Counter(_norm(item.get("text")) for item in cited if _norm(item.get("text")))
    largest_copy_group = max(text_counts.values(), default=0)
    cross_platform = len(platforms) >= 2
    breadth_ok = (
        len(authors) >= 2 and len(roots) >= 2
        and largest_copy_group < max(2, len(cited))
    )
    breadth = _gate(
        "pass" if breadth_ok else "fail",
        (
            "Evidence contains independent roots and voices across multiple platforms."
            if cross_platform else
            "Evidence contains independent roots and voices on one platform; cross-platform confirmation is still absent."
        )
        if breadth_ok else "Evidence is too concentrated in one voice, root, or copied text.",
        {
            "authors": len(authors), "roots": len(roots), "platforms": len(platforms),
            "cross_platform": cross_platform, "largest_copy_group": largest_copy_group,
        },
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
    summary_anchored = _summary_supports_anchor(
        proposal.get("summary"), normalized_anchors
    )
    investable_ok = all(hypothesis_texts) and claims_safe and summary_anchored
    investigability = _gate(
        "pass" if investable_ok else "fail",
        "Possible mechanism, diligence question, counterevidence, and invalidation are explicit and contain no unsupported financial claim."
        if investable_ok else "The proposal is unanchored, incomplete, or contains an unsupported financial claim.",
    )

    gates = {
        "specificity": specificity,
        "behavior": behavior_gate,
        "evidence_quality": evidence_quality,
        "persistence": persistence,
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
        "panel_id": str(proposal.get("panel_id") or ""),
        "qualification_status": status,
        "label": label,
        "behaviour_type": behaviour,
        "social_pattern": social_pattern,
        "anchor_terms": anchors,
        "summary": str(proposal.get("summary") or "").strip(),
        "economic_mechanism": str(proposal.get("economic_mechanism") or "").strip(),
        "why_investigate": str(proposal.get("why_investigate") or "").strip(),
        "contradiction": str(proposal.get("contradiction") or "").strip(),
        "invalidation": str(proposal.get("invalidation") or "").strip(),
        "evidence_ids": [item["id"] for item in cited],
        "evidence_kinds": evidence_kinds,
        "engagement_by_id": engagement_by_id,
        "citation_filter": {
            "requested": len(requested_cited),
            "relevant": len(cited),
            "dropped": max(0, len(eligible_roots) - len(anchor_relevant_roots)),
            "propagation_dropped": propagation_dropped,
            "non_root_or_repost_dropped": non_root_or_repost_dropped,
        },
        "voice_count": len(authors),
        "platforms": sorted({str(item.get("platform")) for item in cited}),
        "gates": gates,
        "parity": dict(parity),
        "windows": window_rows,
    }
