"""Deterministic root identity and collection-observation deduplication.

Deduplication is intentionally conservative. Stable source identity wins, then a
canonical URL. An exact normalized content hash is only used when a record has
neither stable identity, so separately addressable copies remain available to
propagation analysis. No fuzzy or near-duplicate matching happens here.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_IDENTITY_PRIORITY = {
    "platform_external_id": 0,
    "canonical_url": 1,
    "content_hash": 2,
}
_TRACKING_QUERY_KEYS = {
    "dclid",
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "ref_src",
    "s_cid",
}
_TRACKING_QUERY_PREFIXES = ("utm_",)
_WHITESPACE = re.compile(r"\s+")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _platform_external_id(item: Mapping[str, Any]) -> tuple[str, str] | None:
    platform = _text(item.get("platform"))
    external_id = _text(item.get("external_id")) or _text(item.get("post_id"))
    if not platform or not external_id:
        return None
    return platform.casefold(), external_id


def canonicalize_url(value: Any) -> str | None:
    """Return a stable HTTP(S) URL with tracking-only variation removed.

    This function does not follow redirects or guess destinations for short
    links. Host aliases that directly identify the same public content URL are
    normalized locally.
    """
    text = _text(value)
    if not text:
        return None
    if text.startswith("//"):
        text = "https:" + text
    elif "://" not in text and not text.startswith("/"):
        text = "https://" + text

    try:
        parsed = urlsplit(text)
        host = (parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.casefold() not in {"http", "https"} or not host:
        return None

    if host.startswith("www."):
        host = host[4:]
    host_aliases = {
        "m.youtube.com": "youtube.com",
        "old.reddit.com": "reddit.com",
        "m.reddit.com": "reddit.com",
        "twitter.com": "x.com",
        "mobile.twitter.com": "x.com",
    }
    host = host_aliases.get(host, host)

    path = parsed.path or ""
    pairs = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_QUERY_KEYS
        and not key.casefold().startswith(_TRACKING_QUERY_PREFIXES)
    ]

    # Direct YouTube aliases point at the same stable video URL.
    if host == "youtu.be":
        video_id = path.strip("/").split("/", 1)[0]
        if video_id:
            host = "youtube.com"
            path = "/watch"
            pairs.append(("v", video_id))
    elif host == "youtube.com":
        path_parts = [part for part in path.split("/") if part]
        if len(path_parts) >= 2 and path_parts[0] in {"embed", "shorts", "live"}:
            path = "/watch"
            pairs.append(("v", path_parts[1]))

    if path == "/":
        path = ""
    elif path:
        path = path.rstrip("/")
    pairs.sort(key=lambda pair: (pair[0].casefold(), pair[0], pair[1]))

    default_port = port is None or port in {80, 443}
    netloc = host if default_port else f"{host}:{port}"
    return urlunsplit(("https", netloc, path, urlencode(pairs, doseq=True), ""))


def _normalized_content(item: Mapping[str, Any]) -> str | None:
    pieces: list[str] = []
    for key in ("title", "text"):
        value = item.get(key)
        if value is None:
            pieces.append("")
            continue
        piece = unicodedata.normalize("NFKC", str(value))
        pieces.append(_WHITESPACE.sub(" ", piece).strip().casefold())
    if not any(pieces):
        return None
    return "\n".join(pieces)


def normalized_content_hash(item: Mapping[str, Any]) -> str | None:
    """Hash exact NFKC/case/whitespace-normalized title and text content."""
    content = _normalized_content(item)
    if content is None:
        return None
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, order=True)
class RootIdentity:
    """The strongest available identity for one root or duplicate group."""

    strategy: str
    value: str

    def __post_init__(self):
        if self.strategy not in _IDENTITY_PRIORITY:
            raise ValueError(f"unsupported root identity strategy: {self.strategy}")
        if not self.value:
            raise ValueError("root identity value cannot be empty")

    def to_dict(self) -> dict[str, str]:
        return {"strategy": self.strategy, "value": self.value}


def root_identity(item: Mapping[str, Any]) -> RootIdentity | None:
    """Choose identity in the approved platform/ID, URL, exact-hash order."""
    source_id = _platform_external_id(item)
    if source_id is not None:
        return RootIdentity("platform_external_id", f"{source_id[0]}:{source_id[1]}")
    url = canonicalize_url(item.get("url"))
    if url is not None:
        return RootIdentity("canonical_url", url)
    content_hash = normalized_content_hash(item)
    if content_hash is not None:
        return RootIdentity("content_hash", content_hash)
    return None


@dataclass(frozen=True)
class DeduplicationGroup:
    """One unique root and every raw collection observation matched to it."""

    identity: RootIdentity | None
    representative: dict[str, Any]
    observations: tuple[dict[str, Any], ...]
    match_bases: tuple[str, ...]

    @property
    def observation_count(self) -> int:
        return len(self.observations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict() if self.identity is not None else None,
            "match_bases": list(self.match_bases),
            "observation_count": self.observation_count,
            "observations": deepcopy(list(self.observations)),
        }


@dataclass(frozen=True)
class DeduplicationResult:
    """Unique roots plus a lossless manifest of their source observations."""

    groups: tuple[DeduplicationGroup, ...]
    input_root_count: int

    @property
    def unique_root_count(self) -> int:
        return len(self.groups)

    @property
    def duplicate_observation_count(self) -> int:
        return self.input_root_count - self.unique_root_count

    @property
    def unique_roots(self) -> list[dict[str, Any]]:
        return [deepcopy(group.representative) for group in self.groups]

    def roots_with_provenance(self) -> list[dict[str, Any]]:
        """Return representatives carrying every duplicate collection provenance."""
        roots: list[dict[str, Any]] = []
        for group in self.groups:
            root = deepcopy(group.representative)
            provenance = [
                deepcopy(observation["provenance"])
                for observation in group.observations
                if "provenance" in observation
            ]
            root["_collection_provenance"] = provenance
            roots.append(root)
        return roots

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_root_count": self.input_root_count,
            "unique_root_count": self.unique_root_count,
            "duplicate_observation_count": self.duplicate_observation_count,
            "groups": [group.to_dict() for group in self.groups],
        }


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
        # The earliest collection observation remains the representative.
        if left_root < right_root:
            self.parents[right_root] = left_root
        else:
            self.parents[left_root] = right_root


def _identity_keys(item: Mapping[str, Any]) -> dict[str, str]:
    keys: dict[str, str] = {}
    source_id = _platform_external_id(item)
    if source_id is not None:
        keys["platform_external_id"] = f"{source_id[0]}:{source_id[1]}"
    url = canonicalize_url(item.get("url"))
    if url is not None:
        keys["canonical_url"] = url
    # Hash is a fallback identity, not permission to erase addressable copies.
    if source_id is None and url is None:
        content_hash = normalized_content_hash(item)
        if content_hash is not None:
            keys["content_hash"] = content_hash
    return keys


def deduplicate_roots(roots: Iterable[Mapping[str, Any]]) -> DeduplicationResult:
    """Deduplicate root observations without discarding provenance.

    Stable IDs and canonical URLs can bridge connector/query representations.
    Content hashes merge only records without either stronger identity. Distinct
    addressable exact copies therefore remain roots for propagation modeling.
    """
    observations: list[dict[str, Any]] = []
    for root in roots:
        if not isinstance(root, Mapping):
            raise TypeError("each root must be a mapping")
        observations.append(deepcopy(dict(root)))

    union_find = _UnionFind(len(observations))
    keys_by_index = [_identity_keys(item) for item in observations]
    first_by_key: dict[tuple[str, str], int] = {}
    for index, keys in enumerate(keys_by_index):
        for strategy in (
            "platform_external_id",
            "canonical_url",
            "content_hash",
        ):
            value = keys.get(strategy)
            if value is None:
                continue
            key = (strategy, value)
            previous = first_by_key.setdefault(key, index)
            union_find.union(previous, index)

    members_by_root: dict[int, list[int]] = defaultdict(list)
    for index in range(len(observations)):
        members_by_root[union_find.find(index)].append(index)

    groups: list[DeduplicationGroup] = []
    for representative_index in sorted(members_by_root):
        member_indexes = members_by_root[representative_index]
        member_keys = [keys_by_index[index] for index in member_indexes]
        identities = [
            RootIdentity(strategy, value)
            for keys in member_keys
            for strategy, value in keys.items()
        ]
        identity = min(
            identities,
            key=lambda candidate: (
                _IDENTITY_PRIORITY[candidate.strategy],
                candidate.value,
            ),
            default=None,
        )

        shared_bases: list[str] = []
        for strategy in (
            "platform_external_id",
            "canonical_url",
            "content_hash",
        ):
            counts = Counter(keys.get(strategy) for keys in member_keys)
            if any(value is not None and count > 1 for value, count in counts.items()):
                shared_bases.append(strategy)

        groups.append(
            DeduplicationGroup(
                identity=identity,
                representative=deepcopy(observations[member_indexes[0]]),
                observations=tuple(
                    deepcopy(observations[index]) for index in member_indexes
                ),
                match_bases=tuple(shared_bases),
            )
        )

    return DeduplicationResult(groups=tuple(groups), input_root_count=len(observations))


__all__ = [
    "DeduplicationGroup",
    "DeduplicationResult",
    "RootIdentity",
    "canonicalize_url",
    "deduplicate_roots",
    "normalized_content_hash",
    "root_identity",
]
