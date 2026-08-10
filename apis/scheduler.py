"""
Scheduler for periodic zone collection.

Runs as a background asyncio task inside the FastAPI app.
Checks every 30 minutes for zones that are due, then collects + clusters + enriches.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 1800  # 30 minutes
_SCHEDULER_TASK = None


async def _scheduler_loop():
    """Background loop that collects due zones."""
    from apis.social_search_api import build_default_broker
    from apis.dashboard_api import _get_registry
    from social_scraper.monitoring import TrendMonitor
    from social_scraper.enrichment import EnrichmentEngine

    registry = _get_registry()
    broker = build_default_broker(route_timeout_seconds=240.0)
    monitor = TrendMonitor(registry, broker)
    engine = EnrichmentEngine(llm_call_fn=_llm_call)

    logger.info("Trend monitoring scheduler started")

    while True:
        try:
            due_zones = registry.list_due()
            if due_zones:
                logger.info(f"Scheduler: {len(due_zones)} zones due for collection")

            for zone in due_zones:
                try:
                    logger.info(f"Scheduler: collecting zone '{zone.name}'")
                    report = await monitor.run_zone(zone.name)

                    # Enrich sample posts
                    if engine and report.top_clusters:
                        all_posts = []
                        for cluster in report.top_clusters:
                            all_posts.extend(cluster.get("sample_posts", []))
                        if all_posts:
                            try:
                                enriched = await engine.enrich_posts(all_posts)
                                logger.info(f"Scheduler: enriched {enriched.success_count} posts for zone '{zone.name}'")
                            except Exception as e:
                                logger.warning(f"Scheduler: enrichment failed for '{zone.name}': {e}")

                    logger.info(
                        f"Scheduler: zone '{zone.name}' done — "
                        f"{report.total_items} items, {report.cluster_count} clusters, "
                        f"{len(report.alerts)} alerts"
                    )
                except Exception as e:
                    logger.error(f"Scheduler: zone '{zone.name}' failed: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Scheduler loop error: {e}", exc_info=True)

        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)


async def _llm_call(system_prompt: str, user_prompt: str) -> str:
    """Call the shared, provider-switchable Bounty LLM client."""
    from social_scraper.llm_client import call_llm

    return await call_llm(system_prompt, user_prompt, max_tokens=4000)


def start_scheduler():
    """Start the scheduler as a background task. Call once on app startup."""
    global _SCHEDULER_TASK
    if _SCHEDULER_TASK is None or _SCHEDULER_TASK.done():
        _SCHEDULER_TASK = asyncio.create_task(_scheduler_loop())
        logger.info("Scheduler task created")
    return _SCHEDULER_TASK


def stop_scheduler():
    """Stop the scheduler."""
    global _SCHEDULER_TASK
    if _SCHEDULER_TASK and not _SCHEDULER_TASK.done():
        _SCHEDULER_TASK.cancel()
        logger.info("Scheduler task cancelled")
