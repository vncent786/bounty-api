"""SQLite persistence for reusable lenses and typed custom field definitions."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .compiler import LensCompileError, compile_lens


_SAFE_KEY = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_ALLOWED_DATA_TYPES = {"number", "boolean", "string", "enum"}
_ALLOWED_SOURCE_STAGES = {
    "candidate", "root_probe", "horizontal_analysis", "custom_extraction"
}
_ALLOWED_EXTRACTION_MODES = {"deterministic", "signal_aggregation", "llm"}
_ALLOWED_CRITERION_MODES = {"filter", "score", "display"}
_ALLOWED_MISSING_POLICIES = {"keep_unknown", "score_zero", "exclude"}
_FORBIDDEN_DEFINITION_KEYS = {
    "code", "python", "python_code", "sql", "sql_query", "script", "expression"
}


class LensStoreError(Exception):
    """Base error suitable for translation at an API boundary."""


class NotFoundError(LensStoreError):
    pass


class ConflictError(LensStoreError):
    pass


class ValidationError(LensStoreError, ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("value must be JSON serializable") from exc


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{label} must be an object")
    # JSON round-trip gives storage callers the same types API callers can supply.
    try:
        result = json.loads(_json(value))
    except json.JSONDecodeError as exc:  # pragma: no cover - generated JSON is valid
        raise ValidationError(f"{label} must be valid JSON") from exc
    return result


def _validate_definition(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_DEFINITION_KEYS:
                raise ValidationError("definition cannot contain executable Python/SQL")
            _validate_definition(child)
    elif isinstance(value, list):
        for child in value:
            _validate_definition(child)


class LensStore:
    """Owns schema and atomic CRUD; it never imports or invokes source/LLM code."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._memory_connection: sqlite3.Connection | None = None
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:", timeout=10)
            connection = self._memory_connection
        else:
            connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS research_lenses (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    archived_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_research_lenses_workspace
                    ON research_lenses(workspace_id, archived_at, created_at);
                CREATE TABLE IF NOT EXISTS research_lens_versions (
                    lens_id TEXT NOT NULL REFERENCES research_lenses(id),
                    version INTEGER NOT NULL CHECK(version > 0),
                    spec_json TEXT NOT NULL,
                    compiled_requirements_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(lens_id, version)
                );
                CREATE TABLE IF NOT EXISTS custom_field_definitions (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    label TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    data_type TEXT NOT NULL CHECK(data_type IN ('number','boolean','string','enum')),
                    source_stage TEXT NOT NULL CHECK(source_stage IN (
                        'candidate','root_probe','horizontal_analysis','custom_extraction'
                    )),
                    extraction_mode TEXT NOT NULL CHECK(extraction_mode IN (
                        'deterministic','signal_aggregation','llm'
                    )),
                    definition_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    archived_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_custom_field_active_workspace_key
                    ON custom_field_definitions(workspace_id, key)
                    WHERE archived_at IS NULL;
                CREATE INDEX IF NOT EXISTS idx_custom_fields_workspace
                    ON custom_field_definitions(workspace_id, archived_at, created_at);
            """)

    @staticmethod
    def _workspace(workspace_id: str) -> str:
        value = str(workspace_id or "").strip()
        if not value:
            raise ValidationError("workspace_id is required")
        return value

    @staticmethod
    def _lens_row(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _version_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["spec"] = json.loads(result.pop("spec_json"))
        result["compiled_requirements"] = json.loads(
            result.pop("compiled_requirements_json")
        )
        return result

    @staticmethod
    def _field_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["definition"] = json.loads(result.pop("definition_json"))
        return result

    def list_custom_fields(
        self, workspace_id: str, *, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        workspace_id = self._workspace(workspace_id)
        clause = "" if include_archived else " AND archived_at IS NULL"
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM custom_field_definitions WHERE workspace_id = ?" + clause
                + " ORDER BY created_at, id", (workspace_id,)
            ).fetchall()
        return [self._field_row(row) for row in rows]

    def get_custom_field(
        self, workspace_id: str, field_id: str, *, include_archived: bool = False
    ) -> dict[str, Any]:
        workspace_id = self._workspace(workspace_id)
        clause = "" if include_archived else " AND archived_at IS NULL"
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM custom_field_definitions WHERE workspace_id = ? AND id = ?"
                + clause, (workspace_id, field_id)
            ).fetchone()
        if row is None:
            raise NotFoundError("custom field not found")
        return self._field_row(row)

    def create_custom_field(
        self, workspace_id: str, *, key: str, label: str, data_type: str,
        source_stage: str, extraction_mode: str, definition: Mapping[str, Any],
        description: str = "",
    ) -> dict[str, Any]:
        workspace_id = self._workspace(workspace_id)
        key = str(key or "").strip()
        if not _SAFE_KEY.fullmatch(key):
            raise ValidationError("field key must be safe snake_case")
        label = str(label or "").strip()
        if not label:
            raise ValidationError("field label is required")
        if data_type not in _ALLOWED_DATA_TYPES:
            raise ValidationError("invalid custom field data_type")
        if source_stage not in _ALLOWED_SOURCE_STAGES:
            raise ValidationError("invalid custom field source_stage")
        if extraction_mode not in _ALLOWED_EXTRACTION_MODES:
            raise ValidationError("invalid custom field extraction_mode")
        definition_object = _object(definition, "definition")
        _validate_definition(definition_object)
        if data_type == "enum":
            values = definition_object.get("values", definition_object.get("enum_values"))
            if (not isinstance(values, list) or not values
                    or any(not isinstance(item, str) or not item.strip() for item in values)
                    or len(set(values)) != len(values)):
                raise ValidationError("enum values must be a non-empty unique string list")
        field_id = str(uuid.uuid4())
        try:
            with self._connect() as connection:
                connection.execute("""
                    INSERT INTO custom_field_definitions (
                        id, workspace_id, key, label, description, data_type, source_stage,
                        extraction_mode, definition_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    field_id, workspace_id, key, label, str(description or ""), data_type,
                    source_stage, extraction_mode, _json(definition_object), _now(),
                ))
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                raise ConflictError("an active field with this key already exists") from exc
            raise
        return self.get_custom_field(workspace_id, field_id)

    def archive_custom_field(self, workspace_id: str, field_id: str) -> dict[str, Any]:
        field = self.get_custom_field(workspace_id, field_id)
        archived_at = _now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE custom_field_definitions SET archived_at = ? WHERE id = ?",
                (archived_at, field["id"]),
            )
        return self.get_custom_field(workspace_id, field_id, include_archived=True)

    def _active_fields(self, workspace_id: str) -> list[dict[str, Any]]:
        return self.list_custom_fields(workspace_id)

    def _prepare_spec(self, workspace_id: str, spec: Mapping[str, Any]) -> tuple[dict, dict]:
        spec_object = _object(spec, "spec")
        criteria = spec_object.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            raise ValidationError("lens must contain at least one criterion")
        seen: set[str] = set()
        for criterion in criteria:
            if not isinstance(criterion, dict):
                raise ValidationError("each criterion must be an object")
            criterion_id = str(criterion.get("criterion_id") or "").strip()
            if not criterion_id:
                raise ValidationError("criterion_id is required")
            if criterion_id in seen:
                raise ValidationError(f"duplicate criterion: {criterion_id}")
            seen.add(criterion_id)
            if criterion.get("mode", "score") not in _ALLOWED_CRITERION_MODES:
                raise ValidationError("invalid criterion mode")
            if criterion.get("missing_policy", "keep_unknown") not in _ALLOWED_MISSING_POLICIES:
                raise ValidationError("invalid missing policy")
            weight = criterion.get("weight", 0.0)
            if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight < 0:
                raise ValidationError("criterion weight must be non-negative")
        try:
            compiled = compile_lens(spec_object, self._active_fields(workspace_id))
        except LensCompileError as exc:
            raise ValidationError(str(exc)) from exc
        return spec_object, compiled

    def create_lens(
        self, workspace_id: str, name: str, description: str,
        spec: Mapping[str, Any],
    ) -> dict[str, Any]:
        workspace_id = self._workspace(workspace_id)
        name = str(name or "").strip()
        if not name:
            raise ValidationError("lens name is required")
        spec_object, compiled = self._prepare_spec(workspace_id, spec)
        lens_id, created_at = str(uuid.uuid4()), _now()
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO research_lenses
                    (id, workspace_id, name, description, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (lens_id, workspace_id, name, str(description or ""), created_at))
            connection.execute("""
                INSERT INTO research_lens_versions
                    (lens_id, version, spec_json, compiled_requirements_json, created_at)
                VALUES (?, 1, ?, ?, ?)
            """, (lens_id, _json(spec_object), _json(compiled), created_at))
        return self.get_lens(workspace_id, lens_id)

    def list_lenses(
        self, workspace_id: str, *, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        workspace_id = self._workspace(workspace_id)
        clause = "" if include_archived else " AND l.archived_at IS NULL"
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT l.*, v.version, v.spec_json, v.compiled_requirements_json,
                       v.created_at AS version_created_at
                FROM research_lenses l
                JOIN research_lens_versions v ON v.lens_id = l.id
                    AND v.version = (SELECT MAX(v2.version) FROM research_lens_versions v2
                                     WHERE v2.lens_id = l.id)
                WHERE l.workspace_id = ?
            """ + clause + " ORDER BY l.created_at, l.id", (workspace_id,)).fetchall()
        results = []
        for row in rows:
            raw = dict(row)
            lens = {key: raw[key] for key in (
                "id", "workspace_id", "name", "description", "created_at", "archived_at"
            )}
            lens["latest_version"] = {
                "lens_id": raw["id"], "version": raw["version"],
                "spec": json.loads(raw["spec_json"]),
                "compiled_requirements": json.loads(raw["compiled_requirements_json"]),
                "created_at": raw["version_created_at"],
            }
            results.append(lens)
        return results

    def get_lens(
        self, workspace_id: str, lens_id: str, *, include_archived: bool = False
    ) -> dict[str, Any]:
        matches = [row for row in self.list_lenses(
            workspace_id, include_archived=include_archived
        ) if row["id"] == lens_id]
        if not matches:
            raise NotFoundError("lens not found")
        return matches[0]

    def list_lens_versions(
        self, workspace_id: str, lens_id: str
    ) -> list[dict[str, Any]]:
        lens = self.get_lens(workspace_id, lens_id, include_archived=True)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_lens_versions WHERE lens_id = ? ORDER BY version",
                (lens["id"],),
            ).fetchall()
        return [self._version_row(row) for row in rows]

    def get_lens_version(
        self, workspace_id: str, lens_id: str, version: int
    ) -> dict[str, Any]:
        lens = self.get_lens(workspace_id, lens_id, include_archived=True)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_lens_versions WHERE lens_id = ? AND version = ?",
                (lens["id"], version),
            ).fetchone()
        if row is None:
            raise NotFoundError("lens version not found")
        return self._version_row(row)

    def create_lens_version(
        self, workspace_id: str, lens_id: str, spec: Mapping[str, Any], *,
        name: str | None = None, description: str | None = None,
    ) -> dict[str, Any]:
        lens = self.get_lens(workspace_id, lens_id)
        spec_object, compiled = self._prepare_spec(workspace_id, spec)
        created_at = _now()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                version = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM research_lens_versions WHERE lens_id = ?",
                    (lens["id"],),
                ).fetchone()[0]
                connection.execute("""
                    INSERT INTO research_lens_versions
                        (lens_id, version, spec_json, compiled_requirements_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (lens["id"], version, _json(spec_object), _json(compiled), created_at))
                if name is not None or description is not None:
                    new_name = lens["name"] if name is None else str(name).strip()
                    if not new_name:
                        raise ValidationError("lens name is required")
                    new_description = lens["description"] if description is None else str(description)
                    connection.execute(
                        "UPDATE research_lenses SET name = ?, description = ? WHERE id = ?",
                        (new_name, new_description, lens["id"]),
                    )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("lens version conflict") from exc
        return self.get_lens_version(workspace_id, lens_id, version)

    def duplicate_lens(
        self, workspace_id: str, lens_id: str, *, name: str | None = None
    ) -> dict[str, Any]:
        source = self.get_lens(workspace_id, lens_id)
        return self.create_lens(
            workspace_id,
            name or f"{source['name']} copy",
            source["description"],
            source["latest_version"]["spec"],
        )

    def archive_lens(self, workspace_id: str, lens_id: str) -> dict[str, Any]:
        lens = self.get_lens(workspace_id, lens_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE research_lenses SET archived_at = ? WHERE id = ?",
                (_now(), lens["id"]),
            )
        return self.get_lens(workspace_id, lens_id, include_archived=True)
