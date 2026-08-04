"""Run one atomic batch of due social collection queries.

Designed for a platform scheduler or cron invocation. The process exits after
one batch; overlapping invocations remain safe because query leases live in the
database.
"""

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apis.social_search_api import build_collection_broker, default_store  # noqa: E402
from social_scraper.collection import CollectionService  # noqa: E402


async def run_due_collections():
    service = CollectionService(build_collection_broker(), default_store())
    results = await service.collect_due()
    summary = []
    failed = 0
    for result in results:
        platform_results = result.get("platform_results", {}) or {}
        route_errors = sorted(
            platform
            for platform, route in platform_results.items()
            if route.get("status") == "error"
        )
        collection_failed = result.get("collection_status") == "error"
        if collection_failed or route_errors:
            failed += 1
        summary.append({
            "query_id": result.get("query_id"),
            "collection_status": result.get("collection_status", "unknown"),
            "collection_run_id": result.get("collection_run_id"),
            "items": result.get("count", 0),
            "route_errors": route_errors,
        })
    output = {
        "queries_processed": len(results),
        "queries_with_errors": failed,
        "collections": summary,
    }
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_due_collections()))
