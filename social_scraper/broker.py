"""Source broker with ordered, health-visible connector failover."""

import asyncio
from collections import defaultdict
from dataclasses import dataclass

from social_scraper.base import BaseConnector, ConnectorResult, SocialItem, SourceHealth


@dataclass(order=True)
class ConnectorRoute:
    priority: int
    connector: BaseConnector


class SourceBroker:
    """Route each platform through an ordered pool of replaceable connectors."""

    def __init__(self, route_timeout_seconds: float = 30.0):
        self._routes: dict[str, list[ConnectorRoute]] = defaultdict(list)
        self.route_timeout_seconds = route_timeout_seconds

    def register(self, connector: BaseConnector, priority: int = 100):
        routes = self._routes[connector.platform]
        routes[:] = [route for route in routes if route.connector.connector_name != connector.connector_name]
        routes.append(ConnectorRoute(priority=priority, connector=connector))
        routes.sort(key=lambda route: route.priority)

    def list_platforms(self) -> list[str]:
        return list(self._routes.keys())

    def list_routes(self) -> dict[str, list[dict]]:
        return {
            platform: [
                {"connector": route.connector.connector_name, "priority": route.priority}
                for route in routes
            ]
            for platform, routes in self._routes.items()
        }

    @staticmethod
    def _thread_post(item: dict) -> SocialItem:
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        engagement = item.get("engagement") if isinstance(item.get("engagement"), dict) else {}
        return SocialItem(
            platform=str(item.get("platform") or ""),
            post_id=str(item.get("post_id") or item.get("external_id") or ""),
            url=str(item.get("url") or ""),
            author_username=str(author.get("username") or ""),
            author_display_name=str(author.get("display_name") or ""),
            text=str(item.get("text") or ""),
            created_at=item.get("created_at"),
            comments=engagement.get("comments") if isinstance(engagement.get("comments"), int) else None,
            raw={"provenance": item.get("provenance") or {}},
        )

    async def fetch_thread(
        self, item: dict, max_comments: int = 20, max_depth: int = 2
    ):
        """Hydrate one broker post using its selected route, then bounded failover."""
        post = self._thread_post(item)
        selected = (item.get("provenance") or {}).get("connector")
        routes = list(self._routes.get(post.platform, []))
        routes.sort(key=lambda route: (route.connector.connector_name != selected, route.priority))
        last_result = None
        for route in routes:
            try:
                result = await asyncio.wait_for(
                    route.connector.fetch_thread(post, max_comments, max_depth),
                    timeout=max(self.route_timeout_seconds, 90),
                )
            except asyncio.TimeoutError:
                continue
            except Exception:
                continue
            last_result = result
            if result.status in {"complete", "partial", "empty", "disabled"}:
                return result
        if last_result is not None:
            return last_result
        from social_scraper.conversations.thread_reader import unsupported_thread_result
        return unsupported_thread_result(
            post.platform, post.post_id, max_comments, max_depth
        )

    @staticmethod
    def _public_health(health: SourceHealth) -> dict:
        data = health.to_dict()
        if data.get("error"):
            safe_errors = {
                "connector_timeout",
                "arctic_shift_rate_limited",
                "arctic_shift_unavailable",
                "unsupported_sort",
                "unsupported_time_filter",
                "camoufox_verification_challenge",
                "camoufox_timeout",
                "camoufox_busy",
                "reddit_rss_rate_limited",
                "reddit_rss_unavailable",
                "reddit_mobile_rate_limited",
                "reddit_mobile_auth_failed",
                "reddit_mobile_unavailable",
                "reddit_mobile_not_installed",
                "x_rate_limited",
                "x_auth_expired",
                "x_forbidden",
                "x_error",
                "x_credentials_missing",
                "x_login_failed",
                "x_health_error",
                "x_full_archive_disabled",
                "x_partial_response",
                "x_page_cap_reached",
                "x_daily_budget_exhausted",
                "x_account_pool_unavailable",
                "x_transaction_id_failed",
                "ig_rate_limited",
                "ig_blocked",
                "ig_error",
                "ig_empty_tag",
            }
            data["error"] = data["error"] if data["error"] in safe_errors else "connector_error"
        return data

    async def _call_search(
        self, connector, platform, keyword, count, time_filter, sort, region, options
    ):
        if getattr(connector, "requires_options", False) and not options:
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform=platform,
                    connector=connector.connector_name,
                    status="skipped",
                    items_requested=count,
                ),
            )
        if options and not hasattr(connector, "search_with_options"):
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform=platform,
                    connector=connector.connector_name,
                    status="skipped",
                    items_requested=count,
                ),
            )
        if hasattr(connector, "can_handle_options") and not connector.can_handle_options(options):
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform=platform,
                    connector=connector.connector_name,
                    status="skipped",
                    items_requested=count,
                ),
            )
        try:
            operation = (
                connector.search_with_options(
                    keyword, count, time_filter, sort, region, options
                )
                if options else connector.search(keyword, count, time_filter, sort, region)
            )
            if getattr(connector, "manages_timeout", False):
                return await operation
            return await asyncio.wait_for(operation, timeout=self.route_timeout_seconds)
        except asyncio.TimeoutError:
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform=platform,
                    connector=connector.connector_name,
                    status="error",
                    items_requested=count,
                    error="connector_timeout",
                ),
            )
        except Exception:
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform=platform,
                    connector=connector.connector_name,
                    status="error",
                    items_requested=count,
                    error="connector_exception",
                ),
            )

    async def _search_platform(
        self, platform, keyword, count, time_filter, sort, region, options
    ):
        routes = self._routes.get(platform, [])
        if not routes:
            health = SourceHealth(
                platform=platform,
                connector="none",
                status="skipped",
                items_requested=count,
                error="No connector registered",
            )
            return {
                "platform": platform,
                "items": [],
                "health": [health],
                "selected_connector": None,
                "selected_health": None,
                "status": "skipped",
                "raw_records": [],
            }

        attempts = []
        attempted_raw_records = []
        partial_candidate = None
        for route in routes:
            connector = route.connector
            result = await self._call_search(
                connector, platform, keyword, count, time_filter, sort, region, options
            )
            attempts.append(result.health)
            for record in result.raw_records:
                attempted_raw_records.append({
                    **record,
                    "connector": connector.connector_name,
                    "fetched_at": record.get("fetched_at") or result.health.fetched_at,
                })
            if result.health.status == "ok" and result.items:
                return {
                    "platform": platform,
                    "items": result.items,
                    "health": attempts,
                    "selected_connector": connector.connector_name,
                    "selected_health": result.health,
                    "status": "ok",
                    "raw_records": attempted_raw_records,
                }
            if result.items and partial_candidate is None:
                partial_candidate = (result, connector.connector_name)

        if partial_candidate:
            result, connector_name = partial_candidate
            return {
                "platform": platform,
                "items": result.items,
                "health": attempts,
                "selected_connector": connector_name,
                "selected_health": result.health,
                "status": "partial",
                "raw_records": attempted_raw_records,
            }

        terminal_statuses = {health.status for health in attempts}
        no_usable_route = (
            "error" in terminal_statuses
            and terminal_statuses.issubset({"error", "skipped"})
        )
        return {
            "platform": platform,
            "items": [],
            "health": attempts,
            "selected_connector": None,
            "selected_health": None,
            "status": "error" if no_usable_route else "partial",
            "raw_records": attempted_raw_records,
        }

    async def search(
        self,
        keyword: str,
        platforms: list[str] = None,
        count: int = 20,
        time_filter: str = "",
        sort: str = "",
        region: str = "",
        platform_options: dict = None,
        include_source_records: bool = False,
    ) -> dict:
        """Search platforms concurrently and routes within each platform sequentially."""
        requested_platforms = platforms if platforms is not None else self.list_platforms()
        requested_platforms = list(dict.fromkeys(requested_platforms))
        platform_options = platform_options or {}
        tasks = [
            self._search_platform(
                platform,
                keyword,
                count,
                time_filter,
                sort,
                region,
                platform_options.get(platform, {}),
            )
            for platform in requested_platforms
        ]
        results = await asyncio.gather(*tasks) if tasks else []

        serialized_items = []
        source_health = []
        source_records = []
        platform_results = {}
        for result in results:
            attempted = [health.connector for health in result["health"]]
            platform_results[result["platform"]] = {
                "status": result["status"],
                "selected_connector": result["selected_connector"],
                "attempted_connectors": attempted,
                "coverage": (
                    result["selected_health"].coverage
                    if result["selected_health"] else {}
                ),
            }
            source_health.extend(self._public_health(health) for health in result["health"])
            selected_health = result["selected_health"]
            if include_source_records:
                for record in result.get("raw_records", []):
                    source_records.append({
                        "platform": result["platform"],
                        "connector": record.get("connector") or result["selected_connector"],
                        "source_id": str(record.get("source_id", "")),
                        "fetched_at": record.get("fetched_at") or (
                            selected_health.fetched_at if selected_health else None
                        ),
                        "payload_format": record.get("payload_format", "json"),
                        "payload": record.get("payload"),
                    })
            for item in result["items"]:
                serialized = item.to_dict()
                serialized["provenance"] = {
                    "connector": result["selected_connector"],
                    "fetched_at": selected_health.fetched_at if selected_health else None,
                    "region": region or None,
                    "query": keyword,
                }
                if item.raw.get("source_observed_at"):
                    serialized["provenance"]["source_observed_at"] = item.raw["source_observed_at"]
                if item.raw.get("source_kind"):
                    serialized["provenance"]["source_kind"] = item.raw["source_kind"]
                if item.raw.get("source_updated_at"):
                    serialized["provenance"]["source_updated_at"] = item.raw["source_updated_at"]
                if item.raw.get("source_timestamp_kind"):
                    serialized["provenance"]["source_timestamp_kind"] = item.raw["source_timestamp_kind"]
                if item.raw.get("subreddit"):
                    serialized["provenance"]["subreddit"] = item.raw["subreddit"]
                engagement_sources = item.raw.get("engagement_sources")
                if isinstance(engagement_sources, dict) and engagement_sources:
                    serialized["provenance"]["engagement_sources"] = dict(
                        engagement_sources
                    )
                serialized_items.append(serialized)

        def engagement_score(item):
            engagement = item.get("engagement", {})
            return (engagement.get("likes") or 0) + (engagement.get("comments") or 0)

        for platform, platform_result in platform_results.items():
            platform_items = [item for item in serialized_items if item.get("platform") == platform]
            created_values = sorted(
                item["created_at"] for item in platform_items if item.get("created_at")
            )
            observed_values = sorted(
                item.get("provenance", {}).get("source_observed_at")
                for item in platform_items
                if item.get("provenance", {}).get("source_observed_at")
            )
            platform_result["data_quality"] = {
                "items": len(platform_items),
                "created_at_present": len(created_values),
                "source_observed_at_present": len(observed_values),
                "newest_created_at": created_values[-1] if created_values else None,
                "newest_source_observed_at": observed_values[-1] if observed_values else None,
            }

        if sort == "latest":
            serialized_items.sort(
                key=lambda item: item.get("created_at") or "",
                reverse=True,
            )
        else:
            serialized_items.sort(key=engagement_score, reverse=True)
        response = {
            "query": keyword,
            "platforms": requested_platforms,
            "region": region or None,
            "platform_options": platform_options,
            "count": len(serialized_items),
            "items": serialized_items,
            "source_health": source_health,
            "platform_results": platform_results,
        }
        if include_source_records:
            response["_source_records"] = source_records
        return response

    async def health_check_all(self) -> list[dict]:
        """Probe every registered route with the same timeout and error sanitization."""
        routes = [route for platform_routes in self._routes.values() for route in platform_routes]
        results = await asyncio.gather(
            *(
                asyncio.wait_for(route.connector.health_check(), timeout=self.route_timeout_seconds)
                for route in routes
            ),
            return_exceptions=True,
        )
        output = []
        for route, result in zip(routes, results):
            if isinstance(result, asyncio.TimeoutError):
                output.append({
                    "platform": route.connector.platform,
                    "connector": route.connector.connector_name,
                    "status": "error",
                    "error": "connector_timeout",
                })
            elif isinstance(result, Exception):
                output.append({
                    "platform": route.connector.platform,
                    "connector": route.connector.connector_name,
                    "status": "error",
                    "error": "connector_error",
                })
            else:
                output.append(self._public_health(result))
        return output
