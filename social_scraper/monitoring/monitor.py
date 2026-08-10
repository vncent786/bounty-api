"""
Trend monitor — collects zones, clusters posts, diffs over time.

Workflow:
1. collect_zone(zone) → searches all keywords across all platforms via broker
2. cluster_posts(items) → LLM groups posts by semantic similarity (truncated text)
3. diff_snapshots(prev, curr) → detects new/growing/shrinking clusters
4. generate_report(zone, diffs) → human-readable trend alert
"""

import asyncio
import hashlib
import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from .zones import ZoneRegistry, Zone

logger = logging.getLogger(__name__)

# Truncate post text to ~30 tokens for clustering (saves LLM cost)
_MAX_CLUSTER_TEXT = 200  # chars ≈ ~30-40 tokens

# Engagement velocity threshold: cluster grew >2x week-over-week
_VELOCITY_THRESHOLD = 2.0

# Minimum engagement for a cluster to be "interesting"
_MIN_ENGAGEMENT = 100


@dataclass
class Cluster:
    """A group of semantically similar posts."""
    label: str
    keywords: list[str] = field(default_factory=list)
    post_count: int = 0
    total_likes: int = 0
    total_views: int = 0
    platforms: list[str] = field(default_factory=list)
    sample_posts: list[dict] = field(default_factory=list)
    # Stable hash for diffing across snapshots
    cluster_hash: str = ""

    @property
    def avg_engagement(self) -> float:
        if self.post_count == 0:
            return 0
        return (self.total_likes + self.total_views) / self.post_count

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrendAlert:
    """A detected change in cluster velocity."""
    alert_type: str  # new | growing | shrinking | stable
    cluster_label: str
    zone_name: str
    current_count: int
    previous_count: int
    growth_rate: float
    platforms: list[str]
    sample_text: str
    detected_at: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrendReport:
    """Full report for a zone collection cycle."""
    zone_name: str
    collected_at: str
    total_items: int
    cluster_count: int
    platform_summary: dict
    alerts: list[TrendAlert] = field(default_factory=list)
    top_clusters: list[dict] = field(default_factory=list)
    enrichment: dict = field(default_factory=dict)
    source_health: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "zone_name": self.zone_name,
            "collected_at": self.collected_at,
            "total_items": self.total_items,
            "cluster_count": self.cluster_count,
            "platform_summary": self.platform_summary,
            "alerts": [a.to_dict() for a in self.alerts],
            "top_clusters": self.top_clusters,
            "enrichment": self.enrichment,
            "source_health": self.source_health,
        }


class TrendMonitor:
    """Orchestrates zone collection, clustering, and trend detection."""

    def __init__(self, registry: ZoneRegistry, broker, llm_cluster_fn=None):
        """
        Args:
            registry: ZoneRegistry for zone CRUD + snapshot storage
            broker: SourceBroker for multi-platform collection
            llm_cluster_fn: async function(texts: list[str]) -> list[list[int]]
                            that groups text indices into clusters.
                            If None, falls back to keyword-based clustering.
        """
        self.registry = registry
        self.broker = broker
        self._llm_cluster = llm_cluster_fn

    async def collect_zone(self, zone: Zone) -> tuple[list[dict], dict]:
        """Collect a zone while preserving the historical two-value API."""
        items, platform_summary, _ = await self._collect_zone_with_health(zone)
        return items, platform_summary

    async def _collect_zone_with_health(
        self, zone: Zone
    ) -> tuple[list[dict], dict, list[dict]]:
        """Collect a zone plus sanitized connector and request health."""
        all_items = []
        platform_counts = defaultdict(int)
        platform_engagement = defaultdict(lambda: {"likes": 0, "views": 0})
        source_health = []

        for keyword in zone.keywords:
            logger.info(f"Zone '{zone.name}': collecting '{keyword}' across {zone.platforms}")
            try:
                result = await self.broker.search(
                    keyword=keyword,
                    platforms=zone.platforms,
                    count=10,  # 10 per keyword per platform
                )
                health_entries = result.get("source_health", [])
                if health_entries:
                    source_health.extend(
                        {"keyword": keyword, **entry} for entry in health_entries
                    )
                else:
                    source_health.extend(
                        {
                            "keyword": keyword,
                            "platform": platform,
                            "status": details.get("status", "unknown"),
                            "connector": details.get("selected_connector"),
                            "attempted_connectors": details.get("attempted_connectors", []),
                        }
                        for platform, details in result.get("platform_results", {}).items()
                    )
                items = result.get("items", [])
                for item in items:
                    # Tag with source keyword
                    item["_zone_keyword"] = keyword
                    all_items.append(item)
                    p = item.get("platform", "unknown")
                    platform_counts[p] += 1
                    eng = item.get("engagement", {})
                    platform_engagement[p]["likes"] += eng.get("likes") or 0
                    platform_engagement[p]["views"] += eng.get("views") or 0
            except Exception as e:
                logger.warning(f"Zone '{zone.name}' keyword '{keyword}' failed: {e}")
                source_health.extend(
                    {
                        "keyword": keyword,
                        "platform": platform,
                        "connector": None,
                        "status": "error",
                        "error": "keyword_collection_exception",
                        "scope": "keyword_request",
                    }
                    for platform in zone.platforms
                )

        platform_summary = {
            p: {"items": platform_counts[p], **platform_engagement[p]}
            for p in platform_counts
        }

        logger.info(f"Zone '{zone.name}': collected {len(all_items)} items from {len(platform_summary)} platforms")
        return all_items, platform_summary, source_health

    def cluster_posts(self, items: list[dict]) -> list[Cluster]:
        """Group posts into semantic clusters.

        If LLM clustering function is provided, use it.
        Otherwise fall back to keyword overlap clustering.
        """
        if not items:
            return []

        # Extract truncated text for each item
        texts = [self._extract_text(item) for item in items]

        if self._llm_cluster:
            # Use LLM-based clustering
            try:
                groups = asyncio.get_event_loop().run_until_complete(
                    self._llm_cluster(texts)
                ) if not asyncio.get_event_loop().is_running() else None
                if groups is None:
                    # We're in an async context — can't run_until_complete
                    # Fall back to keyword clustering
                    groups = self._keyword_cluster(texts)
            except Exception:
                groups = self._keyword_cluster(texts)
        else:
            groups = self._keyword_cluster(texts)

        clusters = []
        for group_indices in groups:
            group_items = [items[i] for i in group_indices if i < len(items)]
            if not group_items:
                continue

            # Build cluster
            label = self._derive_label(group_items)
            platforms = list(set(gi.get("platform", "") for gi in group_items))
            total_likes = sum(gi.get("engagement", {}).get("likes") or 0 for gi in group_items)
            total_views = sum(gi.get("engagement", {}).get("views") or 0 for gi in group_items)
            sample_posts = [
                {
                    "platform": gi.get("platform", ""),
                    "author": gi.get("author_username") or gi.get("author_display_name") or "",
                    "text": self._extract_text(gi)[:100],
                    "likes": gi.get("engagement", {}).get("likes"),
                    "views": gi.get("engagement", {}).get("views"),
                    "url": gi.get("url", ""),
                }
                for gi in group_items[:5]
            ]
            cluster_hash = self._hash_cluster(group_items)

            clusters.append(Cluster(
                label=label,
                post_count=len(group_items),
                total_likes=total_likes,
                total_views=total_views,
                platforms=platforms,
                sample_posts=sample_posts,
                cluster_hash=cluster_hash,
            ))

        # Sort by engagement
        clusters.sort(key=lambda c: c.avg_engagement, reverse=True)
        return clusters

    @staticmethod
    def _extract_text(item: dict) -> str:
        """Extract and truncate text content from a social item."""
        text = item.get("text") or ""
        title = item.get("title") or ""
        full = f"{title} {text}".strip()
        # Clean
        full = re.sub(r"http\S+", "", full)
        full = re.sub(r"#[\w]+", "", full)  # Remove hashtags for clustering
        full = re.sub(r"\s+", " ", full).strip()
        return full[:_MAX_CLUSTER_TEXT]

    @staticmethod
    def _keyword_cluster(texts: list[str]) -> list[list[int]]:
        """Fallback: cluster by significant keyword overlap."""
        # Extract meaningful keywords from each text
        stop_words = {
            "the", "a", "an", "to", "and", "or", "of", "in", "on", "for",
            "is", "are", "was", "were", "be", "been", "being", "have", "has",
            "had", "do", "does", "did", "will", "would", "could", "should",
            "may", "might", "must", "can", "this", "that", "these", "those",
            "i", "you", "he", "she", "it", "we", "they", "my", "your",
            "his", "her", "its", "our", "their", "me", "him", "us", "them",
            "with", "at", "from", "by", "about", "as", "into", "through",
            "during", "before", "after", "above", "below", "up", "down",
            "but", "not", "no", "so", "than", "too", "very", "just", "also",
            "if", "then", "when", "where", "why", "how", "all", "each",
            "every", "both", "few", "more", "most", "other", "some", "such",
            "only", "own", "same", "what", "which", "who", "whom",
        }
        text_keywords = []
        for text in texts:
            words = re.findall(r"[a-zA-Z]{3,}", text.lower())
            significant = [w for w in words if w not in stop_words]
            text_keywords.append(set(significant))

        # Group by keyword overlap
        used = set()
        groups = []
        for i, kw_i in enumerate(text_keywords):
            if i in used:
                continue
            group = [i]
            used.add(i)
            for j, kw_j in enumerate(text_keywords):
                if j in used or j <= i:
                    continue
                overlap = len(kw_i & kw_j)
                # At least 2 shared keywords, or >40% of the smaller set
                min_size = min(len(kw_i), len(kw_j))
                if min_size > 0 and (overlap >= 2 or overlap / min_size > 0.4):
                    group.append(j)
                    used.add(j)
            groups.append(group)

        # Lone items become their own cluster
        for i in range(len(texts)):
            if i not in used:
                groups.append([i])

        return groups

    @staticmethod
    def _derive_label(items: list[dict]) -> str:
        """Derive a human-readable label for a cluster."""
        # Extract most common significant words
        all_words = []
        stop_words = {"the", "a", "an", "to", "and", "or", "of", "in", "on", "for",
                       "is", "are", "was", "were", "be", "with", "at", "from", "by",
                       "about", "as", "this", "that", "it", "i", "you", "he", "she",
                       "they", "we", "my", "your", "his", "her", "but", "not", "no"}
        for item in items:
            text = f"{item.get('text', '')} {item.get('title', '')}"
            words = re.findall(r"[a-zA-Z]{4,}", text.lower())
            all_words.extend([w for w in words if w not in stop_words])

        if not all_words:
            return "misc"

        counter = Counter(all_words)
        top = counter.most_common(3)
        return " / ".join(word for word, _ in top)

    @staticmethod
    def _hash_cluster(items: list[dict]) -> str:
        """Create stable hash for cluster identity (for diffing)."""
        # Use the derived label keywords as identity
        text_parts = []
        for item in items[:10]:
            text = f"{item.get('text', '')} {item.get('title', '')}".lower()
            text_parts.append(re.sub(r"[^a-z0-9 ]", "", text)[:50])
        combined = "|".join(sorted(text_parts))
        return hashlib.md5(combined.encode()).hexdigest()[:12]

    def diff_snapshots(self, prev_clusters: list[dict], curr_clusters: list[dict]) -> list[TrendAlert]:
        """Compare two cluster snapshots to detect emerging trends.

        Matches clusters by label similarity and detects:
        - new: cluster exists in current but not in previous
        - growing: cluster item count grew >2x
        - shrinking: cluster shrunk >50%
        - stable: no significant change
        """
        alerts = []
        prev_map = {c.get("label", ""): c for c in prev_clusters}
        curr_map = {c.get("label", ""): c for c in curr_clusters}

        for label, curr in curr_map.items():
            if label not in prev_map:
                # New cluster
                alerts.append(TrendAlert(
                    alert_type="new",
                    cluster_label=label,
                    zone_name="",  # filled by caller
                    current_count=curr.get("post_count", 0),
                    previous_count=0,
                    growth_rate=float("inf"),
                    platforms=curr.get("platforms", []),
                    sample_text=curr.get("sample_posts", [{}])[0].get("text", ""),
                    detected_at=datetime.now(timezone.utc).isoformat(),
                ))
            else:
                prev = prev_map[label]
                prev_count = prev.get("post_count", 0)
                curr_count = curr.get("post_count", 0)

                if prev_count == 0:
                    continue

                growth = curr_count / prev_count if prev_count > 0 else 1.0

                if growth >= _VELOCITY_THRESHOLD:
                    alerts.append(TrendAlert(
                        alert_type="growing",
                        cluster_label=label,
                        zone_name="",
                        current_count=curr_count,
                        previous_count=prev_count,
                        growth_rate=growth,
                        platforms=curr.get("platforms", []),
                        sample_text=curr.get("sample_posts", [{}])[0].get("text", ""),
                        detected_at=datetime.now(timezone.utc).isoformat(),
                    ))
                elif growth <= 0.5:
                    alerts.append(TrendAlert(
                        alert_type="shrinking",
                        cluster_label=label,
                        zone_name="",
                        current_count=curr_count,
                        previous_count=prev_count,
                        growth_rate=growth,
                        platforms=curr.get("platforms", []),
                        sample_text=curr.get("sample_posts", [{}])[0].get("text", ""),
                        detected_at=datetime.now(timezone.utc).isoformat(),
                    ))

        return alerts

    async def run_zone(self, zone_name: str) -> TrendReport:
        """Full monitoring cycle for one zone: collect → cluster → diff → report."""
        zone = self.registry.get_by_name(zone_name)
        if not zone:
            raise ValueError(f"Zone not found: {zone_name}")

        # 1. Collect
        items, platform_summary, source_health = await self._collect_zone_with_health(zone)

        # 2. Cluster
        clusters = self.cluster_posts(items)

        # 3. Get previous snapshot for diffing
        snapshots = self.registry.get_snapshots(zone.id, limit=2)
        prev_clusters = snapshots[0]["clusters"] if snapshots else []

        # 4. Diff
        alerts = self.diff_snapshots(prev_clusters, [c.to_dict() for c in clusters])
        for alert in alerts:
            alert.zone_name = zone.name

        # 5. Save snapshot
        self.registry.save_snapshot(
            zone.id,
            [c.to_dict() for c in clusters],
            len(items),
            platform_summary,
            source_health,
        )

        # 6. Update zone timing
        self.registry.update_collected(zone.id)

        # 7. Build report
        report = TrendReport(
            zone_name=zone.name,
            collected_at=datetime.now(timezone.utc).isoformat(),
            total_items=len(items),
            cluster_count=len(clusters),
            platform_summary=platform_summary,
            alerts=alerts,
            top_clusters=[c.to_dict() for c in clusters[:10]],
            source_health=source_health,
        )

        logger.info(
            f"Zone '{zone.name}': {len(items)} items → {len(clusters)} clusters "
            f"→ {len(alerts)} alerts ({sum(1 for a in alerts if a.alert_type == 'new')} new, "
            f"{sum(1 for a in alerts if a.alert_type == 'growing')} growing)"
        )

        return report

    async def run_all_due(self) -> list[TrendReport]:
        """Run monitoring for all zones that are due for collection."""
        due = self.registry.list_due()
        reports = []
        for zone in due:
            try:
                report = await self.run_zone(zone.name)
                reports.append(report)
            except Exception as e:
                logger.error(f"Zone '{zone.name}' failed: {e}", exc_info=True)
        return reports
