"""Append-only storage for source-grounded investment dossiers."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


class InvestmentResearchStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS investment_dossiers (
                dossier_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_investment_dossiers_case
                ON investment_dossiers(case_id, created_at DESC);
            CREATE TRIGGER IF NOT EXISTS investment_dossiers_no_update
            BEFORE UPDATE ON investment_dossiers BEGIN
                SELECT RAISE(ABORT, 'investment dossier is immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS investment_dossiers_no_delete
            BEFORE DELETE ON investment_dossiers BEGIN
                SELECT RAISE(ABORT, 'investment dossier is immutable');
            END;
            CREATE TABLE IF NOT EXISTS investment_research_runs (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                source_scan_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                selection_mode TEXT NOT NULL CHECK(selection_mode IN ('qualified','research_only')),
                candidate_hash TEXT NOT NULL,
                handoff_json TEXT NOT NULL,
                target_json TEXT NOT NULL,
                options_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL CHECK(status IN (
                    'planned','running','complete','partial','error','cancelled'
                )),
                stage TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                error_category TEXT,
                result_json TEXT NOT NULL DEFAULT '{}',
                dossier_id TEXT,
                idempotency_key TEXT,
                claim_token TEXT,
                claim_until TEXT,
                UNIQUE(workspace_id,idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS idx_investment_research_runs_workspace
                ON investment_research_runs(workspace_id,created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_investment_research_runs_candidate
                ON investment_research_runs(source_scan_id,candidate_id,created_at DESC);
            """)
            connection.commit()

    @staticmethod
    def _canonical_payload(payload: dict[str, Any]) -> str:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def append_dossier(self, payload: dict[str, Any]) -> dict[str, Any]:
        dossier_id = str(payload.get("dossier_id") or "").strip()
        case_id = str(payload.get("case_id") or "").strip()
        schema_version = str(payload.get("schema_version") or "").strip()
        status = str(payload.get("status") or "").strip()
        if not all((dossier_id, case_id, schema_version, status)):
            raise ValueError("dossier_id, case_id, schema_version and status are required")
        encoded = self._canonical_payload(payload)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        created_at = str(
            payload.get("created_at")
            or datetime.now(timezone.utc).isoformat()
        )
        try:
            with self._transaction() as connection:
                connection.execute(
                    """INSERT INTO investment_dossiers
                       (dossier_id,case_id,schema_version,status,created_at,payload_json,payload_sha256)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        dossier_id,
                        case_id,
                        schema_version,
                        status,
                        created_at,
                        encoded,
                        digest,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"dossier already exists: {dossier_id}") from exc
        return {"dossier_id": dossier_id, "payload_sha256": digest, "created_at": created_at}

    def get_dossier(self, dossier_id: str) -> dict[str, Any] | None:
        record = self.get_dossier_record(dossier_id)
        return None if record is None else record["payload"]

    def get_dossier_record(self, dossier_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT payload_json,payload_sha256,created_at,status,case_id
                   FROM investment_dossiers WHERE dossier_id=?""",
                (str(dossier_id),),
            ).fetchone()
        if row is None:
            return None
        return {
            "payload": json.loads(str(row["payload_json"])),
            "payload_sha256": str(row["payload_sha256"]),
            "created_at": str(row["created_at"]),
            "status": str(row["status"]),
            "case_id": str(row["case_id"]),
        }

    def latest_for_case(self, case_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT payload_json FROM investment_dossiers
                   WHERE case_id=? ORDER BY created_at DESC,dossier_id DESC LIMIT 1""",
                (str(case_id),),
            ).fetchone()
        return None if row is None else json.loads(str(row["payload_json"]))

    def verify_dossier(self, dossier_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT payload_json,payload_sha256 FROM investment_dossiers
                   WHERE dossier_id=?""",
                (str(dossier_id),),
            ).fetchone()
        if row is None:
            return False
        digest = hashlib.sha256(str(row["payload_json"]).encode("utf-8")).hexdigest()
        return digest == str(row["payload_sha256"])

    @staticmethod
    def _utc_iso(value: datetime | None = None) -> str:
        current = value or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc).isoformat()

    @classmethod
    def _public_run(cls, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        for field in ("handoff_json", "target_json", "options_json", "result_json"):
            output_name = field.removesuffix("_json")
            try:
                value[output_name] = json.loads(str(value.pop(field) or "{}"))
            except json.JSONDecodeError:
                value[output_name] = {}
        value.pop("claim_token", None)
        value.pop("claim_until", None)
        return value

    def create_research_run(
        self,
        *,
        workspace_id: str,
        handoff: dict[str, Any],
        target: dict[str, Any],
        options: dict[str, Any],
        idempotency_key: str | None,
    ) -> tuple[dict[str, Any], bool]:
        workspace = str(workspace_id or "").strip()
        source_scan_id = str(handoff.get("source_scan_id") or "").strip()
        candidate_id = str(handoff.get("candidate_id") or "").strip()
        selection_mode = str(handoff.get("selection_mode") or "").strip()
        candidate_hash = str(handoff.get("candidate_hash") or "").strip()
        company_name = str(target.get("company_name") or "").strip()
        if not all((workspace, source_scan_id, candidate_id, candidate_hash, company_name)):
            raise ValueError("workspace, candidate handoff and company name are required")
        if selection_mode not in {"qualified", "research_only"}:
            raise ValueError("invalid selection mode")
        key = str(idempotency_key or "").strip() or None
        now = self._utc_iso()
        with self._transaction() as connection:
            if key:
                existing = connection.execute(
                    """SELECT * FROM investment_research_runs
                       WHERE workspace_id=? AND idempotency_key=?""",
                    (workspace, key),
                ).fetchone()
                if existing is not None:
                    return self._public_run(existing), False  # type: ignore[return-value]
            run_id = uuid.uuid4().hex
            connection.execute(
                """INSERT INTO investment_research_runs
                   (id,workspace_id,source_scan_id,candidate_id,selection_mode,
                    candidate_hash,handoff_json,target_json,options_json,status,stage,
                    progress,created_at,updated_at,idempotency_key)
                   VALUES (?,?,?,?,?,?,?,?,?,'planned','queued',0,?,?,?)""",
                (
                    run_id,
                    workspace,
                    source_scan_id,
                    candidate_id,
                    selection_mode,
                    candidate_hash,
                    self._canonical_payload(handoff),
                    self._canonical_payload(target),
                    self._canonical_payload(options),
                    now,
                    now,
                    key,
                ),
            )
            row = connection.execute(
                "SELECT * FROM investment_research_runs WHERE id=?", (run_id,)
            ).fetchone()
        return self._public_run(row), True  # type: ignore[return-value]

    def get_research_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM investment_research_runs WHERE id=?", (str(run_id),)
            ).fetchone()
        return self._public_run(row)

    def list_research_runs(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as connection:
            if workspace_id:
                rows = connection.execute(
                    """SELECT * FROM investment_research_runs WHERE workspace_id=?
                       ORDER BY created_at DESC,id DESC""",
                    (str(workspace_id),),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM investment_research_runs
                       ORDER BY created_at DESC,id DESC"""
                ).fetchall()
        return [self._public_run(row) for row in rows if row is not None]  # type: ignore[misc]

    def claim_research_run(
        self,
        run_id: str,
        *,
        lease_seconds: int = 180,
        now: datetime | None = None,
    ) -> str | None:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        token = uuid.uuid4().hex
        now_iso = self._utc_iso(current)
        until_iso = self._utc_iso(current + timedelta(seconds=max(1, int(lease_seconds))))
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT status,claim_until FROM investment_research_runs WHERE id=?",
                (str(run_id),),
            ).fetchone()
            if row is None or row["status"] in {"complete", "partial", "cancelled"}:
                return None
            active_claim = (
                row["status"] == "running"
                and row["claim_until"]
                and str(row["claim_until"]) > now_iso
            )
            if active_claim:
                return None
            cursor = connection.execute(
                """UPDATE investment_research_runs
                   SET status='running',started_at=COALESCE(started_at,?),updated_at=?,
                       claim_token=?,claim_until=?,error_category=NULL
                   WHERE id=?""",
                (now_iso, now_iso, token, until_iso, str(run_id)),
            )
            if cursor.rowcount != 1:
                return None
        return token

    def renew_research_claim(
        self,
        run_id: str,
        *,
        claim_token: str,
        lease_seconds: int = 180,
        now: datetime | None = None,
    ) -> bool:
        current = now or datetime.now(timezone.utc)
        now_iso = self._utc_iso(current)
        until_iso = self._utc_iso(current + timedelta(seconds=max(1, int(lease_seconds))))
        with self._transaction() as connection:
            cursor = connection.execute(
                """UPDATE investment_research_runs SET claim_until=?,updated_at=?
                   WHERE id=? AND status='running' AND claim_token=?
                     AND claim_until>?""",
                (until_iso, now_iso, str(run_id), str(claim_token), now_iso),
            )
        return cursor.rowcount == 1

    def _require_claim(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        claim_token: str,
        *,
        now_iso: str,
    ) -> None:
        row = connection.execute(
            """SELECT id FROM investment_research_runs
               WHERE id=? AND status='running' AND claim_token=? AND claim_until>?""",
            (str(run_id), str(claim_token), now_iso),
        ).fetchone()
        if row is None:
            raise ValueError("research run claim is missing, stale or invalid")

    def update_research_run(
        self,
        run_id: str,
        *,
        claim_token: str,
        stage: str,
        progress: int,
        result: dict[str, Any] | None = None,
    ) -> None:
        now_iso = self._utc_iso()
        with self._transaction() as connection:
            self._require_claim(connection, run_id, claim_token, now_iso=now_iso)
            connection.execute(
                """UPDATE investment_research_runs
                   SET stage=?,progress=?,updated_at=?,result_json=COALESCE(?,result_json)
                   WHERE id=?""",
                (
                    str(stage),
                    max(0, min(99, int(progress))),
                    now_iso,
                    self._canonical_payload(result) if result is not None else None,
                    str(run_id),
                ),
            )

    def complete_research_run(
        self,
        run_id: str,
        *,
        claim_token: str,
        status: str,
        dossier_id: str | None,
        result: dict[str, Any],
        error_category: str | None = None,
    ) -> None:
        if status not in {"complete", "partial", "error", "cancelled"}:
            raise ValueError("invalid terminal research status")
        now_iso = self._utc_iso()
        with self._transaction() as connection:
            self._require_claim(connection, run_id, claim_token, now_iso=now_iso)
            connection.execute(
                """UPDATE investment_research_runs
                   SET status=?,stage=?,progress=100,updated_at=?,completed_at=?,
                       dossier_id=?,result_json=?,error_category=?,claim_token=NULL,
                       claim_until=NULL WHERE id=?""",
                (
                    status,
                    status,
                    now_iso,
                    now_iso,
                    dossier_id,
                    self._canonical_payload(result),
                    error_category,
                    str(run_id),
                ),
            )

    def finalize_research_run_with_dossier(
        self,
        run_id: str,
        *,
        claim_token: str,
        status: str,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        if status not in {"complete", "partial"}:
            raise ValueError("dossier run must complete or remain partial")
        dossier_id = str(payload.get("dossier_id") or "")
        case_id = str(payload.get("case_id") or "")
        schema_version = str(payload.get("schema_version") or "")
        dossier_status = str(payload.get("status") or "")
        if not all((dossier_id, case_id, schema_version, dossier_status)):
            raise ValueError("invalid dossier payload")
        encoded = self._canonical_payload(payload)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        now_iso = self._utc_iso()
        created_at = str(payload.get("created_at") or now_iso)
        with self._transaction() as connection:
            self._require_claim(connection, run_id, claim_token, now_iso=now_iso)
            existing = connection.execute(
                """SELECT payload_sha256 FROM investment_dossiers
                   WHERE dossier_id=?""",
                (dossier_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """INSERT INTO investment_dossiers
                       (dossier_id,case_id,schema_version,status,created_at,payload_json,payload_sha256)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        dossier_id,
                        case_id,
                        schema_version,
                        dossier_status,
                        created_at,
                        encoded,
                        digest,
                    ),
                )
            elif str(existing["payload_sha256"]) != digest:
                raise ValueError("dossier id already exists with different content")
            connection.execute(
                """UPDATE investment_research_runs
                   SET status=?,stage=?,progress=100,updated_at=?,completed_at=?,
                       dossier_id=?,result_json=?,error_category=NULL,claim_token=NULL,
                       claim_until=NULL WHERE id=?""",
                (
                    status,
                    status,
                    now_iso,
                    now_iso,
                    dossier_id,
                    self._canonical_payload(result),
                    str(run_id),
                ),
            )
        return {"dossier_id": dossier_id, "payload_sha256": digest}
