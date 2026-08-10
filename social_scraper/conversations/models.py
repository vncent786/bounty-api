"""Canonical conversation models.

These models are internal evidence records. They do not replace the existing
public SocialItem response contract.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


RECORD_TYPES = {"post", "comment", "reply"}
ENGAGEMENT_FIELDS = ("views", "likes", "comments", "shares", "collects")


def canonical_json(value: Any) -> str:
    """Serialize JSON deterministically for evidence hashing."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def conversation_identity(
    platform: str,
    external_id: str,
    object_type: str = "post",
) -> str:
    """Stable identity based only on a source-supplied identity tuple."""
    normalized_platform = str(platform or "").strip().lower()
    normalized_id = str(external_id if external_id is not None else "").strip()
    normalized_type = str(object_type or "").strip().lower()
    if not normalized_platform or not normalized_id or not normalized_type:
        raise ValueError(
            "platform, object_type, and external_id are required for canonical identity"
        )
    material = canonical_json(
        [
            "bounty-conversation-v1",
            normalized_platform,
            normalized_type,
            normalized_id,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CanonicalConversationRecord:
    platform: str
    external_id: str
    object_type: str
    record_type: str
    depth: int | None
    identity_key: str
    raw_payload_hash: str
    raw_payload: dict
    source_route: str | None = None
    parent_external_id: str | None = None
    root_post_external_id: str | None = None
    author_external_id: str | None = None
    author_username: str | None = None
    author_display_name: str | None = None
    text: str | None = None
    title: str | None = None
    url: str | None = None
    published_at: str | None = None
    published_date: str | None = None
    language: str | None = None
    is_repost: bool | None = None
    repost_of_external_id: str | None = None

    def __post_init__(self):
        if self.record_type not in RECORD_TYPES:
            raise ValueError(f"unsupported record_type: {self.record_type}")
        if self.depth is not None and self.depth < 0:
            raise ValueError("depth cannot be negative")
        expected = conversation_identity(
            self.platform,
            self.external_id,
            self.object_type,
        )
        if self.identity_key != expected:
            raise ValueError("identity_key does not match platform and external_id")
        if self.record_type == "post":
            if self.parent_external_id is not None:
                raise ValueError("post cannot have parent_external_id")
            if self.depth != 0:
                raise ValueError("post depth must be zero")
        if self.is_repost is False and self.repost_of_external_id is not None:
            raise ValueError("non-repost cannot reference repost source")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CanonicalObservation:
    collected_at: str
    source_observed_at: str | None = None
    engagement: dict[str, int | None] = field(
        default_factory=lambda: {key: None for key in ENGAGEMENT_FIELDS}
    )

    def __post_init__(self):
        normalized = {key: self.engagement.get(key) for key in ENGAGEMENT_FIELDS}
        for key, value in normalized.items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError(f"engagement {key} must be an integer or None")
        object.__setattr__(self, "engagement", normalized)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CanonicalBundle:
    record: CanonicalConversationRecord
    observation: CanonicalObservation

    def to_dict(self) -> dict:
        return {
            "record": self.record.to_dict(),
            "observation": self.observation.to_dict(),
        }
