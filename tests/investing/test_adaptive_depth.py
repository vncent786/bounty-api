import sqlite3
from datetime import datetime, timedelta, timezone

from social_scraper.conversations.thread_reader import ThreadFetchResult, ThreadRecord
from social_scraper.investing.owned_radar import _normalise_item, _thread_evidence
from social_scraper.investing.private_radar import PrivateRadarStore


def test_root_normalization_sets_provenance_defaults():
    value = _normalise_item(
        {
            "platform": "reddit",
            "post_id": "root-1",
            "url": "https://reddit.com/r/test/root-1",
            "author": {"username": "person", "id": "author-1"},
            "subreddit": "BuyItForLife",
            "text": "I switched to a silicone air fryer liner",
        },
        panel_id="household_cleaning",
        window_key="current",
        query="silicone air fryer liner",
        query_lineage_id="lineage-1",
    )

    assert value["record_type"] == "root"
    assert value["parent_external_id"] is None
    assert value["root_post_external_id"] == "root-1"
    assert value["thread_depth"] == 0
    assert value["query_lineage_id"] == "lineage-1"
    assert value["community_id"] == "BuyItForLife"
    assert value["creator_id"] == "author-1"
    assert value["truncated"] is False


def test_stale_ranked_search_root_does_not_count_as_current_evidence():
    value = _normalise_item(
        {
            "platform": "tiktok",
            "post_id": "old-root",
            "url": "https://tiktok.com/old-root",
            "author": {"username": "person"},
            "text": "I switched to a silicone air fryer liner",
            "created_at": "2021-12-28T14:32:01Z",
        },
        panel_id="household_cleaning",
        window_key="current",
        query="silicone air fryer liner",
    )

    assert value is None


def test_current_discovery_keeps_90_days_and_rejects_older_rows():
    now = datetime.now(timezone.utc)

    def normalize(age_days):
        return _normalise_item(
            {
                "platform": "tiktok",
                "post_id": f"root-{age_days}",
                "url": f"https://tiktok.com/root-{age_days}",
                "author": {"username": "person"},
                "text": "I switched to a silicone air fryer liner",
                "created_at": (
                    now - timedelta(days=age_days)
                ).isoformat(),
            },
            panel_id="household_cleaning",
            window_key="current",
            query="silicone air fryer liner",
        )

    retained = normalize(89)
    rejected = normalize(91)

    assert retained is not None
    assert retained["recency_bucket"] == "last_90_days"
    assert retained["age_days"] == 89
    assert rejected is None


def test_thread_evidence_preserves_relationships_and_truncation():
    root = "root-1"
    comment = ThreadRecord(
        platform="tiktok",
        external_id="comment-1",
        record_type="comment",
        parent_external_id=root,
        root_post_external_id=root,
        depth=1,
        text="I bought one after seeing this",
        author_external_id="author-1",
        author_username="person",
        url="https://tiktok.com/comment-1",
        published_at="2026-08-30T00:00:00Z",
        likes=7,
    )
    reply = ThreadRecord(
        platform="tiktok",
        external_id="reply-1",
        record_type="reply",
        parent_external_id="comment-1",
        root_post_external_id=root,
        depth=2,
        text="Same here",
        author_external_id="author-2",
        author_username="other",
        url="https://tiktok.com/reply-1",
    )
    result = ThreadFetchResult(
        platform="tiktok",
        root_post_external_id=root,
        status="partial",
        records=(comment, reply),
        truncated=True,
        attempted_route="owned_comments",
        platform_reported_total=200,
        max_comments=20,
        max_depth=2,
        limitations=("bounded sample",),
    )

    comment_value = _thread_evidence(
        comment,
        panel_id="household_cleaning",
        query="silicone air fryer liner",
        query_lineage_id="lineage-1",
        thread_result=result,
    )
    reply_value = _thread_evidence(
        reply,
        panel_id="household_cleaning",
        query="silicone air fryer liner",
        query_lineage_id="lineage-1",
        thread_result=result,
    )

    assert comment_value["record_type"] == "comment"
    assert comment_value["parent_external_id"] == root
    assert comment_value["root_post_external_id"] == root
    assert comment_value["thread_depth"] == 1
    assert comment_value["creator_id"] == "author-1"
    assert comment_value["query_lineage_id"] == "lineage-1"
    assert comment_value["truncated"] is True
    assert reply_value["record_type"] == "reply"
    assert reply_value["parent_external_id"] == "comment-1"
    assert reply_value["thread_depth"] == 2


def test_private_radar_evidence_migration_keeps_legacy_rows_readable(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
        CREATE TABLE private_radar_scans (
          id TEXT PRIMARY KEY, started_at TEXT NOT NULL, heartbeat_at TEXT,
          completed_at TEXT, status TEXT NOT NULL, stage TEXT NOT NULL,
          progress INTEGER NOT NULL DEFAULT 0, panel_version TEXT NOT NULL,
          requested_panels_json TEXT NOT NULL, evidence_count INTEGER NOT NULL DEFAULT 0,
          candidate_count INTEGER NOT NULL DEFAULT 0, decisions_json TEXT NOT NULL DEFAULT '[]',
          limitations_json TEXT NOT NULL DEFAULT '[]', sources_json TEXT NOT NULL DEFAULT '[]',
          error_category TEXT
        );
        CREATE TABLE private_radar_evidence (
          run_id TEXT NOT NULL, id TEXT NOT NULL, panel_id TEXT NOT NULL,
          platform TEXT NOT NULL, external_id TEXT, url TEXT NOT NULL,
          author TEXT, text TEXT NOT NULL, created_at TEXT, observed_at TEXT NOT NULL,
          window_key TEXT, query TEXT, raw_json TEXT NOT NULL,
          PRIMARY KEY(run_id,id)
        );
        """)
        connection.execute(
            "INSERT INTO private_radar_scans (id,started_at,status,stage,panel_version,requested_panels_json) VALUES ('run','2026-08-30','running','test','v','[]')"
        )
        connection.execute(
            "INSERT INTO private_radar_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("run", "e1", "retail", "x", "root-1", "https://x.com/root-1", "a", "text", None, "2026-08-30", "current", "query", "{}"),
        )

    store = PrivateRadarStore(path)
    values = store.evidence_for_run("run")

    assert values[0]["record_type"] == "root"
    assert values[0]["root_post_external_id"] == "root-1"
    assert values[0]["thread_depth"] == 0
    assert values[0]["truncated"] is False
