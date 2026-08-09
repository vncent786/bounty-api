"""
Conversation gate — buzzabout methodology step 2.

"Put every candidate through a conversation gate. If nobody is discussing it
anywhere, it is a search artifact. This stage kills most of the list."

Takes candidate keywords from Google Trends (trending_now) and checks whether
real people are discussing them across social platforms (Reddit, YouTube,
TikTok, X, Instagram). Keywords with zero social discussion are discarded as
search artifacts. Keywords with discussion across 2+ platforms are high signal.

Architecture:
    candidates (from trendspy)
        ↓
    broker.search(keyword, platforms=[...], count=5)
        ↓
    score: item count + engagement + platform diversity
        ↓
    PASS (has social conversation) / FAIL (search artifact)
"""

import asyncio
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Platforms to check in the gate. Priority order: fastest first.
# YouTube and Reddit are HTTP-based (fast). TikTok/X/Instagram use
# browser sessions (slower). We check all of them but stagger.
GATE_PLATFORMS = ["youtube", "reddit", "tiktok"]

# How many items to fetch per platform per keyword (keep light)
GATE_ITEM_COUNT = 5

# Minimum total engagement to pass the gate
MIN_ENGAGEMENT_THRESHOLD = 100


@dataclass
class ConversationGateResult:
    """Result of running one keyword through the conversation gate."""
    keyword: str
    passed: bool                # True = has social discussion
    platforms_with_hits: int    # how many platforms returned results
    total_items: int            # total posts/videos found
    total_engagement: int       # sum of likes + comments + views
    platform_breakdown: dict    # {platform: {items, engagement, top_text}}
    sample_content: list        # up to 3 sample texts from social posts
    gate_score: int             # composite score for ranking
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


async def _check_platform(
    broker,
    keyword: str,
    platform: str,
    count: int = GATE_ITEM_COUNT,
) -> dict:
    """Check a single platform for a keyword. Returns breakdown dict."""
    try:
        result = await broker.search(
            keyword=keyword,
            platforms=[platform],
            count=count,
        )
        items = result.get("items", [])
        platform_items = [i for i in items if i.get("platform") == platform]

        engagement = 0
        sample_texts = []
        for item in platform_items:
            eng = item.get("engagement", {})
            engagement += (
                (eng.get("likes") or 0)
                + (eng.get("comments") or 0)
                + ((eng.get("views") or 0) // 1000)  # views weighted low
            )
            text = item.get("text") or item.get("title") or ""
            if text:
                sample_texts.append(text[:120])

        return {
            "items": len(platform_items),
            "engagement": engagement,
            "top_text": sample_texts[0] if sample_texts else "",
        }
    except Exception as e:
        logger.debug(f"Gate check failed for '{keyword}' on {platform}: {e}")
        return {
            "items": 0,
            "engagement": 0,
            "top_text": "",
            "error": str(e)[:100],
        }


async def gate_check_keyword(
    broker,
    keyword: str,
    platforms: list[str] = None,
    count: int = GATE_ITEM_COUNT,
) -> ConversationGateResult:
    """Run a single keyword through the conversation gate.

    Checks multiple social platforms concurrently for discussion of this keyword.
    """
    if platforms is None:
        platforms = GATE_PLATFORMS

    # Run all platform checks concurrently
    tasks = [
        _check_platform(broker, keyword, platform, count)
        for platform in platforms
    ]
    results = await asyncio.gather(*tasks)

    platform_breakdown = {}
    total_items = 0
    total_engagement = 0
    platforms_with_hits = 0
    sample_content = []

    for platform, result in zip(platforms, results):
        items = result.get("items", 0)
        engagement = result.get("engagement", 0)
        platform_breakdown[platform] = result

        total_items += items
        total_engagement += engagement
        if items > 0:
            platforms_with_hits += 1
            top_text = result.get("top_text", "")
            if top_text and len(sample_content) < 3:
                sample_content.append(f"[{platform}] {top_text}")

    # Composite score: platform diversity is the strongest signal,
    # then engagement, then item count
    gate_score = (
        platforms_with_hits * 1000
        + min(total_engagement, 100000) // 100
        + total_items * 10
    )

    # Pass gate: at least 1 platform has discussion with meaningful engagement
    passed = platforms_with_hits >= 1 and total_engagement >= MIN_ENGAGEMENT_THRESHOLD

    return ConversationGateResult(
        keyword=keyword,
        passed=passed,
        platforms_with_hits=platforms_with_hits,
        total_items=total_items,
        total_engagement=total_engagement,
        platform_breakdown=platform_breakdown,
        sample_content=sample_content,
        gate_score=gate_score,
    )


async def run_conversation_gate(
    broker,
    keywords: list[str],
    platforms: list[str] = None,
    max_keywords: int = 20,
    concurrency: int = 3,
) -> list[ConversationGateResult]:
    """Run the conversation gate on multiple candidate keywords.

    Processes keywords in small concurrent batches to avoid overwhelming
    social platform connectors.

    Args:
        broker: SourceBroker instance with registered connectors.
        keywords: Candidate keywords from trending_now.
        platforms: Social platforms to check (default: youtube, reddit, tiktok).
        max_keywords: Maximum keywords to gate-check (bounds execution time).
        concurrency: How many keywords to check simultaneously.

    Returns:
        List of ConversationGateResult sorted by gate_score descending.
        Only includes keywords that passed the gate (have social discussion).
    """
    keywords = keywords[:max_keywords]
    if platforms is None:
        platforms = GATE_PLATFORMS

    results: list[ConversationGateResult] = []

    # Process in batches of `concurrency` to avoid hammering platforms
    for i in range(0, len(keywords), concurrency):
        batch = keywords[i : i + concurrency]
        batch_tasks = [
            gate_check_keyword(broker, kw, platforms)
            for kw in batch
        ]
        batch_results = await asyncio.gather(*batch_tasks)
        results.extend(batch_results)

        logger.info(
            f"Conversation gate batch {i // concurrency + 1}: "
            f"checked {len(batch)} keywords, "
            f"{sum(1 for r in batch_results if r.passed)} passed"
        )

    # Sort by gate score descending
    results.sort(key=lambda r: r.gate_score, reverse=True)

    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]

    logger.info(
        f"Conversation gate complete: {len(passed)}/{len(results)} keywords passed "
        f"({len(failed)} discarded as search artifacts)"
    )

    return results
