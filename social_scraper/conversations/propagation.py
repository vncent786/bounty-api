"""Deterministic propagation summaries for already-deduplicated root posts.

Native repost relationships and exact normalized copies form propagation
clusters. Fuzzy similarity is deliberately excluded. Repost/copy authors do
not count as independent corroboration, while their observed reach remains
available with nullable metric coverage.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .deduplication import normalized_content_hash, root_identity


REACH_METRICS = (
    "likes",
    "upvotes",
    "comments",
    "replies",
    "views",
    "shares",
    "reposts",
    "bookmarks",
)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _platform(item: Mapping[str, Any]) -> str | None:
    platform = _text(item.get("platform"))
    return platform.casefold() if platform else None


def _external_id(item: Mapping[str, Any]) -> str | None:
    return _text(item.get("external_id")) or _text(item.get("post_id"))


def _stable_member_key(item: Mapping[str, Any], index: int) -> str:
    identity = root_identity(item)
    if identity is not None:
        return f"{identity.strategy}:{identity.value}"
    # Used only for deterministic ordering inside this supplied collection.
    return f"unidentified:{index:012d}"


def _is_native_repost(item: Mapping[str, Any]) -> bool:
    return item.get("is_repost") is True or _text(item.get("repost_of_external_id")) is not None


def _author_identity(item: Mapping[str, Any]) -> str | None:
    platform = _platform(item)
    if platform is None:
        return None
    author = item.get("author") if isinstance(item.get("author"), Mapping) else {}
    author_id = (
        _text(author.get("external_id"))
        or _text(author.get("id"))
        or _text(item.get("author_external_id"))
        or _text(item.get("author_id"))
    )
    if author_id:
        return f"{platform}:id:{author_id}"
    username = (
        _text(author.get("username"))
        or _text(item.get("author_username"))
    )
    if username:
        return f"{platform}:username:{username.casefold()}"
    return None


def _published_sort_key(item: Mapping[str, Any], member_key: str) -> tuple[bool, str, str]:
    published = _text(item.get("published_at")) or _text(item.get("created_at"))
    return published is None, published or "", member_key


def _metric(item: Mapping[str, Any], name: str) -> int | None:
    engagement = item.get("engagement")
    if not isinstance(engagement, Mapping):
        observation = item.get("observation")
        engagement = (
            observation.get("engagement")
            if isinstance(observation, Mapping)
            and isinstance(observation.get("engagement"), Mapping)
            else {}
        )
    value = engagement.get(name) if isinstance(engagement, Mapping) else None
    if value is None:
        value = item.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


class _UnionFind:
    def __init__(self, size: int):
        self.parents = list(range(size))

    def find(self, value: int) -> int:
        parent = self.parents[value]
        if parent != value:
            self.parents[value] = self.find(parent)
        return self.parents[value]

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            self.parents[right_root] = left_root
        else:
            self.parents[left_root] = right_root


@dataclass(frozen=True)
class PropagationCluster:
    """Observed roots connected by native repost or exact-copy evidence."""

    original_platform: str | None
    original_external_id: str | None
    original_observed: bool
    member_identities: tuple[str, ...]
    relationship_bases: tuple[str, ...]
    repost_root_count: int

    @property
    def member_count(self) -> int:
        return len(self.member_identities)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_platform": self.original_platform,
            "original_external_id": self.original_external_id,
            "original_observed": self.original_observed,
            "member_identities": list(self.member_identities),
            "member_count": self.member_count,
            "relationship_bases": list(self.relationship_bases),
            "repost_root_count": self.repost_root_count,
        }


@dataclass(frozen=True)
class PropagationSummary:
    """Required root/corroboration/propagation dimensions kept separate."""

    unique_root_count: int
    independent_author_count: int
    clusters: tuple[PropagationCluster, ...]
    propagation_reach: dict[str, Any]

    @property
    def repost_cluster_count(self) -> int:
        return len(self.clusters)

    @property
    def largest_repost_cluster_size(self) -> int:
        return max((cluster.member_count for cluster in self.clusters), default=0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "unique_root_count": self.unique_root_count,
            "independent_author_count": self.independent_author_count,
            "repost_cluster_count": self.repost_cluster_count,
            "largest_repost_cluster_size": self.largest_repost_cluster_size,
            "propagation_reach": self.propagation_reach,
        }


def summarize_propagation(
    roots: Iterable[Mapping[str, Any]],
) -> PropagationSummary:
    """Summarize propagation over roots deduplicated as collection observations.

    Native repost links are factual source relations. Separately addressable
    roots with an exact normalized content hash are grouped as exact copies;
    no direction is inferred beyond choosing the earliest observed member as a
    deterministic cluster origin. Near-duplicate similarity is not performed.
    """
    items: list[dict[str, Any]] = []
    for root in roots:
        if not isinstance(root, Mapping):
            raise TypeError("each root must be a mapping")
        items.append(dict(root))

    union_find = _UnionFind(len(items))
    member_keys = [_stable_member_key(item, index) for index, item in enumerate(items)]

    source_indexes: dict[tuple[str, str], int] = {}
    composite_indexes: dict[str, int] = {}
    for index, item in enumerate(items):
        platform = _platform(item)
        external_id = _external_id(item)
        if platform and external_id:
            source_indexes.setdefault((platform, external_id), index)
            composite_indexes.setdefault(f"{platform}:{external_id}", index)

    native_children: set[int] = set()
    missing_native_origins: dict[int, tuple[str | None, str]] = {}
    for child_index, item in enumerate(items):
        reference = _text(item.get("repost_of_external_id"))
        if not _is_native_repost(item):
            continue
        native_children.add(child_index)
        if reference is None:
            missing_native_origins[child_index] = (_platform(item), "")
            continue
        reference_platform = _text(item.get("repost_of_platform"))
        platform = reference_platform.casefold() if reference_platform else _platform(item)
        parent_index = source_indexes.get((platform, reference)) if platform else None
        if parent_index is None:
            parent_index = composite_indexes.get(reference)
        if parent_index is None and ":" in reference:
            reference_source, reference_id = reference.split(":", 1)
            parent_index = source_indexes.get((reference_source.casefold(), reference_id))
        if parent_index is None:
            missing_native_origins[child_index] = (platform, reference)
            continue
        union_find.union(child_index, parent_index)

    exact_groups: dict[str, list[int]] = defaultdict(list)
    for index, item in enumerate(items):
        content_hash = normalized_content_hash(item)
        if content_hash is not None:
            exact_groups[content_hash].append(index)
    exact_members: set[int] = set()
    for indexes in exact_groups.values():
        if len(indexes) < 2:
            continue
        first = indexes[0]
        exact_members.update(indexes)
        for index in indexes[1:]:
            union_find.union(first, index)

    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(items)):
        components[union_find.find(index)].append(index)

    native_components = {
        union_find.find(child)
        for child in native_children
    }
    exact_components = {
        union_find.find(index)
        for index in exact_members
    }
    # A source-native repost total is propagation evidence even if individual
    # copy roots were not returned. Keep the count as reach; never materialize
    # it as invented roots or authors.
    reported_repost_components = {
        union_find.find(index)
        for index, item in enumerate(items)
        if (_metric(item, "reposts") or 0) > 0
    }

    cluster_records: list[tuple[PropagationCluster, tuple[int, ...], set[int]]] = []
    for component_root, indexes in components.items():
        has_native = component_root in native_components
        has_exact = component_root in exact_components and len(indexes) > 1
        has_reported_reposts = component_root in reported_repost_components
        if not has_native and not has_exact and not has_reported_reposts:
            continue

        observed_original_candidates = [
            index for index in indexes if index not in native_children
        ]
        if observed_original_candidates:
            original_index = min(
                observed_original_candidates,
                key=lambda index: _published_sort_key(items[index], member_keys[index]),
            )
            original_observed = True
            original_platform = _platform(items[original_index])
            original_external_id = _external_id(items[original_index])
            propagated_indexes = set(indexes) - {original_index}
        else:
            original_index = None
            original_observed = False
            missing = [
                (index, missing_native_origins[index])
                for index in indexes
                if index in missing_native_origins
            ]
            if missing:
                _, (original_platform, original_external_id) = min(
                    missing, key=lambda entry: (entry[1][0] or "", entry[1][1], entry[0])
                )
                original_external_id = original_external_id or None
            else:
                display_index = min(indexes, key=lambda index: member_keys[index])
                original_platform = _platform(items[display_index])
                original_external_id = _external_id(items[display_index])
            propagated_indexes = set(indexes)

        relationship_bases = tuple(
            basis
            for basis, present in (
                ("native_repost", has_native),
                ("exact_content", has_exact),
                ("reported_repost_count", has_reported_reposts),
            )
            if present
        )
        cluster = PropagationCluster(
            original_platform=original_platform,
            original_external_id=original_external_id,
            original_observed=original_observed,
            member_identities=tuple(sorted(member_keys[index] for index in indexes)),
            relationship_bases=relationship_bases,
            repost_root_count=len(propagated_indexes),
        )
        cluster_records.append((cluster, tuple(indexes), propagated_indexes))

    cluster_records.sort(
        key=lambda entry: (
            entry[0].original_platform or "",
            entry[0].original_external_id or "",
            entry[0].member_identities,
        )
    )
    clusters = tuple(entry[0] for entry in cluster_records)
    clustered_indexes = {
        index for _, indexes, _ in cluster_records for index in indexes
    }
    propagated_indexes = {
        index for _, _, propagated in cluster_records for index in propagated
    }

    independent_authors = {
        author
        for index, item in enumerate(items)
        if index not in propagated_indexes
        for author in [_author_identity(item)]
        if author is not None
    }

    platforms = sorted(
        {
            platform
            for index in clustered_indexes
            for platform in [_platform(items[index])]
            if platform is not None
        }
    )
    engagement: dict[str, dict[str, int | None]] = {}
    for metric_name in REACH_METRICS:
        values = [
            value
            for index in sorted(clustered_indexes)
            for value in [_metric(items[index], metric_name)]
            if value is not None
        ]
        engagement[metric_name] = {
            "total": sum(values) if values else None,
            "observed_root_count": len(values),
        }

    propagation_reach = {
        "clustered_root_count": len(clustered_indexes),
        "repost_root_count": len(propagated_indexes),
        "platform_count": len(platforms),
        "platforms": platforms,
        "engagement": engagement,
    }
    return PropagationSummary(
        unique_root_count=len(items),
        independent_author_count=len(independent_authors),
        clusters=clusters,
        propagation_reach=propagation_reach,
    )


__all__ = [
    "PropagationCluster",
    "PropagationSummary",
    "REACH_METRICS",
    "summarize_propagation",
]
