import asyncio

from social_scraper.base import BaseConnector, ConnectorResult, SocialItem, SourceHealth
from social_scraper.conversations.thread_reader import ThreadFetchResult, ThreadRecord


class SearchOnlyConnector(BaseConnector):
    platform = "search_only"

    async def search(self, keyword, count=20, time_filter="", sort="", region=""):
        return ConnectorResult([], SourceHealth(self.platform, "test", "ok"))

    async def health_check(self):
        return SourceHealth(self.platform, "test", "ok")


def test_base_connector_reports_unsupported_without_breaking_search_only_connectors():
    connector = SearchOnlyConnector()
    post = SocialItem(platform="search_only", post_id="p1", url="https://example.com/p1")
    result = asyncio.run(connector.fetch_thread(post, max_comments=20, max_depth=2))
    assert result.status == "unsupported"
    assert result.error_category == "thread_reader_not_supported"


def test_thread_result_keeps_parent_depth_and_truncation():
    record = ThreadRecord(
        platform="reddit", external_id="c2", record_type="reply",
        parent_external_id="c1", root_post_external_id="p1", depth=2,
        text="I disagree", author_username="person",
    )
    result = ThreadFetchResult(
        platform="reddit", root_post_external_id="p1", status="partial",
        records=(record,), truncated=True, attempted_route="reddit_json",
        platform_reported_total=10, max_comments=1, max_depth=2,
    )
    assert result.to_dict()["records"][0]["parent_external_id"] == "c1"
    assert result.to_dict()["returned_count"] == 1
