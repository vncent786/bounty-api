"""Search-interest movement for Radar subjects.

Google Trends is a movement sensor, not behavioral evidence. Values are normalized
0-100 inside one comparable request and never used as social proof.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Mapping, Sequence


_QUERY_STOPWORDS = {
    "a", "an", "and", "after", "as", "at", "because", "before", "bought",
    "buy", "cancelling", "cancellation", "cancel", "from", "in", "into", "my",
    "of", "on", "pain", "post", "pushing", "replacing", "reported", "services",
    "streaming", "the", "their", "this", "to", "true", "using", "with",
    "adoption", "recall", "trying", "membership",
}


def _norm(value: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).split())


def derive_trajectory_query(candidate: Mapping[str, Any]) -> str:
    """Choose a concise repeated phrase without hardcoding a product or subject."""
    phrases = [
        str(candidate.get("label") or ""),
        *[str(value) for value in candidate.get("anchor_terms") or []],
    ]
    normalized = [_norm(value) for value in phrases if _norm(value)]
    token_counts = Counter(
        token for phrase in normalized for token in phrase.split()
        if token not in _QUERY_STOPWORDS and len(token) > 1
    )
    candidates: list[tuple[float, int, int, str]] = []
    seen = set()
    for phrase_index, phrase in enumerate(normalized):
        tokens = phrase.split()
        for size in (2, 3):
            for start in range(len(tokens) - size + 1):
                chunk = tokens[start:start + size]
                if any(token in _QUERY_STOPWORDS or len(token) <= 1 for token in chunk):
                    continue
                value = " ".join(chunk)
                if value in seen:
                    continue
                seen.add(value)
                average_frequency = sum(token_counts[token] for token in chunk) / size
                # Prefer repeated, concise phrases. Later phrases win exact ties so an
                # explicit anchor can beat broad label wording.
                candidates.append((average_frequency, -size, phrase_index, value))
    if candidates:
        base = max(candidates)[-1]
        base_tokens = base.split()
        single_entities = [
            _norm(value)
            for value in candidate.get("anchor_terms") or []
            if len(_norm(value).split()) == 1
            and str(value).strip()[:1].isupper()
            and _norm(value) not in _QUERY_STOPWORDS
            and _norm(value) not in base_tokens
        ]
        if single_entities and len(base_tokens) < 4:
            entity = single_entities[0]
            label_tokens = _norm(candidate.get("label")).split()
            if entity in label_tokens and base_tokens[0] in label_tokens:
                if label_tokens.index(entity) < label_tokens.index(base_tokens[0]):
                    base_tokens.insert(0, entity)
                else:
                    base_tokens.append(entity)
            else:
                base_tokens.append(entity)
        return " ".join(base_tokens[:4])
    fallback = [
        token for token, _count in token_counts.most_common(3)
        if token not in _QUERY_STOPWORDS
    ]
    return " ".join(fallback[:3]) or _norm(candidate.get("label"))[:80]


def collect_search_trajectory(
    query: str,
    *,
    trends=None,
    timeframe: str = "today 3-m",
    geo: str = "",
) -> dict[str, Any]:
    """Fetch one comparable normalized Google Trends series."""
    query = str(query or "").strip()
    base = {
        "query": query,
        "source": "Google Trends",
        "timeframe": timeframe,
        "geo": geo,
        "normalized": True,
        "points": [],
    }
    if not query:
        return {**base, "status": "failed", "error_category": "empty_query"}
    try:
        if trends is None:
            from trendspy import Trends
            trends = Trends(request_delay=2.0)
        frame = trends.interest_over_time([query], timeframe=timeframe, geo=geo)
        if frame is None or len(frame) == 0 or query not in frame:
            return {**base, "status": "insufficient_search_volume", "error_category": None}
        values = frame[query].tolist()
        dates = frame.index.tolist()
        points = [
            {"date": str(day)[:10], "value": int(value)}
            for day, value in zip(dates, values)
            if value is not None
        ]
    except Exception as exc:
        return {
            **base,
            "status": "failed",
            "error_category": type(exc).__name__,
        }
    nonzero = sum(int(point["value"]) > 0 for point in points)
    status = "complete" if len(points) >= 30 and nonzero >= 8 else "insufficient_search_volume"
    return {
        **base,
        "status": status,
        "points": points,
        "error_category": None,
        "nonzero_points": nonzero,
    }


def trajectory_is_usable(value: Mapping[str, Any] | None) -> bool:
    if not isinstance(value, Mapping) or value.get("status") != "complete":
        return False
    points: Sequence[Mapping[str, Any]] = value.get("points") or []
    return len(points) >= 30 and sum(int(point.get("value") or 0) > 0 for point in points) >= 8
