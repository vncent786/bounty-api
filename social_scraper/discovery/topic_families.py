"""Deterministic evidence edges and conservative topic-family planning.

Correlational signals are retained for inspection but can never form a family
without a separate merge-eligible relationship signal.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

RELATIONSHIPS = {
    "alias", "broader", "narrower", "enabling_technology", "alternative",
    "associated_event", "related_distinct", "uncertain",
}
EDGE_KINDS = {
    "trend_keyword_overlap", "root_reply_cooccurrence", "shared_entity",
    "shared_url", "repost_cluster", "content_cluster",
    "temporal_co_movement", "geographic_co_movement",
}
CORRELATIONAL_KINDS = {"temporal_co_movement", "geographic_co_movement"}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def candidate_key(geo: str, keyword: str) -> str:
    geo_key = str(geo or "").strip().upper()
    keyword_key = _norm(keyword)
    if not geo_key:
        raise ValueError("geo is required")
    if not keyword_key:
        raise ValueError("keyword is required")
    return f"{geo_key}:{keyword_key}"


def trend_keyword_evidence(*, left_keyword: str, left_terms: Sequence[Any],
                           right_keyword: str, right_terms: Sequence[Any]) -> dict | None:
    left = {_norm(item) for item in left_terms if _norm(item)}
    right = {_norm(item) for item in right_terms if _norm(item)}
    shared = sorted(left & right)
    if not shared:
        return None
    union = left | right
    return {
        "kind": "trend_keyword_overlap",
        "left_in_right_related": _norm(left_keyword) in right,
        "right_in_left_related": _norm(right_keyword) in left,
        "shared_terms": shared,
        "term_overlap_ratio": round(len(shared) / len(union), 12) if union else 0.0,
    }


def cooccurrence_evidence(left_records: Sequence[Mapping[str, Any]],
                          right_records: Sequence[Mapping[str, Any]]) -> dict | None:
    def key(item: Mapping[str, Any]) -> tuple[str, str]:
        return (_norm(item.get("platform")), _norm(item.get("external_id")))
    right_keys = {key(item) for item in right_records if all(key(item))}
    shared = []
    seen = set()
    for item in left_records:
        item_key = key(item)
        if item_key in right_keys and item_key not in seen:
            seen.add(item_key)
            shared.append({
                "platform": str(item.get("platform") or "").strip(),
                "external_id": str(item.get("external_id") or "").strip(),
                "record_type": str(item.get("record_type") or "").strip(),
            })
    shared.sort(key=lambda item: (item["platform"], item["external_id"]))
    if not shared:
        return None
    return {
        "kind": "root_reply_cooccurrence",
        "shared_item_count": len(shared),
        "shared_root_count": sum(item["record_type"] == "root" for item in shared),
        "shared_reply_count": sum(item["record_type"] == "reply" for item in shared),
        "sample_items": shared[:20],
    }


def shared_artifact_evidence(kind: str, left_values: Sequence[Any],
                             right_values: Sequence[Any]) -> dict | None:
    if kind not in {"shared_entity", "shared_url"}:
        raise ValueError("kind must be shared_entity or shared_url")
    left = {_norm(value) for value in left_values if _norm(value)}
    right = {_norm(value) for value in right_values if _norm(value)}
    shared = sorted(left & right)
    if not shared:
        return None
    return {"kind": kind, "shared_values": shared}


def cluster_evidence(kind: str, left_ids: Sequence[Any], right_ids: Sequence[Any]) -> dict | None:
    if kind not in {"repost_cluster", "content_cluster"}:
        raise ValueError("kind must be repost_cluster or content_cluster")
    shared = sorted({_norm(value) for value in left_ids if _norm(value)} &
                    {_norm(value) for value in right_ids if _norm(value)})
    if not shared:
        return None
    return {"kind": kind, "shared_cluster_ids": shared}


def temporal_co_movement_evidence(left_points: Sequence[Mapping[str, Any]],
                                  right_points: Sequence[Mapping[str, Any]]) -> dict | None:
    right = {str(point.get("observed_at")): point.get("search_volume") for point in right_points}
    aligned: list[list[Any]] = []
    excluded = 0
    for point in left_points:
        stamp = str(point.get("observed_at"))
        left_value = point.get("search_volume")
        if stamp not in right or left_value is None or right[stamp] is None:
            excluded += 1
            continue
        if isinstance(left_value, bool) or isinstance(right[stamp], bool):
            excluded += 1
            continue
        aligned.append([stamp, left_value, right[stamp]])
    if len(aligned) < 2:
        return None
    xs = [float(item[1]) for item in aligned]
    ys = [float(item[2]) for item in aligned]
    xmean, ymean = sum(xs) / len(xs), sum(ys) / len(ys)
    numerator = sum((x - xmean) * (y - ymean) for x, y in zip(xs, ys))
    denominator = math.sqrt(sum((x - xmean) ** 2 for x in xs) *
                            sum((y - ymean) ** 2 for y in ys))
    correlation = 0.0 if denominator == 0 else numerator / denominator
    return {
        "kind": "temporal_co_movement", "pair_count": len(aligned),
        "excluded_missing": excluded, "correlation": round(correlation, 12),
        "aligned_points": aligned,
    }


def geographic_co_movement_evidence(left_geos: Sequence[Any],
                                     right_geos: Sequence[Any]) -> dict | None:
    shared = sorted({str(value).strip().upper() for value in left_geos if str(value).strip()} &
                    {str(value).strip().upper() for value in right_geos if str(value).strip()})
    return {"kind": "geographic_co_movement", "shared_geos": shared} if shared else None


def _strength(evidence: Mapping[str, Any]) -> float:
    kind = evidence["kind"]
    if kind == "trend_keyword_overlap":
        return float(evidence.get("term_overlap_ratio") or 0.0)
    if kind == "temporal_co_movement":
        return float(evidence.get("correlation") or 0.0)
    return 1.0


def build_edge(left_candidate_key: str, right_candidate_key: str,
               evidence: Mapping[str, Any], *, observed_at: datetime | str) -> dict:
    if not isinstance(evidence, Mapping) or not evidence:
        raise ValueError("evidence must be a non-empty mapping")
    kind = str(evidence.get("kind") or "")
    if kind not in EDGE_KINDS:
        raise ValueError(f"unknown evidence kind: {kind}")
    left, right = sorted((str(left_candidate_key), str(right_candidate_key)))
    if left == right:
        raise ValueError("edge requires two different candidates")
    stamp = observed_at.isoformat() if isinstance(observed_at, datetime) else str(observed_at)
    return {
        "left_candidate_key": left, "right_candidate_key": right,
        "edge_type": kind, "strength": _strength(evidence),
        "evidence": dict(evidence), "observed_at": stamp,
    }


def derive_relationship(edges: Sequence[Mapping[str, Any]]) -> dict:
    kinds = sorted({str(edge.get("edge_type") or edge.get("evidence", {}).get("kind") or "")
                    for edge in edges})
    unknown = [kind for kind in kinds if kind not in EDGE_KINDS]
    if unknown:
        raise ValueError(f"unknown evidence kind: {unknown[0]}")
    merge_kinds = set(kinds) - CORRELATIONAL_KINDS
    mutual_alias = any(
        kind == "trend_keyword_overlap" and
        bool(edge.get("evidence", {}).get("left_in_right_related")) and
        bool(edge.get("evidence", {}).get("right_in_left_related"))
        for edge, kind in ((edge, str(edge.get("edge_type") or edge.get("evidence", {}).get("kind"))) for edge in edges)
    )
    if mutual_alias:
        relationship, confidence, eligible = "alias", "high", True
    elif merge_kinds:
        relationship = "related_distinct"
        confidence = "high" if len(merge_kinds) >= 2 else "medium"
        eligible = True
    else:
        relationship, confidence, eligible = "uncertain", "low", False
    return {"relationship": relationship, "confidence": confidence,
            "merge_eligible": eligible, "evidence_kinds": kinds}


def plan_family_links(edges: Sequence[Mapping[str, Any]]) -> list[dict]:
    if not edges:
        return []
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for edge in edges:
        kind = str(edge.get("edge_type") or edge.get("evidence", {}).get("kind") or "")
        if kind not in EDGE_KINDS:
            raise ValueError(f"unknown evidence kind: {kind}")
        left, right = sorted((str(edge["left_candidate_key"]), str(edge["right_candidate_key"])))
        grouped.setdefault((left, right), []).append(edge)
    parent: dict[str, str] = {}
    def find(item: str) -> str:
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item
    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)
    links = []
    for (left, right), pair_edges in sorted(grouped.items()):
        derived = derive_relationship(pair_edges)
        if not derived["merge_eligible"]:
            continue
        union(left, right)
        links.append({
            "left": left, "right": right,
            "relationship": derived["relationship"],
            "confidence": derived["confidence"],
            "evidence_kinds": derived["evidence_kinds"],
            "edge_ids": sorted(int(edge["id"]) for edge in pair_edges if edge.get("id") is not None),
        })
    components: dict[str, set[str]] = {}
    for item in parent:
        components.setdefault(find(item), set()).add(item)
    result = []
    for root, members in sorted(components.items()):
        component_links = [link for link in links if link["left"] in members and link["right"] in members]
        result.append({"members": sorted(members), "links": component_links})
    return result
