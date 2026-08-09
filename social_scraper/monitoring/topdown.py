"""
Top-down keyword discovery — buzzabout methodology.

Two-step pipeline:
1. Candidate generation: Google Trends trending_now via trendspy
   (388 keywords, volume estimates, growth %, start timestamps, no rate limiting)
2. Conversation gate: cross-reference candidates against social platforms
   (Reddit, YouTube, TikTok). Discard keywords with no social discussion.

This replaces the old multi-source approach (Exploding Topics, Google Trends
RSS, pytrends trajectory). trendspy's trending_now is faster, more reliable,
and provides richer metadata than any of those.

Discovered keywords become candidates for new zones.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from .zones import ZoneRegistry, Zone

logger = logging.getLogger(__name__)

# Google Trends trending_now topic ID → human-readable category name.
# Derived empirically from trending_now data (Google does not publish this mapping).
# IDs with unknown values fall back to "Other".
TOPIC_CATEGORIES = {
    1: "Autos",
    2: "Beauty & Fashion",
    3: "Business & Finance",
    4: "Entertainment",
    5: "Food & Drink",
    6: "Gaming & Tech",
    7: "Health",
    8: "Hobbies & Pets",
    9: "Education",
    10: "Society & Culture",
    11: "News & Current Events",
    13: "Pets & Animals",
    14: "Politics & Government",
    15: "Science",
    16: "Travel",
    17: "Sports",
    18: "Consumer Products",
    20: "Weather & Nature",
}


def _topic_ids_to_categories(topic_ids: list[int]) -> str:
    """Convert raw topic IDs to comma-separated category names."""
    names = []
    for tid in topic_ids:
        name = TOPIC_CATEGORIES.get(tid)
        if name and name not in names:
            names.append(name)
    return ", ".join(names) if names else "Other"


@dataclass
class EmergingKeyword:
    """A keyword discovered via top-down scanning."""
    keyword: str
    source: str  # google_trends | conversation_verified
    platform_count: int = 1
    engagement_signal: int = 0
    related_terms: list[str] = field(default_factory=list)
    discovered_at: str = ""
    sample_context: str = ""
    # New fields for trendspy metadata
    search_volume: int = 0
    growth_pct: int = 0
    started_hours_ago: float = 0  # how recently the trend started
    gate_passed: bool = False
    gate_platforms: str = ""  # comma-separated platforms with hits
    gate_total_engagement: int = 0
    gate_total_items: int = 0
    gate_sample: str = ""  # sample social post text
    topic_ids: list[int] = field(default_factory=list)
    categories: str = ""  # comma-separated human-readable category names

    def to_dict(self) -> dict:
        return asdict(self)


class TopDownDiscovery:
    """Discovers emerging keywords via candidate generation + conversation gate."""

    def __init__(self, broker=None):
        self.broker = broker

    # ── Step 1: Candidate Generation ──────────────────────────

    async def fetch_candidates(self, geo: str = "US") -> list[EmergingKeyword]:
        """Fetch trending keywords from Google Trends via trendspy.

        Uses trending_now() which returns 100-400+ keywords with:
        - volume: estimated search count
        - volume_growth_pct: growth percentage
        - started_timestamp: when the trend began
        - trend_keywords: related keyword cluster

        This endpoint is NOT rate-limited (unlike interest_over_time).
        """
        keywords: list[EmergingKeyword] = []

        try:
            from trendspy import Trends
            tr = Trends()
            trends = tr.trending_now(geo=geo)

            now_ts = datetime.now(timezone.utc).timestamp()

            for t in trends:
                # Extract metadata
                volume = getattr(t, "volume", 0) or 0
                growth = getattr(t, "volume_growth_pct", 0) or 0
                started = getattr(t, "started_timestamp", None)

                # Calculate hours ago
                hours_ago = 0.0
                if started:
                    ts = max(started) if isinstance(started, list) else started
                    if ts:
                        hours_ago = max(0, (now_ts - ts) / 3600)

                related = getattr(t, "trend_keywords", []) or []
                topic_ids = getattr(t, "topics", []) or []

                keywords.append(EmergingKeyword(
                    keyword=t.keyword,
                    source="google_trends",
                    engagement_signal=growth,
                    related_terms=related[:5],
                    discovered_at=datetime.now(timezone.utc).isoformat(),
                    sample_context=f"Google Trends {geo}: vol={volume:,}, growth=+{growth}%",
                    search_volume=volume,
                    growth_pct=growth,
                    started_hours_ago=round(hours_ago, 1),
                    topic_ids=topic_ids,
                    categories=_topic_ids_to_categories(topic_ids),
                ))

            logger.info(
                f"Candidate generation: {len(keywords)} keywords from "
                f"Google Trends trending_now ({geo})"
            )

        except ImportError:
            logger.error("trendspy not installed — cannot fetch candidates")
        except Exception as e:
            logger.warning(f"Candidate generation failed: {e}")

        return keywords

    # ── Step 2: Conversation Gate ─────────────────────────────

    async def apply_conversation_gate(
        self,
        candidates: list[EmergingKeyword],
        max_keywords: int = 20,
        platforms: list[str] = None,
    ) -> list[EmergingKeyword]:
        """Run candidates through the conversation gate.

        Checks whether real people are discussing each keyword on social
        platforms. Keywords with no social discussion are marked as
        gate_passed=False but kept in the list (for transparency).

        Only the top `max_keywords` candidates are checked (by freshness
        + growth) to bound execution time.

        Returns ALL candidates, with gate results filled in for those checked.
        """
        if not self.broker:
            logger.warning("No broker — skipping conversation gate")
            return candidates

        from .conversation_gate import run_conversation_gate

        # Select top candidates for gate check: prioritize freshest + highest growth
        gate_candidates = sorted(
            candidates,
            key=lambda k: (
                k.started_hours_ago > 0,  # has timestamp first
                k.growth_pct,
            ),
            reverse=True,
        )[:max_keywords]

        gate_keywords = [k.keyword for k in gate_candidates]

        logger.info(
            f"Conversation gate: checking {len(gate_keywords)} of "
            f"{len(candidates)} candidates"
        )

        results = await run_conversation_gate(
            broker=self.broker,
            keywords=gate_keywords,
            platforms=platforms,
            max_keywords=max_keywords,
        )

        # Build lookup
        gate_map = {r.keyword.lower(): r for r in results}

        for kw in candidates:
            gate_result = gate_map.get(kw.keyword.lower())
            if gate_result:
                kw.gate_passed = gate_result.passed
                kw.gate_platforms = ",".join(
                    p for p, v in gate_result.platform_breakdown.items()
                    if v.get("items", 0) > 0
                )
                kw.gate_total_engagement = gate_result.total_engagement
                kw.gate_total_items = gate_result.total_items
                if gate_result.sample_content:
                    kw.gate_sample = gate_result.sample_content[0][:200]
                if gate_result.passed:
                    kw.source = "conversation_verified"
                    kw.platform_count = gate_result.platforms_with_hits + 1

        passed = sum(1 for k in candidates if k.gate_passed)
        logger.info(
            f"Conversation gate: {passed} keywords verified with social discussion"
        )

        return candidates

    # ── Full Pipeline ─────────────────────────────────────────

    async def scan_all(
        self,
        geo: str = "US",
        apply_gate: bool = True,
        gate_max: int = 20,
        gate_platforms: list[str] = None,
        min_volume: int = 0,
        min_growth: int = 0,
        max_age_hours: float = 0,
        categories: list[str] = None,
        gate_only: bool = False,
    ) -> list[EmergingKeyword]:
        """Run the full discovery pipeline.

        1. Candidate generation: Google Trends trending_now (trendspy)
        2. Optional user filters: volume, growth, age, categories
        3. Conversation gate: social platform verification (if broker available)

        User-facing filters are applied BEFORE the gate so we check the
        most relevant candidates, not waste slots on noise the user doesn't want.

        Args:
            geo: Country code for Google Trends (US, SG, GB, etc.)
            apply_gate: Whether to run the conversation gate.
            gate_max: Max keywords to gate-check.
            gate_platforms: Platforms to check in gate.
            min_volume: Minimum search volume to include (0 = no filter).
            min_growth: Minimum growth % to include (0 = no filter).
            max_age_hours: Only trends started within this window (0 = no filter).
            categories: Only include keywords matching these categories
                        (None = all categories, e.g. ["Health", "Consumer Products"]).
            gate_only: Only return keywords that passed the conversation gate.
        """
        # Step 1: Get candidates
        candidates = await self.fetch_candidates(geo=geo)

        if not candidates:
            return []

        # Step 2: Apply user filters (before gate so we check the right ones)
        before = len(candidates)

        if min_volume > 0:
            candidates = [k for k in candidates if k.search_volume >= min_volume]

        if min_growth > 0:
            candidates = [k for k in candidates if k.growth_pct >= min_growth]

        if max_age_hours > 0:
            candidates = [
                k for k in candidates
                if k.started_hours_ago == 0 or k.started_hours_ago <= max_age_hours
            ]

        if categories:
            cat_set = {c.lower() for c in categories}
            candidates = [
                k for k in candidates
                if any(c.lower() in cat_set for c in k.categories.split(", "))
            ]

        filtered = before - len(candidates)
        logger.info(f"User filters removed {filtered} candidates, {len(candidates)} remain")

        # Step 3: Conversation gate
        if apply_gate and self.broker:
            candidates = await self.apply_conversation_gate(
                candidates,
                max_keywords=gate_max,
                platforms=gate_platforms,
            )

        # Step 4: Filter to gate-only if requested
        if gate_only and any(k.gate_passed for k in candidates):
            candidates = [k for k in candidates if k.gate_passed]

        # Sort: gate-passed first, then freshest, then highest growth
        result = sorted(
            candidates,
            key=lambda k: (
                k.gate_passed,
                k.started_hours_ago > 0,
                -k.started_hours_ago if k.started_hours_ago > 0 else -999,
                k.growth_pct,
            ),
            reverse=True,
        )

        gate_count = sum(1 for k in result if k.gate_passed)
        total = len(result)
        logger.info(
            f"Discovery complete: {total} keywords, "
            f"{gate_count} conversation-verified"
        )

        return result

    # ── Legacy fallback: Google Trends RSS ────────────────────

    async def scan_google_trends_rss(self, geo: str = "US") -> list[EmergingKeyword]:
        """Fallback: Google Trends RSS endpoint (10 keywords, no rate limit).

        Only used if trendspy fails. Returns minimal metadata.
        """
        keywords = []
        try:
            from curl_cffi import requests as curl_requests
            session = curl_requests.Session(impersonate="chrome124")
            url = f"https://trends.google.com/trending/rss?geo={geo}"
            resp = await asyncio.to_thread(session.get, url, timeout=20)

            if resp.status_code != 200:
                return keywords

            import xml.etree.ElementTree as ET
            root = ET.fromstring(resp.text)
            ns = {"ht": "https://trends.google.com/trending/rss"}

            for item in root.findall(".//item")[:20]:
                title = item.findtext("title") or ""
                traffic = item.findtext("ht:approx_traffic", namespaces=ns) or ""
                traffic_num = self._parse_traffic(traffic)

                if title:
                    keywords.append(EmergingKeyword(
                        keyword=title.strip(),
                        source="google_trends_rss",
                        engagement_signal=traffic_num,
                        discovered_at=datetime.now(timezone.utc).isoformat(),
                        sample_context=f"Google Trends RSS {geo}: {traffic}",
                    ))

            logger.info(f"RSS fallback: {len(keywords)} keywords")
        except Exception as e:
            logger.warning(f"Google Trends RSS fallback failed: {e}")

        return keywords

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
