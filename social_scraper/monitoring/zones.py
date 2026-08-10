"""
Zone registry — defines what to monitor.

A Zone is a topic area with:
- name: human-readable label
- keywords: 4-5 seed search terms
- platforms: which social platforms to collect from
- interval_hours: how often to collect (default 168 = weekly)
- region: geo scope
- status: active | paused
"""

import json
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class Zone:
    """A monitored topic area."""
    name: str
    keywords: list[str]
    platforms: list[str] = field(default_factory=lambda: ["youtube", "reddit", "tiktok", "x", "instagram"])
    interval_hours: int = 168  # weekly by default
    region: str = ""
    status: str = "active"  # active | paused
    created_at: str = ""
    updated_at: str = ""
    last_collected_at: str = ""
    description: str = ""
    id: Optional[int] = None

    def __post_init__(self):
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Zone":
        return cls(
            id=row["id"],
            name=row["name"],
            keywords=json.loads(row["keywords_json"]),
            platforms=json.loads(row["platforms_json"]),
            interval_hours=row["interval_hours"],
            region=row["region"] or "",
            status=row["status"],
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
            last_collected_at=row["last_collected_at"] or "",
            description=row["description"] or "",
        )


class ZoneRegistry:
    """SQLite-backed registry of monitored zones."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS zones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    keywords_json TEXT NOT NULL,
                    platforms_json TEXT NOT NULL,
                    interval_hours INTEGER NOT NULL DEFAULT 168,
                    region TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_collected_at TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cluster_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    zone_id INTEGER NOT NULL REFERENCES zones(id),
                    collected_at TEXT NOT NULL,
                    clusters_json TEXT NOT NULL,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    platform_summary_json TEXT NOT NULL DEFAULT '{}',
                    source_health_json TEXT NOT NULL DEFAULT '[]'
                )
            """)
            snapshot_columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(cluster_snapshots)").fetchall()
            }
            if "source_health_json" not in snapshot_columns:
                conn.execute(
                    "ALTER TABLE cluster_snapshots "
                    "ADD COLUMN source_health_json TEXT NOT NULL DEFAULT '[]'"
                )
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_snapshots_zone_time
                    ON cluster_snapshots(zone_id, collected_at)
            """)

    def create(self, zone: Zone) -> int:
        """Insert a new zone, returns zone ID."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO zones
                   (name, description, keywords_json, platforms_json, interval_hours,
                    region, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (zone.name, zone.description,
                 json.dumps(zone.keywords), json.dumps(zone.platforms),
                 zone.interval_hours, zone.region, zone.status, now, now),
            )
            return cursor.lastrowid

    def get(self, zone_id: int) -> Optional[Zone]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM zones WHERE id = ?", (zone_id,)).fetchone()
            return Zone.from_row(row) if row else None

    def get_by_name(self, name: str) -> Optional[Zone]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM zones WHERE name = ?", (name,)).fetchone()
            return Zone.from_row(row) if row else None

    def list_zones(self, status: str = None) -> list[Zone]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM zones WHERE status = ? ORDER BY name", (status,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM zones ORDER BY name").fetchall()
            return [Zone.from_row(r) for r in rows]

    def list_due(self) -> list[Zone]:
        """Return active zones where enough time has passed since last collection."""
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM zones WHERE status = 'active' ORDER BY name"
            ).fetchall()
        due = []
        for row in rows:
            zone = Zone.from_row(row)
            if not zone.last_collected_at:
                due.append(zone)
                continue
            try:
                last = datetime.fromisoformat(zone.last_collected_at)
                if (now - last).total_seconds() / 3600 >= zone.interval_hours:
                    due.append(zone)
            except (ValueError, TypeError):
                due.append(zone)
        return due

    def update_collected(self, zone_id: int):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE zones SET last_collected_at = ?, updated_at = ? WHERE id = ?",
                (now, now, zone_id),
            )

    def update(self, zone_id: int, **kwargs):
        now = datetime.now(timezone.utc).isoformat()
        allowed = {"name", "description", "keywords_json", "platforms_json",
                    "interval_hours", "region", "status"}
        sets = ["updated_at = ?"]
        params = [now]
        for k, v in kwargs.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                params.append(v)
        params.append(zone_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE zones SET {', '.join(sets)} WHERE id = ?", params)

    def delete(self, zone_id: int):
        with self._connect() as conn:
            conn.execute("DELETE FROM cluster_snapshots WHERE zone_id = ?", (zone_id,))
            conn.execute("DELETE FROM zones WHERE id = ?", (zone_id,))

    def save_snapshot(self, zone_id: int, clusters: list[dict], item_count: int,
                       platform_summary: dict, source_health: list[dict] = None):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO cluster_snapshots
                   (zone_id, collected_at, clusters_json, item_count,
                    platform_summary_json, source_health_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (zone_id, now, json.dumps(clusters), item_count,
                 json.dumps(platform_summary), json.dumps(source_health or [])),
            )

    def get_snapshots(self, zone_id: int, limit: int = 4) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM cluster_snapshots
                   WHERE zone_id = ?
                   ORDER BY collected_at DESC LIMIT ?""",
                (zone_id, limit),
            ).fetchall()
            return [{
                "id": r["id"],
                "zone_id": r["zone_id"],
                "collected_at": r["collected_at"],
                "clusters": json.loads(r["clusters_json"]),
                "item_count": r["item_count"],
                "platform_summary": json.loads(r["platform_summary_json"]),
                "source_health": json.loads(r["source_health_json"]),
            } for r in rows]
