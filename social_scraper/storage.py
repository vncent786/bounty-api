"""SQLite persistence for immutable collection runs and scheduled observations."""

import base64
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


class ObservationStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS collection_runs (
                    id TEXT PRIMARY KEY,
                    query TEXT NOT NULL,
                    platforms_json TEXT NOT NULL,
                    options_json TEXT NOT NULL DEFAULT '{}',
                    region TEXT NOT NULL DEFAULT '',
                    collected_at TEXT NOT NULL,
                    raw_response_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_run_id TEXT NOT NULL REFERENCES collection_runs(id),
                    platform TEXT NOT NULL,
                    post_id TEXT NOT NULL,
                    url TEXT NOT NULL DEFAULT '',
                    observed_at TEXT NOT NULL,
                    connector TEXT,
                    views INTEGER,
                    likes INTEGER,
                    comments INTEGER,
                    shares INTEGER,
                    raw_item_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_observations_item_time
                    ON observations(platform, post_id, observed_at, id);

                CREATE TABLE IF NOT EXISTS source_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_run_id TEXT NOT NULL REFERENCES collection_runs(id),
                    platform TEXT NOT NULL,
                    connector TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    payload_format TEXT NOT NULL DEFAULT 'json',
                    payload_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_source_records_run
                    ON source_records(collection_run_id, id);

                CREATE TABLE IF NOT EXISTS collection_queries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT NOT NULL,
                    platforms_json TEXT NOT NULL,
                    options_json TEXT NOT NULL DEFAULT '{}',
                    region TEXT NOT NULL DEFAULT '',
                    interval_minutes INTEGER NOT NULL CHECK(interval_minutes > 0),
                    next_run_at TEXT NOT NULL,
                    last_run_at TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    lease_token TEXT,
                    lease_until TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(keyword, platforms_json, options_json, region)
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(collection_queries)").fetchall()
            }
            if "lease_token" not in columns:
                connection.execute("ALTER TABLE collection_queries ADD COLUMN lease_token TEXT")
            if "lease_until" not in columns:
                connection.execute("ALTER TABLE collection_queries ADD COLUMN lease_until TEXT")
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(collection_queries)").fetchall()
            }
            scope_unique = False
            for index in connection.execute("PRAGMA index_list(collection_queries)").fetchall():
                if not index["unique"]:
                    continue
                index_columns = [
                    row["name"]
                    for row in connection.execute(
                        f"PRAGMA index_info('{index['name']}')"
                    ).fetchall()
                ]
                if index_columns == ["keyword", "platforms_json", "options_json", "region"]:
                    scope_unique = True
                    break
            if "options_json" not in columns or not scope_unique:
                options_expression = "options_json" if "options_json" in columns else "'{}'"
                connection.execute(
                    "ALTER TABLE collection_queries RENAME TO collection_queries_legacy"
                )
                connection.execute(
                    """
                    CREATE TABLE collection_queries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        keyword TEXT NOT NULL,
                        platforms_json TEXT NOT NULL,
                        options_json TEXT NOT NULL DEFAULT '{}',
                        region TEXT NOT NULL DEFAULT '',
                        interval_minutes INTEGER NOT NULL CHECK(interval_minutes > 0),
                        next_run_at TEXT NOT NULL,
                        last_run_at TEXT,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        lease_token TEXT,
                        lease_until TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(keyword, platforms_json, options_json, region)
                    )
                    """
                )
                connection.execute(
                    f"""
                    INSERT INTO collection_queries (
                        id, keyword, platforms_json, options_json, region,
                        interval_minutes, next_run_at, last_run_at, enabled,
                        lease_token, lease_until, created_at, updated_at
                    )
                    SELECT id, keyword, platforms_json, {options_expression}, region,
                           interval_minutes, next_run_at, last_run_at, enabled,
                           lease_token, lease_until, created_at, updated_at
                    FROM collection_queries_legacy
                    """
                )
                connection.execute("DROP TABLE collection_queries_legacy")
            run_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(collection_runs)").fetchall()
            }
            if "options_json" not in run_columns:
                connection.execute(
                    "ALTER TABLE collection_runs ADD COLUMN options_json TEXT NOT NULL DEFAULT '{}'"
                )
            source_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(source_records)").fetchall()
            }
            if "payload_format" not in source_columns:
                connection.execute(
                    "ALTER TABLE source_records ADD COLUMN payload_format TEXT NOT NULL DEFAULT 'json'"
                )

    @staticmethod
    def _iso(value=None):
        value = value or datetime.now(timezone.utc)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    @classmethod
    def _validated_iso(cls, value, fallback):
        if not value:
            return fallback
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return fallback
        return cls._iso(parsed)

    @staticmethod
    def _platforms_json(platforms):
        return json.dumps(sorted(set(platforms)), separators=(",", ":"))

    @staticmethod
    def _options_json(platform_options=None):
        normalized = json.loads(json.dumps(platform_options or {}, ensure_ascii=False))
        reddit = normalized.get("reddit")
        if isinstance(reddit, dict) and isinstance(reddit.get("subreddits"), list):
            reddit["subreddits"] = sorted({
                value.strip().lower()
                for value in reddit["subreddits"]
                if isinstance(value, str) and value.strip()
            })
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _insert_collection(
        self, connection, response, platforms, region, collected_at, platform_options=None
    ):
        run_id = str(uuid.uuid4())
        timestamp = self._iso(collected_at)
        raw_response = json.loads(json.dumps(response, ensure_ascii=False))
        source_records = raw_response.pop("_source_records", [])
        connection.execute(
            """
            INSERT INTO collection_runs (
                id, query, platforms_json, options_json, region, collected_at,
                raw_response_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                str(response.get("query", "")),
                self._platforms_json(platforms),
                self._options_json(platform_options),
                region or "",
                timestamp,
                json.dumps(raw_response, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        for item in response.get("items", []):
            engagement = item.get("engagement", {}) or {}
            provenance = item.get("provenance", {}) or {}
            observed_at = self._validated_iso(
                provenance.get("source_observed_at") or provenance.get("fetched_at"),
                timestamp,
            )
            connection.execute(
                """
                INSERT INTO observations (
                    collection_run_id, platform, post_id, url, observed_at, connector,
                    views, likes, comments, shares, raw_item_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(item.get("platform", "")),
                    str(item.get("post_id", "")),
                    str(item.get("url", "")),
                    observed_at,
                    provenance.get("connector"),
                    engagement.get("views"),
                    engagement.get("likes"),
                    engagement.get("comments"),
                    engagement.get("shares"),
                    json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                ),
            )
        for record in source_records:
            if not isinstance(record, dict):
                continue
            payload_json = json.dumps(
                record.get("payload"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            payload_format = str(record.get("payload_format") or "json")
            if payload_format == "bytes_base64":
                try:
                    hash_input = base64.b64decode(record.get("payload"), validate=True)
                except (TypeError, ValueError):
                    continue
            else:
                payload_format = "json"
                hash_input = payload_json.encode("utf-8")
            connection.execute(
                """
                INSERT INTO source_records (
                    collection_run_id, platform, connector, source_id,
                    fetched_at, payload_format, payload_sha256, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(record.get("platform", "")),
                    str(record.get("connector", "")),
                    str(record.get("source_id", "")),
                    self._validated_iso(record.get("fetched_at"), timestamp),
                    payload_format,
                    hashlib.sha256(hash_input).hexdigest(),
                    payload_json,
                ),
            )
        return run_id

    def record_collection(
        self, response, platforms, region="", collected_at=None, platform_options=None
    ):
        with self._connect() as connection:
            return self._insert_collection(
                connection, response, platforms, region, collected_at, platform_options
            )

    def complete_claimed_collection(
        self,
        query_id,
        claim_token,
        response,
        platforms,
        region,
        collected_at,
        next_run_at,
        platform_options=None,
    ):
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            claimed = connection.execute(
                "SELECT id FROM collection_queries WHERE id = ? AND lease_token = ?",
                (query_id, claim_token),
            ).fetchone()
            if claimed is None:
                raise RuntimeError("Collection query claim is no longer valid")
            run_id = self._insert_collection(
                connection,
                response,
                platforms,
                region,
                collected_at,
                platform_options,
            )
            connection.execute(
                """
                UPDATE collection_queries
                SET last_run_at = ?, next_run_at = ?, lease_token = NULL,
                    lease_until = NULL, updated_at = ?
                WHERE id = ? AND lease_token = ?
                """,
                (self._iso(collected_at), self._iso(next_run_at), self._iso(), query_id, claim_token),
            )
            return run_id

    def get_collection_run(self, run_id):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM collection_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "query": row["query"],
            "platforms": json.loads(row["platforms_json"]),
            "platform_options": json.loads(row["options_json"]),
            "region": row["region"],
            "collected_at": row["collected_at"],
            "raw_response": json.loads(row["raw_response_json"]),
        }

    def get_latest_collection(
        self,
        query,
        platforms,
        region="",
        max_age_minutes=None,
        now=None,
        platform_options=None,
    ):
        platforms_json = self._platforms_json(platforms)
        options_json = self._options_json(platform_options)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM collection_runs
                WHERE query = ? AND platforms_json = ? AND options_json = ? AND region = ?
                ORDER BY collected_at DESC, id DESC
                LIMIT 1
                """,
                (str(query), platforms_json, options_json, region or ""),
            ).fetchone()
        if row is None:
            return None

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        collected = datetime.fromisoformat(row["collected_at"].replace("Z", "+00:00"))
        if collected.tzinfo is None:
            collected = collected.replace(tzinfo=timezone.utc)
        age_delta = current.astimezone(timezone.utc) - collected.astimezone(timezone.utc)
        if age_delta.total_seconds() < -300:
            return None
        age_seconds = max(0, int(age_delta.total_seconds()))
        if max_age_minutes is not None and age_seconds > int(max_age_minutes) * 60:
            return None
        return {
            "id": row["id"],
            "query": row["query"],
            "platforms": json.loads(row["platforms_json"]),
            "platform_options": json.loads(row["options_json"]),
            "region": row["region"],
            "collected_at": row["collected_at"],
            "age_seconds": age_seconds,
            "raw_response": json.loads(row["raw_response_json"]),
        }

    def get_observation_history(self, platform, post_id):
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT collection_run_id, observed_at, connector, views, likes, comments, shares
                FROM observations
                WHERE platform = ? AND post_id = ?
                ORDER BY observed_at, id
                """,
                (platform, post_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_source_records(self, run_id):
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT platform, connector, source_id, fetched_at,
                       payload_format, payload_sha256, payload_json
                FROM source_records
                WHERE collection_run_id = ?
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        output = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            if row["payload_format"] == "bytes_base64":
                try:
                    hash_input = base64.b64decode(payload, validate=True)
                except (TypeError, ValueError):
                    hash_input = b""
            else:
                hash_input = row["payload_json"].encode("utf-8")
            output.append({
                **{key: row[key] for key in row.keys() if key != "payload_json"},
                "payload": payload,
                "hash_valid": hashlib.sha256(hash_input).hexdigest() == row["payload_sha256"],
            })
        return output

    def upsert_query(
        self,
        keyword,
        platforms,
        region,
        interval_minutes,
        next_run_at,
        enabled=True,
        platform_options=None,
    ):
        now = self._iso()
        platforms_json = self._platforms_json(platforms)
        options_json = self._options_json(platform_options)
        next_run_iso = self._iso(next_run_at)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO collection_queries (
                    keyword, platforms_json, options_json, region, interval_minutes,
                    next_run_at, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(keyword, platforms_json, options_json, region) DO UPDATE SET
                    interval_minutes = excluded.interval_minutes,
                    next_run_at = excluded.next_run_at,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    keyword,
                    platforms_json,
                    options_json,
                    region or "",
                    interval_minutes,
                    next_run_iso,
                    int(enabled),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT id FROM collection_queries
                WHERE keyword = ? AND platforms_json = ? AND options_json = ? AND region = ?
                """,
                (keyword, platforms_json, options_json, region or ""),
            ).fetchone()
        return row["id"]

    @staticmethod
    def _query_dict(row):
        return {
            "id": row["id"],
            "keyword": row["keyword"],
            "platforms": json.loads(row["platforms_json"]),
            "platform_options": json.loads(row["options_json"]),
            "region": row["region"],
            "interval_minutes": row["interval_minutes"],
            "next_run_at": row["next_run_at"],
            "last_run_at": row["last_run_at"],
            "enabled": bool(row["enabled"]),
            "claim_token": row["lease_token"],
        }

    def get_query(self, query_id):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM collection_queries WHERE id = ?", (query_id,)).fetchone()
        return self._query_dict(row) if row else None

    def list_due_queries(self, now=None):
        now_iso = self._iso(now)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM collection_queries
                WHERE enabled = 1 AND next_run_at <= ?
                  AND (lease_until IS NULL OR lease_until <= ?)
                ORDER BY next_run_at, id
                """,
                (now_iso, now_iso),
            ).fetchall()
        return [self._query_dict(row) for row in rows]

    def claim_query(self, query_id, now=None, lease_minutes=10):
        claim_time = now or datetime.now(timezone.utc)
        now_iso = self._iso(claim_time)
        lease_until = self._iso(claim_time + timedelta(minutes=lease_minutes))
        token = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE collection_queries
                SET lease_token = ?, lease_until = ?, updated_at = ?
                WHERE id = ? AND enabled = 1
                  AND (lease_until IS NULL OR lease_until <= ?)
                """,
                (token, lease_until, now_iso, query_id, now_iso),
            )
            return token if cursor.rowcount == 1 else None

    def claim_due_queries(self, now=None, lease_minutes=10, limit=100):
        claim_time = now or datetime.now(timezone.utc)
        now_iso = self._iso(claim_time)
        lease_until = self._iso(claim_time + timedelta(minutes=lease_minutes))
        claimed = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM collection_queries
                WHERE enabled = 1 AND next_run_at <= ?
                  AND (lease_until IS NULL OR lease_until <= ?)
                ORDER BY next_run_at, id
                LIMIT ?
                """,
                (now_iso, now_iso, limit),
            ).fetchall()
            for row in rows:
                token = str(uuid.uuid4())
                cursor = connection.execute(
                    """
                    UPDATE collection_queries
                    SET lease_token = ?, lease_until = ?, updated_at = ?
                    WHERE id = ? AND (lease_until IS NULL OR lease_until <= ?)
                    """,
                    (token, lease_until, now_iso, row["id"], now_iso),
                )
                if cursor.rowcount == 1:
                    item = self._query_dict(row)
                    item["claim_token"] = token
                    claimed.append(item)
        return claimed

    def release_claim(self, query_id, claim_token):
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE collection_queries
                SET lease_token = NULL, lease_until = NULL, updated_at = ?
                WHERE id = ? AND lease_token = ?
                """,
                (self._iso(), query_id, claim_token),
            )

    def mark_query_collected(self, query_id, last_run_at, next_run_at):
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE collection_queries
                SET last_run_at = ?, next_run_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (self._iso(last_run_at), self._iso(next_run_at), self._iso(), query_id),
            )
