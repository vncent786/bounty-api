"""
Top-down keyword discovery — buzzabout methodology.

Two-step pipeline:
1. Candidate generation: Google Trends trending_now via trendspy
   (388 keywords, volume estimates, growth %, start timestamps, no rate limiting)
2. Conversation evidence: cross-reference candidates against social platforms
   (Reddit, YouTube, TikTok) without applying a universal relevance filter.

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
from typing import Optional

from .zones import ZoneRegistry, Zone

logger = logging.getLogger(__name__)

# Google Trends Trending Now topic IDs from trendspy==0.1.6 TREND_TOPICS.
# Keep this versioned snapshot source-native: IDs are collection semantics,
# not product taxonomy slots that Bounty may relabel.
TREND_CATEGORY_SCHEMA_VERSION = "trendspy-0.1.6"
TOPIC_CATEGORIES = {
    1: "Autos & Vehicles",
    2: "Beauty & Fashion",
    3: "Business & Finance",
    20: "Climate",
    4: "Entertainment",
    5: "Food & Drink",
    6: "Games",
    7: "Health",
    8: "Hobbies & Leisure",
    9: "Jobs & Education",
    10: "Law & Government",
    11: "Other",
    13: "Pets & Animals",
    14: "Politics",
    15: "Science",
    16: "Shopping",
    17: "Sports",
    18: "Technology",
    19: "Travel & Transportation",
}
TREND_CATEGORY_NAMES = tuple(dict.fromkeys(TOPIC_CATEGORIES.values()))

# Verified 2026-08-16 against trendspy==0.1.6 Trends.trending_now. The
# endpoint returned data for all 125 entries; unsupported geographies are not
# exposed as choices because trendspy otherwise fails with an empty payload.
TRENDING_NOW_COUNTRIES_VERSION = "2026-08-16/trendspy-0.1.6"
TRENDING_NOW_COUNTRIES = (
    ("AL", "Albania"),
    ("DZ", "Algeria"),
    ("AO", "Angola"),
    ("AR", "Argentina"),
    ("AM", "Armenia"),
    ("AU", "Australia"),
    ("AT", "Austria"),
    ("AZ", "Azerbaijan"),
    ("BH", "Bahrain"),
    ("BD", "Bangladesh"),
    ("BY", "Belarus"),
    ("BE", "Belgium"),
    ("BJ", "Benin"),
    ("BO", "Bolivia"),
    ("BA", "Bosnia & Herzegovina"),
    ("BR", "Brazil"),
    ("BG", "Bulgaria"),
    ("BF", "Burkina Faso"),
    ("KH", "Cambodia"),
    ("CM", "Cameroon"),
    ("CA", "Canada"),
    ("CL", "Chile"),
    ("CO", "Colombia"),
    ("CD", "Congo - Kinshasa"),
    ("CR", "Costa Rica"),
    ("HR", "Croatia"),
    ("CU", "Cuba"),
    ("CY", "Cyprus"),
    ("CZ", "Czechia"),
    ("CI", "Côte d’Ivoire"),
    ("DK", "Denmark"),
    ("DO", "Dominican Republic"),
    ("EC", "Ecuador"),
    ("EG", "Egypt"),
    ("SV", "El Salvador"),
    ("EE", "Estonia"),
    ("ET", "Ethiopia"),
    ("FI", "Finland"),
    ("FR", "France"),
    ("GE", "Georgia"),
    ("DE", "Germany"),
    ("GH", "Ghana"),
    ("GR", "Greece"),
    ("GT", "Guatemala"),
    ("HT", "Haiti"),
    ("HN", "Honduras"),
    ("HK", "Hong Kong"),
    ("HU", "Hungary"),
    ("IN", "India"),
    ("ID", "Indonesia"),
    ("IR", "Iran"),
    ("IQ", "Iraq"),
    ("IE", "Ireland"),
    ("IL", "Israel"),
    ("IT", "Italy"),
    ("JM", "Jamaica"),
    ("JP", "Japan"),
    ("JO", "Jordan"),
    ("KZ", "Kazakhstan"),
    ("KE", "Kenya"),
    ("KW", "Kuwait"),
    ("KG", "Kyrgyzstan"),
    ("LV", "Latvia"),
    ("LB", "Lebanon"),
    ("LY", "Libya"),
    ("LT", "Lithuania"),
    ("MY", "Malaysia"),
    ("ML", "Mali"),
    ("MX", "Mexico"),
    ("MD", "Moldova"),
    ("MA", "Morocco"),
    ("MZ", "Mozambique"),
    ("MM", "Myanmar (Burma)"),
    ("NP", "Nepal"),
    ("NL", "Netherlands"),
    ("NZ", "New Zealand"),
    ("NI", "Nicaragua"),
    ("NG", "Nigeria"),
    ("MK", "North Macedonia"),
    ("NO", "Norway"),
    ("OM", "Oman"),
    ("PK", "Pakistan"),
    ("PS", "Palestine"),
    ("PA", "Panama"),
    ("PY", "Paraguay"),
    ("PE", "Peru"),
    ("PH", "Philippines"),
    ("PL", "Poland"),
    ("PT", "Portugal"),
    ("PR", "Puerto Rico"),
    ("QA", "Qatar"),
    ("RO", "Romania"),
    ("RU", "Russia"),
    ("SA", "Saudi Arabia"),
    ("SN", "Senegal"),
    ("RS", "Serbia"),
    ("SG", "Singapore"),
    ("SK", "Slovakia"),
    ("SI", "Slovenia"),
    ("ZA", "South Africa"),
    ("KR", "South Korea"),
    ("ES", "Spain"),
    ("LK", "Sri Lanka"),
    ("SE", "Sweden"),
    ("CH", "Switzerland"),
    ("SY", "Syria"),
    ("TW", "Taiwan"),
    ("TZ", "Tanzania"),
    ("TH", "Thailand"),
    ("TT", "Trinidad & Tobago"),
    ("TN", "Tunisia"),
    ("TM", "Turkmenistan"),
    ("TR", "Türkiye"),
    ("UG", "Uganda"),
    ("UA", "Ukraine"),
    ("AE", "United Arab Emirates"),
    ("GB", "United Kingdom"),
    ("US", "United States"),
    ("UY", "Uruguay"),
    ("UZ", "Uzbekistan"),
    ("VE", "Venezuela"),
    ("VN", "Vietnam"),
    ("YE", "Yemen"),
    ("ZM", "Zambia"),
    ("ZW", "Zimbabwe"),
)
TRENDING_NOW_COUNTRY_CODES = frozenset(
    code for code, _name in TRENDING_NOW_COUNTRIES
)


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
    source: str  # google_trends; enrichment never mutates provenance
    platform_count: int = 1
    engagement_signal: int = 0
    related_terms: list[str] = field(default_factory=list)
    discovered_at: str = ""
    sample_context: str = ""
    # trendspy source measurements; missing remains missing, never zero-filled
    search_volume: Optional[int] = None
    growth_pct: Optional[float] = None
    started_hours_ago: Optional[float] = None
    source_started_at: Optional[str] = None
    discovery_run_id: str = ""
    candidate_observation_id: Optional[int] = None
    gate_passed: Optional[bool] = None
    gate_status: str = "not_checked"
    gate_source_health: list[dict] = field(default_factory=list)
    gate_platforms: str = ""  # comma-separated platforms with hits
    gate_total_engagement: int = 0
    gate_total_items: int = 0
    gate_sample: str = ""  # sample social post text
    topic_ids: list[int] = field(default_factory=list)
    categories: str = ""  # comma-separated human-readable category names
    source_record_count: int = 1
    source_observations: list[dict] = field(default_factory=list)
    metric_conflicts: list[str] = field(default_factory=list)
    # Phase 1: LLM conversation reader fields
    conv_summary: str = ""
    conv_sentiment: str = ""
    conv_brands: str = ""  # comma-separated
    conv_trend_type: str = ""  # Product/Consumer | Event | Cultural/Behavioral | Personality
    conv_type_reason: str = ""
    conv_key_quote: str = ""
    conversation_analysis: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _candidate_rank_key(candidate: EmergingKeyword) -> tuple:
    """Rank collection candidates without inventing missing measurements."""
    return (
        candidate.started_hours_ago is not None,
        -candidate.started_hours_ago if candidate.started_hours_ago is not None else -999,
        candidate.growth_pct if candidate.growth_pct is not None else float("-inf"),
    )


def rank_candidates(candidates: list[EmergingKeyword]) -> list[EmergingKeyword]:
    """Return the existing neutral Trends ordering with explicit missingness."""
    return sorted(candidates, key=_candidate_rank_key, reverse=True)


def diversified_candidates(
    candidates: list[EmergingKeyword],
    limit: int | None = None,
) -> list[EmergingKeyword]:
    """Round-robin ranked candidates across their first source category.

    This is category-agnostic: it neither promotes nor excludes Sports (or any
    other category). It prevents a large source bucket from monopolising a
    bounded review/check set while preserving rank within every bucket.
    """
    buckets: dict[str, list[EmergingKeyword]] = {}
    for candidate in candidates:
        primary = next(
            (part.strip() for part in candidate.categories.split(",") if part.strip()),
            "Other",
        )
        buckets.setdefault(primary, []).append(candidate)

    selected: list[EmergingKeyword] = []
    bucket_names = sorted(buckets, key=str.casefold)
    index = 0
    while bucket_names and (limit is None or len(selected) < limit):
        name = bucket_names[index]
        bucket = buckets[name]
        selected.append(bucket.pop(0))
        if not bucket:
            bucket_names.pop(index)
            if not bucket_names:
                break
            index %= len(bucket_names)
        else:
            index = (index + 1) % len(bucket_names)
    return selected


def _deduplicate_candidates(items: list[EmergingKeyword]) -> list[EmergingKeyword]:
    merged: dict[str, EmergingKeyword] = {}
    for item in items:
        key = " ".join(item.keyword.casefold().split())
        if key not in merged:
            merged[key] = item
            continue
        current = merged[key]
        current.source_record_count += item.source_record_count
        current.source_observations.extend(item.source_observations)
        current.related_terms = list(dict.fromkeys(
            current.related_terms + item.related_terms
        ))
        current.topic_ids = list(dict.fromkeys(current.topic_ids + item.topic_ids))
        current.categories = ", ".join(dict.fromkeys(
            [part.strip() for value in (current.categories, item.categories)
             for part in value.split(",") if part.strip()]
        ))
        for field in ("search_volume", "growth_pct", "source_started_at"):
            if getattr(current, field) != getattr(item, field):
                setattr(current, field, None)
                if field not in current.metric_conflicts:
                    current.metric_conflicts.append(field)
        if "source_started_at" in current.metric_conflicts:
            current.started_hours_ago = None
        if "growth_pct" in current.metric_conflicts:
            current.engagement_signal = 0
    return list(merged.values())


class TopDownDiscovery:
    """Discovers emerging keywords via candidate generation + conversation gate."""

    def __init__(self, broker=None, discovery_store=None):
        self.broker = broker
        self.discovery_store = discovery_store
        self.last_run_id = ""

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
        observed_at = datetime.now(timezone.utc)

        try:
            from trendspy import Trends
            tr = Trends()
            trends = tr.trending_now(geo=geo)

            now_ts = datetime.now(timezone.utc).timestamp()

            for t in trends:
                # Extract metadata
                volume = getattr(t, "volume", None)
                growth = getattr(t, "volume_growth_pct", None)
                started = getattr(t, "started_timestamp", None)

                # Calculate hours ago
                hours_ago = None
                source_started_at = None
                if started:
                    ts = max(started) if isinstance(started, list) else started
                    if ts:
                        hours_ago = max(0, (now_ts - float(ts)) / 3600)
                        source_started_at = datetime.fromtimestamp(
                            float(ts), timezone.utc
                        ).isoformat()

                related = getattr(t, "trend_keywords", []) or []
                topic_ids = getattr(t, "topics", []) or []

                keywords.append(EmergingKeyword(
                    keyword=t.keyword,
                    source="google_trends",
                    engagement_signal=growth or 0,
                    related_terms=related[:5],
                    discovered_at=datetime.now(timezone.utc).isoformat(),
                    sample_context=(
                        f"Google Trends {geo}: "
                        f"vol={volume if volume is not None else 'unknown'}, "
                        f"growth={growth if growth is not None else 'unknown'}"
                    ),
                    search_volume=volume,
                    growth_pct=growth,
                    started_hours_ago=(
                        round(hours_ago, 1) if hours_ago is not None else None
                    ),
                    source_started_at=source_started_at,
                    topic_ids=topic_ids,
                    categories=_topic_ids_to_categories(topic_ids),
                    source_observations=[{
                        "keyword": t.keyword,
                        "search_volume": volume,
                        "growth_pct": growth,
                        "source_started_at": source_started_at,
                        "related_terms": related,
                        "topic_ids": topic_ids,
                    }],
                ))

            keywords = _deduplicate_candidates(keywords)
            logger.info(
                f"Candidate generation: {len(keywords)} keywords from "
                f"Google Trends trending_now ({geo})"
            )

            if self.discovery_store:
                self.last_run_id = self.discovery_store.record_feed(
                    geo=geo,
                    observed_at=observed_at,
                    candidates=[{
                        "keyword": item.keyword,
                        "related_terms": item.related_terms,
                        "search_volume": item.search_volume,
                        "growth_pct": item.growth_pct,
                        "source_started_at": item.source_started_at,
                        "topic_ids": item.topic_ids,
                        "categories": item.categories,
                        "source_record_count": item.source_record_count,
                        "source_observations": item.source_observations,
                        "metric_conflicts": item.metric_conflicts,
                    } for item in keywords],
                    source_health=[{
                        "source": "trendspy.trending_now",
                        "status": "complete",
                        "items_returned": len(keywords),
                    }],
                )
                persisted = {
                    row["normalized_keyword"]: row
                    for row in self.discovery_store.list_run_candidates(
                        self.last_run_id
                    )
                }
                for item in keywords:
                    row = persisted.get(" ".join(item.keyword.casefold().split()))
                    if row:
                        item.discovery_run_id = self.last_run_id
                        item.candidate_observation_id = row["observation_id"]

        except ImportError:
            logger.error("trendspy not installed — cannot fetch candidates")
            if self.discovery_store:
                self.last_run_id = self.discovery_store.record_feed(
                    geo=geo, observed_at=observed_at, candidates=[],
                    status="error", comparable=False,
                    error_category="dependency_missing",
                )
        except Exception as e:
            stage = "persistence" if keywords else "collection"
            logger.warning(f"Candidate {stage} failed: {e}")
            if self.discovery_store:
                self.last_run_id = self.discovery_store.record_feed(
                    geo=geo, observed_at=observed_at, candidates=[],
                    status="error", comparable=False,
                    error_category=f"{stage}:{type(e).__name__}",
                    source_health=[{
                        "source": (
                            "discovery_store" if stage == "persistence"
                            else "trendspy.trending_now"
                        ),
                        "status": "error",
                        "error_category": type(e).__name__,
                    }],
                )

        return keywords

    # ── Step 2: Conversation Gate ─────────────────────────────

    async def apply_conversation_gate(
        self,
        candidates: list[EmergingKeyword],
        max_keywords: int = 20,
        platforms: list[str] = None,
        max_threads: int = 0,
    ) -> list[EmergingKeyword]:
        """Attach root social evidence to candidates (conversation gate).

        Checks whether real people are discussing each keyword on social
        platforms. Keywords with no social discussion are marked as
        gate_passed=False but kept in the list (for transparency).

        Only the top `max_keywords` candidates are checked (by freshness
        + growth) to bound execution time.

        ``max_threads`` bounds comment-thread hydration per platform;
        ``0`` means root posts only (root sweep). This method never
        performs LLM analysis: conversation reading belongs to explicit
        research-run stages (discovery/handlers.py).

        Returns ALL candidates, with gate results filled in for those checked.
        """
        if not self.broker:
            logger.warning("No broker — skipping conversation gate")
            return candidates

        from .conversation_gate import run_conversation_gate

        # Rank within each source category, then round-robin categories so a
        # large current-events bucket cannot monopolise the bounded check set.
        gate_candidates = diversified_candidates(
            rank_candidates(candidates),
            limit=max_keywords,
        )

        gate_keywords = [k.keyword for k in gate_candidates]

        logger.info(
            f"Conversation gate: checking {len(gate_keywords)} of "
            f"{len(candidates)} candidates (max_threads={max_threads})"
        )

        results = await run_conversation_gate(
            broker=self.broker,
            keywords=gate_keywords,
            platforms=platforms,
            max_keywords=max_keywords,
            max_threads=max_threads,
        )

        # Build lookup
        gate_map = {r.keyword.lower(): r for r in results}

        for kw in candidates:
            gate_result = gate_map.get(kw.keyword.lower())
            if gate_result:
                kw.gate_passed = gate_result.passed
                kw.gate_status = gate_result.status
                kw.gate_source_health = gate_result.source_health or []
                kw.gate_platforms = ",".join(
                    p for p, v in gate_result.platform_breakdown.items()
                    if v.get("items", 0) > 0
                )
                kw.gate_total_engagement = gate_result.total_engagement
                kw.gate_total_items = gate_result.total_items
                if gate_result.sample_content:
                    kw.gate_sample = gate_result.sample_content[0][:200]
                if gate_result.passed is True:
                    kw.platform_count = gate_result.platforms_with_hits + 1

        passed = sum(1 for k in candidates if k.gate_passed)
        logger.info(
            f"Conversation gate: {passed} keywords verified with social discussion"
        )

        # No LLM reader here. Horizontal conversation analysis runs only in
        # explicit research-run stages (deep_read / horizontal_extraction),
        # never from the Explore Trend feed.

        if self.discovery_store:
            for kw in candidates:
                if kw.candidate_observation_id is None:
                    continue
                gate_result = gate_map.get(kw.keyword.lower())
                if gate_result is None:
                    self.discovery_store.record_gate_check(
                        kw.candidate_observation_id,
                        status="not_checked",
                        passed=None,
                    )
                    continue
                self.discovery_store.record_gate_check(
                    kw.candidate_observation_id,
                    status=gate_result.status,
                    passed=gate_result.passed,
                    platforms=list(gate_result.platform_breakdown),
                    total_items=gate_result.total_items,
                    source_health=gate_result.source_health or [],
                    records=gate_result.raw_posts or [],
                    error_category=gate_result.error or None,
                )

        return candidates

    # ── Full Pipeline ─────────────────────────────────────────

    async def scan_all(
        self,
        geo: str = "US",
        apply_gate: bool | None = None,
        gate_max: int = 20,
        gate_platforms: list[str] = None,
        min_volume: int = 0,
        min_growth: int = 0,
        max_age_hours: float = 0,
        categories: list[str] = None,
        gate_only: bool = False,
        mode=None,
    ) -> list[EmergingKeyword]:
        """Run the discovery pipeline in an explicit scan mode.

        1. Candidate generation: Google Trends trending_now (trendspy)
        2. Optional user filters: volume, growth, age, categories
        3. Mode-governed social check (see scan_modes.py policies)

        Modes:
        - ``trends_snapshot`` (default): Trends metadata only. Persists
          raw candidates; zero broker searches, zero thread hydration,
          zero LLM calls.
        - ``root_sweep``: additionally checks root social records per
          candidate (counts, engagement, source health) with
          ``max_threads=0`` and no LLM.
        - ``deep_read`` / ``horizontal_synthesis`` /
          ``optional_interpretation``: research-run stages only; passing
          them here raises ``ValueError``. Deep reads and LLM analysis
          happen exclusively in explicit research runs.

        ``apply_gate`` is the legacy spelling: ``True`` resolves to
        ``root_sweep`` and ``False`` to ``trends_snapshot``; an explicit
        ``mode`` always wins. No legacy combination triggers LLM analysis.

        Args:
            geo: Country code for Google Trends (US, SG, GB, etc.)
            apply_gate: Legacy flag for the social-source check.
            mode: Explicit scan mode (ScanMode or string).
            gate_max: Max keywords to gate-check.
            gate_platforms: Platforms to check in gate.
            min_volume: Minimum search volume to include (0 = no filter).
            min_growth: Minimum growth % to include (0 = no filter).
            max_age_hours: Only trends started within this window (0 = no filter).
            categories: Only include keywords matching these categories
                        (None = all categories, e.g. ["Health", "Shopping"]).
            gate_only: Only return keywords that passed the conversation gate.
        """
        from social_scraper.discovery.scan_modes import (
            RESEARCH_RUN_MODES,
            policy_for,
            resolve_scan_mode,
        )

        scan_mode = resolve_scan_mode(mode=mode, apply_gate=apply_gate)
        if scan_mode in RESEARCH_RUN_MODES:
            raise ValueError(
                f"scan mode {scan_mode.value} is a research-run stage; "
                "run it through an explicit research run instead"
            )
        policy = policy_for(scan_mode)

        if gate_only and not policy.allows_broker_search:
            raise ValueError(
                "gate_only requires a scan mode that checks conversations; "
                f"{scan_mode.value} never runs the conversation check"
            )

        # Step 1: Get candidates
        candidates = await self.fetch_candidates(geo=geo)

        if not candidates:
            return []

        # Step 2: Apply user filters (before gate so we check the right ones)
        before = len(candidates)

        if min_volume > 0:
            candidates = [
                k for k in candidates
                if k.search_volume is not None and k.search_volume >= min_volume
            ]

        if min_growth > 0:
            candidates = [
                k for k in candidates
                if k.growth_pct is not None and k.growth_pct >= min_growth
            ]

        if max_age_hours > 0:
            candidates = [
                k for k in candidates
                if k.started_hours_ago is not None
                and k.started_hours_ago <= max_age_hours
            ]

        if categories:
            cat_set = {c.lower() for c in categories}
            candidates = [
                k for k in candidates
                if any(c.lower() in cat_set for c in k.categories.split(", "))
            ]

        filtered = before - len(candidates)
        logger.info(f"User filters removed {filtered} candidates, {len(candidates)} remain")

        # Step 3: Mode-governed social check. Only modes whose policy allows
        # broker searches reach the gate; the snapshot stops at Trends
        # metadata. Thread hydration and LLM analysis never happen here.
        if policy.allows_broker_search and self.broker:
            candidates = await self.apply_conversation_gate(
                candidates,
                max_keywords=gate_max,
                platforms=gate_platforms,
                max_threads=policy.max_threads if policy.max_threads is not None else 2,
            )

        # Step 4: Filter to gate-only if requested
        if gate_only:
            candidates = [k for k in candidates if k.gate_passed is True]

        # Neutral collection priority. Active user lenses may rerank this set.
        result = rank_candidates(candidates)

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
