"""
Conversation gate — buzzabout methodology step 2.

Conversation checks attach evidence and source health to candidates. They do
not decide universal relevance: different user lenses can value the same event
very differently.

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

# Platforms to check in the optional pre-research triage. Instagram and TikTok
# lead because short-form consumer conversation is the highest-value early
# screen here; YouTube and Reddit remain additive. Per-source failures stay
# explicit and never remove the underlying Google Trends candidate.
GATE_PLATFORMS = ["instagram", "tiktok", "youtube", "reddit"]

# How many items to fetch per platform per keyword
GATE_ITEM_COUNT = 10

# Minimum total engagement to pass the gate
MIN_ENGAGEMENT_THRESHOLD = 100


@dataclass
class ConversationGateResult:
    """Result of running one keyword through the conversation gate."""
    keyword: str
    passed: Optional[bool]      # None = unchecked/partial/source failure
    platforms_with_hits: int    # how many platforms returned results
    total_items: int            # total posts/videos found
    total_engagement: int       # sum of likes + comments + views
    platform_breakdown: dict    # {platform: {items, engagement, top_text}}
    sample_content: list        # up to 3 sample texts from social posts
    gate_score: int             # composite score for ranking
    raw_posts: list = None      # full post data for LLM reader (added in Phase 1)
    status: str = "not_checked" # complete | empty | partial | failed
    source_health: list = None
    error: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        # Don't serialize raw_posts in normal dict output
        d.pop("raw_posts", None)
        return d


async def _check_platform(
    broker,
    keyword: str,
    platform: str,
    count: int = GATE_ITEM_COUNT,
    max_threads: int = 2,
    max_comments: int = 20,
    max_depth: int = 2,
) -> tuple[dict, list[dict]]:
    """Check a single platform for a keyword. Returns (breakdown_dict, raw_items)."""
    try:
        result = await broker.search(
            keyword=keyword,
            platforms=[platform],
            count=count,
        )
        items = result.get("items", [])
        platform_items = [i for i in items if i.get("platform") == platform]
        source_health = result.get("source_health", [])
        platform_result = (result.get("platform_results") or {}).get(platform, {})

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

        thread_records = []
        thread_reads = []
        if hasattr(broker, "fetch_thread") and max_threads > 0:
            ranked = sorted(
                platform_items,
                key=lambda value: (
                    (value.get("engagement") or {}).get("comments") or 0,
                    (value.get("engagement") or {}).get("likes") or 0,
                ),
                reverse=True,
            )[:max_threads]
            hydrated = await asyncio.gather(*[
                broker.fetch_thread(
                    item, max_comments=max_comments, max_depth=max_depth
                )
                for item in ranked
            ]) if ranked else []
            for result in hydrated:
                thread_reads.append({
                    "platform": platform,
                    "status": result.status,
                    "root_post_external_id": result.root_post_external_id,
                    "returned_count": result.returned_count,
                    "truncated": result.truncated,
                    "attempted_route": result.attempted_route,
                    "error_category": result.error_category,
                    "platform_reported_total": result.platform_reported_total,
                    "limitations": list(result.limitations),
                })
                for record in result.records:
                    thread_records.append({
                        "platform": record.platform,
                        "post_id": record.external_id,
                        "external_id": record.external_id,
                        "record_type": record.record_type,
                        "object_type": "comment",
                        "parent_external_id": record.parent_external_id,
                        "root_post_external_id": record.root_post_external_id,
                        "depth": record.depth,
                        "url": record.url,
                        "author": {
                            "id": record.author_external_id,
                            "username": record.author_username,
                        },
                        "text": record.text,
                        "created_at": record.published_at,
                        "engagement": {"likes": record.likes},
                        "raw": dict(record.raw),
                        "provenance": {
                            "connector": result.attempted_route,
                            "query": keyword,
                        },
                    })

        breakdown = {
            "items": len(platform_items),
            "engagement": engagement,
            "top_text": sample_texts[0] if sample_texts else "",
            "status": platform_result.get("status", "complete"),
            "source_health": source_health,
            "thread_reads": thread_reads,
            "thread_records": len(thread_records),
        }
        return breakdown, platform_items + thread_records
    except Exception as e:
        logger.debug(f"Gate check failed for '{keyword}' on {platform}: {e}")
        return {
            "items": 0,
            "engagement": 0,
            "top_text": "",
            "status": "error",
            "source_health": [{
                "platform": platform,
                "status": "error",
                "error_category": "broker_exception",
            }],
            "error": str(e)[:100],
        }, []


async def gate_check_keyword(
    broker,
    keyword: str,
    platforms: list[str] = None,
    count: int = GATE_ITEM_COUNT,
    max_threads_per_platform: int = 2,
    max_comments_per_thread: int = 20,
    max_thread_depth: int = 2,
) -> ConversationGateResult:
    """Run a single keyword through the conversation gate.

    Checks multiple social platforms concurrently for discussion of this
    keyword. ``max_threads_per_platform=0`` restricts the check to root posts: no
    comment-thread hydration, root counts/engagement/source health only.
    """
    if platforms is None:
        platforms = GATE_PLATFORMS

    # Run all platform checks concurrently
    tasks = [
        _check_platform(
            broker, keyword, platform, count,
            max_threads_per_platform, max_comments_per_thread, max_thread_depth,
        )
        for platform in platforms
    ]
    results = await asyncio.gather(*tasks)

    platform_breakdown = {}
    total_items = 0
    total_engagement = 0
    platforms_with_hits = 0
    sample_content = []
    all_raw_posts = []
    all_source_health = []

    for platform, (breakdown, raw_items) in zip(platforms, results):
        items = breakdown.get("items", 0)
        engagement = breakdown.get("engagement", 0)
        platform_breakdown[platform] = breakdown
        all_source_health.extend(breakdown.get("source_health", []))
        for thread_read in breakdown.get("thread_reads", []):
            all_source_health.append({
                "platform": platform,
                "connector": thread_read.get("attempted_route"),
                "status": thread_read.get("status"),
                "items_returned": thread_read.get("returned_count", 0),
                "error": thread_read.get("error_category"),
                "coverage": {
                    "root_post_external_id": thread_read.get("root_post_external_id"),
                    "platform_reported_total": thread_read.get("platform_reported_total"),
                    "truncated": thread_read.get("truncated", False),
                },
            })

        total_items += items
        total_engagement += engagement
        all_raw_posts.extend(raw_items)
        if items > 0:
            platforms_with_hits += 1
            top_text = breakdown.get("top_text", "")
            if top_text and len(sample_content) < 3:
                sample_content.append(f"[{platform}] {top_text}")

    # Composite score: platform diversity is the strongest signal,
    # then engagement, then item count
    gate_score = (
        platforms_with_hits * 1000
        + min(total_engagement, 100000) // 100
        + total_items * 10
    )

    platform_statuses = {
        str(value.get("status") or "").lower()
        for value in platform_breakdown.values()
    }
    failed_statuses = {"error", "failed", "blocked", "skipped", "unavailable"}
    if platform_statuses and platform_statuses.issubset(failed_statuses):
        status = "failed"
        passed = None
    elif "partial" in platform_statuses or platform_statuses & failed_statuses:
        status = "partial"
        passed = None
    elif total_items == 0:
        status = "empty"
        passed = False
    else:
        status = "complete"
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
        raw_posts=all_raw_posts,
        status=status,
        source_health=all_source_health,
    )


async def run_conversation_gate(
    broker,
    keywords: list[str],
    platforms: list[str] = None,
    max_keywords: int = 20,
    concurrency: int = 5,
    max_threads: int = 2,
) -> list[ConversationGateResult]:
    """Run the conversation gate on multiple candidate keywords.

    Processes keywords in small concurrent batches to avoid overwhelming
    social platform connectors.

    Args:
        broker: SourceBroker instance with registered connectors.
        keywords: Candidate keywords from trending_now.
        platforms: Social platforms to check (default: Instagram, TikTok, YouTube, Reddit).
        max_keywords: Maximum keywords to gate-check (bounds execution time).
        concurrency: Keyword concurrency for stateless sources. Instagram forces 1.
        max_threads: Thread hydrations allowed per platform per keyword.
            ``0`` means root evidence only (root sweep mode).
    """
    keywords = keywords[:max_keywords]
    if platforms is None:
        platforms = GATE_PLATFORMS
    # The authenticated Instagram connector shares one session and throttle.
    # Serialize keyword batches whenever it is selected so root triage cannot
    # burst concurrent requests through the same account.
    effective_concurrency = 1 if "instagram" in platforms else max(1, concurrency)

    results: list[ConversationGateResult] = []

    # Process in bounded batches to avoid hammering platform accounts.
    for i in range(0, len(keywords), effective_concurrency):
        batch = keywords[i : i + effective_concurrency]
        batch_tasks = [
            gate_check_keyword(broker, kw, platforms, max_threads_per_platform=max_threads)
            for kw in batch
        ]
        batch_results = await asyncio.gather(*batch_tasks)
        results.extend(batch_results)

        logger.info(
            f"Conversation gate batch {i // effective_concurrency + 1}: "
            f"checked {len(batch)} keywords, "
            f"{sum(1 for r in batch_results if r.passed is True)} passed"
        )

    # Sort by gate score descending
    results.sort(key=lambda r: r.gate_score, reverse=True)

    passed = [r for r in results if r.passed is True]
    failed = [r for r in results if r.passed is False]

    logger.info(
        f"Conversation gate complete: {len(passed)}/{len(results)} keywords passed "
        f"({len(failed)} below the configured evidence threshold)"
    )

    return results
