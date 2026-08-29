"""Append-only storage for source-grounded investment dossiers."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
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
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM investment_dossiers WHERE dossier_id=?",
                (str(dossier_id),),
            ).fetchone()
        return None if row is None else json.loads(str(row["payload_json"]))

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
