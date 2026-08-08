"""
Top-down keyword discovery — catch emerging topics you didn't know to search for.

Source priority (buzzabout methodology: candidate generation → conversation gate):
1. Exploding Topics (PRIMARY) — curated growth-stage trends, not news events
2. Google Trends RSS (SECONDARY) — daily trending, geo-specific but news-heavy
3. YouTube trending (TERTIARY) — keyword extraction from high-view videos
4. Reddit rising (TERTIARY) — social discussion signal

Each source has its own EmergingKeyword entries. The scan_all method merges
and deduplicates, prioritizing keywords that appear across multiple sources.

Optional trajectory enrichment (breakout detection) can be layered on top
via trajectory.analyze_batch().

Discovered keywords become candidates for new zones.
"""

import asyncio
import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

from .zones import ZoneRegistry, Zone

logger = logging.getLogger(__name__)


@dataclass
class EmergingKeyword:
    """A keyword discovered via top-down scanning."""
    keyword: str
    source: str  # google_trends | reddit_rising | youtube_trending | cross_platform
    platform_count: int = 1  # how many platforms it appeared on
    engagement_signal: int = 0  # proxy for velocity
    related_terms: list[str] = field(default_factory=list)
    discovered_at: str = ""
    sample_context: str = ""  # where we found it

    def to_dict(self) -> dict:
        return asdict(self)


class TopDownDiscovery:
    """Scans external sources to discover emerging keywords."""

    def __init__(self, broker=None):
        self.broker = broker

    async def scan_exploding_topics(self) -> list[EmergingKeyword]:
        """Fetch curated emerging trends from Exploding Topics.

        This is the PRIMARY source — Exploding Topics curates for growth-stage
        trends (not news events), with built-in growth percentages.

        Returns EmergingKeyword objects with growth_value as engagement_signal.
        """
        keywords: list[EmergingKeyword] = []
        try:
            from social_scraper.connectors.exploding_topics import (
                fetch_exploding_topics,
            )

            topics = await fetch_exploding_topics(timeout=30)

            for topic in topics:
                keywords.append(EmergingKeyword(
                    keyword=topic.name,
                    source="exploding_topics",
                    engagement_signal=topic.growth_value,
                    related_terms=[topic.slug],
                    discovered_at=topic.discovered_at,
                    sample_context=(
                        f"Exploding Topics: {topic.name} "
                        f"({topic.growth}, vol={topic.volume})"
                    ),
                ))

            logger.info(
                f"Exploding Topics: discovered {len(keywords)} trends "
                f"(top: {topics[0].name if topics else 'none'})"
            )
        except Exception as e:
            logger.warning(f"Exploding Topics scan failed: {e}")

        return keywords

    async def scan_google_trends(self, geo: str = "US") -> list[EmergingKeyword]:
        """Fetch Google Trends daily trending searches.

        Uses the unofficial RSS endpoint (free, no key).
        Returns trending search terms with their related queries.
        """
        keywords = []
        try:
            from curl_cffi import requests as curl_requests
            session = curl_requests.Session(impersonate="chrome124")

            # Google Trends daily trending searches RSS
            url = f"https://trends.google.com/trending/rss?geo={geo}"
            resp = await asyncio.to_thread(
                session.get, url, timeout=20
            )

            if resp.status_code != 200:
                logger.warning(f"Google Trends RSS returned {resp.status_code}")
                return keywords

            # Parse XML
            import xml.etree.ElementTree as ET
            root = ET.fromstring(resp.text)

            # Namespace handling
            ns = {"ht": "https://trends.google.com/trending/rss"}

            for item in root.findall(".//item")[:20]:
                title = item.findtext("title") or ""
                # Get traffic/engagement if available
                traffic = item.findtext("ht:approx_traffic", namespaces=ns) or ""
                traffic_num = self._parse_traffic(traffic)

                # Get related news if available
                news_title = item.findtext("ht:news_item_title", namespaces=ns) or ""

                if title:
                    keywords.append(EmergingKeyword(
                        keyword=title.strip(),
                        source="google_trends",
                        engagement_signal=traffic_num,
                        related_terms=[],
                        discovered_at=datetime.now(timezone.utc).isoformat(),
                        sample_context=f"Google Trends {geo} trending: {traffic}",
                    ))

            logger.info(f"Google Trends: discovered {len(keywords)} trending keywords for {geo}")
        except Exception as e:
            logger.warning(f"Google Trends scan failed: {e}")

        return keywords

    async def scan_reddit_rising(self, keywords_from_gt: list[EmergingKeyword]) -> list[EmergingKeyword]:
        """Scan Reddit for rising posts to find emerging discussion topics.

        Uses the broker's Reddit connectors to search /r/all or broad terms.
        Cross-references with Google Trends keywords to boost multi-platform signals.
        """
        if not self.broker:
            return []

        keywords = []

        # Collect from Reddit with broad discovery queries
        # Use the Google Trends terms as seeds to find Reddit discussion
        gt_terms = [k.keyword for k in keywords_from_gt[:10]]
        # Also add some generic discovery terms
        discovery_queries = gt_terms[:5] if gt_terms else ["viral", "trending", "new"]

        reddit_texts = []
        for query in discovery_queries:
            try:
                result = await self.broker.search(
                    keyword=query,
                    platforms=["reddit"],
                    count=10,
                )
                items = result.get("items", [])
                for item in items:
                    text = item.get("text") or ""
                    reddit_texts.append(text)
            except Exception as e:
                logger.warning(f"Reddit scan for '{query}' failed: {e}")

        # Extract significant keywords from Reddit posts
        reddit_keywords = self._extract_significant_terms(reddit_texts, source="reddit_rising")

        # Cross-reference: boost keywords that appear in both GT and Reddit
        gt_set = {k.keyword.lower() for k in keywords_from_gt}
        for rk in reddit_keywords:
            if rk.keyword.lower() in gt_set:
                rk.platform_count += 1
                rk.source = "cross_platform"
            keywords.append(rk)

        logger.info(f"Reddit rising: discovered {len(keywords)} keywords, "
                    f"{sum(1 for k in keywords if k.source == 'cross_platform')} cross-platform")
        return keywords

    async def scan_youtube_trending(self) -> list[EmergingKeyword]:
        """Scan YouTube for high-engagement recent videos as a trending proxy.

        YouTube's trending page is fully JS-rendered and yt-dlp can't extract it.
        Instead, we search for broad category terms, extract keywords from titles
        of high-view-count videos. High view count = currently popular topic.
        """
        if not self.broker:
            return []

        keywords = []
        # Category-based discovery queries (not literal "trending")
        discovery_queries = [
            "new 2026", "viral", "review",
        ]

        all_texts = []
        all_views = []

        for query in discovery_queries:
            try:
                result = await self.broker.search(
                    keyword=query,
                    platforms=["youtube"],
                    count=10,
                )
                items = result.get("items", [])
                for item in items:
                    text = item.get("text") or item.get("title") or ""
                    eng = item.get("engagement", {})
                    views = eng.get("views") or 0
                    all_texts.append(text)
                    all_views.append(views)
            except Exception as e:
                logger.warning(f"YouTube scan for '{query}' failed: {e}")

        # Weight keyword extraction by view count
        keywords = self._extract_significant_terms_weighted(all_texts, all_views, source="youtube_trending")
        logger.info(f"YouTube discovery: found {len(keywords)} keywords from {len(all_texts)} videos")
        return keywords

    async def scan_all(
        self,
        geo: str = "US",
        with_trajectory: bool = False,
        max_trajectory: int = 15,
    ) -> list[EmergingKeyword]:
        """Run all discovery sources and merge results.

        Source priority:
        1. Exploding Topics (PRIMARY) — curated growth trends
        2. Google Trends RSS (SECONDARY) — geo-specific daily trending
        3. YouTube + Reddit (TERTIARY) — social signals, run in parallel

        Cross-platform keywords (appearing on 2+ sources) are prioritized.

        Args:
            geo: Country code for Google Trends (US, SG, GB, etc.)
            with_trajectory: If True, run breakout detection on top
                candidates via pytrends interest_over_time.
            max_trajectory: Max keywords to analyze for trajectory
                (each takes ~3s, so 15 = ~45s extra).
        """
        # Phase 1: Exploding Topics (PRIMARY — independent, highest quality)
        et_keywords = await self.scan_exploding_topics()

        # Phase 2: Google Trends RSS (SECONDARY — geo-specific)
        gt_keywords = await self.scan_google_trends(geo)

        # Phase 3: Reddit + YouTube (TERTIARY — social signals)
        reddit_keywords, yt_keywords = await asyncio.gather(
            self.scan_reddit_rising(gt_keywords),
            self.scan_youtube_trending(),
        )

        # Merge and deduplicate
        all_kw = et_keywords + gt_keywords + reddit_keywords + yt_keywords

        # Group by keyword (case-insensitive)
        merged = {}
        for kw in all_kw:
            key = kw.keyword.lower().strip()
            if key in merged:
                existing = merged[key]
                existing.platform_count += 1
                existing.engagement_signal = max(
                    existing.engagement_signal, kw.engagement_signal
                )
                existing.related_terms.extend(kw.related_terms)
            else:
                merged[key] = kw

        # Sort by platform count (cross-platform first), then engagement
        result = sorted(
            merged.values(),
            key=lambda k: (k.platform_count, k.engagement_signal),
            reverse=True,
        )

        # Optional trajectory enrichment
        if with_trajectory and result:
            try:
                from social_scraper.monitoring.trajectory import analyze_batch

                top_kw = [k.keyword for k in result[:max_trajectory]]
                trajectories = await analyze_batch(top_kw, geo=geo)

                # Build a lookup for trajectory results
                traj_map = {t.keyword.lower(): t for t in trajectories}

                for kw in result:
                    traj = traj_map.get(kw.keyword.lower())
                    if traj and traj.status != "UNKNOWN":
                        kw.related_terms.append(
                            f"trajectory:{traj.status}"
                        )
                        if traj.status in ("BREAKOUT", "RISING"):
                            kw.engagement_signal += 5000
            except Exception as e:
                logger.warning(f"Trajectory enrichment failed: {e}")

        logger.info(
            f"Top-down discovery complete: {len(result)} unique keywords, "
            f"{sum(1 for k in result if k.platform_count >= 2)} cross-platform, "
            f"sources: ET={len(et_keywords)}, GT={len(gt_keywords)}, "
            f"Reddit={len(reddit_keywords)}, YT={len(yt_keywords)}"
        )
        return result

    @staticmethod
    def _parse_traffic(traffic_str: str) -> int:
        """Parse Google Trends traffic like '200K+' or '1.5M+' to int."""
        if not traffic_str:
            return 0
        traffic_str = traffic_str.replace("+", "").replace(",", "").strip()
        multipliers = {"K": 1000, "M": 1_000_000, "B": 1_000_000_000}
        try:
            if traffic_str and traffic_str[-1] in multipliers:
                return int(float(traffic_str[:-1]) * multipliers[traffic_str[-1]])
            return int(float(traffic_str))
        except (ValueError, IndexError):
            return 0

    @staticmethod
    def _extract_significant_terms(texts: list[str], source: str) -> list[EmergingKeyword]:
        """Extract meaningful keywords from a corpus of social posts."""
        return TopDownDiscovery._extract_significant_terms_weighted(
            texts, [1] * len(texts), source
        )

    @staticmethod
    def _extract_significant_terms_weighted(
        texts: list[str], weights: list[int], source: str
    ) -> list[EmergingKeyword]:
        """Extract keywords weighted by engagement (views/likes).

        Keywords from high-engagement posts get higher signal scores.
        """
        stop_words = {
            "the", "a", "an", "to", "and", "or", "of", "in", "on", "for",
            "is", "are", "was", "were", "be", "been", "being", "have", "has",
            "had", "do", "does", "did", "will", "would", "could", "should",
            "this", "that", "these", "those", "i", "you", "he", "she", "it",
            "we", "they", "my", "your", "his", "her", "its", "our", "their",
            "with", "at", "from", "by", "about", "as", "into", "through",
            "but", "not", "no", "so", "than", "too", "very", "just", "also",
            "if", "then", "when", "where", "why", "how", "all", "each",
            "https", "http", "www", "com", "org", "net", "watch", "video",
            "subscribe", "channel", "like", "comment", "share", "follow",
            "check", "link", "bio", "new", "best", "top", "via",
        }

        word_freq = {}
        phrase_freq = {}

        for text, weight in zip(texts, weights):
            w = max(int(weight), 1)
            clean = re.sub(r"http\S+", "", text)
            clean = re.sub(r"#[\w]+", "", clean)
            words = re.findall(r"[a-zA-Z]{3,}", clean.lower())
            significant = [word for word in words if word not in stop_words]

            for word in significant:
                word_freq[word] = word_freq.get(word, 0) + w

            for i in range(len(significant) - 1):
                phrase = f"{significant[i]} {significant[i+1]}"
                phrase_freq[phrase] = phrase_freq.get(phrase, 0) + w

        # Top single words
        keywords = []
        seen = set()
        sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        for word, score in sorted_words[:20]:
            if score >= 2 and word not in seen:
                keywords.append(EmergingKeyword(
                    keyword=word,
                    source=source,
                    engagement_signal=score * 100,  # frequency as proxy
                    discovered_at=datetime.now(timezone.utc).isoformat(),
                ))
                seen.add(word)

        # Top phrases (higher value than single words)
        sorted_phrases = sorted(phrase_freq.items(), key=lambda x: x[1], reverse=True)
        for phrase, score in sorted_phrases[:15]:
            if score >= 2:
                keywords.append(EmergingKeyword(
                    keyword=phrase,
                    source=source,
                    engagement_signal=score * 200,  # phrases weighted higher
                    discovered_at=datetime.now(timezone.utc).isoformat(),
                ))

        return keywords
