"""Collection orchestration connecting scheduled queries, broker, and storage."""

from datetime import datetime, timedelta, timezone


class CollectionService:
    def __init__(self, broker, store, clock=None):
        self.broker = broker
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def collect_query(self, query_id: int, now=None):
        query = self.store.get_query(query_id)
        if query is None:
            raise KeyError(f"Unknown collection query: {query_id}")
        if not query["enabled"]:
            raise ValueError(f"Collection query is disabled: {query_id}")

        collected_at = now or self.clock()
        token = self.store.claim_query(query_id, now=self.clock(), lease_minutes=30)
        if not token:
            raise RuntimeError(f"Collection query is already claimed: {query_id}")

        try:
            platform_options = query.get("platform_options", {})
            search_options = platform_options.get("_search", {})
            connector_options = {
                key: value for key, value in platform_options.items() if key != "_search"
            }
            response = await self.broker.search(
                keyword=query["keyword"],
                platforms=query["platforms"],
                region=query["region"],
                time_filter=search_options.get("time_filter", ""),
                sort=search_options.get("sort", ""),
                platform_options=connector_options,
                include_source_records=True,
            )
            next_run_at = collected_at + timedelta(minutes=query["interval_minutes"])
            run_id = self.store.complete_claimed_collection(
                query_id=query_id,
                claim_token=token,
                response=response,
                platforms=query["platforms"],
                region=query["region"],
                collected_at=collected_at,
                next_run_at=next_run_at,
                platform_options=query.get("platform_options", {}),
            )
        except Exception:
            self.store.release_claim(query_id, token)
            raise
        platform_errors = any(
            result.get("status") == "error"
            for result in response.get("platform_results", {}).values()
        )
        public_response = {
            key: value for key, value in response.items() if key != "_source_records"
        }
        return {
            **public_response,
            "query_id": query_id,
            "collection_run_id": run_id,
            "collection_status": "error" if platform_errors else "completed",
        }

    async def collect_due(self, now=None):
        collection_time = now or datetime.now(timezone.utc)
        due_queries = self.store.list_due_queries(collection_time)
        results = []
        for query in due_queries:
            try:
                results.append(await self.collect_query(query["id"], now=collection_time))
            except Exception:
                results.append({
                    "query_id": query["id"],
                    "collection_status": "error",
                })
        return results
