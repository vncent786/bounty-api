"""Deterministic seed-to-observation extraction for adaptive Radar investigation."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from typing import Any, Mapping, Sequence

from social_scraper.investing.qualification import (
    BEHAVIOUR_PHRASES,
    BROAD_PANEL_TERMS,
    GENERIC_TOPICS,
    is_specific_anchor,
)


_PROMOTIONAL_OR_REPORTING = re.compile(
    r"\b(sponsored|advertisement|promo code|use my code|breaking news|news roundup|"
    r"according to|reported by|press release|giveaway|limited time offer|promoted by)\b",
    re.IGNORECASE,
)
_BOUNDARY_TOKENS = {
    "after", "although", "and", "because", "before", "but", "despite", "during",
    "except", "for", "from", "if", "or", "since", "so", "than", "that", "then",
    "though", "until", "when", "where", "while", "with", "without", "yet",
}
_LEADING_TOKENS = {
    "a", "an", "another", "any", "her", "his", "its", "my", "our", "some", "that",
    "the", "their", "these", "this", "those", "your",
}
_TRAILING_NOISE = {
    "again", "already", "anymore", "everywhere", "lately", "now", "online", "today",
}


def normalize_observation_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"https?://\S+", " ", text)
    return " ".join(re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).split())


def _singularize_last_token(value: str) -> str:
    tokens = value.split()
    if not tokens:
        return value
    last = tokens[-1]
    if len(last) > 4 and last.endswith("ies"):
        tokens[-1] = f"{last[:-3]}y"
    elif len(last) > 3 and last.endswith("s") and not last.endswith(("ss", "us")):
        tokens[-1] = last[:-1]
    return " ".join(tokens)


def _engagement_total(item: Mapping[str, Any]) -> int:
    raw = item.get("engagement") if isinstance(item.get("engagement"), Mapping) else {}
    total = 0
    for key in ("comments", "replies", "shares", "likes", "upvotes", "views"):
        value = raw.get(key)
        if isinstance(value, bool) or value is None:
            continue
        try:
            total += max(0, int(value))
        except (TypeError, ValueError):
            continue
    return total


def _phrase_location(tokens: list[str], phrase: str) -> tuple[int, int] | None:
    phrase_tokens = normalize_observation_text(phrase).split()
    if not phrase_tokens:
        return None
    for index in range(len(tokens) - len(phrase_tokens) + 1):
        if tokens[index:index + len(phrase_tokens)] == phrase_tokens:
            return index, index + len(phrase_tokens)
    return None


def _object_after(tokens: list[str], end: int) -> str:
    values = list(tokens[end:])
    while values and values[0] in _LEADING_TOKENS:
        values.pop(0)
    selected = []
    for token in values:
        if token in _BOUNDARY_TOKENS:
            break
        selected.append(token)
        if len(selected) >= 6:
            break
    while selected and selected[-1] in _TRAILING_NOISE:
        selected.pop()
    return _singularize_last_token(" ".join(selected))


def _object_before(tokens: list[str], start: int) -> str:
    values = tokens[:start]
    selected = []
    for token in reversed(values):
        if token in _BOUNDARY_TOKENS or token in {"i", "im", "ive", "we", "they", "people"}:
            break
        selected.append(token)
        if len(selected) >= 6:
            break
    selected.reverse()
    while selected and selected[0] in _LEADING_TOKENS:
        selected.pop(0)
    return _singularize_last_token(" ".join(selected))


def _extract_record_observations(item: Mapping[str, Any]) -> list[dict[str, str]]:
    raw_text = str(item.get("text") or "")
    if not raw_text or _PROMOTIONAL_OR_REPORTING.search(raw_text):
        return []
    results = []
    clauses = re.split(r"[,.!?;:\n\r\u2026]+", raw_text)
    for raw_clause in clauses:
        clause = normalize_observation_text(raw_clause)
        tokens = clause.split()
        if not tokens:
            continue
        for behavior_type, phrases in BEHAVIOUR_PHRASES.items():
            for phrase in phrases:
                location = _phrase_location(tokens, phrase)
                if location is None:
                    continue
                start, end = location
                anchor = _object_after(tokens, end) or _object_before(tokens, start)
                anchor = normalize_observation_text(anchor)
                if not anchor or anchor in GENERIC_TOPICS or anchor in BROAD_PANEL_TERMS:
                    continue
                if not is_specific_anchor(anchor) or len(anchor.split()) < 2:
                    continue
                results.append({
                    "anchor_text": anchor,
                    "normalized_anchor": anchor,
                    "object_phrase": anchor,
                    "behavior_type": behavior_type,
                    "behavior_phrase": normalize_observation_text(phrase),
                })
    unique = {}
    for result in results:
        unique[(result["normalized_anchor"], result["behavior_type"])] = result
    return list(unique.values())


def make_query_lineage_id(
    *, panel_id: str, platform: str, seed_query: str, anchor_id: str, query: str,
) -> str:
    payload = "\x1f".join((
        normalize_observation_text(panel_id),
        normalize_observation_text(platform),
        normalize_observation_text(seed_query),
        normalize_observation_text(anchor_id),
        normalize_observation_text(query),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def extract_observation_anchors(
    records: Sequence[Mapping[str, Any]], *, panel_id: str, seed_query: str,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[tuple[Mapping[str, Any], dict[str, str]]]] = defaultdict(list)
    for source in records:
        item = dict(source)
        if str(item.get("panel_id") or "") != str(panel_id):
            continue
        if str(item.get("record_type") or "root") != "root":
            continue
        for observation in _extract_record_observations(item):
            key = (observation["normalized_anchor"], observation["behavior_type"])
            groups[key].append((item, observation))

    anchors = []
    for (normalized_anchor, behavior_type), rows in groups.items():
        evidence_ids = sorted({str(item.get("id")) for item, _ in rows if item.get("id")})
        root_ids = sorted({
            str(item.get("root_post_external_id") or item.get("external_id"))
            for item, _ in rows
            if item.get("root_post_external_id") or item.get("external_id")
        })
        authors = {
            str(item.get("creator_id") or item.get("author") or "")
            for item, _ in rows
        }
        authors.discard("")
        platforms = sorted({str(item.get("platform") or "unknown") for item, _ in rows})
        communities = sorted({str(item.get("community_id")) for item, _ in rows if item.get("community_id")})
        anchor_id = hashlib.sha256(
            f"{panel_id}|{behavior_type}|{normalized_anchor}".encode("utf-8")
        ).hexdigest()[:24]
        observation = rows[0][1]
        anchors.append({
            "anchor_id": anchor_id,
            "panel_id": str(panel_id),
            "anchor_text": observation["anchor_text"],
            "normalized_anchor": normalized_anchor,
            "object_phrase": observation["object_phrase"],
            "behavior_type": behavior_type,
            "behavior_phrase": observation["behavior_phrase"],
            "seed_query": str(seed_query),
            "source_evidence_ids": evidence_ids,
            "source_root_external_ids": root_ids,
            "support_count": len(evidence_ids),
            "distinct_authors": len(authors),
            "platforms": platforms,
            "communities": communities,
            "engagement_total": sum(_engagement_total(item) for item, _ in rows),
            "query_lineage_id": make_query_lineage_id(
                panel_id=str(panel_id),
                platform="seed",
                seed_query=str(seed_query),
                anchor_id=anchor_id,
                query=normalized_anchor,
            ),
        })
    return sorted(
        anchors,
        key=lambda item: (
            -int(item["support_count"]),
            -int(item["distinct_authors"]),
            -len(item["platforms"]),
            -int(item["engagement_total"]),
            str(item["normalized_anchor"]),
            str(item["anchor_id"]),
        ),
    )


def _token_similarity(left: str, right: str) -> float:
    a, b = set(left.split()), set(right.split())
    return len(a & b) / max(1, len(a | b))


def plan_adaptive_anchor_batches(
    panel_anchors: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    panel_order: Sequence[str],
    max_total: int = 20,
) -> dict[str, list[dict[str, Any]]]:
    """Cover panels once, then spend a small reserve on quiet exploration anchors."""
    limit = max(0, int(max_total))
    planned: dict[str, list[dict[str, Any]]] = {
        str(panel_id): [] for panel_id in panel_order
    }
    used = 0
    for panel_id in panel_order:
        values = [dict(value) for value in panel_anchors.get(str(panel_id), ())]
        if not values or used >= limit:
            continue
        planned[str(panel_id)].append(values[0])
        used += 1

    extras = []
    for panel_index, panel_id in enumerate(panel_order):
        for value in panel_anchors.get(str(panel_id), ())[1:]:
            item = dict(value)
            extras.append((
                int(item.get("engagement_total") or 0),
                -int(item.get("support_count") or 0),
                panel_index,
                str(item.get("anchor_id") or ""),
                str(panel_id),
                item,
            ))
    extras.sort(key=lambda row: row[:-1])
    for _engagement, _support, _panel_index, _anchor_id, panel_id, item in extras:
        if used >= limit:
            break
        planned[panel_id].append(item)
        used += 1
    return planned


def select_adaptive_anchors(
    anchors: Sequence[Mapping[str, Any]], *,
    high_support_limit: int = 4,
    exploration_limit: int = 2,
) -> list[dict[str, Any]]:
    ordered = sorted(
        (dict(item) for item in anchors),
        key=lambda item: (
            -int(item.get("support_count") or 0),
            -int(item.get("distinct_authors") or 0),
            -len(item.get("platforms") or []),
            -int(item.get("engagement_total") or 0),
            str(item.get("normalized_anchor") or ""),
            str(item.get("anchor_id") or ""),
        ),
    )
    selected = []
    for item in ordered:
        if len(selected) >= max(0, int(high_support_limit)):
            break
        if any(
            _token_similarity(str(item.get("normalized_anchor") or ""), str(existing.get("normalized_anchor") or "")) >= 0.8
            for existing in selected
        ):
            continue
        selected.append(item)

    selected_ids = {str(item.get("anchor_id")) for item in selected}
    exploration = sorted(
        (item for item in ordered if str(item.get("anchor_id")) not in selected_ids),
        key=lambda item: (
            int(item.get("engagement_total") or 0),
            -int(item.get("distinct_authors") or 0),
            str(item.get("normalized_anchor") or ""),
            str(item.get("anchor_id") or ""),
        ),
    )
    for item in exploration:
        if len(selected) >= max(0, int(high_support_limit)) + max(0, int(exploration_limit)):
            break
        if any(
            _token_similarity(str(item.get("normalized_anchor") or ""), str(existing.get("normalized_anchor") or "")) >= 0.8
            for existing in selected
        ):
            continue
        selected.append(item)
    return selected
