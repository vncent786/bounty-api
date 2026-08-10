"""Deterministic evidence bundles and shared horizontal extraction caching.

Cache identity is derived from canonical JSON structures. Evidence text is never
interpolated into delimiter-based strings, avoiding ambiguous cache material.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence


NORMALIZER_VERSION = "conversation-evidence/1"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _string(value: Any, *, folded: bool = False) -> str:
    normalized = unicodedata.normalize("NFKC", str(value if value is not None else "")).strip()
    return normalized.casefold() if folded else normalized


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    return value


def _record_mapping(value: Any) -> dict[str, Any]:
    value = _plain(value)
    if not isinstance(value, Mapping):
        raise TypeError("evidence records must be mappings or dataclass-like values")
    # CanonicalBundle-like values expose the immutable record under ``record``.
    nested = _plain(value.get("record"))
    if isinstance(nested, Mapping):
        return dict(nested)
    return dict(value)


def _canonical_value(value: Any, *, unordered_lists: bool = False) -> Any:
    value = _plain(value)
    if isinstance(value, Mapping):
        return {
            _string(key): _canonical_value(item, unordered_lists=unordered_lists)
            for key, item in sorted(value.items(), key=lambda pair: _string(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_canonical_value(item, unordered_lists=unordered_lists) for item in value]
        if unordered_lists or isinstance(value, (set, frozenset)):
            items.sort(key=_canonical_json)
        return items
    if isinstance(value, str):
        return unicodedata.normalize("NFKC", value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _string(value)


@dataclass(frozen=True)
class EvidenceBundleMember:
    member_key: str
    ordinal: int
    content_hash: str
    platform: str
    object_type: str
    parent_id: str | None
    root_id: str | None


@dataclass(frozen=True)
class EvidenceBundle:
    evidence_hash: str
    coverage_hash: str
    normalizer_version: str
    members: tuple[EvidenceBundleMember, ...]
    coverage: Any


@dataclass(frozen=True)
class CachedHorizontalResult:
    result: Any
    usage: dict[str, Any]
    cache_key: str
    evidence_bundle_id: str
    horizontal_extraction_id: int
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_member(record_value: Any) -> dict[str, Any]:
    record = _record_mapping(record_value)
    platform = _string(record.get("platform") or "unknown", folded=True)
    object_type = _string(
        record.get("object_type") or record.get("record_type") or "post", folded=True
    )
    external_id = _string(
        record.get("external_id") or record.get("post_id") or record.get("comment_id")
        or record.get("id")
    )
    identity_key = _string(record.get("identity_key"))
    parent_id = _string(record.get("parent_external_id") or record.get("parent_id")) or None
    root_id = _string(
        record.get("root_post_external_id") or record.get("root_id")
        or (external_id if object_type == "post" else "")
    ) or None

    identity_material = (
        ["canonical_identity", identity_key]
        if identity_key
        else ["source_identity", platform, object_type, external_id, parent_id, root_id]
    )
    # Keep extraction-relevant content explicit. A canonical raw payload hash is
    # included when present but never trusted as a replacement for visible text.
    content_material = {
        "title": _canonical_value(record.get("title")),
        "text": _canonical_value(
            record.get("text") if record.get("text") is not None
            else record.get("body") if record.get("body") is not None
            else record.get("content")
        ),
        "raw_payload_hash": _string(record.get("raw_payload_hash")) or None,
        "author": _canonical_value(
            record.get("author") if record.get("author") is not None else {
                "external_id": record.get("author_external_id") or record.get("author_id"),
                "username": record.get("author_username"),
            }
        ),
        "url": _string(record.get("url")) or None,
        "language": _string(record.get("language"), folded=True) or None,
        "published_at": _string(record.get("published_at") or record.get("created_at")) or None,
    }
    content_hash = _hash(content_material)
    member_key = _hash(identity_material)
    return {
        "member_key": member_key,
        "content_hash": content_hash,
        "platform": platform,
        "object_type": object_type,
        "parent_id": parent_id,
        "root_id": root_id,
    }


def normalize_coverage_contract(coverage: Any) -> Any:
    """Canonicalize coverage; source/list ordering has no semantic meaning."""
    return _canonical_value(coverage if coverage is not None else {}, unordered_lists=True)


def build_evidence_bundle(
    records: Sequence[Any],
    coverage: Any,
    *,
    normalizer_version: str = NORMALIZER_VERSION,
) -> EvidenceBundle:
    """Create an order-independent immutable description of extraction evidence."""
    normalized = [_normalize_member(record) for record in records]
    normalized.sort(key=lambda item: (item["member_key"], item["content_hash"]))
    members = tuple(
        EvidenceBundleMember(ordinal=index, **item) for index, item in enumerate(normalized)
    )
    normalized_coverage = normalize_coverage_contract(coverage)
    coverage_hash = _hash(["coverage_contract", normalized_coverage])
    evidence_hash = _hash([
        "evidence_bundle",
        normalizer_version,
        [
            {
                "member_key": member.member_key,
                "content_hash": member.content_hash,
                "platform": member.platform,
                "object_type": member.object_type,
                "parent_id": member.parent_id,
                "root_id": member.root_id,
            }
            for member in members
        ],
    ])
    return EvidenceBundle(
        evidence_hash=evidence_hash,
        coverage_hash=coverage_hash,
        normalizer_version=normalizer_version,
        members=members,
        coverage=normalized_coverage,
    )


def build_horizontal_cache_key(
    bundle: EvidenceBundle,
    *,
    extraction_schema_version: str,
    prompt_version: str,
    provider: str,
    model: str,
) -> str:
    """Build the exact shared key. Lens and subject IDs are intentionally absent."""
    return _hash([
        "horizontal_extraction",
        bundle.evidence_hash,
        bundle.coverage_hash,
        bundle.normalizer_version,
        _string(extraction_schema_version),
        _string(prompt_version),
        _string(provider, folded=True),
        _string(model),
    ])


def _json_result(value: Any) -> Any:
    value = _plain(value)
    # Round-trip now so cache misses and cache hits have the exact same JSON shape.
    return json.loads(_canonical_json(value))


def _split_result_and_usage(value: Any) -> tuple[Any, dict[str, Any]]:
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], Mapping):
        return value[0], dict(value[1])
    if isinstance(value, Mapping) and set(value) == {"result", "usage"} \
            and isinstance(value["usage"], Mapping):
        return value["result"], dict(value["usage"])
    return value, {}


class CachedHorizontalAnalyzer:
    """Cache one reusable horizontal analysis across every downstream lens."""

    def __init__(
        self,
        store: Any,
        analyze_fn: Callable[..., Awaitable[Any]],
        *,
        extraction_schema_version: str,
        prompt_version: str,
        provider: str,
        model: str,
        normalizer_version: str = NORMALIZER_VERSION,
    ):
        self.store = store
        self.analyze_fn = analyze_fn
        self.extraction_schema_version = _string(extraction_schema_version)
        self.prompt_version = _string(prompt_version)
        self.provider = _string(provider, folded=True)
        self.model = _string(model)
        self.normalizer_version = _string(normalizer_version)

    async def analyze(
        self,
        *args: Any,
        records: Sequence[Any] | None = None,
        coverage: Any = None,
        subject_key: str | None = None,
        lens_id: str | None = None,
        **kwargs: Any,
    ) -> CachedHorizontalResult:
        """Analyze once and return a persisted, JSON-exact result thereafter.

        ``records`` may be supplied explicitly, as the first positional argument,
        or as the second positional argument for ``analyze_conversation(topic,
        posts)``. ``lens_id`` is accepted only for caller compatibility and never
        participates in cache identity.
        """
        del lens_id
        call_args = args
        records_were_explicit = records is not None
        if records is None:
            if len(args) >= 2 and isinstance(args[1], Sequence) and not isinstance(args[1], str):
                records = args[1]
            elif args and isinstance(args[0], Sequence) and not isinstance(args[0], str):
                records = args[0]
            else:
                raise TypeError("records must be supplied")
        records = list(records)
        if records_were_explicit:
            call_args = (*call_args, records)
        if coverage is None:
            coverage = kwargs.get("source_health", {})
        bundle = build_evidence_bundle(
            records, coverage, normalizer_version=self.normalizer_version
        )
        stored_bundle = self.store.create_evidence_bundle(bundle, subject_key=subject_key)
        cache_key = build_horizontal_cache_key(
            bundle,
            extraction_schema_version=self.extraction_schema_version,
            prompt_version=self.prompt_version,
            provider=self.provider,
            model=self.model,
        )
        cached = self.store.get_horizontal_extraction(cache_key)
        if cached is not None:
            return CachedHorizontalResult(
                result=cached["result"],
                usage={
                    "cache_hit": True,
                    "llm_calls": 0,
                    "input_records": cached["input_records"],
                    "input_tokens": cached["input_tokens"],
                    "output_tokens": cached["output_tokens"],
                    "tokens_estimated": cached["tokens_estimated"],
                },
                cache_key=cache_key,
                evidence_bundle_id=stored_bundle["id"],
                horizontal_extraction_id=cached["id"],
                status=cached["status"],
            )

        pending = self.analyze_fn(*call_args, **kwargs)
        if not hasattr(pending, "__await__"):
            raise TypeError("analyze_fn must return an awaitable")
        raw = await pending
        result_value, provided_usage = _split_result_and_usage(raw)
        result = _json_result(result_value)
        status = _string(
            provided_usage.get("status")
            or (result.get("status") if isinstance(result, Mapping) else None)
            or "complete",
            folded=True,
        )
        input_tokens = provided_usage.get("input_tokens")
        output_tokens = provided_usage.get("output_tokens")
        tokens_estimated = bool(provided_usage.get("tokens_estimated", False))
        stored = self.store.put_horizontal_extraction(
            evidence_bundle_id=stored_bundle["id"],
            extraction_schema_version=self.extraction_schema_version,
            prompt_version=self.prompt_version,
            provider=self.provider,
            model=self.model,
            cache_key=cache_key,
            status=status,
            result=result,
            input_records=len(records),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_estimated=tokens_estimated,
        )
        return CachedHorizontalResult(
            result=stored["result"],
            usage={
                "cache_hit": False,
                "llm_calls": int(provided_usage.get("llm_calls", 1)),
                "input_records": stored["input_records"],
                "input_tokens": stored["input_tokens"],
                "output_tokens": stored["output_tokens"],
                "tokens_estimated": stored["tokens_estimated"],
            },
            cache_key=cache_key,
            evidence_bundle_id=stored_bundle["id"],
            horizontal_extraction_id=stored["id"],
            status=stored["status"],
        )

    __call__ = analyze
