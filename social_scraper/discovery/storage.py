"""Persistent, gap-preserving Google Trends candidate history."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .budgets import StageUsage


_ALLOWED_RUN_STATUS = {"complete", "partial", "error"}
_ALLOWED_GATE_STATUS = {"not_checked", "complete", "empty", "partial", "failed"}


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().casefold().split())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _array(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return list(value)


def _merge_run_candidates(candidates: Iterable[dict]) -> list[dict]:
    """One series observation per run, retaining duplicate source records."""
    merged: dict[str, dict] = {}
    scalar_fields = ("search_volume", "growth_pct", "source_started_at")
    array_fields = ("related_terms", "topic_ids", "categories")
    for raw_candidate in candidates:
        candidate = dict(raw_candidate)
        keyword = str(candidate.get("keyword") or "").strip()
        if not keyword:
            continue
        normalized = _key(keyword)
        if normalized not in merged:
            candidate["source_records"] = [dict(raw_candidate)]
            candidate["source_record_count"] = 1
            candidate["metric_conflicts"] = []
            merged[normalized] = candidate
            continue
        current = merged[normalized]
        current["source_records"].append(dict(raw_candidate))
        current["source_record_count"] += 1
        for field in scalar_fields:
            left = current.get(field)
            right = candidate.get(field)
            if left != right:
                current[field] = None
                if field not in current["metric_conflicts"]:
                    current["metric_conflicts"].append(field)
        for field in array_fields:
            current[field] = list(dict.fromkeys(
                _array(current.get(field)) + _array(candidate.get(field))
            ))
    return list(merged.values())


class DiscoveryStore:
    """SQLite store for immutable Discovery observations and explicit gaps."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discovery_runs (
                    id TEXT PRIMARY KEY,
                    geo TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('complete','partial','error')),
                    comparable INTEGER NOT NULL CHECK(comparable IN (0,1)),
                    candidate_count INTEGER NOT NULL,
                    error_category TEXT,
                    source_health_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS discovery_candidate_series (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    geo TEXT NOT NULL,
                    normalized_keyword TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    consecutive_observations INTEGER NOT NULL DEFAULT 1,
                    total_observations INTEGER NOT NULL DEFAULT 0,
                    presence_status TEXT NOT NULL DEFAULT 'present'
                        CHECK(presence_status IN ('present','missing','unknown')),
                    UNIQUE(geo, normalized_keyword)
                );
                CREATE TABLE IF NOT EXISTS discovery_candidate_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    discovery_run_id TEXT NOT NULL REFERENCES discovery_runs(id),
                    candidate_series_id INTEGER NOT NULL REFERENCES discovery_candidate_series(id),
                    observed_at TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    search_volume INTEGER,
                    growth_pct REAL,
                    source_started_at TEXT,
                    related_terms_json TEXT NOT NULL,
                    topic_ids_json TEXT NOT NULL,
                    categories_json TEXT NOT NULL,
                    raw_payload_hash TEXT NOT NULL,
                    raw_payload_json TEXT NOT NULL,
                    UNIQUE(discovery_run_id, candidate_series_id)
                );
                CREATE TABLE IF NOT EXISTS discovery_candidate_gaps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_series_id INTEGER NOT NULL REFERENCES discovery_candidate_series(id),
                    started_run_id TEXT NOT NULL REFERENCES discovery_runs(id),
                    started_at TEXT NOT NULL,
                    missed_comparable_runs INTEGER NOT NULL DEFAULT 1,
                    ended_run_id TEXT REFERENCES discovery_runs(id),
                    ended_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_discovery_open_gap
                    ON discovery_candidate_gaps(candidate_series_id)
                    WHERE ended_at IS NULL;
                CREATE INDEX IF NOT EXISTS idx_discovery_observations_series_time
                    ON discovery_candidate_observations(candidate_series_id, observed_at, id);
                CREATE INDEX IF NOT EXISTS idx_discovery_runs_geo_time
                    ON discovery_runs(geo, observed_at, id);
                CREATE TABLE IF NOT EXISTS discovery_stage_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    discovery_run_id TEXT NOT NULL REFERENCES discovery_runs(id),
                    stage TEXT NOT NULL CHECK(stage IN (
                        'observed','screening','root_probe','deep_read',
                        'horizontal_extraction','lens_evaluation','optional_enrichment'
                    )),
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    duration_seconds REAL NOT NULL CHECK(duration_seconds >= 0),
                    candidates_considered INTEGER NOT NULL CHECK(candidates_considered >= 0),
                    candidates_processed INTEGER NOT NULL CHECK(candidates_processed >= 0),
                    records_returned INTEGER NOT NULL CHECK(records_returned >= 0),
                    external_calls INTEGER NOT NULL CHECK(external_calls >= 0),
                    llm_calls INTEGER NOT NULL CHECK(llm_calls >= 0),
                    cache_hits INTEGER NOT NULL CHECK(cache_hits >= 0),
                    status TEXT NOT NULL CHECK(status IN (
                        'not_checked','complete','empty','partial','unavailable',
                        'failed','skipped'
                    )),
                    error_category TEXT,
                    input_tokens INTEGER CHECK(input_tokens IS NULL OR input_tokens >= 0),
                    output_tokens INTEGER CHECK(output_tokens IS NULL OR output_tokens >= 0),
                    tokens_estimated INTEGER NOT NULL DEFAULT 0
                        CHECK(tokens_estimated IN (0,1))
                );
                CREATE INDEX IF NOT EXISTS idx_discovery_stage_usage_run
                    ON discovery_stage_usage(discovery_run_id, id);
                CREATE TABLE IF NOT EXISTS discovery_gate_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_observation_id INTEGER NOT NULL
                        REFERENCES discovery_candidate_observations(id),
                    checked_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('not_checked','complete','empty','partial','failed')),
                    passed INTEGER CHECK(passed IS NULL OR passed IN (0,1)),
                    platforms_json TEXT NOT NULL DEFAULT '[]',
                    total_items INTEGER,
                    independent_voices INTEGER,
                    source_health_json TEXT NOT NULL DEFAULT '[]',
                    records_json TEXT NOT NULL DEFAULT '[]',
                    analysis_json TEXT,
                    error_category TEXT
                );
                CREATE TABLE IF NOT EXISTS discovery_lens_evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_observation_id INTEGER NOT NULL
                        REFERENCES discovery_candidate_observations(id),
                    lens_id TEXT NOT NULL,
                    lens_version TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    score REAL,
                    score_coverage REAL NOT NULL,
                    spec_json TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    result_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_discovery_lens_candidate
                    ON discovery_lens_evaluations(
                        candidate_observation_id, lens_id, lens_version, evaluated_at
                    );
                CREATE TABLE IF NOT EXISTS research_runs (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    source_discovery_run_id TEXT REFERENCES discovery_runs(id),
                    status TEXT NOT NULL CHECK(status IN
                        ('planned','running','complete','partial','error','cancelled')),
                    requested_budget_json TEXT NOT NULL,
                    effective_budget_json TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    error_category TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_research_runs_workspace_created
                    ON research_runs(workspace_id, created_at, id);
                CREATE TABLE IF NOT EXISTS research_run_candidates (
                    research_run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                    candidate_id TEXT NOT NULL,
                    candidate_json TEXT NOT NULL,
                    priority_components_json TEXT NOT NULL,
                    stages_json TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    manual_promoted INTEGER NOT NULL DEFAULT 0 CHECK(manual_promoted IN (0,1)),
                    PRIMARY KEY(research_run_id, candidate_id)
                );
                CREATE TABLE IF NOT EXISTS candidate_stage_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    research_run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                    candidate_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    transitioned_at TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(research_run_id, candidate_id)
                        REFERENCES research_run_candidates(research_run_id, candidate_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_candidate_stage_history_lookup
                    ON candidate_stage_history(research_run_id, candidate_id, id);
                CREATE TABLE IF NOT EXISTS evidence_bundles (
                    id TEXT PRIMARY KEY,
                    subject_key TEXT,
                    evidence_hash TEXT NOT NULL,
                    normalizer_version TEXT NOT NULL,
                    coverage_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(evidence_hash, normalizer_version, coverage_hash)
                );
                CREATE TABLE IF NOT EXISTS evidence_bundle_members (
                    bundle_id TEXT NOT NULL REFERENCES evidence_bundles(id) ON DELETE CASCADE,
                    member_key TEXT NOT NULL,
                    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
                    content_hash TEXT NOT NULL,
                    PRIMARY KEY(bundle_id, ordinal)
                );
                CREATE INDEX IF NOT EXISTS idx_evidence_bundle_members_key
                    ON evidence_bundle_members(member_key, content_hash);
                CREATE TABLE IF NOT EXISTS horizontal_extractions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    evidence_bundle_id TEXT NOT NULL REFERENCES evidence_bundles(id),
                    extraction_schema_version TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    cache_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    input_records INTEGER NOT NULL CHECK(input_records >= 0),
                    input_tokens INTEGER CHECK(input_tokens IS NULL OR input_tokens >= 0),
                    output_tokens INTEGER CHECK(output_tokens IS NULL OR output_tokens >= 0),
                    tokens_estimated INTEGER NOT NULL DEFAULT 0 CHECK(tokens_estimated IN (0,1)),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS optional_interpretations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    horizontal_extraction_id INTEGER NOT NULL
                        REFERENCES horizontal_extractions(id) ON DELETE CASCADE,
                    interpretation_type TEXT NOT NULL,
                    interpretation_version TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    cache_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    input_records INTEGER NOT NULL CHECK(input_records >= 0),
                    input_tokens INTEGER CHECK(input_tokens IS NULL OR input_tokens >= 0),
                    output_tokens INTEGER CHECK(output_tokens IS NULL OR output_tokens >= 0),
                    tokens_estimated INTEGER NOT NULL DEFAULT 0 CHECK(tokens_estimated IN (0,1)),
                    created_at TEXT NOT NULL
                );
                """
            )
            gate_columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(discovery_gate_checks)"
                )
            }
            if "records_json" not in gate_columns:
                connection.execute(
                    "ALTER TABLE discovery_gate_checks "
                    "ADD COLUMN records_json TEXT NOT NULL DEFAULT '[]'"
                )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(name, applied_at) VALUES (?, ?)",
                ("2026_08_10_phase1b_discovery_history", datetime.now(timezone.utc).isoformat()),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(name, applied_at) VALUES (?, ?)",
                ("2026_08_10_discovery_stage_usage", datetime.now(timezone.utc).isoformat()),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(name, applied_at) VALUES (?, ?)",
                ("2026_08_10_phase2_staged_research", datetime.now(timezone.utc).isoformat()),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(name, applied_at) VALUES (?, ?)",
                ("2026_08_10_shared_evidence_cache", datetime.now(timezone.utc).isoformat()),
            )

    @staticmethod
    def _cached_row(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        item["result"] = json.loads(item.pop("result_json"))
        item["tokens_estimated"] = bool(item["tokens_estimated"])
        return item

    def create_evidence_bundle(self, bundle: Any, *, subject_key: str | None = None) -> dict:
        """Persist or return one immutable evidence/coverage bundle."""
        bundle_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO evidence_bundles
                   (id, subject_key, evidence_hash, normalizer_version,
                    coverage_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
                (bundle_id, subject_key, bundle.evidence_hash,
                 bundle.normalizer_version, bundle.coverage_hash, created_at),
            )
            row = connection.execute(
                """SELECT * FROM evidence_bundles
                   WHERE evidence_hash = ? AND normalizer_version = ?
                     AND coverage_hash = ?""",
                (bundle.evidence_hash, bundle.normalizer_version, bundle.coverage_hash),
            ).fetchone()
            if row is None:  # pragma: no cover - defensive against SQLite failure
                raise RuntimeError("failed to persist evidence bundle")
            for member in bundle.members:
                connection.execute(
                    """INSERT OR IGNORE INTO evidence_bundle_members
                       (bundle_id, member_key, ordinal, content_hash)
                       VALUES (?, ?, ?, ?)""",
                    (row["id"], member.member_key, member.ordinal, member.content_hash),
                )
            item = dict(row)
            item["members"] = [
                dict(member) for member in connection.execute(
                    """SELECT member_key, ordinal, content_hash
                       FROM evidence_bundle_members WHERE bundle_id = ? ORDER BY ordinal""",
                    (row["id"],),
                ).fetchall()
            ]
            return item

    def get_evidence_bundle(
        self, identifier: str, *, normalizer_version: str | None = None,
        coverage_hash: str | None = None,
    ) -> dict | None:
        with self._connect() as connection:
            if normalizer_version is None and coverage_hash is None:
                row = connection.execute(
                    "SELECT * FROM evidence_bundles WHERE id = ?", (identifier,),
                ).fetchone()
            elif normalizer_version is not None and coverage_hash is not None:
                row = connection.execute(
                    """SELECT * FROM evidence_bundles WHERE evidence_hash = ?
                       AND normalizer_version = ? AND coverage_hash = ?""",
                    (identifier, normalizer_version, coverage_hash),
                ).fetchone()
            else:
                raise TypeError("normalizer_version and coverage_hash must be supplied together")
            if row is None:
                return None
            item = dict(row)
            item["members"] = [dict(member) for member in connection.execute(
                """SELECT member_key, ordinal, content_hash FROM evidence_bundle_members
                   WHERE bundle_id = ? ORDER BY ordinal""", (row["id"],),
            ).fetchall()]
            return item

    def get_horizontal_extraction(self, cache_key: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM horizontal_extractions WHERE cache_key = ?", (cache_key,),
            ).fetchone()
        return self._cached_row(row)

    def put_horizontal_extraction(
        self, *, evidence_bundle_id: str, extraction_schema_version: str,
        prompt_version: str, provider: str, model: str, cache_key: str,
        status: str, result: Any, input_records: int,
        input_tokens: int | None = None, output_tokens: int | None = None,
        tokens_estimated: bool = False,
    ) -> dict:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO horizontal_extractions
                   (evidence_bundle_id, extraction_schema_version, prompt_version,
                    provider, model, cache_key, status, result_json, input_records,
                    input_tokens, output_tokens, tokens_estimated, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (evidence_bundle_id, extraction_schema_version, prompt_version,
                 provider, model, cache_key, status, _json(result), input_records,
                 input_tokens, output_tokens, int(tokens_estimated),
                 datetime.now(timezone.utc).isoformat()),
            )
            row = connection.execute(
                "SELECT * FROM horizontal_extractions WHERE cache_key = ?", (cache_key,),
            ).fetchone()
        result_row = self._cached_row(row)
        if result_row is None:  # pragma: no cover
            raise RuntimeError("failed to persist horizontal extraction")
        return result_row

    def get_optional_interpretation(self, cache_key: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM optional_interpretations WHERE cache_key = ?", (cache_key,),
            ).fetchone()
        return self._cached_row(row)

    def put_optional_interpretation(
        self, *, horizontal_extraction_id: int, interpretation_type: str,
        interpretation_version: str, config: Mapping[str, Any], provider: str,
        model: str, status: str, result: Any, input_records: int,
        input_tokens: int | None = None, output_tokens: int | None = None,
        tokens_estimated: bool = False, cache_key: str | None = None,
    ) -> dict:
        config_json = _json(config)
        config_hash = hashlib.sha256(config_json.encode("utf-8")).hexdigest()
        if cache_key is None:
            material = _json([
                "optional_interpretation", horizontal_extraction_id,
                interpretation_type, interpretation_version, config_hash,
                provider.casefold(), model,
            ])
            cache_key = hashlib.sha256(material.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO optional_interpretations
                   (horizontal_extraction_id, interpretation_type,
                    interpretation_version, config_hash, provider, model, cache_key,
                    status, result_json, input_records, input_tokens, output_tokens,
                    tokens_estimated, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (horizontal_extraction_id, interpretation_type, interpretation_version,
                 config_hash, provider, model, cache_key, status, _json(result),
                 input_records, input_tokens, output_tokens, int(tokens_estimated),
                 datetime.now(timezone.utc).isoformat()),
            )
            row = connection.execute(
                "SELECT * FROM optional_interpretations WHERE cache_key = ?", (cache_key,),
            ).fetchone()
        result_row = self._cached_row(row)
        if result_row is None:  # pragma: no cover
            raise RuntimeError("failed to persist optional interpretation")
        return result_row

    def record_feed(
        self,
        *,
        geo: str,
        observed_at: datetime | str,
        candidates: Iterable[dict],
        status: str = "complete",
        comparable: bool = True,
        error_category: str | None = None,
        source_health: list[dict] | None = None,
        run_id: str | None = None,
    ) -> str:
        if status not in _ALLOWED_RUN_STATUS:
            raise ValueError(f"invalid discovery status: {status}")
        if status != "complete" and comparable:
            comparable = False
        observed = _iso(observed_at)
        rows = _merge_run_candidates(candidates)
        run_id = run_id or str(uuid.uuid4())
        seen_series: set[int] = set()

        with self._connect() as connection:
            connection.execute(
                """INSERT INTO discovery_runs
                   (id, geo, observed_at, completed_at, status, comparable,
                    candidate_count, error_category, source_health_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, geo.upper(), observed, datetime.now(timezone.utc).isoformat(),
                    status, int(comparable), len(rows), error_category,
                    _json(source_health or []),
                ),
            )
            for candidate in rows:
                keyword = str(candidate.get("keyword") or "").strip()
                if not keyword:
                    continue
                normalized = _key(keyword)
                series = connection.execute(
                    """SELECT * FROM discovery_candidate_series
                       WHERE geo = ? AND normalized_keyword = ?""",
                    (geo.upper(), normalized),
                ).fetchone()
                if series is None:
                    cursor = connection.execute(
                        """INSERT INTO discovery_candidate_series
                           (geo, normalized_keyword, first_seen_at, last_seen_at,
                            consecutive_observations, total_observations, presence_status)
                           VALUES (?, ?, ?, ?, 1, 0, 'present')""",
                        (geo.upper(), normalized, observed, observed),
                    )
                    series_id = cursor.lastrowid
                    consecutive = 1
                else:
                    series_id = int(series["id"])
                    consecutive = (
                        int(series["consecutive_observations"]) + 1
                        if series["presence_status"] == "present" else 1
                    )
                seen_series.add(series_id)
                payload = dict(candidate)
                categories = _array(payload.get("categories"))
                related = _array(payload.get("related_terms"))
                topics = _array(payload.get("topic_ids"))
                raw = _json(payload)
                connection.execute(
                    """INSERT INTO discovery_candidate_observations
                       (discovery_run_id, candidate_series_id, observed_at, keyword,
                        search_volume, growth_pct, source_started_at,
                        related_terms_json, topic_ids_json, categories_json,
                        raw_payload_hash, raw_payload_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id, series_id, observed, keyword,
                        payload.get("search_volume"), payload.get("growth_pct"),
                        _iso(payload.get("source_started_at")), _json(related),
                        _json(topics), _json(categories),
                        hashlib.sha256(raw.encode("utf-8")).hexdigest(), raw,
                    ),
                )
                connection.execute(
                    """UPDATE discovery_candidate_series
                       SET last_seen_at = ?, consecutive_observations = ?,
                           total_observations = total_observations + 1,
                           presence_status = 'present'
                       WHERE id = ?""",
                    (observed, consecutive, series_id),
                )
                connection.execute(
                    """UPDATE discovery_candidate_gaps
                       SET ended_run_id = ?, ended_at = ?
                       WHERE candidate_series_id = ? AND ended_at IS NULL""",
                    (run_id, observed, series_id),
                )

            if comparable:
                all_series = connection.execute(
                    "SELECT id, presence_status FROM discovery_candidate_series WHERE geo = ?",
                    (geo.upper(),),
                ).fetchall()
                for series in all_series:
                    series_id = int(series["id"])
                    if series_id in seen_series:
                        continue
                    if series["presence_status"] == "present":
                        connection.execute(
                            """INSERT INTO discovery_candidate_gaps
                               (candidate_series_id, started_run_id, started_at,
                                missed_comparable_runs)
                               VALUES (?, ?, ?, 1)""",
                            (series_id, run_id, observed),
                        )
                    else:
                        connection.execute(
                            """UPDATE discovery_candidate_gaps
                               SET missed_comparable_runs = missed_comparable_runs + 1
                               WHERE candidate_series_id = ? AND ended_at IS NULL""",
                            (series_id,),
                        )
                    connection.execute(
                        """UPDATE discovery_candidate_series
                           SET presence_status = 'missing', consecutive_observations = 0
                           WHERE id = ?""",
                        (series_id,),
                    )
        return run_id

    def discovery_run_exists(self, run_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM discovery_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return row is not None

    def get_discovery_run(self, run_id: str) -> dict | None:
        """Return persisted run truth, including explicit failure/comparability state."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM discovery_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["comparable"] = bool(item["comparable"])
        item["source_health"] = json.loads(item.pop("source_health_json") or "[]")
        return item

    def record_stage_usage(
        self,
        usage: StageUsage | Mapping[str, Any] | str | None = None,
        **fields: Any,
    ) -> int:
        """Persist one validated usage receipt and return its row ID.

        A receipt model/mapping is preferred; accepting a run ID plus keyword fields
        keeps the method consistent with the store's other ``record_*`` helpers.
        """
        if isinstance(usage, str):
            fields["discovery_run_id"] = usage
            usage = fields
        elif usage is None:
            usage = fields
        elif fields:
            raise TypeError("stage usage fields cannot accompany a receipt object")
        if not isinstance(usage, StageUsage):
            usage = StageUsage.from_dict(usage)
        if not self.discovery_run_exists(usage.discovery_run_id):
            raise ValueError(f"unknown discovery run: {usage.discovery_run_id}")
        row = usage.to_dict()
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO discovery_stage_usage
                   (discovery_run_id, stage, started_at, completed_at, duration_seconds,
                    candidates_considered, candidates_processed, records_returned,
                    external_calls, llm_calls, cache_hits, status, error_category,
                    input_tokens, output_tokens, tokens_estimated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["discovery_run_id"], row["stage"], row["started_at"],
                    row["completed_at"], row["duration_seconds"],
                    row["candidates_considered"], row["candidates_processed"],
                    row["records_returned"], row["external_calls"], row["llm_calls"],
                    row["cache_hits"], row["status"], row["error_category"],
                    row["input_tokens"], row["output_tokens"],
                    int(row["tokens_estimated"]),
                ),
            )
            return int(cursor.lastrowid)

    def list_stage_usage(self, run_id: str) -> list[dict]:
        """Return a run's receipts in insertion order without inventing missing usage."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM discovery_stage_usage
                   WHERE discovery_run_id = ? ORDER BY id""",
                (run_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["tokens_estimated"] = bool(item["tokens_estimated"])
            result.append(item)
        return result

    def list_run_candidates(self, run_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT o.*, s.normalized_keyword, s.first_seen_at, s.last_seen_at,
                          s.consecutive_observations, s.total_observations,
                          s.presence_status
                   FROM discovery_candidate_observations o
                   JOIN discovery_candidate_series s ON s.id = o.candidate_series_id
                   WHERE o.discovery_run_id = ? ORDER BY o.id""",
                (run_id,),
            ).fetchall()
        return [self._observation(row) for row in rows]

    def get_candidate_history(self, geo: str, keyword: str) -> dict:
        with self._connect() as connection:
            series = connection.execute(
                """SELECT * FROM discovery_candidate_series
                   WHERE geo = ? AND normalized_keyword = ?""",
                (geo.upper(), _key(keyword)),
            ).fetchone()
            if series is None:
                return {"series": None, "observations": [], "gaps": []}
            observations = connection.execute(
                """SELECT o.*, s.normalized_keyword, s.first_seen_at, s.last_seen_at,
                          s.consecutive_observations, s.total_observations,
                          s.presence_status
                   FROM discovery_candidate_observations o
                   JOIN discovery_candidate_series s ON s.id = o.candidate_series_id
                   WHERE o.candidate_series_id = ? ORDER BY o.observed_at, o.id""",
                (series["id"],),
            ).fetchall()
            gaps = connection.execute(
                """SELECT started_at, ended_at, missed_comparable_runs
                   FROM discovery_candidate_gaps
                   WHERE candidate_series_id = ? ORDER BY id""",
                (series["id"],),
            ).fetchall()
        return {
            "series": dict(series),
            "observations": [self._observation(row) for row in observations],
            "gaps": [dict(row) for row in gaps],
        }

    def record_gate_check(
        self,
        candidate_observation_id: int,
        *,
        status: str,
        passed: bool | None,
        platforms: list[str] | None = None,
        total_items: int | None = None,
        independent_voices: int | None = None,
        source_health: list[dict] | None = None,
        records: list[dict] | None = None,
        analysis: dict | None = None,
        error_category: str | None = None,
        checked_at: datetime | str | None = None,
    ) -> int:
        if status not in _ALLOWED_GATE_STATUS:
            raise ValueError(f"invalid gate status: {status}")
        if status in {"not_checked", "partial", "failed"} and passed is not None:
            raise ValueError(f"passed must be null for {status}")
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO discovery_gate_checks
                   (candidate_observation_id, checked_at, status, passed,
                    platforms_json, total_items, independent_voices,
                    source_health_json, records_json, analysis_json, error_category)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    candidate_observation_id,
                    _iso(checked_at or datetime.now(timezone.utc)), status,
                    None if passed is None else int(passed), _json(platforms or []),
                    total_items, independent_voices, _json(source_health or []),
                    _json(records or []),
                    _json(analysis) if analysis is not None else None, error_category,
                ),
            )
            return int(cursor.lastrowid)

    def list_gate_checks(self, candidate_observation_id: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM discovery_gate_checks
                   WHERE candidate_observation_id = ? ORDER BY id""",
                (candidate_observation_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["passed"] = None if item["passed"] is None else bool(item["passed"])
            item["platforms"] = json.loads(item.pop("platforms_json"))
            item["source_health"] = json.loads(item.pop("source_health_json"))
            item["records"] = json.loads(item.pop("records_json"))
            item["analysis"] = json.loads(item["analysis_json"]) if item["analysis_json"] else None
            item.pop("analysis_json")
            result.append(item)
        return result

    def get_latest_candidate_context(self, geo: str, keyword: str) -> dict | None:
        """Return the latest observation and latest gate check, without filling gaps."""
        history = self.get_candidate_history(geo, keyword)
        if not history["observations"]:
            return None
        observation = history["observations"][-1]
        checks = self.list_gate_checks(observation["observation_id"])
        return {
            "series": history["series"],
            "observation": observation,
            "gate_check": checks[-1] if checks else None,
        }

    def record_lens_evaluation(
        self,
        candidate_observation_id: int,
        *,
        lens_id: str,
        lens_version: str,
        spec: dict,
        features: dict,
        result: dict,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO discovery_lens_evaluations
                   (candidate_observation_id, lens_id, lens_version, evaluated_at,
                    status, score, score_coverage, spec_json, features_json, result_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    candidate_observation_id, lens_id, lens_version,
                    str(result.get("evaluated_at") or datetime.now(timezone.utc).isoformat()),
                    str(result.get("status") or "error"), result.get("score"),
                    float(result.get("score_coverage") or 0.0), _json(spec),
                    _json(features), _json(result),
                ),
            )
            return int(cursor.lastrowid)

    def create_research_run(
        self, *, workspace_id: str, requested_budget: Mapping[str, Any],
        effective_budget: Mapping[str, Any], plan: Mapping[str, Any],
        source_discovery_run_id: str | None = None, status: str = "planned",
        run_id: str | None = None,
    ) -> dict:
        allowed = {"planned", "running", "complete", "partial", "error", "cancelled"}
        if status not in allowed:
            raise ValueError(f"invalid research run status: {status}")
        workspace_id = str(workspace_id).strip()
        if not workspace_id:
            raise ValueError("workspace_id must not be empty")
        run_id, now = run_id or str(uuid.uuid4()), datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_runs
                   (id, workspace_id, source_discovery_run_id, status,
                    requested_budget_json, effective_budget_json, plan_json,
                    created_at, started_at, completed_at, error_category)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                (run_id, workspace_id, source_discovery_run_id, status,
                 _json(requested_budget), _json(effective_budget), _json(plan), now,
                 now if status == "running" else None,
                 now if status in {"complete", "partial", "error", "cancelled"} else None),
            )
            for row in plan.get("candidates", []):
                candidate_id = str(row["candidate_id"])
                priority = row.get("priority_components", {})
                connection.execute(
                    """INSERT INTO research_run_candidates
                       (research_run_id, candidate_id, candidate_json,
                        priority_components_json, stages_json, outcome, manual_promoted)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, candidate_id, _json(row.get("candidate", {})), _json(priority),
                     _json(row.get("stages", {})), str(row.get("outcome", "complete")),
                     int(bool(priority.get("manual_promoted", False)))),
                )
                for stage, outcome in row.get("stages", {}).items():
                    connection.execute(
                        """INSERT INTO candidate_stage_history
                           (research_run_id, candidate_id, workspace_id, stage, outcome,
                            transitioned_at, details_json) VALUES (?, ?, ?, ?, ?, ?, '{}')""",
                        (run_id, candidate_id, workspace_id, stage, outcome, now),
                    )
        return self.get_research_run(run_id)  # type: ignore[return-value]

    @staticmethod
    def _research_run(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["requested_budget"] = json.loads(item.pop("requested_budget_json"))
        item["effective_budget"] = json.loads(item.pop("effective_budget_json"))
        item["plan"] = json.loads(item.pop("plan_json"))
        return item

    def get_research_run(self, run_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM research_runs WHERE id = ?", (run_id,)).fetchone()
        return self._research_run(row) if row else None

    def list_research_runs(self, workspace_id: str | None = None) -> list[dict]:
        with self._connect() as connection:
            if workspace_id is None:
                rows = connection.execute(
                    "SELECT * FROM research_runs ORDER BY created_at DESC, id DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM research_runs WHERE workspace_id = ?
                       ORDER BY created_at DESC, id DESC""", (workspace_id,),
                ).fetchall()
        return [self._research_run(row) for row in rows]

    def update_research_run(
        self, run_id: str, *, status: str, error_category: str | None = None,
    ) -> dict:
        allowed = {"planned", "running", "complete", "partial", "error", "cancelled"}
        if status not in allowed:
            raise ValueError(f"invalid research run status: {status}")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE research_runs SET status = ?, error_category = ?,
                   started_at = CASE WHEN ? = 'running' AND started_at IS NULL THEN ? ELSE started_at END,
                   completed_at = CASE WHEN ? IN ('complete','partial','error','cancelled')
                                       THEN ? ELSE completed_at END WHERE id = ?""",
                (status, error_category, status, now, status, now, run_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"unknown research run: {run_id}")
        return self.get_research_run(run_id)  # type: ignore[return-value]

    def list_research_run_candidates(self, run_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM research_run_candidates
                   WHERE research_run_id = ? ORDER BY rowid""", (run_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["candidate"] = json.loads(item.pop("candidate_json"))
            item["priority_components"] = json.loads(item.pop("priority_components_json"))
            item["stages"] = json.loads(item.pop("stages_json"))
            item["manual_promoted"] = bool(item["manual_promoted"])
            result.append(item)
        return result

    def record_stage_transition(
        self, run_id: str, candidate_id: str, *, stage: str, outcome: str,
        details: Mapping[str, Any] | None = None, transitioned_at: datetime | str | None = None,
    ) -> int:
        with self._connect() as connection:
            run = connection.execute(
                "SELECT workspace_id FROM research_runs WHERE id = ?", (run_id,),
            ).fetchone()
            candidate = connection.execute(
                """SELECT 1 FROM research_run_candidates
                   WHERE research_run_id = ? AND candidate_id = ?""", (run_id, candidate_id),
            ).fetchone()
            if run is None or candidate is None:
                raise ValueError("unknown research run candidate")
            cursor = connection.execute(
                """INSERT INTO candidate_stage_history
                   (research_run_id, candidate_id, workspace_id, stage, outcome,
                    transitioned_at, details_json) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, candidate_id, run["workspace_id"], stage, outcome,
                 _iso(transitioned_at or datetime.now(timezone.utc)), _json(details or {})),
            )
            return int(cursor.lastrowid)

    def list_stage_transitions(self, run_id: str, candidate_id: str | None = None) -> list[dict]:
        with self._connect() as connection:
            if candidate_id is None:
                rows = connection.execute(
                    """SELECT * FROM candidate_stage_history
                       WHERE research_run_id = ? ORDER BY id""", (run_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM candidate_stage_history
                       WHERE research_run_id = ? AND candidate_id = ? ORDER BY id""",
                    (run_id, candidate_id),
                ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            result.append(item)
        return result

    def promote_research_candidate(self, run_id: str, candidate_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT priority_components_json FROM research_run_candidates
                   WHERE research_run_id = ? AND candidate_id = ?""", (run_id, candidate_id),
            ).fetchone()
            if row is None:
                raise ValueError("unknown research run candidate")
            components = json.loads(row["priority_components_json"])
            components["manual_promoted"] = True
            connection.execute(
                """UPDATE research_run_candidates
                   SET manual_promoted = 1, priority_components_json = ?
                   WHERE research_run_id = ? AND candidate_id = ?""",
                (_json(components), run_id, candidate_id),
            )
        self.record_stage_transition(
            run_id, candidate_id, stage="screening", outcome="manual_promoted",
            details={"manual_promoted": True},
        )
        return next(row for row in self.list_research_run_candidates(run_id)
                    if row["candidate_id"] == candidate_id)

    @staticmethod
    def _observation(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["observation_id"] = item.pop("id")
        item["related_terms"] = json.loads(item.pop("related_terms_json"))
        item["topic_ids"] = json.loads(item.pop("topic_ids_json"))
        item["categories"] = json.loads(item.pop("categories_json"))
        item["raw_payload"] = json.loads(item.pop("raw_payload_json"))
        return item
