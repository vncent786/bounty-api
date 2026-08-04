import asyncio
from datetime import datetime, timedelta, timezone

from social_scraper.base import BaseConnector, ConnectorResult, SocialItem, SourceHealth
from social_scraper.collection import CollectionService
from social_scraper.storage import ObservationStore


class StaticConnector(BaseConnector):
    platform = "reddit"
    connector_name = "static"

    async def search(self, keyword, count=20, time_filter="", sort="", region=""):
        item = SocialItem(
            platform="reddit",
            post_id="abc",
            url="https://reddit.com/r/test/abc",
            text=keyword,
            likes=12,
        )
        return ConnectorResult(
            items=[item],
            health=SourceHealth(
                platform="reddit",
                connector=self.connector_name,
                status="ok",
                items_returned=1,
                items_requested=count,
            ),
        )

    async def health_check(self):
        return SourceHealth(platform="reddit", connector=self.connector_name, status="ok")


def test_store_keeps_repeated_observations_instead_of_overwriting_history(tmp_path):
    store = ObservationStore(tmp_path / "social.db")
    response = {
        "query": "running shoes",
        "items": [{
            "platform": "reddit",
            "post_id": "abc",
            "url": "https://reddit.com/abc",
            "engagement": {"likes": 10, "comments": 2, "views": None, "shares": None},
            "provenance": {"connector": "oauth", "fetched_at": "2026-08-02T01:00:00+00:00"},
        }],
        "source_health": [],
    }

    first_run = store.record_collection(response, platforms=["reddit"], region="US")
    response["items"][0]["engagement"]["likes"] = 15
    response["items"][0]["provenance"]["fetched_at"] = "2026-08-02T02:00:00+00:00"
    second_run = store.record_collection(response, platforms=["reddit"], region="US")

    assert first_run != second_run
    history = store.get_observation_history("reddit", "abc")
    assert [row["likes"] for row in history] == [10, 15]
    assert store.get_collection_run(first_run)["raw_response"]["query"] == "running shoes"


def test_latest_collection_uses_exact_query_platforms_region_and_age(tmp_path):
    store = ObservationStore(tmp_path / "social.db")
    old_time = datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc)
    new_time = datetime(2026, 8, 2, 2, 0, tzinfo=timezone.utc)
    old_response = {"query": "python", "items": [{"post_id": "old"}]}
    new_response = {"query": "python", "items": [{"post_id": "new"}]}
    store.record_collection(old_response, ["reddit"], "US", collected_at=old_time)
    newest_id = store.record_collection(new_response, ["reddit"], "US", collected_at=new_time)
    store.record_collection(
        {"query": "python", "items": [{"post_id": "sg"}]},
        ["reddit"],
        "SG",
        collected_at=new_time,
    )

    latest = store.get_latest_collection(
        "python",
        ["reddit"],
        "US",
        max_age_minutes=90,
        now=datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc),
    )

    assert latest["id"] == newest_id
    assert latest["raw_response"]["items"][0]["post_id"] == "new"
    assert latest["age_seconds"] == 3600
    assert store.get_latest_collection(
        "python",
        ["reddit"],
        "US",
        max_age_minutes=30,
        now=datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc),
    ) is None

    store.record_collection(
        {"query": "future", "items": []},
        ["reddit"],
        "US",
        collected_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    assert store.get_latest_collection(
        "future",
        ["reddit"],
        "US",
        max_age_minutes=60,
        now=datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc),
    ) is None


def test_query_registry_and_cache_keys_include_platform_scope(tmp_path):
    store = ObservationStore(tmp_path / "scopes.db")
    now = datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc)
    python_scope = {"reddit": {"subreddits": ["Python"]}}
    marketing_scope = {"reddit": {"subreddits": ["Marketing"]}}

    python_id = store.upsert_query(
        "pricing", ["reddit"], "US", 60, now, platform_options=python_scope,
    )
    marketing_id = store.upsert_query(
        "pricing", ["reddit"], "US", 60, now, platform_options=marketing_scope,
    )
    assert python_id != marketing_id
    assert store.get_query(python_id)["platform_options"] == {
        "reddit": {"subreddits": ["python"]}
    }

    store.record_collection(
        {"query": "pricing", "items": [{"post_id": "python"}]},
        ["reddit"], "US", collected_at=now, platform_options=python_scope,
    )
    assert store.get_latest_collection(
        "pricing", ["reddit"], "US", platform_options=python_scope, now=now,
    )["raw_response"]["items"][0]["post_id"] == "python"
    assert store.get_latest_collection(
        "pricing", ["reddit"], "US", platform_options=marketing_scope, now=now,
    ) is None

    week_id = store.upsert_query(
        "global", ["reddit"], "US", 60, now,
        platform_options={"_search": {"time_filter": "week"}},
    )
    month_id = store.upsert_query(
        "global", ["reddit"], "US", 60, now,
        platform_options={"_search": {"time_filter": "month"}},
    )
    assert week_id != month_id


def test_existing_query_database_migrates_to_scope_aware_keys(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("""
            CREATE TABLE collection_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                platforms_json TEXT NOT NULL,
                region TEXT NOT NULL DEFAULT '',
                interval_minutes INTEGER NOT NULL,
                next_run_at TEXT NOT NULL,
                last_run_at TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                lease_token TEXT,
                lease_until TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(keyword, platforms_json, region)
            )
        """)
        connection.execute("""
            CREATE TABLE collection_runs (
                id TEXT PRIMARY KEY, query TEXT NOT NULL,
                platforms_json TEXT NOT NULL, region TEXT NOT NULL DEFAULT '',
                collected_at TEXT NOT NULL, raw_response_json TEXT NOT NULL
            )
        """)
        connection.execute("""
            INSERT INTO collection_queries (
                keyword, platforms_json, region, interval_minutes, next_run_at,
                enabled, created_at, updated_at
            ) VALUES ('pricing', '[\"reddit\"]', 'US', 60,
                      '2026-08-02T01:00:00+00:00', 1,
                      '2026-08-02T00:00:00+00:00', '2026-08-02T00:00:00+00:00')
        """)

    store = ObservationStore(path)
    original = store.get_query(1)
    scoped_id = store.upsert_query(
        "pricing", ["reddit"], "US", 60,
        datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc),
        platform_options={"reddit": {"subreddits": ["Marketing"]}},
    )

    assert original["platform_options"] == {}
    assert scoped_id != original["id"]


def test_scope_migration_repairs_options_column_with_stale_unique_constraint(tmp_path):
    import sqlite3

    path = tmp_path / "partial-migration.db"
    with sqlite3.connect(path) as connection:
        connection.execute("""
            CREATE TABLE collection_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                platforms_json TEXT NOT NULL,
                options_json TEXT NOT NULL DEFAULT '{}',
                region TEXT NOT NULL DEFAULT '',
                interval_minutes INTEGER NOT NULL,
                next_run_at TEXT NOT NULL,
                last_run_at TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                lease_token TEXT,
                lease_until TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(keyword, platforms_json, region)
            )
        """)
        connection.execute("""
            CREATE TABLE collection_runs (
                id TEXT PRIMARY KEY, query TEXT NOT NULL,
                platforms_json TEXT NOT NULL, options_json TEXT NOT NULL DEFAULT '{}',
                region TEXT NOT NULL DEFAULT '', collected_at TEXT NOT NULL,
                raw_response_json TEXT NOT NULL
            )
        """)

    store = ObservationStore(path)
    first = store.upsert_query(
        "pricing", ["reddit"], "US", 60,
        datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc),
        platform_options={"reddit": {"subreddits": ["Python"]}},
    )
    second = store.upsert_query(
        "pricing", ["reddit"], "US", 60,
        datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc),
        platform_options={"reddit": {"subreddits": ["Marketing"]}},
    )

    assert first != second


def test_query_registry_returns_only_due_enabled_queries(tmp_path):
    store = ObservationStore(tmp_path / "social.db")
    now = datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc)
    due_id = store.upsert_query("shoes", ["reddit"], "US", interval_minutes=60, next_run_at=now - timedelta(minutes=1))
    store.upsert_query("bags", ["reddit"], "US", interval_minutes=60, next_run_at=now + timedelta(minutes=1))
    disabled_id = store.upsert_query("hats", ["reddit"], "US", interval_minutes=60, next_run_at=now - timedelta(minutes=1), enabled=False)

    due = store.list_due_queries(now=now)

    assert [row["id"] for row in due] == [due_id]
    assert disabled_id not in [row["id"] for row in due]


def test_collection_service_persists_run_and_advances_query_schedule(tmp_path):
    from social_scraper.broker import SourceBroker

    store = ObservationStore(tmp_path / "social.db")
    broker = SourceBroker()
    broker.register(StaticConnector())
    query_id = store.upsert_query(
        "running shoes", ["reddit"], "US", interval_minutes=60,
        next_run_at=datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc),
    )
    service = CollectionService(broker, store)

    result = asyncio.run(service.collect_query(query_id, now=datetime(2026, 8, 2, 2, 0, tzinfo=timezone.utc)))

    assert result["count"] == 1
    assert result["query_id"] == query_id
    assert result["collection_status"] == "completed"
    assert result["collection_run_id"]
    saved = store.get_query(query_id)
    assert saved["last_run_at"] == "2026-08-02T02:00:00+00:00"
    assert saved["next_run_at"] == "2026-08-02T03:00:00+00:00"


def test_collection_marks_persisted_platform_outage_as_error(tmp_path):
    class ErrorBroker:
        async def search(self, **kwargs):
            assert kwargs["time_filter"] == "week"
            assert kwargs["platform_options"] == {
                "reddit": {"subreddits": ["stocks"]}
            }
            return {
                "query": kwargs["keyword"],
                "platforms": kwargs["platforms"],
                "region": kwargs.get("region"),
                "platform_options": kwargs.get("platform_options", {}),
                "count": 0,
                "items": [],
                "source_health": [{
                    "platform": "reddit", "connector": "arctic_shift_scoped",
                    "status": "error", "items_returned": 0,
                }],
                "platform_results": {"reddit": {
                    "status": "error", "selected_connector": None,
                    "attempted_connectors": ["arctic_shift_scoped"],
                }},
            }

    store = ObservationStore(tmp_path / "outage.db")
    now = datetime(2026, 8, 2, 2, 0, tzinfo=timezone.utc)
    query_id = store.upsert_query(
        "earnings", ["reddit"], "", 60, now,
        platform_options={
            "reddit": {"subreddits": ["stocks"]},
            "_search": {"time_filter": "week"},
        },
    )

    result = asyncio.run(CollectionService(ErrorBroker(), store).collect_query(query_id, now=now))

    assert result["collection_status"] == "error"
    assert result["collection_run_id"]


def test_query_claim_prevents_a_second_worker_from_collecting_same_query(tmp_path):
    store = ObservationStore(tmp_path / "social.db")
    now = datetime(2026, 8, 2, 2, 0, tzinfo=timezone.utc)
    query_id = store.upsert_query(
        "running shoes", ["reddit"], "US", interval_minutes=60,
        next_run_at=now - timedelta(minutes=1),
    )

    first_token = store.claim_query(query_id, now=now)

    assert first_token
    assert store.claim_query(query_id, now=now) is None


def test_claim_due_queries_is_atomic_and_excludes_claimed_rows(tmp_path):
    store = ObservationStore(tmp_path / "social.db")
    now = datetime(2026, 8, 2, 2, 0, tzinfo=timezone.utc)
    store.upsert_query(
        "running shoes", ["reddit"], "US", interval_minutes=60,
        next_run_at=now - timedelta(minutes=1),
    )

    first_batch = store.claim_due_queries(now=now)
    second_batch = store.claim_due_queries(now=now)

    assert len(first_batch) == 1
    assert second_batch == []


class FailFirstCompletionStore(ObservationStore):
    def __init__(self, path):
        super().__init__(path)
        self.completions = 0

    def complete_claimed_collection(self, *args, **kwargs):
        self.completions += 1
        if self.completions == 1:
            raise RuntimeError("simulated storage failure")
        return super().complete_claimed_collection(*args, **kwargs)


def test_collect_due_continues_after_one_query_fails_and_releases_claim(tmp_path):
    from social_scraper.broker import SourceBroker

    store = FailFirstCompletionStore(tmp_path / "social.db")
    broker = SourceBroker()
    broker.register(StaticConnector())
    service = CollectionService(broker, store)
    now = datetime(2026, 8, 2, 2, 0, tzinfo=timezone.utc)
    first_id = store.upsert_query("first", ["reddit"], "US", 60, now - timedelta(minutes=1))
    second_id = store.upsert_query("second", ["reddit"], "US", 60, now - timedelta(minutes=1))

    results = asyncio.run(service.collect_due(now=now))

    assert results[0] == {"query_id": first_id, "collection_status": "error"}
    assert results[1]["collection_run_id"]
    assert store.claim_query(first_id, now=now)
    assert store.get_query(second_id)["last_run_at"] == "2026-08-02T02:00:00+00:00"


class MutableClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class ClaimTimeRecordingStore(ObservationStore):
    def __init__(self, path, clock):
        super().__init__(path)
        self.clock = clock
        self.claim_times = []
        self.completed = 0

    def claim_query(self, query_id, now=None, lease_minutes=10):
        self.claim_times.append(now)
        return super().claim_query(query_id, now=now, lease_minutes=lease_minutes)

    def complete_claimed_collection(self, *args, **kwargs):
        run_id = super().complete_claimed_collection(*args, **kwargs)
        self.completed += 1
        if self.completed == 1:
            self.clock.value += timedelta(minutes=20)
        return run_id


def test_each_due_query_uses_fresh_wall_clock_time_for_its_lease(tmp_path):
    from social_scraper.broker import SourceBroker

    batch_time = datetime(2026, 8, 2, 2, 0, tzinfo=timezone.utc)
    clock = MutableClock(batch_time)
    store = ClaimTimeRecordingStore(tmp_path / "social.db", clock)
    broker = SourceBroker()
    broker.register(StaticConnector())
    service = CollectionService(broker, store, clock=clock)
    store.upsert_query("first", ["reddit"], "US", 60, batch_time - timedelta(minutes=1))
    store.upsert_query("second", ["reddit"], "US", 60, batch_time - timedelta(minutes=1))

    asyncio.run(service.collect_due(now=batch_time))

    assert store.claim_times == [batch_time, batch_time + timedelta(minutes=20)]
