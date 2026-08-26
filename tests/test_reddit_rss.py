import asyncio
from datetime import datetime, timezone

from social_scraper.broker import SourceBroker
from social_scraper.connectors.reddit_rss import RedditRSSConnector, parse_reddit_atom
from social_scraper.storage import ObservationStore


ATOM = b'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <author><name>/u/alice</name><uri>https://www.reddit.com/user/alice</uri></author>
    <category term="stocks" label="r/stocks" />
    <content type="html">&lt;p&gt;Quarterly results and guidance.&lt;/p&gt;</content>
    <id>t3_abc123</id>
    <link href="https://www.reddit.com/r/stocks/comments/abc123/earnings_revision/" />
    <published>2026-08-03T04:00:00+00:00</published>
    <updated>2026-08-03T04:01:00+00:00</updated>
    <title>Earnings revision accelerates</title>
  </entry>
  <entry>
    <category term="investing" label="r/investing" />
    <id>t3_def456</id>
    <link href="https://www.reddit.com/r/investing/comments/def456/earnings_outlook/" />
    <updated>2026-08-03T04:02:00+00:00</updated>
    <title>Earnings outlook discussion</title>
  </entry>
  <entry>
    <category term="notallowed" label="r/notallowed" />
    <id>t3_bad999</id>
    <link href="https://www.reddit.com/r/notallowed/comments/bad999/earnings/" />
    <updated>2026-08-03T04:03:00+00:00</updated>
    <title>Earnings outside scope</title>
  </entry>
</feed>'''


def test_atom_parser_preserves_canonical_identity_without_fabricating_metrics():
    items = parse_reddit_atom(ATOM, ["stocks", "investing"], keyword="earnings", count=10)

    assert [item.post_id for item in items] == ["abc123", "def456"]
    assert items[0].url == "https://www.reddit.com/r/stocks/comments/abc123/earnings_revision/"
    assert items[0].created_at == "2026-08-03T04:00:00+00:00"
    assert items[0].likes is None
    assert items[0].comments is None
    assert items[0].raw["source_kind"] == "feed"
    assert items[0].raw["source_updated_at"] == "2026-08-03T04:01:00+00:00"
    assert items[1].created_at is None
    assert items[1].raw["source_timestamp_kind"] == "atom_updated_only"


def test_atom_global_search_accepts_any_real_subreddit_without_fabricating_scope():
    urls = []

    def fake_fetch(url):
        urls.append(url)
        return ATOM

    connector = RedditRSSConnector(
        fetch_feed=fake_fetch,
        clock=lambda: datetime(2026, 8, 10, 4, 0, tzinfo=timezone.utc),
    )
    result = asyncio.run(connector.search(
        "earnings", count=10, time_filter="month", sort="latest"
    ))

    assert len(result.items) == 3
    assert urls[0].startswith("https://www.reddit.com/search.rss?")
    assert "q=earnings" in urls[0]
    assert "sort=new" in urls[0]
    assert "t=month" in urls[0]
    assert result.health.status == "ok"
    assert result.health.coverage["kind"] == "global_atom_search"
    assert result.health.coverage["global_query"] is True
    assert result.health.coverage["global_coverage"] is False


def test_atom_connector_uses_one_combined_scoped_request_and_reports_window_limits():
    urls = []

    def fake_fetch(url):
        urls.append(url)
        return ATOM

    connector = RedditRSSConnector(fetch_feed=fake_fetch)
    result = asyncio.run(connector.search_with_options(
        "earnings",
        count=10,
        options={"subreddits": ["stocks", "investing"]},
    ))

    assert urls == ["https://www.reddit.com/r/stocks+investing/new/.rss"]
    assert result.health.status == "ok"
    assert result.health.coverage == {
        "kind": "combined_atom_new_feed",
        "requested_subreddits": ["stocks", "investing"],
        "observed_subreddits": ["investing", "stocks"],
        "global_coverage": False,
        "window_limited": True,
        "engagement_available": False,
        "source_kind": "feed",
    }


def test_atom_observation_is_immutable_and_uses_collection_time_not_feed_update(tmp_path):
    connector = RedditRSSConnector(fetch_feed=lambda _url: ATOM)
    broker = SourceBroker()
    broker.register(connector)
    response = asyncio.run(broker.search(
        "earnings",
        platforms=["reddit"],
        count=2,
        platform_options={"reddit": {"subreddits": ["stocks", "investing"]}},
        include_source_records=True,
    ))

    assert response["items"][0]["provenance"]["source_updated_at"] == "2026-08-03T04:01:00+00:00"
    assert response["items"][0]["provenance"]["source_timestamp_kind"] == "atom_published"
    assert response["items"][0]["engagement"]["likes"] is None
    assert response["items"][0]["engagement"]["comments"] is None

    store = ObservationStore(tmp_path / "rss.db")
    run_id = store.record_collection(
        response,
        ["reddit"],
        platform_options={"reddit": {"subreddits": ["stocks", "investing"]}},
    )
    saved = store.get_collection_run(run_id)
    history = store.get_observation_history("reddit", "abc123")
    source_records = store.get_source_records(run_id)

    assert saved["raw_response"]["items"][0]["post_id"] == "abc123"
    assert source_records[0]["payload_format"] == "bytes_base64"
    assert source_records[0]["hash_valid"] is True
    assert history[0]["connector"] == "reddit_atom_scoped"
    assert history[0]["likes"] is None
    assert history[0]["comments"] is None
    assert history[0]["observed_at"] != "2026-08-03T04:01:00+00:00"


def test_atom_parser_rejects_mismatched_id_and_scope():
    payload = ATOM.replace(b"t3_abc123", b"t3_wrong1", 1)
    items = parse_reddit_atom(payload, ["stocks"], keyword="earnings", count=10)
    assert items == []


def test_atom_connector_enforces_requested_time_window():
    connector = RedditRSSConnector(
        fetch_feed=lambda _url: ATOM,
        clock=lambda: datetime(2026, 8, 10, 4, 0, tzinfo=timezone.utc),
    )
    result = asyncio.run(connector.search_with_options(
        "earnings",
        time_filter="1day",
        options={"subreddits": ["stocks", "investing"]},
    ))
    assert result.items == []
    assert result.health.status == "partial"
