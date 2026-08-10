"""
Bounty Social Data API — Connector Interface

Every platform connector implements this interface.
The source broker routes requests to connectors by platform name.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import asyncio


@dataclass
class SocialItem:
    """Normalized social media item — one unit of content from any platform."""
    platform: str
    post_id: str
    url: str
    author_username: str = ""
    author_display_name: str = ""
    author_profile_url: str = ""
    author_follower_count: Optional[int] = None
    text: str = ""
    created_at: Optional[str] = None  # ISO 8601
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    collects: Optional[int] = None
    media_type: str = "text"  # video|image|text|gallery
    thumbnail_url: Optional[str] = None
    media_urls: list = field(default_factory=list)
    hashtags: list = field(default_factory=list)
    mentions: list = field(default_factory=list)
    language: Optional[str] = None
    region: Optional[str] = None
    raw: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "platform": self.platform,
            "post_id": self.post_id,
            "url": self.url,
            "author": {
                "username": self.author_username,
                "display_name": self.author_display_name,
                "profile_url": self.author_profile_url,
                "follower_count": self.author_follower_count,
            },
            "text": self.text,
            "created_at": self.created_at,
            "engagement": {
                "views": self.views,
                "likes": self.likes,
                "comments": self.comments,
                "shares": self.shares,
                "collects": self.collects,
            },
            "media": {
                "type": self.media_type,
                "thumbnail_url": self.thumbnail_url,
                "media_urls": self.media_urls,
            },
            "hashtags": self.hashtags,
            "mentions": self.mentions,
            "language": self.language,
            "region": self.region,
        }


@dataclass
class SourceHealth:
    """Per-source health report attached to every response."""
    platform: str
    connector: str
    status: str  # ok|partial|error|skipped
    items_returned: int = 0
    items_requested: int = 0
    latency_ms: int = 0
    error: Optional[str] = None
    fetched_at: str = ""
    coverage: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.fetched_at:
            self.fetched_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self):
        return {
            "platform": self.platform,
            "connector": self.connector,
            "status": self.status,
            "items_returned": self.items_returned,
            "items_requested": self.items_requested,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "fetched_at": self.fetched_at,
            "coverage": self.coverage,
        }


@dataclass
class ConnectorResult:
    """What a connector returns after a search/fetch operation."""
    items: list  # List[SocialItem]
    health: SourceHealth
    raw_records: list = field(default_factory=list)


class BaseConnector(ABC):
    """Abstract connector — every platform implements this."""

    platform: str = "unknown"
    connector_name: str = "base"

    @abstractmethod
    async def search(self, keyword: str, count: int = 20, time_filter: str = "",
                     sort: str = "", region: str = "") -> ConnectorResult:
        """Search for content by keyword."""
        pass

    @abstractmethod
    async def health_check(self) -> SourceHealth:
        """Quick health probe."""
        pass

    async def fetch_thread(
        self,
        post: SocialItem,
        max_comments: int,
        max_depth: int,
    ):
        """Optional bounded thread reader; search-only connectors remain valid."""
        from social_scraper.conversations.thread_reader import unsupported_thread_result

        return unsupported_thread_result(
            self.platform, post.post_id, max_comments, max_depth
        )
