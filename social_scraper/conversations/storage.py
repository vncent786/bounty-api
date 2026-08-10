"""SQLite persistence for the canonical conversation corpus.

The schema is additive to ObservationStore. Existing collection/API tables stay
unchanged; canonical records are written beside them in the same transaction.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from .models import CanonicalBundle, canonical_json
from .normalize import NormalizationError, normalize_broker_item


MIGRATION_NAME = "2026_08_10_phase1_canonical_conversations"


class ConversationStore:
    """Read/write facade for canonical conversation evidence."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self.ensure_schema(connection)

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @staticmethod
    def ensure_schema(connection):
        """Apply the additive Phase 1 schema idempotently."""
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS collection_runs (
                id TEXT PRIMARY KEY,
                query TEXT NOT NULL,
                platforms_json TEXT NOT NULL,
                options_json TEXT NOT NULL DEFAULT '{}',
                region TEXT NOT NULL DEFAULT '',
                collected_at TEXT NOT NULL,
                raw_response_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversation_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identity_key TEXT NOT NULL,
                platform TEXT NOT NULL CHECK(platform <> ''),
                source_route TEXT,
                external_id TEXT NOT NULL CHECK(external_id <> ''),
                object_type TEXT NOT NULL CHECK(object_type <> ''),
                parent_external_id TEXT,
                root_post_external_id TEXT,
                record_type TEXT NOT NULL
                    CHECK(record_type IN ('post', 'comment', 'reply')),
                depth INTEGER CHECK(depth IS NULL OR depth >= 0),
                author_external_id TEXT,
                author_username TEXT,
                author_display_name TEXT,
                text TEXT,
                title TEXT,
                url TEXT,
                published_at TEXT,
                published_date TEXT,
                language TEXT,
                is_repost INTEGER CHECK(is_repost IS NULL OR is_repost IN (0, 1)),
                repost_of_external_id TEXT,
                raw_payload_hash TEXT NOT NULL,
                raw_payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_conversation_records_identity
                ON conversation_records(platform, object_type, external_id, id);
            CREATE INDEX IF NOT EXISTS idx_conversation_records_root
                ON conversation_records(
                    platform, root_post_external_id, depth,
                    parent_external_id, id
                );
            CREATE INDEX IF NOT EXISTS idx_conversation_records_parent
                ON conversation_records(platform, parent_external_id, id);

            CREATE TABLE IF NOT EXISTS conversation_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_run_id TEXT NOT NULL REFERENCES collection_runs(id),
                conversation_record_id INTEGER NOT NULL REFERENCES conversation_records(id),
                collected_at TEXT NOT NULL,
                source_observed_at TEXT,
                source_route TEXT,
                views INTEGER,
                likes INTEGER,
                comments INTEGER,
                shares INTEGER,
                collects INTEGER,
                engagement_json TEXT NOT NULL DEFAULT '{}',
                raw_item_json TEXT NOT NULL,
                UNIQUE(collection_run_id, conversation_record_id)
            );
            CREATE INDEX IF NOT EXISTS idx_conversation_observations_record_time
                ON conversation_observations(
                    conversation_record_id, collected_at, id
                );
            CREATE INDEX IF NOT EXISTS idx_conversation_observations_run
                ON conversation_observations(collection_run_id, id);

            CREATE TABLE IF NOT EXISTS collection_run_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_run_id TEXT NOT NULL REFERENCES collection_runs(id),
                platform TEXT NOT NULL CHECK(platform <> ''),
                source_route TEXT NOT NULL CHECK(source_route <> ''),
                attempt_index INTEGER NOT NULL CHECK(attempt_index >= 0),
                status TEXT,
                selected INTEGER CHECK(selected IS NULL OR selected IN (0, 1)),
                items_requested INTEGER,
                items_returned INTEGER,
                latency_ms INTEGER,
                fetched_at TEXT,
                error_category TEXT,
                coverage_json TEXT NOT NULL DEFAULT '{}',
                raw_health_json TEXT NOT NULL,
                UNIQUE(collection_run_id, platform, source_route, attempt_index)
            );
            CREATE INDEX IF NOT EXISTS idx_collection_run_sources_run
                ON collection_run_sources(
                    collection_run_id, platform, attempt_index, id
                );

            CREATE TABLE IF NOT EXISTS zone_record_membership (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zone_id INTEGER NOT NULL,
                collection_run_id TEXT NOT NULL REFERENCES collection_runs(id),
                conversation_record_id INTEGER NOT NULL REFERENCES conversation_records(id),
                keyword TEXT NOT NULL CHECK(keyword <> ''),
                UNIQUE(
                    zone_id, collection_run_id,
                    conversation_record_id, keyword
                )
            );
            CREATE INDEX IF NOT EXISTS idx_zone_record_membership_zone
                ON zone_record_membership(
                    zone_id, collection_run_id, keyword,
                    conversation_record_id
                );

            CREATE TABLE IF NOT EXISTS normalization_diagnostics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_run_id TEXT NOT NULL REFERENCES collection_runs(id),
                item_index INTEGER NOT NULL,
                reason TEXT NOT NULL,
                raw_item_json TEXT NOT NULL,
                UNIQUE(collection_run_id, item_index)
            );

            CREATE INDEX IF NOT EXISTS idx_collection_runs_cache_scope
                ON collection_runs(
                    query, platforms_json, options_json,
                    region, collected_at, id
                );
            """
        )
        record_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(conversation_records)")
        }
        if "object_type" not in record_columns:
            connection.execute(
                "ALTER TABLE conversation_records "
                "ADD COLUMN object_type TEXT NOT NULL DEFAULT 'post'"
            )
            connection.execute(
                "UPDATE conversation_records SET object_type = 'comment' "
                "WHERE record_type IN ('comment', 'reply')"
            )
        connection.execute("DROP INDEX IF EXISTS idx_conversation_records_identity")
        connection.execute(
            """
            CREATE INDEX idx_conversation_records_identity
            ON conversation_records(platform, object_type, external_id, id)
            """
        )
        connection.execute("DROP INDEX IF EXISTS uq_conversation_record_version")
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_record_version
            ON conversation_records(
                platform, object_type, external_id,
                COALESCE(source_route, ''), raw_payload_hash
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO schema_migrations(name, applied_at)
            SELECT ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (MIGRATION_NAME,),
        )

    @staticmethod
    def _record_id(connection, bundle: CanonicalBundle) -> int:
        record = bundle.record
        connection.execute(
            """
            INSERT OR IGNORE INTO conversation_records (
                identity_key, platform, source_route, external_id, object_type,
                parent_external_id, root_post_external_id, record_type, depth,
                author_external_id, author_username, author_display_name,
                text, title, url, published_at, published_date, language,
                is_repost, repost_of_external_id,
                raw_payload_hash, raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.identity_key,
                record.platform,
                record.source_route,
                record.external_id,
                record.object_type,
                record.parent_external_id,
                record.root_post_external_id,
                record.record_type,
                record.depth,
                record.author_external_id,
                record.author_username,
                record.author_display_name,
                record.text,
                record.title,
                record.url,
                record.published_at,
                record.published_date,
                record.language,
                None if record.is_repost is None else int(record.is_repost),
                record.repost_of_external_id,
                record.raw_payload_hash,
                canonical_json(record.raw_payload),
            ),
        )
        row = connection.execute(
            """
            SELECT id FROM conversation_records
            WHERE platform = ? AND object_type = ? AND external_id = ?
              AND COALESCE(source_route, '') = COALESCE(?, '')
              AND raw_payload_hash = ?
            """,
            (
                record.platform,
                record.object_type,
                record.external_id,
                record.source_route,
                record.raw_payload_hash,
            ),
        ).fetchone()
        return int(row["id"])

    @classmethod
    def persist_bundle(cls, connection, run_id: str, bundle: CanonicalBundle) -> int:
        record_id = cls._record_id(connection, bundle)
        observation = bundle.observation
        engagement = observation.engagement
        connection.execute(
            """
            INSERT OR IGNORE INTO conversation_observations (
                collection_run_id, conversation_record_id, collected_at,
                source_observed_at, source_route, views, likes, comments,
                shares, collects, engagement_json, raw_item_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                record_id,
                observation.collected_at,
                observation.source_observed_at,
                bundle.record.source_route,
                engagement["views"],
                engagement["likes"],
                engagement["comments"],
                engagement["shares"],
                engagement["collects"],
                canonical_json(engagement),
                canonical_json(bundle.record.raw_payload),
            ),
        )
        return record_id

    @classmethod
    def persist_response(cls, connection, run_id: str, response: dict, collected_at: str):
        """Persist canonical records and route attempts beside one legacy run.

        The schema is initialized when ObservationStore/ConversationStore opens.
        Re-running DDL here would implicitly commit the caller's transaction.
        """
        for index, item in enumerate(response.get("items", [])):
            try:
                bundle = normalize_broker_item(item, collected_at=collected_at)
                cls.persist_bundle(connection, run_id, bundle)
            except (NormalizationError, ValueError, TypeError) as exc:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO normalization_diagnostics (
                        collection_run_id, item_index, reason, raw_item_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (run_id, index, str(exc), canonical_json(item)),
                )
        cls._persist_run_sources(connection, run_id, response)

    @staticmethod
    def _persist_run_sources(connection, run_id: str, response: dict):
        selected_by_platform = {
            platform: details.get("selected_connector")
            for platform, details in (response.get("platform_results") or {}).items()
            if isinstance(details, dict)
        }
        attempts = defaultdict(int)
        for health in response.get("source_health", []) or []:
            if not isinstance(health, dict):
                continue
            platform = str(health.get("platform") or "").strip().lower()
            route = str(health.get("connector") or "").strip()
            if not platform or not route:
                continue
            attempt_index = attempts[platform]
            attempts[platform] += 1
            selected_route = selected_by_platform.get(platform)
            selected = None if selected_route is None else int(route == selected_route)
            connection.execute(
                """
                INSERT OR IGNORE INTO collection_run_sources (
                    collection_run_id, platform, source_route, attempt_index,
                    status, selected, items_requested, items_returned,
                    latency_ms, fetched_at, error_category,
                    coverage_json, raw_health_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    platform,
                    route,
                    attempt_index,
                    health.get("status"),
                    selected,
                    health.get("items_requested"),
                    health.get("items_returned"),
                    health.get("latency_ms"),
                    health.get("fetched_at") or None,
                    health.get("error") or None,
                    canonical_json(health.get("coverage") or {}),
                    canonical_json(health),
                ),
            )

    def record_bundles(
        self,
        run_id: str,
        bundles: list[CanonicalBundle],
        *,
        zone_id: int | None = None,
        keyword: str | None = None,
    ) -> list[int]:
        """Atomically append normalized posts/comments/replies to an existing run."""
        if zone_id is not None and not str(keyword or "").strip():
            raise ValueError("keyword is required when zone_id is provided")
        with self._connect() as connection:
            run = connection.execute(
                "SELECT id FROM collection_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise ValueError(f"collection run not found: {run_id}")
            record_ids = [
                self.persist_bundle(connection, run_id, bundle) for bundle in bundles
            ]
            if zone_id is not None:
                for record_id in record_ids:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO zone_record_membership (
                            zone_id, collection_run_id,
                            conversation_record_id, keyword
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (zone_id, run_id, record_id, str(keyword).strip()),
                    )
            return record_ids

    def get_record_versions(
        self,
        platform: str,
        external_id: str,
        object_type: str = "post",
    ) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT r.*, o.id AS observation_id, o.collected_at,
                       o.source_observed_at, o.views, o.likes, o.comments,
                       o.shares, o.collects
                FROM conversation_observations o
                JOIN conversation_records r ON r.id = o.conversation_record_id
                WHERE r.platform = ? AND r.object_type = ? AND r.external_id = ?
                ORDER BY o.collected_at, o.id
                """,
                (platform.lower(), object_type.lower(), external_id),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get_run_records(self, run_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT r.*, o.id AS observation_id, o.collected_at,
                       o.source_observed_at, o.views, o.likes, o.comments,
                       o.shares, o.collects
                FROM conversation_observations o
                JOIN conversation_records r ON r.id = o.conversation_record_id
                WHERE o.collection_run_id = ?
                ORDER BY o.id
                """,
                (run_id,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get_run_sources(self, run_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM collection_run_sources
                WHERE collection_run_id = ?
                ORDER BY platform, attempt_index, id
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                **dict(row),
                "selected": None if row["selected"] is None else bool(row["selected"]),
                "coverage": json.loads(row["coverage_json"]),
                "raw_health": json.loads(row["raw_health_json"]),
            }
            for row in rows
        ]

    def get_normalization_diagnostics(self, run_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT item_index, reason, raw_item_json
                FROM normalization_diagnostics
                WHERE collection_run_id = ? ORDER BY item_index
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                "item_index": row["item_index"],
                "reason": row["reason"],
                "raw_item": json.loads(row["raw_item_json"]),
            }
            for row in rows
        ]

    def add_run_zone_memberships(
        self,
        zone_id: int,
        run_id: str,
        keyword: str,
    ) -> int:
        """Link every canonical observation in a run to its zone seed."""
        keyword = str(keyword or "").strip()
        if not keyword:
            raise ValueError("keyword is required")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO zone_record_membership (
                    zone_id, collection_run_id, conversation_record_id, keyword
                )
                SELECT ?, o.collection_run_id, o.conversation_record_id, ?
                FROM conversation_observations o
                WHERE o.collection_run_id = ?
                """,
                (zone_id, keyword, run_id),
            )
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM zone_record_membership
                WHERE zone_id = ? AND collection_run_id = ? AND keyword = ?
                """,
                (zone_id, run_id, keyword),
            ).fetchone()
            return int(row["count"])

    def list_zone_records(self, zone_id: int, keyword: str | None = None) -> list[dict]:
        query = """
            SELECT r.*, o.id AS observation_id, o.collected_at,
                   o.source_observed_at, o.views, o.likes, o.comments,
                   o.shares, o.collects, m.keyword,
                   m.collection_run_id AS membership_run_id
            FROM zone_record_membership m
            JOIN conversation_records r ON r.id = m.conversation_record_id
            JOIN conversation_observations o
              ON o.collection_run_id = m.collection_run_id
             AND o.conversation_record_id = m.conversation_record_id
            WHERE m.zone_id = ?
        """
        parameters: list = [zone_id]
        if keyword is not None:
            query += " AND m.keyword = ?"
            parameters.append(keyword)
        query += " ORDER BY o.collected_at, o.id, m.id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        records = []
        for row in rows:
            record = self._row_to_record(row)
            record["keyword"] = row["keyword"]
            record["collection_run_id"] = row["membership_run_id"]
            records.append(record)
        return records

    def add_zone_membership(
        self,
        zone_id: int,
        run_id: str,
        record_id: int,
        keyword: str,
    ):
        keyword = str(keyword or "").strip()
        if not keyword:
            raise ValueError("keyword is required")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO zone_record_membership (
                    zone_id, collection_run_id, conversation_record_id, keyword
                ) VALUES (?, ?, ?, ?)
                """,
                (zone_id, run_id, record_id, keyword),
            )

    def get_thread(self, platform: str, root_external_id: str) -> dict:
        """Return the latest observed version of each record in a thread."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT r.*, o.id AS observation_id, o.collected_at,
                       o.source_observed_at, o.views, o.likes, o.comments,
                       o.shares, o.collects
                FROM conversation_observations o
                JOIN conversation_records r ON r.id = o.conversation_record_id
                WHERE r.platform = ?
                  AND (r.external_id = ? OR r.root_post_external_id = ?)
                ORDER BY o.collected_at DESC, o.id DESC
                """,
                (platform.lower(), root_external_id, root_external_id),
            ).fetchall()
        latest = {}
        for row in rows:
            latest.setdefault(row["identity_key"], self._row_to_record(row))
        records = list(latest.values())
        nodes = {
            (record["object_type"], record["external_id"]): {
                **record,
                "children": [],
            }
            for record in records
        }
        roots = []
        orphans = []
        for node in nodes.values():
            parent_id = node["parent_external_id"]
            if parent_id is None:
                roots.append(node)
                continue
            parent_type = "post" if node["record_type"] == "comment" else "comment"
            parent_key = (parent_type, parent_id)
            if parent_key in nodes:
                nodes[parent_key]["children"].append(node)
            else:
                orphans.append(node)
        self._sort_tree(roots)
        self._sort_tree(orphans)
        return {"roots": roots, "orphans": orphans, "record_count": len(records)}

    @classmethod
    def _sort_tree(cls, nodes: list[dict]):
        nodes.sort(
            key=lambda node: (
                node["published_at"] is None,
                node["published_at"] or "",
                node["external_id"],
            )
        )
        for node in nodes:
            cls._sort_tree(node["children"])

    @staticmethod
    def _row_to_record(row) -> dict:
        return {
            "id": row["id"],
            "identity_key": row["identity_key"],
            "platform": row["platform"],
            "source_route": row["source_route"],
            "external_id": row["external_id"],
            "object_type": row["object_type"],
            "parent_external_id": row["parent_external_id"],
            "root_post_external_id": row["root_post_external_id"],
            "record_type": row["record_type"],
            "depth": row["depth"],
            "author_external_id": row["author_external_id"],
            "author_username": row["author_username"],
            "author_display_name": row["author_display_name"],
            "text": row["text"],
            "title": row["title"],
            "url": row["url"],
            "published_at": row["published_at"],
            "published_date": row["published_date"],
            "language": row["language"],
            "is_repost": None if row["is_repost"] is None else bool(row["is_repost"]),
            "repost_of_external_id": row["repost_of_external_id"],
            "raw_payload_hash": row["raw_payload_hash"],
            "observation_id": row["observation_id"],
            "collected_at": row["collected_at"],
            "source_observed_at": row["source_observed_at"],
            "engagement": {
                "views": row["views"],
                "likes": row["likes"],
                "comments": row["comments"],
                "shares": row["shares"],
                "collects": row["collects"],
            },
        }
