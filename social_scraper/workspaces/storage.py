"""SQLite persistence for project-scoped monitored subjects and durable actions."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

PROJECT_STATUSES = frozenset({"active", "archived"})
ALIAS_KINDS = frozenset({"include", "exclude", "disambiguation"})
ACTION_TYPES = frozenset({
    "run_discovery", "promote_candidate", "deep_read", "analyze_candidate",
    "request_enrichment", "start_monitoring", "pause_monitoring", "export_report",
})
ACTION_STATUSES = frozenset({"queued", "running", "completed", "failed", "cancelled"})


class WorkspaceStoreError(Exception):
    pass


class NotFoundError(WorkspaceStoreError):
    pass


class ConflictError(WorkspaceStoreError):
    pass


class ValidationError(WorkspaceStoreError, ValueError):
    pass


def _now(value: datetime | str | None = None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _required(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValidationError(f"{label} is required")
    return result


def _json(value: Any, label: str = "value") -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} must be JSON serializable") from exc


def _object(value: Mapping[str, Any] | None, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValidationError(f"{label} must be an object")
    return json.loads(_json(value, label))


class WorkspaceStore:
    """Owns additive project/workflow tables in the shared Discovery database."""

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

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        owns = connection is not self._memory_connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            if owns:
                connection.close()

    def ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    default_geo TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active','archived')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_projects_workspace_status
                    ON projects(workspace_id, status, created_at, id);
                CREATE TABLE IF NOT EXISTS monitored_subjects (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    geo TEXT NOT NULL DEFAULT '',
                    platforms_json TEXT NOT NULL DEFAULT '[]',
                    cadence_minutes INTEGER NOT NULL CHECK(cadence_minutes > 0),
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    lens_id TEXT,
                    lens_version INTEGER,
                    budget_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK((lens_id IS NULL AND lens_version IS NULL) OR
                          (lens_id IS NOT NULL AND lens_version IS NOT NULL AND lens_version > 0))
                );
                CREATE INDEX IF NOT EXISTS idx_subjects_project_active
                    ON monitored_subjects(project_id, active, created_at, id);
                CREATE TABLE IF NOT EXISTS subject_aliases (
                    id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL REFERENCES monitored_subjects(id) ON DELETE CASCADE,
                    alias TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('include','exclude','disambiguation')),
                    UNIQUE(subject_id, alias, kind)
                );
                CREATE TABLE IF NOT EXISTS research_actions (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    subject_id TEXT REFERENCES monitored_subjects(id),
                    actor_id TEXT,
                    action_type TEXT NOT NULL CHECK(action_type IN (
                        'run_discovery','promote_candidate','deep_read','analyze_candidate',
                        'request_enrichment','start_monitoring','pause_monitoring','export_report'
                    )),
                    target_type TEXT NOT NULL,
                    target_id TEXT,
                    status TEXT NOT NULL CHECK(status IN
                        ('queued','running','completed','failed','cancelled')),
                    idempotency_key TEXT,
                    requested_budget_json TEXT NOT NULL DEFAULT '{}',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT,
                    error_category TEXT,
                    requested_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    lease_token TEXT,
                    lease_expires_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_actions_workspace_idempotency
                    ON research_actions(workspace_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_actions_project_requested
                    ON research_actions(workspace_id, project_id, requested_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_actions_claim
                    ON research_actions(status, lease_expires_at, requested_at, id);
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
            """)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(name, applied_at) VALUES (?, ?)",
                ("2026_08_10_workspace_projects_actions", _now()),
            )

    @staticmethod
    def _project(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _alias(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _subject(row: sqlite3.Row, aliases: Sequence[sqlite3.Row] = ()) -> dict[str, Any]:
        item = dict(row)
        item["platforms"] = json.loads(item.pop("platforms_json"))
        item["budget"] = json.loads(item.pop("budget_json"))
        item["active"] = bool(item["active"])
        item["aliases"] = [dict(alias) for alias in aliases]
        return item

    @staticmethod
    def _action(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["requested_budget"] = json.loads(item.pop("requested_budget_json"))
        item["payload"] = json.loads(item.pop("payload_json"))
        raw_result = item.pop("result_json")
        item["result"] = json.loads(raw_result) if raw_result is not None else None
        return item

    def create_project(self, workspace_id: str, name: str, *, description: str = "",
                       default_geo: str = "", project_id: str | None = None,
                       connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        workspace_id, name = _required(workspace_id, "workspace_id"), _required(name, "project name")
        project_id, now = project_id or str(uuid.uuid4()), _now()
        values = (project_id, workspace_id, name, str(description or ""),
                  str(default_geo or "").strip().upper(), now, now)
        if connection is None:
            with self._connect() as connection:
                connection.execute("""INSERT INTO projects
                    (id,workspace_id,name,description,default_geo,status,created_at,updated_at)
                    VALUES (?,?,?,?,?,'active',?,?)""", values)
        else:
            connection.execute("""INSERT INTO projects
                (id,workspace_id,name,description,default_geo,status,created_at,updated_at)
                VALUES (?,?,?,?,?,'active',?,?)""", values)
        return self.get_project(workspace_id, project_id, connection=connection)

    def get_project(self, workspace_id: str, project_id: str, *, include_archived: bool = True,
                    connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        clause = "" if include_archived else " AND status = 'active'"
        params = (_required(workspace_id, "workspace_id"), project_id)
        if connection is None:
            with self._connect() as connection:
                row = connection.execute("SELECT * FROM projects WHERE workspace_id=? AND id=?" + clause,
                                         params).fetchone()
        else:
            row = connection.execute("SELECT * FROM projects WHERE workspace_id=? AND id=?" + clause,
                                     params).fetchone()
        if row is None:
            raise NotFoundError("project not found")
        return self._project(row)

    def list_projects(self, workspace_id: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
        clause = "" if include_archived else " AND status='active'"
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM projects WHERE workspace_id=?" + clause + " ORDER BY created_at,id",
                (_required(workspace_id, "workspace_id"),)).fetchall()
        return [self._project(row) for row in rows]

    def update_project(self, workspace_id: str, project_id: str, **changes: Any) -> dict[str, Any]:
        current = self.get_project(workspace_id, project_id)
        allowed = {"name", "description", "default_geo", "status"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationError(f"unknown project fields: {', '.join(sorted(unknown))}")
        values = {key: current[key] for key in allowed}
        values.update(changes)
        values["name"] = _required(values["name"], "project name")
        values["default_geo"] = str(values["default_geo"] or "").strip().upper()
        if values["status"] not in PROJECT_STATUSES:
            raise ValidationError("invalid project status")
        with self._connect() as connection:
            connection.execute("""UPDATE projects SET name=?,description=?,default_geo=?,status=?,updated_at=?
                                  WHERE workspace_id=? AND id=?""",
                               (values["name"], str(values["description"] or ""), values["default_geo"],
                                values["status"], _now(), workspace_id, project_id))
        return self.get_project(workspace_id, project_id)

    def archive_project(self, workspace_id: str, project_id: str) -> dict[str, Any]:
        return self.update_project(workspace_id, project_id, status="archived")

    @staticmethod
    def _platforms(value: Sequence[str] | None) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (str, bytes)):
            raise ValidationError("platforms must be a list")
        result = [str(item).strip().casefold() for item in value]
        if any(not item for item in result) or len(set(result)) != len(result):
            raise ValidationError("platforms must be a unique list of non-empty strings")
        return result

    def create_subject(self, workspace_id: str, project_id: str, name: str, *,
                       description: str = "", geo: str = "", platforms: Sequence[str] | None = None,
                       cadence_minutes: int = 10080, active: bool = True,
                       lens_id: str | None = None, lens_version: int | None = None,
                       budget: Mapping[str, Any] | None = None, subject_id: str | None = None,
                       connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        self.get_project(workspace_id, project_id, connection=connection)
        name = _required(name, "subject name")
        if isinstance(cadence_minutes, bool) or not isinstance(cadence_minutes, int) or cadence_minutes <= 0:
            raise ValidationError("cadence_minutes must be a positive integer")
        if (lens_id is None) != (lens_version is None):
            raise ValidationError("lens_id and lens_version must be supplied together")
        if lens_version is not None and (isinstance(lens_version, bool) or lens_version <= 0):
            raise ValidationError("lens_version must be a positive integer")
        subject_id, now = subject_id or str(uuid.uuid4()), _now()
        values = (subject_id, project_id, name, str(description or ""),
                  str(geo or "").strip().upper(), _json(self._platforms(platforms), "platforms"),
                  cadence_minutes, int(bool(active)), lens_id, lens_version,
                  _json(_object(budget, "budget"), "budget"), now, now)
        sql = """INSERT INTO monitored_subjects
                 (id,project_id,name,description,geo,platforms_json,cadence_minutes,active,
                  lens_id,lens_version,budget_json,created_at,updated_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        if connection is None:
            with self._connect() as connection:
                connection.execute(sql, values)
        else:
            connection.execute(sql, values)
        return self.get_subject(workspace_id, project_id, subject_id, connection=connection)

    def get_subject(self, workspace_id: str, project_id: str, subject_id: str, *,
                    connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        self.get_project(workspace_id, project_id, connection=connection)
        sql = "SELECT * FROM monitored_subjects WHERE project_id=? AND id=?"
        if connection is None:
            with self._connect() as connection:
                row = connection.execute(sql, (project_id, subject_id)).fetchone()
                aliases = connection.execute(
                    "SELECT * FROM subject_aliases WHERE subject_id=? ORDER BY alias,kind,id",
                    (subject_id,)).fetchall() if row else []
        else:
            row = connection.execute(sql, (project_id, subject_id)).fetchone()
            aliases = connection.execute(
                "SELECT * FROM subject_aliases WHERE subject_id=? ORDER BY alias,kind,id",
                (subject_id,)).fetchall() if row else []
        if row is None:
            raise NotFoundError("subject not found")
        return self._subject(row, aliases)

    def list_subjects(self, workspace_id: str, project_id: str, *,
                      include_inactive: bool = True) -> list[dict[str, Any]]:
        self.get_project(workspace_id, project_id)
        clause = "" if include_inactive else " AND active=1"
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM monitored_subjects WHERE project_id=?" + clause
                                      + " ORDER BY created_at,id", (project_id,)).fetchall()
            result = []
            for row in rows:
                aliases = connection.execute(
                    "SELECT * FROM subject_aliases WHERE subject_id=? ORDER BY alias,kind,id",
                    (row["id"],)).fetchall()
                result.append(self._subject(row, aliases))
        return result

    def update_subject(self, workspace_id: str, project_id: str, subject_id: str,
                       **changes: Any) -> dict[str, Any]:
        current = self.get_subject(workspace_id, project_id, subject_id)
        allowed = {"name", "description", "geo", "platforms", "cadence_minutes", "active",
                   "lens_id", "lens_version", "budget"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationError(f"unknown subject fields: {', '.join(sorted(unknown))}")
        values = {key: current[key] for key in allowed}
        values.update(changes)
        values["name"] = _required(values["name"], "subject name")
        values["platforms"] = self._platforms(values["platforms"])
        cadence = values["cadence_minutes"]
        if isinstance(cadence, bool) or not isinstance(cadence, int) or cadence <= 0:
            raise ValidationError("cadence_minutes must be a positive integer")
        if (values["lens_id"] is None) != (values["lens_version"] is None):
            raise ValidationError("lens_id and lens_version must be supplied together")
        with self._connect() as connection:
            connection.execute("""UPDATE monitored_subjects SET name=?,description=?,geo=?,platforms_json=?,
                cadence_minutes=?,active=?,lens_id=?,lens_version=?,budget_json=?,updated_at=?
                WHERE project_id=? AND id=?""", (
                values["name"], str(values["description"] or ""), str(values["geo"] or "").upper(),
                _json(values["platforms"]), cadence, int(bool(values["active"])), values["lens_id"],
                values["lens_version"], _json(_object(values["budget"], "budget")), _now(),
                project_id, subject_id))
        return self.get_subject(workspace_id, project_id, subject_id)

    def archive_subject(self, workspace_id: str, project_id: str, subject_id: str) -> dict[str, Any]:
        return self.update_subject(workspace_id, project_id, subject_id, active=False)

    def create_alias(self, workspace_id: str, project_id: str, subject_id: str, alias: str,
                     kind: str, *, alias_id: str | None = None) -> dict[str, Any]:
        self.get_subject(workspace_id, project_id, subject_id)
        alias = _required(alias, "alias")
        kind = str(kind or "").strip().casefold()
        if kind not in ALIAS_KINDS:
            raise ValidationError("alias kind must be include, exclude, or disambiguation")
        alias_id = alias_id or str(uuid.uuid4())
        try:
            with self._connect() as connection:
                connection.execute("INSERT INTO subject_aliases(id,subject_id,alias,kind) VALUES (?,?,?,?)",
                                   (alias_id, subject_id, alias, kind))
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                raise ConflictError("alias already exists") from exc
            raise
        return self.get_alias(workspace_id, project_id, subject_id, alias_id)

    def get_alias(self, workspace_id: str, project_id: str, subject_id: str,
                  alias_id: str) -> dict[str, Any]:
        self.get_subject(workspace_id, project_id, subject_id)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM subject_aliases WHERE subject_id=? AND id=?",
                                     (subject_id, alias_id)).fetchone()
        if row is None:
            raise NotFoundError("alias not found")
        return self._alias(row)

    def list_aliases(self, workspace_id: str, project_id: str, subject_id: str) -> list[dict[str, Any]]:
        return self.get_subject(workspace_id, project_id, subject_id)["aliases"]

    def delete_alias(self, workspace_id: str, project_id: str, subject_id: str,
                     alias_id: str) -> None:
        alias = self.get_alias(workspace_id, project_id, subject_id, alias_id)
        with self._connect() as connection:
            connection.execute("DELETE FROM subject_aliases WHERE id=?", (alias["id"],))

    def create_action(self, workspace_id: str, project_id: str, action_type: str, *,
                      subject_id: str | None = None, actor_id: str | None = None,
                      target_type: str = "project", target_id: str | None = None,
                      idempotency_key: str | None = None,
                      requested_budget: Mapping[str, Any] | None = None,
                      payload: Mapping[str, Any] | None = None, action_id: str | None = None,
                      connection: sqlite3.Connection | None = None) -> tuple[dict[str, Any], bool]:
        workspace_id = _required(workspace_id, "workspace_id")
        self.get_project(workspace_id, project_id, connection=connection)
        if subject_id is not None:
            self.get_subject(workspace_id, project_id, subject_id, connection=connection)
        action_type = str(action_type or "").strip()
        if action_type not in ACTION_TYPES:
            raise ValidationError("invalid action_type")
        target_type = _required(target_type, "target_type")
        idempotency_key = str(idempotency_key).strip() if idempotency_key is not None else None
        if idempotency_key == "":
            raise ValidationError("idempotency_key must not be empty")
        existing = self._find_idempotent(workspace_id, idempotency_key, connection)
        if existing is not None:
            if existing["project_id"] != project_id:
                raise ConflictError("idempotency key already belongs to another project")
            return existing, False
        action_id, now = action_id or str(uuid.uuid4()), _now()
        values = (action_id, workspace_id, project_id, subject_id, actor_id, action_type,
                  target_type, target_id, idempotency_key,
                  _json(_object(requested_budget, "requested_budget")),
                  _json(_object(payload, "payload")), now)
        sql = """INSERT INTO research_actions
            (id,workspace_id,project_id,subject_id,actor_id,action_type,target_type,target_id,
             status,idempotency_key,requested_budget_json,payload_json,requested_at)
            VALUES (?,?,?,?,?,?,?,?,'queued',?,?,?,?)"""
        try:
            if connection is None:
                with self._connect() as connection:
                    connection.execute(sql, values)
            else:
                connection.execute(sql, values)
        except sqlite3.IntegrityError as exc:
            existing = self._find_idempotent(workspace_id, idempotency_key, connection)
            if existing is not None:
                if existing["project_id"] != project_id:
                    raise ConflictError("idempotency key already belongs to another project") from exc
                return existing, False
            raise
        return self.get_action(workspace_id, project_id, action_id, connection=connection), True

    def _find_idempotent(self, workspace_id: str, key: str | None,
                         connection: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        if key is None:
            return None
        if connection is None:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM research_actions WHERE workspace_id=? AND idempotency_key=?",
                    (workspace_id, key)).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM research_actions WHERE workspace_id=? AND idempotency_key=?",
                (workspace_id, key)).fetchone()
        return self._action(row) if row else None

    def get_action(self, workspace_id: str, project_id: str, action_id: str, *,
                   connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        self.get_project(workspace_id, project_id, connection=connection)
        sql = "SELECT * FROM research_actions WHERE workspace_id=? AND project_id=? AND id=?"
        if connection is None:
            with self._connect() as connection:
                row = connection.execute(sql, (workspace_id, project_id, action_id)).fetchone()
        else:
            row = connection.execute(sql, (workspace_id, project_id, action_id)).fetchone()
        if row is None:
            raise NotFoundError("action not found")
        return self._action(row)

    def list_actions(self, workspace_id: str, project_id: str, *, status: str | None = None,
                     subject_id: str | None = None) -> list[dict[str, Any]]:
        self.get_project(workspace_id, project_id)
        clauses, params = ["workspace_id=?", "project_id=?"], [workspace_id, project_id]
        if status is not None:
            if status not in ACTION_STATUSES:
                raise ValidationError("invalid action status")
            clauses.append("status=?"); params.append(status)
        if subject_id is not None:
            self.get_subject(workspace_id, project_id, subject_id)
            clauses.append("subject_id=?"); params.append(subject_id)
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM research_actions WHERE " + " AND ".join(clauses)
                                      + " ORDER BY requested_at DESC,id DESC", params).fetchall()
        return [self._action(row) for row in rows]

    def cancel_action(self, workspace_id: str, project_id: str, action_id: str,
                      *, now: datetime | str | None = None) -> dict[str, Any]:
        self.get_action(workspace_id, project_id, action_id)
        timestamp = _now(now)
        with self._connect() as connection:
            cursor = connection.execute("""UPDATE research_actions SET status='cancelled',completed_at=?,
                lease_token=NULL,lease_expires_at=NULL WHERE workspace_id=? AND project_id=? AND id=?
                AND status='queued'""", (timestamp, workspace_id, project_id, action_id))
        if cursor.rowcount == 0:
            raise ConflictError("only queued actions can be cancelled")
        return self.get_action(workspace_id, project_id, action_id)

    def claim_action(self, workspace_id: str, project_id: str, action_id: str | None = None, *,
                     lease_seconds: int = 300, lease_token: str | None = None,
                     now: datetime | str | None = None) -> dict[str, Any] | None:
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
            raise ValidationError("lease_seconds must be a positive integer")
        self.get_project(workspace_id, project_id)
        at = datetime.fromisoformat(_now(now))
        expires = (at + timedelta(seconds=lease_seconds)).isoformat()
        token = lease_token or str(uuid.uuid4())
        with self.transaction() as connection:
            params: list[Any] = [workspace_id, project_id, at.isoformat()]
            extra = ""
            if action_id is not None:
                extra = " AND id=?"; params.append(action_id)
            row = connection.execute("""SELECT id FROM research_actions
                WHERE workspace_id=? AND project_id=?
                  AND (status='queued' OR (status='running' AND lease_expires_at<=?))"""
                + extra + " ORDER BY requested_at,id LIMIT 1", params).fetchone()
            if row is None:
                return None
            connection.execute("""UPDATE research_actions SET status='running',lease_token=?,
                lease_expires_at=?,started_at=COALESCE(started_at,?) WHERE id=?""",
                (token, expires, at.isoformat(), row["id"]))
            return self.get_action(workspace_id, project_id, row["id"], connection=connection)

    def _finish_action(self, workspace_id: str, project_id: str, action_id: str, status: str, *,
                       lease_token: str | None, result: Mapping[str, Any] | None,
                       error_category: str | None, now: datetime | str | None,
                       connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        self.get_action(workspace_id, project_id, action_id, connection=connection)
        timestamp = _now(now)
        conditions, params = "workspace_id=? AND project_id=? AND id=? AND status IN ('queued','running')", [workspace_id, project_id, action_id]
        if lease_token is not None:
            conditions += " AND lease_token=?"; params.append(lease_token)
        sql = f"""UPDATE research_actions SET status=?,result_json=?,error_category=?,completed_at=?,
                  lease_token=NULL,lease_expires_at=NULL WHERE {conditions}"""
        values = [status, _json(result) if result is not None else None, error_category, timestamp, *params]
        if connection is None:
            with self._connect() as connection:
                cursor = connection.execute(sql, values)
        else:
            cursor = connection.execute(sql, values)
        if cursor.rowcount == 0:
            raise ConflictError("action is not claimable or lease token does not match")
        return self.get_action(workspace_id, project_id, action_id, connection=connection)

    def complete_action(self, workspace_id: str, project_id: str, action_id: str, *,
                        result: Mapping[str, Any] | None = None, lease_token: str | None = None,
                        now: datetime | str | None = None,
                        connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        return self._finish_action(workspace_id, project_id, action_id, "completed",
                                   lease_token=lease_token, result=result, error_category=None,
                                   now=now, connection=connection)

    def fail_action(self, workspace_id: str, project_id: str, action_id: str, *,
                    error_category: str, result: Mapping[str, Any] | None = None,
                    lease_token: str | None = None, now: datetime | str | None = None) -> dict[str, Any]:
        return self._finish_action(workspace_id, project_id, action_id, "failed",
                                   lease_token=lease_token, result=result,
                                   error_category=_required(error_category, "error_category"), now=now)
