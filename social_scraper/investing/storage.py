"""Additive SQLite persistence for the global investing Breaking Now Radar."""

from __future__ import annotations

import dataclasses
import json
import math
import numbers
import sqlite3
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence


_MIGRATION = "2026_08_16_global_investing_radar_v1"
_INVESTING_CATEGORY_ORDER = (
    "Business & Finance", "Technology", "Shopping", "Health", "Climate",
    "Autos & Vehicles", "Jobs & Education", "Travel & Transportation",
    "Food & Drink", "Science", "Law & Government", "Politics",
    "Beauty & Fashion", "Games", "Hobbies & Leisure", "Pets & Animals",
    "Entertainment", "Other", "Sports",
)


class InvestingRadarError(Exception):
    """Base error for invalid Radar persistence operations."""


class RadarNotFoundError(InvestingRadarError, LookupError):
    pass


class RadarConflictError(InvestingRadarError):
    pass


class RadarValidationError(InvestingRadarError, ValueError):
    pass


def _utc_iso(value: datetime | str | None = None, *, label: str = "timestamp") -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            raise RadarValidationError(f"{label} must be an ISO-8601 timestamp")
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise RadarValidationError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def normalize_keyword(value: str) -> str:
    """Conservative identity: Unicode/case/outer and repeated whitespace only."""
    keyword = str(value or "").strip()
    if not keyword:
        raise RadarValidationError("candidate keyword is required")
    return " ".join(unicodedata.normalize("NFKC", keyword).casefold().split())


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, datetime):
        return _utc_iso(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_jsonable(item) for item in sorted(value, key=str)]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        return float(value)
    raise RadarValidationError(f"candidate payload contains non-JSON value {type(value).__name__}")


def _json(value: Any) -> str:
    try:
        return json.dumps(
            _jsonable(value), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise RadarValidationError("candidate payload must be JSON serializable") from exc


def _candidate_mapping(candidate: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(candidate):
        raw = dataclasses.asdict(candidate)
    elif isinstance(candidate, Mapping):
        raw = dict(candidate)
    elif callable(getattr(candidate, "to_dict", None)):
        raw = candidate.to_dict()
    elif hasattr(candidate, "__dict__"):
        raw = {key: value for key, value in vars(candidate).items() if not key.startswith("_")}
    else:
        raise RadarValidationError("candidate must be a mapping or data object")
    if not isinstance(raw, Mapping):
        raise RadarValidationError("candidate to_dict() must return a mapping")
    return dict(_jsonable(raw))


def _array(value: Any, *, split_commas: bool = False) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        if split_commas:
            return [part.strip() for part in value.split(",") if part.strip()]
        return [value]
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    raise RadarValidationError("candidate list metadata must be a list or string")


def _number(value: Any, label: str, *, integer: bool = False) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise RadarValidationError(f"{label} must be numeric or null")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise RadarValidationError(f"{label} must be finite or null")
    if integer:
        if not numeric.is_integer():
            raise RadarValidationError(f"{label} must be an integer or null")
        return int(value)
    return numeric


def _country(value: str) -> str:
    result = str(value or "").strip().upper()
    if not result:
        raise RadarValidationError("country is required")
    return result


def _unique(values: Sequence[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


class InvestingRadarStore:
    """Thread-safe, append-only Radar store using a fresh connection per call."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], datetime | str] | None = None,
    ) -> None:
        self.db_path = str(db_path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._uri = False
        self._keeper: sqlite3.Connection | None = None
        self._connect_target = self.db_path
        if self.db_path == ":memory:":
            self._uri = True
            self._connect_target = f"file:investing-radar-{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._keeper = self._new_connection()
        else:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    def _now(self) -> str:
        return _utc_iso(self._clock())

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._connect_target,
            timeout=10,
            uri=self._uri,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        if not self._uri:
            connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._new_connection()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def ensure_schema(self) -> None:
        """Create only namespaced tables, leaving every legacy object untouched."""
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS investing_radar_schema_migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS investing_radar_sweeps (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL CHECK(status IN
                        ('running','complete','partial','failed')),
                    total_markets INTEGER NOT NULL CHECK(total_markets >= 0),
                    recorded_markets INTEGER NOT NULL DEFAULT 0 CHECK(recorded_markets >= 0),
                    complete_markets INTEGER NOT NULL DEFAULT 0 CHECK(complete_markets >= 0),
                    empty_markets INTEGER NOT NULL DEFAULT 0 CHECK(empty_markets >= 0),
                    failed_markets INTEGER NOT NULL DEFAULT 0 CHECK(failed_markets >= 0),
                    candidate_observations INTEGER NOT NULL DEFAULT 0
                        CHECK(candidate_observations >= 0),
                    unique_candidates INTEGER NOT NULL DEFAULT 0
                        CHECK(unique_candidates >= 0)
                );
                CREATE TABLE IF NOT EXISTS investing_radar_market_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sweep_id TEXT NOT NULL REFERENCES investing_radar_sweeps(id),
                    country TEXT NOT NULL,
                    country_name TEXT,
                    status TEXT NOT NULL CHECK(status IN ('complete','empty','failed')),
                    observed_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    candidate_count INTEGER NOT NULL CHECK(candidate_count >= 0),
                    error_category TEXT,
                    UNIQUE(sweep_id, country),
                    CHECK(
                        (status = 'complete' AND candidate_count > 0 AND error_category IS NULL)
                        OR (status = 'empty' AND candidate_count = 0 AND error_category IS NULL)
                        OR (status = 'failed' AND candidate_count = 0 AND error_category IS NOT NULL)
                    )
                );
                CREATE TABLE IF NOT EXISTS investing_radar_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    normalized_keyword TEXT NOT NULL UNIQUE,
                    display_keyword TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    observation_count INTEGER NOT NULL DEFAULT 0
                        CHECK(observation_count >= 0)
                );
                CREATE TABLE IF NOT EXISTS investing_radar_candidate_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sweep_id TEXT NOT NULL REFERENCES investing_radar_sweeps(id),
                    market_outcome_id INTEGER NOT NULL
                        REFERENCES investing_radar_market_outcomes(id),
                    candidate_id INTEGER NOT NULL REFERENCES investing_radar_candidates(id),
                    country TEXT NOT NULL,
                    market_rank INTEGER NOT NULL CHECK(market_rank >= 1),
                    keyword TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    source TEXT,
                    source_run_id TEXT,
                    source_observation_id TEXT,
                    source_record_count INTEGER,
                    search_volume INTEGER,
                    growth_pct REAL,
                    started_hours_ago REAL,
                    source_started_at TEXT,
                    related_terms_json TEXT NOT NULL,
                    topic_ids_json TEXT NOT NULL,
                    categories_json TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    raw_payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_investing_radar_sweeps_started
                    ON investing_radar_sweeps(started_at, id);
                CREATE INDEX IF NOT EXISTS idx_investing_radar_markets_sweep
                    ON investing_radar_market_outcomes(sweep_id, country, id);
                CREATE INDEX IF NOT EXISTS idx_investing_radar_observations_sweep_candidate
                    ON investing_radar_candidate_observations(sweep_id, candidate_id, id);
                CREATE INDEX IF NOT EXISTS idx_investing_radar_observations_country
                    ON investing_radar_candidate_observations(country, sweep_id, id);
                CREATE INDEX IF NOT EXISTS idx_investing_radar_observations_candidate
                    ON investing_radar_candidate_observations(candidate_id, id);
                CREATE TRIGGER IF NOT EXISTS investing_radar_observations_no_update
                BEFORE UPDATE ON investing_radar_candidate_observations
                BEGIN
                    SELECT RAISE(ABORT, 'investing Radar observations are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS investing_radar_observations_no_delete
                BEFORE DELETE ON investing_radar_candidate_observations
                BEGIN
                    SELECT RAISE(ABORT, 'investing Radar observations are immutable');
                END;
                """
            )
            connection.execute(
                """INSERT OR IGNORE INTO investing_radar_schema_migrations(name, applied_at)
                   VALUES (?, ?)""",
                (_MIGRATION, self._now()),
            )
            connection.commit()

    @staticmethod
    def _require_running(connection: sqlite3.Connection, sweep_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM investing_radar_sweeps WHERE id = ?", (str(sweep_id),)
        ).fetchone()
        if row is None:
            raise RadarNotFoundError(f"sweep {sweep_id!r} was not found")
        if row["status"] != "running":
            raise RadarConflictError(f"sweep {sweep_id!r} is already finalized")
        outcomes = connection.execute(
            "SELECT COUNT(*) FROM investing_radar_market_outcomes WHERE sweep_id = ?",
            (str(sweep_id),),
        ).fetchone()[0]
        if outcomes >= row["total_markets"]:
            raise RadarConflictError(f"sweep {sweep_id!r} already has all market outcomes")
        return row

    def create_sweep(self, total_markets: int, *, started_at: datetime | str | None = None) -> str:
        if isinstance(total_markets, bool) or not isinstance(total_markets, numbers.Integral):
            raise RadarValidationError("total_markets must be a non-negative integer")
        total = int(total_markets)
        if total < 0:
            raise RadarValidationError("total_markets must be a non-negative integer")
        sweep_id = uuid.uuid4().hex
        timestamp = _utc_iso(started_at) if started_at is not None else self._now()
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO investing_radar_sweeps
                   (id, started_at, status, total_markets) VALUES (?, ?, 'running', ?)""",
                (sweep_id, timestamp, total),
            )
        return sweep_id

    def create_sweep_if_idle(
        self,
        total_markets: int,
        *,
        started_at: datetime | str | None = None,
    ) -> tuple[str, bool]:
        """Atomically create a sweep unless another collector already owns one."""
        if isinstance(total_markets, bool) or not isinstance(total_markets, numbers.Integral):
            raise RadarValidationError("total_markets must be a non-negative integer")
        total = int(total_markets)
        if total < 0:
            raise RadarValidationError("total_markets must be a non-negative integer")
        timestamp = _utc_iso(started_at) if started_at is not None else self._now()
        with self._transaction() as connection:
            running = connection.execute(
                """SELECT id FROM investing_radar_sweeps
                   WHERE status = 'running' ORDER BY rowid DESC LIMIT 1"""
            ).fetchone()
            if running:
                return str(running["id"]), False
            sweep_id = uuid.uuid4().hex
            connection.execute(
                """INSERT INTO investing_radar_sweeps
                   (id, started_at, status, total_markets) VALUES (?, ?, 'running', ?)""",
                (sweep_id, timestamp, total),
            )
        return sweep_id, True

    @staticmethod
    def _prepare_candidate(raw_candidate: Any, fallback_observed_at: str) -> dict[str, Any]:
        raw = _candidate_mapping(raw_candidate)
        keyword = str(raw.get("keyword") or "").strip()
        normalized = normalize_keyword(keyword)
        observed_value = raw.get("discovered_at") or raw.get("observed_at")
        observed_at = (
            _utc_iso(observed_value, label="candidate observed_at")
            if observed_value else fallback_observed_at
        )
        source_started = raw.get("source_started_at")
        source_started_at = (
            _utc_iso(source_started, label="source_started_at") if source_started else None
        )
        categories = _unique(_array(raw.get("categories"), split_commas=True))
        related_terms = _unique(_array(raw.get("related_terms")))
        topic_ids = _unique(_array(raw.get("topic_ids")))
        source_record_count = raw.get("source_record_count")
        if source_record_count is not None:
            source_record_count = _number(
                source_record_count, "source_record_count", integer=True,
            )
            if source_record_count < 0:
                raise RadarValidationError("source_record_count must be non-negative or null")
        source = raw.get("source")
        source = str(source).strip() if source not in (None, "") else None
        source_run_id = raw.get("discovery_run_id", raw.get("source_run_id"))
        source_run_id = str(source_run_id) if source_run_id not in (None, "") else None
        source_observation_id = raw.get(
            "candidate_observation_id", raw.get("source_observation_id")
        )
        provenance = {
            "source": source,
            "source_run_id": source_run_id,
            "source_observation_id": source_observation_id,
            "source_record_count": source_record_count,
            "source_observations": raw.get("source_observations", []),
            "metric_conflicts": raw.get("metric_conflicts", []),
        }
        return {
            "keyword": keyword,
            "normalized_keyword": normalized,
            "observed_at": observed_at,
            "source": source,
            "source_run_id": source_run_id,
            "source_observation_id": source_observation_id,
            "source_record_count": source_record_count,
            "search_volume": _number(raw.get("search_volume"), "search_volume", integer=True),
            "growth_pct": _number(raw.get("growth_pct"), "growth_pct"),
            "started_hours_ago": _number(raw.get("started_hours_ago"), "started_hours_ago"),
            "source_started_at": source_started_at,
            "related_terms": related_terms,
            "topic_ids": topic_ids,
            "categories": categories,
            "provenance": provenance,
            "raw_payload": raw,
        }

    def record_market_success(
        self,
        sweep_id: str,
        country: str,
        candidates: Sequence[Any],
        *,
        observed_at: datetime | str | None = None,
        country_name: str | None = None,
    ) -> dict[str, Any]:
        if candidates is None or isinstance(candidates, (str, bytes, Mapping)):
            raise RadarValidationError("candidates must be a sequence")
        code = _country(country)
        market_observed_at = _utc_iso(observed_at) if observed_at is not None else self._now()
        prepared = [
            self._prepare_candidate(candidate, market_observed_at) for candidate in candidates
        ]
        status = "complete" if prepared else "empty"
        recorded_at = self._now()
        try:
            with self._transaction() as connection:
                self._require_running(connection, sweep_id)
                cursor = connection.execute(
                    """INSERT INTO investing_radar_market_outcomes
                       (sweep_id, country, country_name, status, observed_at, recorded_at,
                        candidate_count, error_category)
                       VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
                    (
                        str(sweep_id), code,
                        str(country_name).strip() if country_name else None,
                        status, market_observed_at, recorded_at, len(prepared),
                    ),
                )
                outcome_id = cursor.lastrowid
                for rank, item in enumerate(prepared, start=1):
                    connection.execute(
                        """INSERT OR IGNORE INTO investing_radar_candidates
                           (normalized_keyword, display_keyword, first_seen_at, last_seen_at,
                            observation_count) VALUES (?, ?, ?, ?, 0)""",
                        (
                            item["normalized_keyword"], item["keyword"], market_observed_at,
                            market_observed_at,
                        ),
                    )
                    candidate_row = connection.execute(
                        """SELECT id FROM investing_radar_candidates
                           WHERE normalized_keyword = ?""",
                        (item["normalized_keyword"],),
                    ).fetchone()
                    candidate_id = candidate_row["id"]
                    connection.execute(
                        """UPDATE investing_radar_candidates
                           SET last_seen_at = ?, observation_count = observation_count + 1
                           WHERE id = ?""",
                        (market_observed_at, candidate_id),
                    )
                    connection.execute(
                        """INSERT INTO investing_radar_candidate_observations
                           (sweep_id, market_outcome_id, candidate_id, country, market_rank,
                            keyword, observed_at, source, source_run_id, source_observation_id,
                            source_record_count, search_volume, growth_pct, started_hours_ago,
                            source_started_at, related_terms_json, topic_ids_json,
                            categories_json, provenance_json, raw_payload_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            str(sweep_id), outcome_id, candidate_id, code, rank,
                            item["keyword"], item["observed_at"], item["source"],
                            item["source_run_id"], item["source_observation_id"],
                            item["source_record_count"], item["search_volume"],
                            item["growth_pct"], item["started_hours_ago"],
                            item["source_started_at"], _json(item["related_terms"]),
                            _json(item["topic_ids"]), _json(item["categories"]),
                            _json(item["provenance"]), _json(item["raw_payload"]),
                        ),
                    )
                success_column = (
                    "complete_markets" if status == "complete" else "empty_markets"
                )
                connection.execute(
                    f"""UPDATE investing_radar_sweeps
                        SET recorded_markets = recorded_markets + 1,
                            {success_column} = {success_column} + 1,
                            candidate_observations = candidate_observations + ?,
                            unique_candidates = (
                                SELECT COUNT(DISTINCT candidate_id)
                                FROM investing_radar_candidate_observations
                                WHERE sweep_id = ?
                            )
                        WHERE id = ?""",
                    (len(prepared), str(sweep_id), str(sweep_id)),
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise RadarConflictError(
                    f"market {code!r} already has an outcome in sweep {sweep_id!r}"
                ) from exc
            raise
        return self._get_market_outcome(str(sweep_id), code)

    def record_market_failure(
        self,
        sweep_id: str,
        country: str,
        error_category: str,
        *,
        observed_at: datetime | str | None = None,
        country_name: str | None = None,
    ) -> dict[str, Any]:
        code = _country(country)
        error = str(error_category or "").strip()
        if not error:
            raise RadarValidationError("error_category is required for a failed market")
        timestamp = _utc_iso(observed_at) if observed_at is not None else self._now()
        try:
            with self._transaction() as connection:
                self._require_running(connection, sweep_id)
                connection.execute(
                    """INSERT INTO investing_radar_market_outcomes
                       (sweep_id, country, country_name, status, observed_at, recorded_at,
                        candidate_count, error_category)
                       VALUES (?, ?, ?, 'failed', ?, ?, 0, ?)""",
                    (
                        str(sweep_id), code,
                        str(country_name).strip() if country_name else None,
                        timestamp, self._now(), error,
                    ),
                )
                connection.execute(
                    """UPDATE investing_radar_sweeps
                       SET recorded_markets = recorded_markets + 1,
                           failed_markets = failed_markets + 1
                       WHERE id = ?""",
                    (str(sweep_id),),
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise RadarConflictError(
                    f"market {code!r} already has an outcome in sweep {sweep_id!r}"
                ) from exc
            raise
        return self._get_market_outcome(str(sweep_id), code)

    def finalize_sweep(
        self,
        sweep_id: str,
        *,
        completed_at: datetime | str | None = None,
    ) -> dict[str, Any]:
        timestamp = _utc_iso(completed_at) if completed_at is not None else self._now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM investing_radar_sweeps WHERE id = ?", (str(sweep_id),)
            ).fetchone()
            if row is None:
                raise RadarNotFoundError(f"sweep {sweep_id!r} was not found")
            if row["status"] == "running":
                counts = {
                    status: count
                    for status, count in connection.execute(
                        """SELECT status, COUNT(*) FROM investing_radar_market_outcomes
                           WHERE sweep_id = ? GROUP BY status""",
                        (str(sweep_id),),
                    ).fetchall()
                }
                complete = counts.get("complete", 0)
                empty = counts.get("empty", 0)
                failed = counts.get("failed", 0)
                recorded = complete + empty + failed
                total = row["total_markets"]
                if total == 0:
                    status = "complete"
                elif recorded == 0 or (recorded == total and failed == total):
                    status = "failed"
                elif recorded < total or failed:
                    status = "partial"
                else:
                    status = "complete"
                observation_count, unique_count = connection.execute(
                    """SELECT COUNT(*), COUNT(DISTINCT candidate_id)
                       FROM investing_radar_candidate_observations WHERE sweep_id = ?""",
                    (str(sweep_id),),
                ).fetchone()
                connection.execute(
                    """UPDATE investing_radar_sweeps
                       SET completed_at = ?, status = ?, recorded_markets = ?,
                           complete_markets = ?, empty_markets = ?, failed_markets = ?,
                           candidate_observations = ?, unique_candidates = ?
                       WHERE id = ?""",
                    (
                        timestamp, status, recorded, complete, empty, failed,
                        observation_count, unique_count, str(sweep_id),
                    ),
                )
        result = self.get_sweep(str(sweep_id))
        assert result is not None
        return result

    @staticmethod
    def _market(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def _get_market_outcome(self, sweep_id: str, country: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT * FROM investing_radar_market_outcomes
                   WHERE sweep_id = ? AND country = ?""",
                (sweep_id, country),
            ).fetchone()
        if row is None:
            raise RadarNotFoundError("market outcome was not found after recording")
        return self._market(row)

    def get_sweep(self, sweep_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM investing_radar_sweeps WHERE id = ?", (str(sweep_id),)
            ).fetchone()
            if row is None:
                return None
            markets = connection.execute(
                """SELECT * FROM investing_radar_market_outcomes
                   WHERE sweep_id = ? ORDER BY id""",
                (str(sweep_id),),
            ).fetchall()
        result = dict(row)
        result["markets"] = [self._market(market) for market in markets]
        result["unattempted_markets"] = max(
            0, result["total_markets"] - len(result["markets"])
        )
        return result

    def latest_sweep(self) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT id FROM investing_radar_sweeps ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        return self.get_sweep(row["id"]) if row else None

    def latest_data_sweep(self) -> dict[str, Any] | None:
        """Latest completed sweep that actually supplies displayed observations."""
        with self._connection() as connection:
            row = connection.execute(
                """SELECT id FROM investing_radar_sweeps
                   WHERE completed_at IS NOT NULL AND candidate_observations > 0
                   ORDER BY rowid DESC LIMIT 1"""
            ).fetchone()
        return self.get_sweep(row["id"]) if row else None

    @staticmethod
    def _observation(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key in ("related_terms", "topic_ids", "categories", "provenance", "raw_payload"):
            item[key] = json.loads(item.pop(f"{key}_json"))
        return item

    @staticmethod
    def _consensus(observations: Sequence[dict[str, Any]], field: str) -> Any:
        values = [observation[field] for observation in observations]
        if not values:
            return None
        first = values[0]
        return first if all(value == first for value in values[1:]) else None

    @classmethod
    def _radar_item(
        cls,
        candidate: Mapping[str, Any],
        observations: list[dict[str, Any]],
        sweep_id: str,
    ) -> dict[str, Any]:
        categories = _unique([
            category for observation in observations for category in observation["categories"]
        ])
        related_terms = _unique([
            term for observation in observations for term in observation["related_terms"]
        ])
        sources = _unique([
            observation["source"] for observation in observations if observation["source"]
        ])
        metric_fields = (
            "search_volume", "growth_pct", "started_hours_ago", "source_started_at",
        )
        metric_conflicts = [
            field for field in metric_fields
            if len({json.dumps(observation[field]) for observation in observations}) > 1
        ]
        result = dict(candidate)
        result.update({
            "candidate_id": result["id"],
            "keyword": result["display_keyword"],
            "sweep_id": sweep_id,
            "countries": sorted({observation["country"] for observation in observations}),
            "country_count": len({observation["country"] for observation in observations}),
            "categories": categories,
            "related_terms": related_terms,
            "sources": sources,
            "metric_conflicts": metric_conflicts,
            "observed_at": max(observation["observed_at"] for observation in observations),
            "observations": observations,
        })
        for field in metric_fields:
            result[field] = cls._consensus(observations, field)
        return result

    def list_radar(
        self,
        limit: int = 100,
        country: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, numbers.Integral) or int(limit) < 1:
            raise RadarValidationError("limit must be a positive integer")
        country_filter = _country(country) if country else None
        category_filter = str(category).strip().casefold() if category else None
        with self._connection() as connection:
            sweep = connection.execute(
                """SELECT id FROM investing_radar_sweeps
                   WHERE completed_at IS NOT NULL AND candidate_observations > 0
                   ORDER BY rowid DESC LIMIT 1"""
            ).fetchone()
            if sweep is None:
                return []
            params: list[Any] = [sweep["id"]]
            country_clause = ""
            if country_filter:
                country_clause = " AND o.country = ?"
                params.append(country_filter)
            rows = connection.execute(
                f"""SELECT o.*, c.normalized_keyword, c.display_keyword,
                           c.first_seen_at, c.last_seen_at, c.observation_count
                    FROM investing_radar_candidate_observations o
                    JOIN investing_radar_candidates c ON c.id = o.candidate_id
                    WHERE o.sweep_id = ?{country_clause}
                    ORDER BY o.candidate_id, o.id""",
                params,
            ).fetchall()
        grouped: dict[int, list[dict[str, Any]]] = {}
        candidates: dict[int, dict[str, Any]] = {}
        for row in rows:
            candidate_id = row["candidate_id"]
            observation = self._observation(row)
            for key in (
                "normalized_keyword", "display_keyword", "first_seen_at",
                "last_seen_at", "observation_count",
            ):
                observation.pop(key, None)
            grouped.setdefault(candidate_id, []).append(observation)
            candidates[candidate_id] = {
                "id": candidate_id,
                "normalized_keyword": row["normalized_keyword"],
                "display_keyword": row["display_keyword"],
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
                "observation_count": row["observation_count"],
            }
        items: list[dict[str, Any]] = []
        for candidate_id, observations in grouped.items():
            if category_filter and not any(
                str(value).casefold() == category_filter
                for observation in observations for value in observation["categories"]
            ):
                continue
            items.append(self._radar_item(candidates[candidate_id], observations, sweep["id"]))
        items.sort(key=lambda item: (
            -item["country_count"],
            min(observation["market_rank"] for observation in item["observations"]),
            item["normalized_keyword"],
        ))
        if category_filter:
            return items[: int(limit)]

        # The source feed is often dominated by one category (especially
        # sports). Keep every item queryable by category, but interleave the
        # unfiltered investing Radar so one bucket cannot monopolize the page.
        buckets: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            bucket = str((item.get("categories") or ["Other"])[0])
            buckets.setdefault(bucket, []).append(item)
        diversified: list[dict[str, Any]] = []
        priority = {name: index for index, name in enumerate(_INVESTING_CATEGORY_ORDER)}
        bucket_names = sorted(
            buckets,
            key=lambda name: (priority.get(name, len(priority)), name.casefold()),
        )
        index = 0
        while len(diversified) < int(limit) and bucket_names:
            name = bucket_names[index % len(bucket_names)]
            values = buckets[name]
            if values:
                diversified.append(values.pop(0))
            if not values:
                bucket_names.remove(name)
                if not bucket_names:
                    break
                index %= len(bucket_names)
            else:
                index += 1
        return diversified

    def get_candidate(self, candidate_id: int | str) -> dict[str, Any] | None:
        try:
            identifier = int(candidate_id)
        except (TypeError, ValueError):
            return None
        data_sweep = self.latest_data_sweep()
        if not data_sweep:
            return None
        with self._connection() as connection:
            candidate = connection.execute(
                "SELECT * FROM investing_radar_candidates WHERE id = ?", (identifier,)
            ).fetchone()
            if candidate is None:
                return None
            rows = connection.execute(
                """SELECT o.*, m.status AS market_status, m.country_name,
                          s.status AS sweep_status, s.completed_at AS sweep_completed_at
                   FROM investing_radar_candidate_observations o
                   JOIN investing_radar_market_outcomes m ON m.id = o.market_outcome_id
                   JOIN investing_radar_sweeps s ON s.id = o.sweep_id
                   WHERE o.candidate_id = ? AND o.sweep_id = ?
                   ORDER BY o.id DESC""",
                (identifier, data_sweep["id"]),
            ).fetchall()
        if not rows:
            return None
        observations = [self._observation(row) for row in rows]
        result = dict(candidate)
        result.update({
            "candidate_id": result["id"],
            "keyword": result["display_keyword"],
            "countries": sorted({observation["country"] for observation in observations}),
            "categories": _unique([
                category for observation in observations for category in observation["categories"]
            ]),
            "related_terms": _unique([
                term for observation in observations for term in observation["related_terms"]
            ]),
            "observations": observations,
        })
        return result
