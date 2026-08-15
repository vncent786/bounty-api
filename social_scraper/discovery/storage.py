"""Persistent, gap-preserving Google Trends candidate history."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .budgets import StageUsage
from .scan_modes import FEED_MODES, coerce_scan_mode
from .topic_families import EDGE_KINDS, RELATIONSHIPS, candidate_key


_ALLOWED_RUN_STATUS = {"complete", "partial", "error"}
_ALLOWED_GATE_STATUS = {"not_checked", "complete", "empty", "partial", "failed"}
_RADAR_SCOPE_TYPES = frozenset({"geography", "subject"})
_RADAR_ATTEMPT_STATUS = frozenset({"complete", "partial", "error"})


def _utc_parse(value: datetime | str, *, label: str = "timestamp") -> datetime:
    """Parse an injected clock value into an aware UTC datetime.

    Aware datetimes are converted; naive datetimes are read as UTC. Strings
    must be ISO-8601 (trailing ``Z`` accepted); anything else is rejected
    rather than silently replaced with ``now``.
    """
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime | str | None, *, label: str = "timestamp") -> str:
    """UTC ISO string for radar lease timestamps; ``None`` means now."""
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    return _utc_parse(value, label=label).isoformat()


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
                        CHECK(tokens_estimated IN (0,1)),
                    input_records INTEGER NOT NULL DEFAULT 0
                        CHECK(input_records >= 0),
                    input_characters INTEGER NOT NULL DEFAULT 0
                        CHECK(input_characters >= 0),
                    input_tokens_reported INTEGER
                        CHECK(input_tokens_reported IS NULL OR input_tokens_reported >= 0),
                    output_tokens_reported INTEGER
                        CHECK(output_tokens_reported IS NULL OR output_tokens_reported >= 0),
                    topic_family_id TEXT,
                    shared_evidence_reuse INTEGER NOT NULL DEFAULT 0
                        CHECK(shared_evidence_reuse IN (0,1))
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
                CREATE TABLE IF NOT EXISTS engagement_baseline_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    root_external_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    published_at TEXT,
                    content_age_seconds REAL
                        CHECK(content_age_seconds IS NULL OR content_age_seconds >= 0),
                    content_age_bucket TEXT,
                    creator_size_bucket TEXT,
                    raw_counts_json TEXT NOT NULL,
                    UNIQUE(platform, root_external_id, observed_at)
                );
                CREATE INDEX IF NOT EXISTS idx_engagement_baseline_dimensions
                    ON engagement_baseline_observations(
                        platform, content_age_bucket, creator_size_bucket, observed_at
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
                CREATE TABLE IF NOT EXISTS research_findings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    research_run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                    candidate_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    status TEXT NOT NULL,
                    analysis_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_research_findings_run
                    ON research_findings(research_run_id, candidate_id);
                CREATE TABLE IF NOT EXISTS topic_families (
                    id TEXT PRIMARY KEY,
                    canonical_label TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','retired')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS topic_family_memberships (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    family_id TEXT NOT NULL REFERENCES topic_families(id),
                    candidate_series_id INTEGER NOT NULL
                        REFERENCES discovery_candidate_series(id),
                    relationship TEXT NOT NULL,
                    confidence TEXT NOT NULL CHECK(confidence IN ('low','medium','high')),
                    evidence_json TEXT NOT NULL,
                    first_linked_at TEXT NOT NULL,
                    UNIQUE(family_id, candidate_series_id)
                );
                CREATE INDEX IF NOT EXISTS idx_topic_family_members_family
                    ON topic_family_memberships(family_id, id);
                CREATE TABLE IF NOT EXISTS topic_relationship_edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    left_candidate_series_id INTEGER NOT NULL
                        REFERENCES discovery_candidate_series(id),
                    right_candidate_series_id INTEGER NOT NULL
                        REFERENCES discovery_candidate_series(id),
                    edge_type TEXT NOT NULL,
                    strength REAL NOT NULL CHECK(strength >= -1 AND strength <= 1),
                    evidence_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    UNIQUE(left_candidate_series_id, right_candidate_series_id,
                           edge_type, strength, evidence_json, observed_at)
                );
                CREATE INDEX IF NOT EXISTS idx_topic_edges_pair
                    ON topic_relationship_edges(
                        left_candidate_series_id, right_candidate_series_id, id
                    );
                CREATE TABLE IF NOT EXISTS promotion_evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    family_id TEXT,
                    policy_version TEXT NOT NULL,
                    shadow INTEGER NOT NULL CHECK(shadow IN (0,1)),
                    evaluation_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_promotion_eval_workspace_time
                    ON promotion_evaluations(workspace_id, evaluated_at, id);
                CREATE TABLE IF NOT EXISTS promotion_labels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL,
                    candidate_id TEXT,
                    family_id TEXT,
                    evaluation_id INTEGER REFERENCES promotion_evaluations(id),
                    action_type TEXT NOT NULL,
                    route TEXT,
                    outcome TEXT,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_promotion_labels_workspace_time
                    ON promotion_labels(workspace_id, created_at, id);
                """
            )
            # Additive 2026-08-15 radar schedule persistence (Task 1.3a):
            # new tables only, existing discovery tables are never rebuilt.
            schedulable_modes = ",".join(
                f"'{mode.value}'"
                for mode in sorted(FEED_MODES, key=lambda item: item.value)
            )
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS radar_schedules (
                    id TEXT PRIMARY KEY,
                    scan_mode TEXT NOT NULL
                        CHECK(scan_mode IN ({schedulable_modes})),
                    scope_type TEXT NOT NULL
                        CHECK(scope_type IN ('geography','subject')),
                    scope_key TEXT NOT NULL,
                    geo TEXT NOT NULL,
                    subject_id TEXT,
                    interval_minutes INTEGER NOT NULL CHECK(interval_minutes > 0),
                    next_run_at TEXT NOT NULL,
                    last_attempt_at TEXT,
                    last_successful_comparable_run_id INTEGER,
                    last_status TEXT CHECK(last_status IS NULL
                        OR last_status IN ('complete','partial','error')),
                    last_error_category TEXT,
                    last_source_health_json TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                    lease_token TEXT,
                    lease_until TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(scan_mode, scope_type, scope_key)
                );
                CREATE INDEX IF NOT EXISTS idx_radar_schedules_due
                    ON radar_schedules(enabled, next_run_at, id);
                CREATE TABLE IF NOT EXISTS radar_schedule_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_id TEXT NOT NULL REFERENCES radar_schedules(id),
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    status TEXT NOT NULL
                        CHECK(status IN ('complete','partial','error')),
                    comparable INTEGER CHECK(comparable IS NULL OR comparable IN (0,1)),
                    discovery_run_id TEXT,
                    source_health_json TEXT,
                    error_category TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_radar_schedule_runs_schedule
                    ON radar_schedule_runs(schedule_id, id);
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
            # Additive 2026-08-15 cost-receipt columns: legacy rows keep their
            # values, new columns arrive defaulted, reported tokens stay NULL.
            usage_columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(discovery_stage_usage)"
                )
            }
            for column, definition in (
                ("input_records",
                 "INTEGER NOT NULL DEFAULT 0 CHECK(input_records >= 0)"),
                ("input_characters",
                 "INTEGER NOT NULL DEFAULT 0 CHECK(input_characters >= 0)"),
                ("input_tokens_reported",
                 "INTEGER CHECK(input_tokens_reported IS NULL "
                 "OR input_tokens_reported >= 0)"),
                ("output_tokens_reported",
                 "INTEGER CHECK(output_tokens_reported IS NULL "
                 "OR output_tokens_reported >= 0)"),
                ("topic_family_id", "TEXT"),
                ("shared_evidence_reuse",
                 "INTEGER NOT NULL DEFAULT 0 CHECK(shared_evidence_reuse IN (0,1))"),
            ):
                if column not in usage_columns:
                    connection.execute(
                        f"ALTER TABLE discovery_stage_usage "
                        f"ADD COLUMN {column} {definition}"
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
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(name, applied_at) VALUES (?, ?)",
                ("2026_08_11_research_findings", datetime.now(timezone.utc).isoformat()),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(name, applied_at) VALUES (?, ?)",
                ("2026_08_15_stage_usage_cost_receipts",
                 datetime.now(timezone.utc).isoformat()),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(name, applied_at) VALUES (?, ?)",
                ("2026_08_15_radar_schedules",
                 datetime.now(timezone.utc).isoformat()),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(name, applied_at) VALUES (?, ?)",
                ("2026_08_15_engagement_baseline_observations",
                 datetime.now(timezone.utc).isoformat()),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(name, applied_at) VALUES (?, ?)",
                ("2026_08_15_topic_families", datetime.now(timezone.utc).isoformat()),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(name, applied_at) VALUES (?, ?)",
                ("2026_08_15_promotion_shadow_labels",
                 datetime.now(timezone.utc).isoformat()),
            )

    # --- Topic families and evidence edges (Tasks 3.1-3.2) ---------------

    @staticmethod
    def _topic_family_row(row: sqlite3.Row, memberships: list[dict]) -> dict:
        item = dict(row)
        item["memberships"] = memberships
        return item

    @staticmethod
    def _topic_membership(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["evidence"] = json.loads(item.pop("evidence_json"))
        return item

    def create_topic_family(
        self, *, canonical_label: str, status: str = "active",
        now: datetime | str | None = None, family_id: str | None = None,
    ) -> dict:
        label = str(canonical_label or "").strip()
        if not label:
            raise ValueError("canonical_label is required")
        if status not in {"active", "retired"}:
            raise ValueError(f"invalid family status: {status}")
        stamp = _iso(now or datetime.now(timezone.utc))
        identifier = str(family_id or uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO topic_families
                   (id, canonical_label, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (identifier, label, status, stamp, stamp),
            )
        return self.get_topic_family(identifier)  # type: ignore[return-value]

    def get_topic_family(self, family_id: str) -> dict | None:
        with self._connect() as connection:
            family = connection.execute(
                "SELECT * FROM topic_families WHERE id = ?", (str(family_id),),
            ).fetchone()
            if family is None:
                return None
            rows = connection.execute(
                """SELECT m.id, m.family_id, s.geo, s.normalized_keyword,
                          m.relationship, m.confidence, m.evidence_json,
                          m.first_linked_at
                   FROM topic_family_memberships m
                   JOIN discovery_candidate_series s
                     ON s.id = m.candidate_series_id
                   WHERE m.family_id = ?
                   ORDER BY s.normalized_keyword, s.geo, m.id""",
                (str(family_id),),
            ).fetchall()
        return self._topic_family_row(
            family, [self._topic_membership(row) for row in rows]
        )

    def list_topic_families(self, *, status: str | None = None) -> list[dict]:
        if status is not None and status not in {"active", "retired"}:
            raise ValueError(f"invalid family status: {status}")
        with self._connect() as connection:
            if status is None:
                rows = connection.execute(
                    "SELECT id FROM topic_families ORDER BY created_at, id"
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT id FROM topic_families WHERE status = ?
                       ORDER BY created_at, id""", (status,),
                ).fetchall()
        return [self.get_topic_family(row["id"]) for row in rows]  # type: ignore[list-item]

    def set_topic_family_status(
        self, family_id: str, status: str, *, now: datetime | str | None = None,
    ) -> dict | None:
        if status not in {"active", "retired"}:
            raise ValueError(f"invalid family status: {status}")
        stamp = _iso(now or datetime.now(timezone.utc))
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE topic_families SET status = ?, updated_at = ? WHERE id = ?",
                (status, stamp, str(family_id)),
            )
        return self.get_topic_family(family_id) if cursor.rowcount else None

    def link_topic_family_member(
        self, *, family_id: str, geo: str, keyword: str, relationship: str,
        confidence: str, evidence: Mapping[str, Any],
        now: datetime | str | None = None,
    ) -> dict:
        if relationship not in RELATIONSHIPS:
            raise ValueError(f"invalid relationship: {relationship}")
        if confidence not in {"low", "medium", "high"}:
            raise ValueError(f"invalid confidence: {confidence}")
        if not isinstance(evidence, Mapping) or not evidence:
            raise ValueError("evidence must be a non-empty mapping")
        normalized_geo, normalized_keyword = str(geo).strip().upper(), _key(keyword)
        evidence_json = _json(dict(evidence))
        stamp = _iso(now or datetime.now(timezone.utc))
        with self._connect() as connection:
            family = connection.execute(
                "SELECT status FROM topic_families WHERE id = ?", (str(family_id),),
            ).fetchone()
            if family is None:
                raise ValueError("unknown topic family")
            if family["status"] != "active":
                raise ValueError("topic family is not active")
            series = connection.execute(
                """SELECT id FROM discovery_candidate_series
                   WHERE geo = ? AND normalized_keyword = ?""",
                (normalized_geo, normalized_keyword),
            ).fetchone()
            if series is None:
                raise ValueError("unknown candidate series")
            existing = connection.execute(
                """SELECT * FROM topic_family_memberships
                   WHERE family_id = ? AND candidate_series_id = ?""",
                (str(family_id), series["id"]),
            ).fetchone()
            if existing is not None:
                if (existing["relationship"] != relationship or
                        existing["confidence"] != confidence or
                        existing["evidence_json"] != evidence_json):
                    raise ValueError("conflicting membership write")
                membership_id = existing["id"]
            else:
                cursor = connection.execute(
                    """INSERT INTO topic_family_memberships
                       (family_id, candidate_series_id, relationship, confidence,
                        evidence_json, first_linked_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (str(family_id), series["id"], relationship, confidence,
                     evidence_json, stamp),
                )
                membership_id = cursor.lastrowid
        family_result = self.get_topic_family(family_id)
        return next(item for item in family_result["memberships"]
                    if item["id"] == membership_id)

    def record_topic_edge(
        self, *, left_geo: str, left_keyword: str, right_geo: str,
        right_keyword: str, edge_type: str, evidence: Mapping[str, Any],
        strength: float, observed_at: datetime | str,
    ) -> int:
        if edge_type not in EDGE_KINDS:
            raise ValueError(f"invalid edge type: {edge_type}")
        if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not -1 <= strength <= 1:
            raise ValueError("strength must be between -1 and 1")
        if not isinstance(evidence, Mapping) or not evidence:
            raise ValueError("evidence must be a non-empty mapping")
        keyed = sorted((
            (candidate_key(left_geo, left_keyword), str(left_geo).strip().upper(), _key(left_keyword)),
            (candidate_key(right_geo, right_keyword), str(right_geo).strip().upper(), _key(right_keyword)),
        ))
        if keyed[0][0] == keyed[1][0]:
            raise ValueError("edge requires two different candidates")
        evidence_json = _json(dict(evidence))
        stamp = _iso(observed_at)
        with self._connect() as connection:
            series_ids = []
            for _, edge_geo, edge_keyword in keyed:
                row = connection.execute(
                    """SELECT id FROM discovery_candidate_series
                       WHERE geo = ? AND normalized_keyword = ?""",
                    (edge_geo, edge_keyword),
                ).fetchone()
                if row is None:
                    raise ValueError("unknown candidate series")
                series_ids.append(row["id"])
            existing = connection.execute(
                """SELECT id FROM topic_relationship_edges
                   WHERE left_candidate_series_id = ?
                     AND right_candidate_series_id = ? AND edge_type = ?
                     AND strength = ? AND evidence_json = ? AND observed_at = ?""",
                (series_ids[0], series_ids[1], edge_type, float(strength),
                 evidence_json, stamp),
            ).fetchone()
            if existing is not None:
                return int(existing["id"])
            cursor = connection.execute(
                """INSERT INTO topic_relationship_edges
                   (left_candidate_series_id, right_candidate_series_id,
                    edge_type, strength, evidence_json, observed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (series_ids[0], series_ids[1], edge_type, float(strength),
                 evidence_json, stamp),
            )
            return int(cursor.lastrowid)

    def list_topic_edges(
        self, *, edge_type: str | None = None, geo: str | None = None,
        keyword: str | None = None,
    ) -> list[dict]:
        if edge_type is not None and edge_type not in EDGE_KINDS:
            raise ValueError(f"invalid edge type: {edge_type}")
        if (geo is None) != (keyword is None):
            raise ValueError("geo and keyword must be provided together")
        conditions, parameters = [], []
        if edge_type is not None:
            conditions.append("e.edge_type = ?")
            parameters.append(edge_type)
        if geo is not None:
            normalized_geo, normalized_keyword = str(geo).strip().upper(), _key(keyword)
            conditions.append(
                "((ls.geo = ? AND ls.normalized_keyword = ?) OR "
                "(rs.geo = ? AND rs.normalized_keyword = ?))"
            )
            parameters.extend([normalized_geo, normalized_keyword,
                               normalized_geo, normalized_keyword])
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT e.*, ls.geo AS left_geo,
                          ls.normalized_keyword AS left_keyword,
                          rs.geo AS right_geo,
                          rs.normalized_keyword AS right_keyword
                   FROM topic_relationship_edges e
                   JOIN discovery_candidate_series ls
                     ON ls.id = e.left_candidate_series_id
                   JOIN discovery_candidate_series rs
                     ON rs.id = e.right_candidate_series_id""" + where +
                " ORDER BY e.id", tuple(parameters),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["left_candidate_key"] = candidate_key(
                item.pop("left_geo"), item.pop("left_keyword")
            )
            item["right_candidate_key"] = candidate_key(
                item.pop("right_geo"), item.pop("right_keyword")
            )
            item.pop("left_candidate_series_id")
            item.pop("right_candidate_series_id")
            item["evidence"] = json.loads(item.pop("evidence_json"))
            result.append(item)
        return result

    # --- Promotion shadow evaluations and labels (Tasks 4.2-4.4) ---------

    @staticmethod
    def _promotion_evaluation(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["shadow"] = bool(item["shadow"])
        item["evaluation"] = json.loads(item.pop("evaluation_json"))
        item["evidence"] = json.loads(item.pop("evidence_json"))
        return item

    def record_promotion_evaluation(
        self, *, workspace_id: str, candidate_id: str,
        policy_version: str, evaluation: Mapping[str, Any],
        evidence: Mapping[str, Any], family_id: str | None = None,
        shadow: bool = True, evaluated_at: datetime | str | None = None,
    ) -> dict:
        if not str(workspace_id).strip() or not str(candidate_id).strip():
            raise ValueError("workspace_id and candidate_id are required")
        if not isinstance(evaluation, Mapping) or not evaluation:
            raise ValueError("evaluation must be a non-empty mapping")
        if not isinstance(evidence, Mapping):
            raise ValueError("evidence must be a mapping")
        stamp = _iso(evaluated_at or datetime.now(timezone.utc))
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO promotion_evaluations
                   (workspace_id, candidate_id, family_id, policy_version,
                    shadow, evaluation_json, evidence_json, evaluated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(workspace_id).strip(), str(candidate_id).strip(), family_id,
                 str(policy_version), int(bool(shadow)), _json(dict(evaluation)),
                 _json(dict(evidence)), stamp),
            )
            row = connection.execute(
                "SELECT * FROM promotion_evaluations WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return self._promotion_evaluation(row)

    def list_promotion_evaluations(
        self, *, workspace_id: str, family_id: str | None = None,
    ) -> list[dict]:
        with self._connect() as connection:
            if family_id is None:
                rows = connection.execute(
                    """SELECT * FROM promotion_evaluations
                       WHERE workspace_id = ? ORDER BY evaluated_at, id""",
                    (workspace_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM promotion_evaluations
                       WHERE workspace_id = ? AND family_id = ?
                       ORDER BY evaluated_at, id""", (workspace_id, family_id),
                ).fetchall()
        return [self._promotion_evaluation(row) for row in rows]

    @staticmethod
    def _promotion_label(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["details"] = json.loads(item.pop("details_json"))
        return item

    def record_promotion_label(
        self, *, workspace_id: str, action_type: str,
        candidate_id: str | None = None, family_id: str | None = None,
        evaluation_id: int | None = None, route: str | None = None,
        outcome: str | None = None, details: Mapping[str, Any] | None = None,
        created_at: datetime | str | None = None,
    ) -> dict:
        if action_type not in {"investigate", "monitor", "dismiss", "outcome"}:
            raise ValueError(f"invalid promotion label action: {action_type}")
        if candidate_id is None and family_id is None:
            raise ValueError("candidate_id or family_id is required")
        if action_type == "outcome" and not str(outcome or "").strip():
            raise ValueError("outcome is required for outcome labels")
        if action_type != "outcome" and outcome is not None:
            raise ValueError("outcome is only valid for outcome labels")
        stamp = _iso(created_at or datetime.now(timezone.utc))
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO promotion_labels
                   (workspace_id, candidate_id, family_id, evaluation_id,
                    action_type, route, outcome, details_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (workspace_id, candidate_id, family_id, evaluation_id,
                 action_type, route, outcome, _json(dict(details or {})), stamp),
            )
            row = connection.execute(
                "SELECT * FROM promotion_labels WHERE id = ?", (cursor.lastrowid,),
            ).fetchone()
        return self._promotion_label(row)

    def list_promotion_labels(
        self, *, workspace_id: str, family_id: str | None = None,
    ) -> list[dict]:
        with self._connect() as connection:
            if family_id is None:
                rows = connection.execute(
                    """SELECT * FROM promotion_labels WHERE workspace_id = ?
                       ORDER BY created_at, id""", (workspace_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM promotion_labels
                       WHERE workspace_id = ? AND family_id = ?
                       ORDER BY created_at, id""", (workspace_id, family_id),
                ).fetchall()
        return [self._promotion_label(row) for row in rows]

    def summarize_promotion_funnel(self, *, workspace_id: str) -> dict:
        rows = self.list_promotion_evaluations(workspace_id=workspace_id)
        counts = {
            "evaluated": len(rows), "eligible": 0, "automatic": 0,
            "manual": 0, "exploration": 0, "not_promoted": 0,
        }
        routes: dict[str, int] = {}
        for row in rows:
            evaluation = row["evaluation"]
            counts["eligible"] += int(bool(evaluation.get("eligible")))
            mode = str(evaluation.get("promotion_mode") or "none")
            if mode in {"automatic", "manual", "exploration"}:
                counts[mode] += 1
            else:
                counts["not_promoted"] += 1
            for route in evaluation.get("routes") or []:
                if route.get("passed"):
                    name = str(route.get("route") or "unknown")
                    routes[name] = routes.get(name, 0) + 1
        labels = self.list_promotion_labels(workspace_id=workspace_id)
        label_counts: dict[str, int] = {}
        for label in labels:
            key = str(label["action_type"])
            label_counts[key] = label_counts.get(key, 0) + 1
        return {
            "workspace_id": workspace_id,
            "counts": counts,
            "route_pass_counts": dict(sorted(routes.items())),
            "label_counts": dict(sorted(label_counts.items())),
        }

    # --- Radar schedules (Task 1.3a: persistence and leases only) ---------

    @staticmethod
    def _radar_schedule(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        health = item.pop("last_source_health_json")
        item["last_source_health"] = json.loads(health) if health is not None else None
        return item

    @staticmethod
    def _radar_run(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["comparable"] = (
            bool(item["comparable"]) if item["comparable"] is not None else None
        )
        health = item.pop("source_health_json")
        item["source_health"] = json.loads(health) if health is not None else None
        return item

    def upsert_radar_schedule(
        self,
        *,
        scan_mode: str,
        scope_type: str,
        geo: str,
        interval_minutes: int,
        next_run_at: datetime | str,
        subject_id: str | None = None,
        scope_key: str | None = None,
        enabled: bool = True,
        schedule_id: str | None = None,
        now: datetime | str | None = None,
    ) -> dict:
        """Create or refresh one durable radar schedule idempotently.

        Only feed modes (``trends_snapshot``/``root_sweep``) may be
        scheduled; research-run modes are rejected here at the storage
        boundary. ``next_run_at`` applies only when the schedule is
        inserted: a conflicting upsert is a config refresh that may update
        geo/subject/cadence/enabled, but the existing schedule keeps its
        current ``next_run_at`` due time, any live lease and the full
        attempt history including the last successful comparable run.
        ``scope_key`` defaults to the geo (geography scope) or the subject
        id (subject scope) so ``UNIQUE(scan_mode, scope_type, scope_key)``
        stays stable.
        """
        mode = coerce_scan_mode(scan_mode)
        if mode not in FEED_MODES:
            valid = ", ".join(
                item.value for item in sorted(FEED_MODES, key=lambda m: m.value)
            )
            raise ValueError(
                f"scan mode cannot be scheduled: {mode.value} "
                f"(schedulable modes: {valid})"
            )
        scope_type = str(scope_type or "").strip().casefold()
        if scope_type not in _RADAR_SCOPE_TYPES:
            raise ValueError("scope_type must be 'geography' or 'subject'")
        geo = str(geo or "").strip().upper()
        if not geo:
            raise ValueError("geo is required")
        if scope_type == "subject":
            if not str(subject_id or "").strip():
                raise ValueError("subject_id is required for subject schedules")
        elif subject_id is not None:
            raise ValueError("subject_id must be null for geography schedules")
        if (
            isinstance(interval_minutes, bool)
            or not isinstance(interval_minutes, int)
            or interval_minutes <= 0
        ):
            raise ValueError("interval_minutes must be a positive integer")
        key = str(scope_key or "").strip() or (
            subject_id if scope_type == "subject" else geo
        )
        next_run_iso = _utc_iso(next_run_at, label="next_run_at")
        now_iso = _utc_iso(now, label="now")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO radar_schedules
                   (id, scan_mode, scope_type, scope_key, geo, subject_id,
                    interval_minutes, next_run_at, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(scan_mode, scope_type, scope_key) DO UPDATE SET
                       geo = excluded.geo,
                       subject_id = excluded.subject_id,
                       interval_minutes = excluded.interval_minutes,
                       enabled = excluded.enabled,
                       updated_at = excluded.updated_at
                   /* next_run_at deliberately not reset: the live schedule
                      keeps its current due time, lease and history. */""",
                (
                    schedule_id or str(uuid.uuid4()), mode.value, scope_type, key,
                    geo, subject_id or None, interval_minutes, next_run_iso,
                    int(bool(enabled)), now_iso, now_iso,
                ),
            )
            row = connection.execute(
                """SELECT * FROM radar_schedules
                   WHERE scan_mode = ? AND scope_type = ? AND scope_key = ?""",
                (mode.value, scope_type, key),
            ).fetchone()
        return self._radar_schedule(row)

    def get_radar_schedule(self, schedule_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM radar_schedules WHERE id = ?", (schedule_id,)
            ).fetchone()
        return self._radar_schedule(row) if row is not None else None

    def list_radar_schedules(self, *, enabled: bool | None = None) -> list[dict]:
        with self._connect() as connection:
            if enabled is None:
                rows = connection.execute(
                    "SELECT * FROM radar_schedules ORDER BY created_at, id"
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM radar_schedules WHERE enabled = ?
                       ORDER BY created_at, id""",
                    (int(enabled),),
                ).fetchall()
        return [self._radar_schedule(row) for row in rows]

    def list_radar_schedule_runs(self, schedule_id: str) -> list[dict]:
        """Return one schedule's attempt history in insertion order."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM radar_schedule_runs WHERE schedule_id = ?
                   ORDER BY id""",
                (schedule_id,),
            ).fetchall()
        return [self._radar_run(row) for row in rows]

    def claim_due_schedules(
        self,
        now: datetime | str | None = None,
        lease_minutes: int = 10,
        limit: int = 100,
    ) -> list[dict]:
        """Atomically claim due schedules; safe across store replicas.

        Runs under ``BEGIN IMMEDIATE`` with a conditional update so two
        ``DiscoveryStore`` instances on the same database can never hold
        the same row. Live leases exclude a row; expired leases are
        reclaimable by anyone. Each claim records ``last_attempt_at``.
        """
        if (
            isinstance(lease_minutes, bool)
            or not isinstance(lease_minutes, int)
            or lease_minutes <= 0
        ):
            raise ValueError("lease_minutes must be a positive integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        at = _utc_parse(now, label="now") if now is not None else datetime.now(timezone.utc)
        now_iso = at.isoformat()
        lease_until = (at + timedelta(minutes=lease_minutes)).isoformat()
        claims: list[dict] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT * FROM radar_schedules
                   WHERE enabled = 1 AND next_run_at <= ?
                     AND (lease_until IS NULL OR lease_until <= ?)
                   ORDER BY next_run_at, id
                   LIMIT ?""",
                (now_iso, now_iso, limit),
            ).fetchall()
            for row in rows:
                token = str(uuid.uuid4())
                cursor = connection.execute(
                    """UPDATE radar_schedules
                       SET lease_token = ?, lease_until = ?, last_attempt_at = ?,
                           updated_at = ?
                       WHERE id = ? AND (lease_until IS NULL OR lease_until <= ?)""",
                    (token, lease_until, now_iso, now_iso, row["id"], now_iso),
                )
                if cursor.rowcount == 1:
                    claim = self._radar_schedule(row)
                    claim.update({
                        "schedule_id": row["id"],
                        "claim_token": token,
                        "lease_until": lease_until,
                        "claimed_at": now_iso,
                        "last_attempt_at": now_iso,
                    })
                    claims.append(claim)
        return claims

    def complete_schedule_attempt(
        self,
        schedule_id: str,
        lease_token: str,
        *,
        status: str,
        comparable: bool,
        discovery_run_id: str | None = None,
        source_health: list[dict] | None = None,
        error_category: str | None = None,
        started_at: datetime | str | None = None,
        now: datetime | str | None = None,
    ) -> dict:
        """Finalize one claimed attempt: run row, lease clear, cadence.

        Requires the matching lease token; stale tokens fail without
        inserting a run row or touching the schedule. ``next_run_at``
        advances from the completion time by the schedule interval.
        ``last_successful_comparable_run_id`` stores the inserted
        ``radar_schedule_runs.id`` of a ``complete`` comparable attempt;
        every other outcome preserves the previous pointer so failures
        never fake candidate gaps. ``discovery_run_id`` is optional
        provenance: a healthy subject root sweep may complete comparable
        with ``discovery_run_id=None``. Missing source health is stored
        as unknown (NULL), never invented.
        """
        if status not in _RADAR_ATTEMPT_STATUS:
            raise ValueError(f"invalid radar attempt status: {status}")
        if not isinstance(comparable, bool):
            raise ValueError("comparable must be a boolean")
        if status != "complete":
            comparable = False
        if status == "error" and not str(error_category or "").strip():
            raise ValueError("error attempts must record error_category")
        at = _utc_parse(now, label="now") if now is not None else datetime.now(timezone.utc)
        completed_iso = at.isoformat()
        health_json = _json(source_health) if source_health is not None else None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            schedule = connection.execute(
                "SELECT * FROM radar_schedules WHERE id = ? AND lease_token = ?",
                (schedule_id, lease_token),
            ).fetchone()
            if schedule is None:
                raise RuntimeError("radar schedule claim is no longer valid")
            if started_at is not None:
                started_iso = _utc_iso(started_at, label="started_at")
            else:
                started_iso = schedule["last_attempt_at"] or completed_iso
            cursor = connection.execute(
                """INSERT INTO radar_schedule_runs
                   (schedule_id, started_at, completed_at, status, comparable,
                    discovery_run_id, source_health_json, error_category)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    schedule_id, started_iso, completed_iso, status,
                    int(comparable), str(discovery_run_id or "").strip() or None,
                    health_json, str(error_category or "").strip() or None,
                ),
            )
            run_id = int(cursor.lastrowid)
            next_run_iso = (
                at + timedelta(minutes=int(schedule["interval_minutes"]))
            ).isoformat()
            success = status == "complete" and comparable
            success_pointer = (
                run_id if success
                else schedule["last_successful_comparable_run_id"]
            )
            connection.execute(
                """UPDATE radar_schedules
                   SET last_attempt_at = ?, last_status = ?,
                       last_error_category = ?, last_source_health_json = ?,
                       last_successful_comparable_run_id = ?, next_run_at = ?,
                       lease_token = NULL, lease_until = NULL, updated_at = ?
                   WHERE id = ? AND lease_token = ?""",
                (
                    started_iso, status,
                    str(error_category or "").strip() or None, health_json,
                    success_pointer, next_run_iso, completed_iso,
                    schedule_id, lease_token,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM radar_schedules WHERE id = ?", (schedule_id,)
            ).fetchone()
            run = connection.execute(
                "SELECT * FROM radar_schedule_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return {
            "schedule": self._radar_schedule(updated),
            "run": self._radar_run(run),
        }

    def set_radar_schedule_enabled(
        self, schedule_id: str, enabled: bool, *,
        now: datetime | str | None = None,
    ) -> dict | None:
        """Enable or disable one existing radar schedule in place.

        Reconciliation (Task 1.3b) disables schedules whose scope left the
        desired set instead of deleting them, so the due time and the full
        attempt history survive a later reactivation. Only ``enabled`` and
        ``updated_at`` change: the current ``next_run_at``, any live lease
        and the success pointer are untouched. Unknown ids return ``None``
        like ``get_radar_schedule``; nothing is ever created here.
        """
        now_iso = _utc_iso(now, label="now")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE radar_schedules SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(bool(enabled)), now_iso, str(schedule_id)),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM radar_schedules WHERE id = ?", (schedule_id,)
            ).fetchone()
        return self._radar_schedule(row)

    def renew_radar_schedule_claim(
        self, schedule_id: str, claim_token: str, *,
        now: datetime | str | None = None, lease_minutes: int = 10,
    ) -> dict | None:
        """Extend one live lease only when its claim token still matches."""
        if (
            isinstance(lease_minutes, bool)
            or not isinstance(lease_minutes, int)
            or lease_minutes <= 0
        ):
            raise ValueError("lease_minutes must be a positive integer")
        token = str(claim_token or "").strip()
        if not token:
            raise ValueError("claim_token is required")
        at = _utc_parse(now, label="now") if now is not None else datetime.now(timezone.utc)
        lease_until = (at + timedelta(minutes=lease_minutes)).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE radar_schedules
                   SET lease_until = ?, updated_at = ?
                   WHERE id = ? AND lease_token = ?""",
                (lease_until, at.isoformat(), str(schedule_id), token),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM radar_schedules WHERE id = ?", (schedule_id,),
            ).fetchone()
        return self._radar_schedule(row)

    def release_radar_schedule_claim(
        self, schedule_id: str, claim_token: str, *,
        now: datetime | str | None = None,
    ) -> bool:
        """Release a live claim without advancing cadence or adding a run."""
        token = str(claim_token or "").strip()
        if not token:
            raise ValueError("claim_token is required")
        now_iso = _utc_iso(now, label="now")
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE radar_schedules
                   SET lease_token = NULL, lease_until = NULL, updated_at = ?
                   WHERE id = ? AND lease_token = ?""",
                (now_iso, str(schedule_id), token),
            )
        return cursor.rowcount == 1

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
                    input_tokens, output_tokens, tokens_estimated,
                    input_records, input_characters,
                    input_tokens_reported, output_tokens_reported,
                    topic_family_id, shared_evidence_reuse)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?, ?)""",
                (
                    row["discovery_run_id"], row["stage"], row["started_at"],
                    row["completed_at"], row["duration_seconds"],
                    row["candidates_considered"], row["candidates_processed"],
                    row["records_returned"], row["external_calls"], row["llm_calls"],
                    row["cache_hits"], row["status"], row["error_category"],
                    row["input_tokens"], row["output_tokens"],
                    int(row["tokens_estimated"]),
                    row["input_records"], row["input_characters"],
                    row["input_tokens_reported"], row["output_tokens_reported"],
                    row["topic_family_id"], int(row["shared_evidence_reuse"]),
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
            item["shared_evidence_reuse"] = bool(item["shared_evidence_reuse"])
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
                          s.presence_status, r.status AS run_status,
                          r.comparable AS run_comparable
                   FROM discovery_candidate_observations o
                   JOIN discovery_candidate_series s ON s.id = o.candidate_series_id
                   JOIN discovery_runs r ON r.id = o.discovery_run_id
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

    @staticmethod
    def _engagement_baseline_observation(row: sqlite3.Row) -> dict:
        """Convert persisted dimensions back to the calculator's root shape."""
        item = dict(row)
        raw = json.loads(item.pop("raw_counts_json"))
        item["external_id"] = item["root_external_id"]
        item["record_type"] = "post"
        item["depth"] = 0
        item["engagement"] = raw
        item["raw_counts"] = dict(raw)
        return item

    def record_engagement_baseline_observation(
        self,
        record: Mapping[str, Any],
        *,
        observed_at: datetime | str | None = None,
        config: Any = None,
    ) -> int:
        """Persist one immutable root observation for engagement baselines.

        Repeating the exact same observation is idempotent.  Reusing its
        platform/root/time identity with different counts or dimensions is
        rejected instead of silently replacing source evidence.
        """
        from social_scraper.analysis.engagement import (
            DEFAULT_CONFIG,
            prepare_baseline_observation,
        )

        normalized = prepare_baseline_observation(
            record,
            observed_at=observed_at,
            config=config or DEFAULT_CONFIG,
        )
        identity = (
            normalized["platform"],
            normalized["root_external_id"],
            normalized["observed_at"],
        )
        values = (
            *identity,
            normalized["published_at"],
            normalized["content_age_seconds"],
            normalized["content_age_bucket"],
            normalized["creator_size_bucket"],
            _json(normalized["raw_counts"]),
        )
        with self._connect() as connection:
            existing = connection.execute(
                """SELECT * FROM engagement_baseline_observations
                   WHERE platform = ? AND root_external_id = ? AND observed_at = ?""",
                identity,
            ).fetchone()
            if existing is not None:
                stored = self._engagement_baseline_observation(existing)
                expected = {
                    "published_at": normalized["published_at"],
                    "content_age_seconds": normalized["content_age_seconds"],
                    "content_age_bucket": normalized["content_age_bucket"],
                    "creator_size_bucket": normalized["creator_size_bucket"],
                    "raw_counts": normalized["raw_counts"],
                }
                if any(stored[key] != value for key, value in expected.items()):
                    raise ValueError(
                        "conflicting engagement baseline observation for "
                        "platform/root/observed_at"
                    )
                return int(existing["id"])
            cursor = connection.execute(
                """INSERT INTO engagement_baseline_observations
                   (platform, root_external_id, observed_at, published_at,
                    content_age_seconds, content_age_bucket, creator_size_bucket,
                    raw_counts_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                values,
            )
            return int(cursor.lastrowid)

    def list_engagement_baseline_observations(
        self,
        *,
        platform: str,
        observed_through: datetime | str,
        trailing_period: timedelta | None = None,
    ) -> list[dict]:
        """Return one platform's persisted roots in an inclusive trailing window."""
        from social_scraper.analysis.engagement import DEFAULT_TRAILING_PERIOD

        normalized_platform = str(platform or "").strip().lower()
        if not normalized_platform:
            raise ValueError("platform is required")
        period = DEFAULT_TRAILING_PERIOD if trailing_period is None else trailing_period
        if not isinstance(period, timedelta) or period <= timedelta(0):
            raise ValueError("trailing_period must be a positive timedelta")
        through = _utc_parse(observed_through, label="observed_through")
        started = through - period
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM engagement_baseline_observations
                   WHERE platform = ? AND observed_at >= ? AND observed_at <= ?
                   ORDER BY observed_at, id""",
                (normalized_platform, started.isoformat(), through.isoformat()),
            ).fetchall()
        return [self._engagement_baseline_observation(row) for row in rows]

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

    def save_findings(
        self, run_id: str, candidate_id: str, topic: str,
        status: str, analysis: Mapping[str, Any],
    ) -> dict:
        """Persist one candidate's analysis result as a research finding."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO research_findings
                   (research_run_id, candidate_id, topic, status, analysis_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, candidate_id, topic, status, _json(analysis), now),
            )
            finding_id = int(cursor.lastrowid)
        return {"id": finding_id, "research_run_id": run_id,
                "candidate_id": candidate_id, "topic": topic,
                "status": status, "analysis": dict(analysis), "created_at": now}

    def list_findings(self, run_id: str) -> list[dict]:
        """Return all persisted findings for one research run."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM research_findings
                   WHERE research_run_id = ? ORDER BY id""", (run_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["analysis"] = json.loads(item.pop("analysis_json"))
            result.append(item)
        return result

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
