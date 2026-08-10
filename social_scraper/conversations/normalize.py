"""Pure normalization from current broker items to canonical evidence records."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from .models import (
    ENGAGEMENT_FIELDS,
    CanonicalBundle,
    CanonicalConversationRecord,
    CanonicalObservation,
    conversation_identity,
    payload_hash,
)


class NormalizationError(ValueError):
    """The item remains in raw/legacy storage but cannot be canonicalized."""


def _nullable_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _strict_instant(value: Any) -> str | None:
    """Validate an aware instant while preserving source-supplied precision."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return text


def _first_present(mapping: dict, *keys: str):
    """Return the first explicit non-null value without collapsing numeric zero."""
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _date_only(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) != 10:
        return None
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return parsed.date().isoformat()


def _metric(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def normalize_broker_item(item: dict, *, collected_at: str) -> CanonicalBundle:
    """Normalize one current broker item without mutating it.

    Missing platform or source ID raises NormalizationError. Callers must retain
    the original raw item rather than inventing an identity.
    """
    if not isinstance(item, dict):
        raise NormalizationError("item_not_object")
    source = deepcopy(item)
    platform = _nullable_text(_first_present(source, "platform"))
    external_id = _nullable_text(_first_present(source, "post_id", "external_id"))
    if not platform:
        raise NormalizationError("missing_platform")
    if not external_id:
        raise NormalizationError("missing_external_id")
    platform = platform.lower()

    record_type = (
        _nullable_text(_first_present(source, "record_type", "_record_type"))
        or "post"
    )
    object_type = _nullable_text(_first_present(source, "object_type", "_object_type"))
    if object_type is None:
        object_type = "post" if record_type == "post" else "comment"
    object_type = object_type.lower()
    parent_external_id = _nullable_text(
        _first_present(source, "parent_external_id", "_parent_external_id")
    )
    root_post_external_id = _nullable_text(
        _first_present(source, "root_post_external_id", "_root_post_external_id")
    )
    depth = _first_present(source, "depth", "_depth")
    if record_type == "post":
        parent_external_id = None
        root_post_external_id = (
            root_post_external_id if root_post_external_id is not None else external_id
        )
        depth = 0
    elif depth is not None and (isinstance(depth, bool) or not isinstance(depth, int)):
        raise NormalizationError("invalid_depth")

    author = source.get("author") if isinstance(source.get("author"), dict) else {}
    provenance = (
        source.get("provenance") if isinstance(source.get("provenance"), dict) else {}
    )
    engagement = (
        source.get("engagement") if isinstance(source.get("engagement"), dict) else {}
    )

    created_at = source.get("created_at")
    published_at = _strict_instant(created_at)
    published_date = _date_only(created_at) if published_at is None else None
    collected_instant = _strict_instant(collected_at)
    if collected_instant is None:
        raise NormalizationError("invalid_collected_at")

    raw_repost = source.get("is_repost")
    is_repost = raw_repost if isinstance(raw_repost, bool) else None

    normalized_engagement = {
        key: _metric(engagement.get(key)) for key in ENGAGEMENT_FIELDS
    }

    record = CanonicalConversationRecord(
        platform=platform,
        external_id=external_id,
        object_type=object_type,
        record_type=record_type,
        depth=depth,
        identity_key=conversation_identity(platform, external_id, object_type),
        raw_payload_hash=payload_hash(source),
        raw_payload=source,
        source_route=_nullable_text(provenance.get("connector")),
        parent_external_id=parent_external_id,
        root_post_external_id=root_post_external_id,
        author_external_id=_nullable_text(
            _first_present(author, "id", "external_id")
        ),
        author_username=_nullable_text(author.get("username")),
        author_display_name=_nullable_text(author.get("display_name")),
        text=_nullable_text(source.get("text")),
        title=_nullable_text(source.get("title")),
        url=_nullable_text(source.get("url")),
        published_at=published_at,
        published_date=published_date,
        language=_nullable_text(source.get("language")),
        is_repost=is_repost,
        repost_of_external_id=_nullable_text(source.get("repost_of_external_id")),
    )
    observation = CanonicalObservation(
        collected_at=collected_instant,
        source_observed_at=_strict_instant(provenance.get("source_observed_at")),
        engagement=normalized_engagement,
    )
    return CanonicalBundle(record=record, observation=observation)
