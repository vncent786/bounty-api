"""Reusable staged handlers that connect StagedRunner to real sources and LLM analysis.

These handlers bridge the deterministic planning layer (DiscoveryScheduler +
StagedRunner) to the actual collection and analysis infrastructure
(SourceBroker + conversation_gate + triage.analyze_conversation).

Each handler conforms to the StageHandlerResult contract so the StagedRunner
can record usage, enforce caps, and prevent budget overruns.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Mapping

from .staged_runner import StageHandlerResult

logger = logging.getLogger(__name__)

DEFAULT_PLATFORMS = ["youtube", "reddit"]


def _budget_from_context(context: Mapping[str, Any] | None, plan: Mapping[str, Any] | None) -> dict[str, int]:
    """Extract thread/comment caps from the effective budget in the plan."""
    budget = {}
    if plan and "effective_budget" in plan:
        eb = plan["effective_budget"]
        budget["threads_per_platform"] = eb.get("threads_per_platform", 2)
        budget["comments_per_thread"] = eb.get("comments_per_thread", 20)
        budget["max_thread_depth"] = eb.get("max_thread_depth", 2)
    else:
        budget["threads_per_platform"] = 2
        budget["comments_per_thread"] = 20
        budget["max_thread_depth"] = 2
    return budget


def _keyword_from_candidate(candidate: Mapping[str, Any]) -> str:
    """Extract the search keyword from a candidate dict."""
    return str(
        candidate.get("keyword") or candidate.get("name") or candidate.get("query") or ""
    ).strip()


def _platforms_from_candidate(candidate: Mapping[str, Any]) -> list[str]:
    """Determine which platforms to search for this candidate."""
    raw = candidate.get("platforms")
    if isinstance(raw, list) and raw:
        return [str(p).lower() for p in raw]
    return DEFAULT_PLATFORMS


async def _ensure_reddit_subreddits(keyword: str) -> list[str]:
    """Auto-discover relevant subreddits for a keyword.

    Tries Reddit's mobile OAuth subreddit search first, then falls back
    to Arctic Shift's archive search. Returns subreddit names that can
    be passed as scope to the mobile and arctic connectors.
    """
    from social_scraper.connectors.reddit_discover import discover_subreddits

    loop = asyncio.get_event_loop()
    try:
        subs = await loop.run_in_executor(None, discover_subreddits, keyword)
        if subs:
            return subs[:5]
    except Exception as exc:
        logger.debug("Subreddit discovery failed for %r: %s", keyword, exc)

    # Fallback: use BOUNTY_REDDIT_SUBREDDITS from env if discovery failed
    import os
    configured = [
        s.strip() for s in os.getenv("BOUNTY_REDDIT_SUBREDDITS", "").split(",")
        if s.strip()
    ]
    return configured[:5] if configured else []


async def make_root_probe_handler(
    broker,
    plan: Mapping[str, Any] | None = None,
):
    """Return an async handler for the root_probe stage.

    Searches for the candidate keyword across platforms and collects root posts.
    No thread reading or LLM calls — that's deep_read and horizontal_extraction.
    """

    async def root_probe(candidate: dict, context: dict | None = None) -> StageHandlerResult:
        keyword = _keyword_from_candidate(candidate)
        if not keyword:
            return StageHandlerResult(status="skipped", error_category="missing_keyword")
        platforms = _platforms_from_candidate(candidate)
        caps = _budget_from_context(context, plan)

        # Auto-discover subreddits for Reddit if none specified
        reddit_options = {}
        if "reddit" in platforms:
            subs = await _ensure_reddit_subreddits(keyword)
            if subs:
                reddit_options = {"subreddits": subs}

        try:
            result = await broker.search(
                keyword=keyword,
                platforms=platforms,
                count=caps.get("root_count", 10),
                platform_options={"reddit": reddit_options} if reddit_options else None,
            )
        except Exception as exc:
            logger.warning("root_probe failed for %r: %s", keyword, exc)
            return StageHandlerResult(
                status="failed", error_category="search_error",
                external_calls=len(platforms),
            )
        items = result.get("items", [])
        source_health = result.get("source_health", [])
        platform_results = result.get("platform_results", {})
        external_calls = len(platforms)
        failed = all(
            str(pr.get("status", "")).lower() in {"error", "failed", "skipped"}
            for pr in platform_results.values()
        ) if platform_results else False
        return StageHandlerResult(
            records_returned=len(items),
            external_calls=external_calls,
            cache_hit=False,
            status="failed" if failed else ("empty" if not items else "complete"),
            error_category="all_sources_failed" if failed else None,
            candidates=[{**candidate, "_root_items": items, "_source_health": source_health}],
        )

    return root_probe


async def make_deep_read_handler(
    broker,
    plan: Mapping[str, Any] | None = None,
    collected: dict[str, list[dict]] | None = None,
):
    """Return an async handler for the deep_read stage.

    Opens top threads from the root probe and reads comments/replies.
    Requires the collected dict to pass root items from root_probe.
    """

    async def handler(candidate: dict, context: dict | None = None) -> StageHandlerResult:
        keyword = _keyword_from_candidate(candidate)
        if not keyword:
            return StageHandlerResult(status="skipped", error_category="missing_keyword")
        caps = _budget_from_context(context, plan)
        platforms = _platforms_from_candidate(candidate)

        # Root items should have been stashed by root_probe
        cid = candidate.get("candidate_id", keyword)
        root_items = []
        if collected and cid in collected:
            root_items = collected[cid]
        else:
            # Fallback: re-search if root items aren't available
            try:
                result = await broker.search(
                    keyword=keyword, platforms=platforms, count=5,
                )
                root_items = result.get("items", [])
            except Exception as exc:
                logger.warning("deep_read fallback search failed for %r: %s", keyword, exc)
                return StageHandlerResult(
                    status="failed", error_category="search_error",
                    external_calls=len(platforms),
                )

        if not root_items:
            return StageHandlerResult(status="empty", error_category="no_root_items")

        # Read threads from top-ranked root items
        max_threads = caps["threads_per_platform"]
        max_comments = caps["comments_per_thread"]
        max_depth = caps["max_thread_depth"]

        all_thread_records: list[dict] = []
        external_calls = 0
        for platform in platforms:
            platform_items = [r for r in root_items if str(r.get("platform", "")).lower() == platform]
            ranked = sorted(
                platform_items,
                key=lambda r: (
                    (r.get("engagement") or {}).get("comments") or 0,
                    (r.get("engagement") or {}).get("likes") or 0,
                ),
                reverse=True,
            )[:max_threads]
            if not ranked:
                continue
            for item in ranked:
                if not hasattr(broker, "fetch_thread"):
                    continue
                external_calls += 1
                try:
                    thread_result = await broker.fetch_thread(
                        item, max_comments=max_comments, max_depth=max_depth,
                    )
                    for record in thread_result.records:
                        all_thread_records.append({
                            "platform": record.platform,
                            "post_id": record.external_id,
                            "external_id": record.external_id,
                            "record_type": record.record_type,
                            "object_type": "comment" if record.record_type != "root" else "post",
                            "parent_external_id": record.parent_external_id,
                            "root_post_external_id": record.root_post_external_id,
                            "depth": record.depth,
                            "url": record.url,
                            "author": {
                                "id": record.author_external_id,
                                "username": record.author_username,
                            },
                            "author_id": record.author_external_id,
                            "author_username": record.author_username,
                            "text": record.text,
                            "title": "",
                            "published_at": record.published_at,
                            "created_at": record.published_at,
                            "engagement": {"likes": record.likes},
                            "provenance": {
                                "connector": thread_result.attempted_route,
                                "query": keyword,
                            },
                        })
                except Exception as exc:
                    logger.debug("thread read failed for %r on %s: %s", keyword, platform, exc)

        # Combine root items + thread records as the evidence pool
        combined = list(root_items) + all_thread_records
        if collected is not None:
            collected[cid + ":deep"] = combined
        return StageHandlerResult(
            records_returned=len(all_thread_records),
            external_calls=external_calls,
            cache_hit=False,
            status="empty" if not all_thread_records else "complete",
            candidates=[{**candidate, "_deep_records": combined}],
        )

    return handler


def make_horizontal_extraction_handler(
    plan: Mapping[str, Any] | None = None,
    collected: dict[str, list[dict]] | None = None,
    llm_call_fn: Callable[[str, str], Awaitable[str]] | None = None,
):
    """Return an async handler for the horizontal_extraction stage.

    Calls analyze_conversation with the collected evidence to produce
    citation-backed signals, entities, coverage, and limitations.
    """

    async def handler(candidate: dict, context: dict | None = None) -> StageHandlerResult:
        from .triage import analyze_conversation, prepare_conversation_prompt

        keyword = _keyword_from_candidate(candidate)
        if not keyword:
            return StageHandlerResult(status="skipped", error_category="missing_keyword")

        cid = candidate.get("candidate_id", keyword)
        # Prefer deep-read records; fall back to root items
        posts: list[dict] = []
        if collected and cid + ":deep" in collected:
            posts = collected[cid + ":deep"]
        elif collected and cid in collected:
            posts = collected[cid]

        if not posts:
            return StageHandlerResult(status="empty", error_category="no_evidence")

        # Measure the exact prompt analyze_conversation transmits: the same
        # deterministic preparation (dedup, per-platform cap, truncation), not
        # the raw collected posts and not an estimate.
        prompt_receipt = prepare_conversation_prompt(keyword, posts)

        source_health: list[dict] = []
        if collected and cid + ":health" in collected:
            source_health = collected[cid + ":health"]

        try:
            analysis = await analyze_conversation(
                topic=keyword,
                posts=posts,
                source_health=source_health,
                llm_call_fn=llm_call_fn,
            )
        except Exception as exc:
            logger.warning("horizontal extraction failed for %r: %s", keyword, exc)
            return StageHandlerResult(
                status="failed", error_category="llm_error",
                llm_calls=1,
            )

        result = analysis.to_dict()
        # Stash findings for persistence
        if collected is not None:
            collected[cid + ":findings"] = result

        return StageHandlerResult(
            records_returned=len(result.get("evidence", [])),
            external_calls=0,
            llm_calls=1,
            cache_hit=False,
            input_records=prompt_receipt.input_records,
            input_characters=prompt_receipt.input_characters,
            status="complete" if result.get("status") == "supported" else (
                "empty" if result.get("status") in {"insufficient_evidence", "sources_unavailable"} else "complete"
            ),
            candidates=[{**candidate, "_findings": result}],
        )

    return handler


def make_optional_enrichment_handler(
    plan: Mapping[str, Any] | None = None,
    collected: dict[str, list[dict]] | None = None,
    llm_call_fn: Callable[[str, str], Awaitable[str]] | None = None,
):
    """Return an async handler for optional_enrichment stage.

    Placeholder for future lens-specific custom extraction. Currently a no-op
    that preserves the collected findings without additional LLM calls.
    """

    async def handler(candidate: dict, context: dict | None = None) -> StageHandlerResult:
        return StageHandlerResult(status="complete")

    return handler


def build_handlers(
    broker,
    plan: Mapping[str, Any] | None = None,
    llm_call_fn: Callable[[str, str], Awaitable[str]] | None = None,
) -> tuple[dict[str, Any], dict[str, list[dict]]]:
    """Build all four stage handlers and a shared collection dict.

    Returns (handlers_dict, collected_dict). The collected_dict is shared
    mutable state that passes evidence between stages within one run.
    """
    collected: dict[str, list[dict]] = {}

    async def root_probe(candidate: dict, context: dict | None = None) -> StageHandlerResult:
        handler = await make_root_probe_handler(broker, plan)
        result = await handler(candidate, context)
        # Transfer root items into the shared collection
        if result.candidates:
            cid = candidate.get("candidate_id", _keyword_from_candidate(candidate))
            root_items = result.candidates[0].get("_root_items", [])
            health = result.candidates[0].get("_source_health", [])
            collected[cid] = root_items
            collected[cid + ":health"] = health
        return result

    async def deep_read(candidate: dict, context: dict | None = None) -> StageHandlerResult:
        handler = await make_deep_read_handler(broker, plan, collected)
        result = await handler(candidate, context)
        return result

    horizontal_extraction = make_horizontal_extraction_handler(plan, collected, llm_call_fn)
    optional_enrichment = make_optional_enrichment_handler(plan, collected, llm_call_fn)

    return {
        "root_probe": root_probe,
        "deep_read": deep_read,
        "horizontal_extraction": horizontal_extraction,
        "optional_enrichment": optional_enrichment,
    }, collected
