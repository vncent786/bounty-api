"""Private, fail-closed day-one information-arbitrage Radar."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator, Mapping, Sequence

from social_scraper.investing.qualification import is_specific_anchor, qualify_candidate
from social_scraper.investing.trajectory import (
    derive_trajectory_query,
    trajectory_is_usable,
)


PANEL_VERSION = "camillo-private-panels/5"
SCAN_SCHEMA_VERSION = "private-investing-radar/1"
MAX_DISCOVERY_RECORDS_PER_PANEL = 16
MAX_SHORTLIST_CANDIDATES = 8
NON_X_DISCOVERY_PLATFORMS = ("tiktok", "instagram", "reddit", "youtube")


@dataclasses.dataclass(frozen=True)
class Panel:
    panel_id: str
    name: str
    x_query: str
    search_term: str
    x_query_slices: tuple[str, ...] = ()


_BEHAVIOR_QUERY = (
    '("switched to" OR "started using" OR "stopped using" OR "stopped buying" '
    'OR "I bought" OR "ordered" OR "returned it" OR "sold out" '
    'OR "out of stock" OR "can\'t find" OR "price increase" '
    'OR "more expensive" OR "workaround")'
)
_BEHAVIOR_QUERY_SLICES = (
    '("switched to" OR "started using" OR "I bought" OR "ordered")',
    '("stopped using" OR "stopped buying" OR "returned it" OR "price increase" OR "more expensive")',
    '("sold out" OR "out of stock" OR "can\'t find" OR "restock")',
    '("doesn\'t work" OR "not working" OR "problem" OR "issue" OR "workaround" OR "temporary fix")',
)


def _panel(panel_id: str, name: str, scope: str, search_term: str) -> Panel:
    return Panel(
        panel_id,
        name,
        f"{_BEHAVIOR_QUERY} ({scope}) -filter:retweets",
        search_term,
        tuple(
            f"{behavior_query} ({scope}) -filter:retweets"
            for behavior_query in _BEHAVIOR_QUERY_SLICES
        ),
    )


DEFAULT_PANELS = (
    _panel(
        "automobiles", "Automobiles",
        "car OR cars OR vehicle OR vehicles OR EV OR SUV OR dealership",
        "cars vehicles people switched to or stopped buying",
    ),
    _panel(
        "airlines", "Airlines",
        "airline OR airlines OR flight OR flights OR baggage OR airport",
        "airlines flights people switched to or stopped using",
    ),
    _panel(
        "hotels_travel", "Hotels and travel",
        "hotel OR hotels OR resort OR travel OR booking OR vacation",
        "hotels travel services people switched to",
    ),
    _panel(
        "restaurants_qsr", "Restaurants and quick service",
        '"fast food" OR restaurant OR restaurants OR delivery OR takeout OR cafe',
        "restaurants delivery people switched to or stopped buying",
    ),
    _panel(
        "food_beverage", "Food and beverage",
        "snack OR snacks OR drink OR drinks OR grocery OR cereal OR soda OR coffee",
        "food drink products people switched to",
    ),
    _panel(
        "beauty_skincare", "Beauty and skincare",
        "skincare OR makeup OR haircare OR beauty OR fragrance",
        "skincare beauty products people switched to",
    ),
    _panel(
        "fashion_apparel", "Fashion and apparel",
        "apparel OR clothing OR shoes OR sneakers OR handbag OR fashion",
        "fashion apparel products people switched to",
    ),
    _panel(
        "luxury", "Luxury goods",
        '"luxury bag" OR watch OR watches OR jewelry OR fragrance OR designer',
        "luxury products people switched to or stopped buying",
    ),
    _panel(
        "retail", "Retail",
        "retailer OR store OR shopping OR membership OR warehouse",
        "retailers stores people switched to or stopped using",
    ),
    _panel(
        "consumer_technology", "Consumer technology",
        "headphones OR wearable OR smartwatch OR device OR gadget OR app",
        "consumer technology people switched to",
    ),
    _panel(
        "streaming", "Streaming and subscriptions",
        'streaming OR subscription OR Netflix OR "Disney Plus" OR Spotify OR YouTube',
        "streaming subscriptions people switched to or cancelled",
    ),
    _panel(
        "telecom", "Telecom and connectivity",
        'carrier OR "mobile plan" OR broadband OR "internet provider" OR telecom',
        "mobile broadband providers people switched to",
    ),
    _panel(
        "fintech_payments", "Fintech and payments",
        '"payment app" OR "bank app" OR "credit card" OR wallet OR "buy now pay later"',
        "payments banking products people switched to",
    ),
    _panel(
        "fitness_wearables", "Fitness and wearables",
        '"fitness tracker" OR smartwatch OR gym OR "workout app" OR wearable',
        "fitness wearables people started or stopped using",
    ),
    _panel(
        "pets", "Pets",
        '"pet food" OR "dog food" OR "cat food" OR veterinary OR "pet insurance"',
        "pet products services people switched to",
    ),
    _panel(
        "household_cleaning", "Household and cleaning",
        "cleaning OR detergent OR appliance OR cookware OR household",
        "household cleaning products people switched to",
    ),
)


_SYSTEM_PROMPT = """Rank and return at most eight strongest specific information-arbitrage candidates from the supplied current multi-platform social evidence. Prefer exact product, service, or problem anchors repeated by independent authors across platforms; a strong single-platform candidate may remain investigable but must not be described as cross-platform. Return JSON only: {"candidates":[{"panel_id":str,"label":str,"behaviour_type":str,"anchor_terms":[str],"trajectory_query":str,"trajectory_query_reason":str,"summary":str,"economic_mechanism":str,"why_investigate":str,"contradiction":str,"invalidation":str,"evidence_ids":[str]}],"limitations":[str]}. Use only supplied evidence IDs. trend_candidates are Google search-attention seeds, never proof; only return one when supplied social records independently support the behavior. trajectory_query must be a concise public search phrase (normally two to four words) for one comparable Google Trends request; trajectory_query_reason must explain why that phrase best represents the specific behavior and name any ambiguity. It is a movement sensor, not social proof. Allowed behaviour types: purchase, adoption, switching, shortage, rejection, pain_point, price_change, workaround. Reject broad themes such as AI, inflation, fitness, technology, news, shopping, and viral. Anchor terms must be exact specific product/service/problem phrases present in cited evidence; never include a broad panel category such as coffee, beauty, food, device, or household. Do not name a company unless cited evidence names it. Do not infer revenue or materiality. Return no candidate when evidence is vague, perennial, promotional, political, entertainment-only, or sentiment without behavior."""


def _utc_iso(value: datetime | str | None = None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def stable_evidence_id(item: Mapping[str, Any]) -> str:
    material = _json([
        "private-radar-evidence/1",
        str(item.get("panel_id") or ""),
        str(item.get("platform") or ""),
        str(item.get("external_id") or ""),
        str(item.get("url") or ""),
        str(item.get("created_at") or ""),
        str(item.get("text") or "")[:1000],
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


class PrivateRadarError(RuntimeError):
    pass


class PrivateRadarCoverageUnavailable(PrivateRadarError):
    pass


REQUIRED_QUALIFICATION_GATES = {
    "specificity", "behavior", "evidence_quality", "persistence", "anomaly",
    "breadth", "parity", "investigability",
}


def is_supported_qualified(
    decision: Mapping[str, Any],
    available_evidence_ids: set[str] | None = None,
    evidence_panel_by_id: Mapping[str, str] | None = None,
) -> bool:
    if decision.get("qualification_status") != "qualified" or not decision.get("candidate_id"):
        return False
    evidence_ids = {str(value) for value in decision.get("evidence_ids") or []}
    if len(evidence_ids) < 2:
        return False
    if available_evidence_ids is not None and not evidence_ids.issubset(available_evidence_ids):
        return False
    if evidence_panel_by_id is not None:
        candidate_panel = str(decision.get("panel_id") or "")
        if not candidate_panel or any(
            evidence_panel_by_id.get(evidence_id) != candidate_panel
            for evidence_id in evidence_ids
        ):
            return False
    gates = decision.get("gates")
    if not isinstance(gates, Mapping) or not REQUIRED_QUALIFICATION_GATES.issubset(gates):
        return False
    return all(gates[name].get("passed") is True for name in REQUIRED_QUALIFICATION_GATES)


def link_review_evidence(
    decision: Mapping[str, Any],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Link review citations to exactly one panel, inferring it only for legacy rows."""
    evidence_ids = list(dict.fromkeys(
        str(value) for value in decision.get("evidence_ids") or []
    ))
    linked = [dict(evidence_by_id[eid]) for eid in evidence_ids if eid in evidence_by_id]
    if not evidence_ids or len(linked) != len(evidence_ids):
        return "", []
    panels = {str(item.get("panel_id") or "") for item in linked}
    if "" in panels or len(panels) != 1:
        return "", []
    linked_panel = next(iter(panels))
    decision_panel = str(decision.get("panel_id") or "")
    if decision_panel and decision_panel != linked_panel:
        return "", []
    return decision_panel or linked_panel, linked


def review_decision_with_current_methodology(
    decision: Mapping[str, Any],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Recheck a saved near-miss against current rules without collecting new data."""
    linked_panel, linked = link_review_evidence(decision, evidence_by_id)
    if not linked:
        return None, []
    required = (
        "label", "behaviour_type", "anchor_terms", "summary", "economic_mechanism",
        "why_investigate", "contradiction", "invalidation",
    )
    if not all(decision.get(field) for field in required):
        value = {**decision}
    else:
        value = qualify_candidate(
            {**decision, "panel_id": linked_panel},
            evidence=linked,
            windows=decision.get("windows") or [],
            parity=decision.get("parity") or {
                "level": "unknown", "status": "not_checked", "articles": [],
            },
        )
        linked_map = {str(item["id"]): item for item in linked}
        linked = [
            linked_map[eid] for eid in value.get("evidence_ids") or []
            if eid in linked_map
        ]
    value["decision_basis"] = (
        "Rechecked with the current deterministic rules against the same stored "
        "evidence. No new collection or historical backfill was performed."
    )
    if isinstance(decision.get("trajectory"), Mapping):
        value["trajectory"] = dict(decision["trajectory"])
    if isinstance(decision.get("movement_bundle"), Mapping):
        value["movement_bundle"] = dict(decision["movement_bundle"])
    value["inferred_panel_id"] = (
        linked_panel if not str(decision.get("panel_id") or "") else None
    )
    return value, linked


def candidate_review_status(decision: Mapping[str, Any]) -> tuple[str, list[str], list[str]]:
    """Classify a cited near-miss without weakening strict lead promotion."""
    gates = decision.get("gates") if isinstance(decision.get("gates"), Mapping) else {}
    blockers = []
    caveats = []
    preflight_failed = any(
        isinstance(gates.get(name), Mapping)
        and gates[name].get("state") == "fail"
        for name in (
            "specificity", "behavior", "evidence_quality", "persistence", "breadth", "investigability"
        )
    )
    for name in (
        "specificity", "behavior", "evidence_quality", "persistence", "breadth",
        "anomaly", "parity", "investigability",
    ):
        if preflight_failed and name in {"anomaly", "parity"}:
            continue
        gate = gates.get(name)
        if not isinstance(gate, Mapping) or gate.get("state") == "pass":
            continue
        reason = str(gate.get("reason") or "").strip()
        if reason and reason not in blockers:
            blockers.append(reason)

    citation_filter = (
        decision.get("citation_filter")
        if isinstance(decision.get("citation_filter"), Mapping)
        else {}
    )
    dropped = int(citation_filter.get("dropped") or 0)
    relevant = int(citation_filter.get("relevant") or 0)
    if dropped:
        caveats.append(
            f"{dropped} cited record{'s were' if dropped != 1 else ' was'} removed because "
            "the text did not support the specific subject."
        )
    breadth_metrics = (
        gates.get("breadth", {}).get("metrics", {})
        if isinstance(gates.get("breadth"), Mapping)
        else {}
    )
    if breadth_metrics.get("cross_platform") is False and int(breadth_metrics.get("authors") or 0) >= 2:
        caveats.append("Independent voices were found on one platform only.")

    failed = {
        name for name, gate in gates.items()
        if isinstance(gate, Mapping) and gate.get("state") == "fail"
    }
    unknown = {
        name for name, gate in gates.items()
        if isinstance(gate, Mapping) and gate.get("state") == "unknown"
    }
    behavior_metrics = (
        gates.get("behavior", {}).get("metrics", {})
        if isinstance(gates.get("behavior"), Mapping)
        else {}
    )
    quality_metrics = (
        gates.get("evidence_quality", {}).get("metrics", {})
        if isinstance(gates.get("evidence_quality"), Mapping)
        else {}
    )
    if relevant < 2 and dropped > 0:
        status = "rejected"
    elif failed & {"specificity", "anomaly", "parity", "investigability"}:
        status = "rejected"
    elif "evidence_quality" in failed:
        status = "rejected" if (
            int(quality_metrics.get("reportage_records") or 0)
            >= max(1, int(quality_metrics.get("firsthand_records") or 0))
        ) else "needs_more_evidence"
    elif unknown & {"anomaly", "parity"} and not failed:
        status = (
            "search_movement_only"
            if trajectory_is_usable(decision.get("trajectory"))
            else "rejected"
        )
    elif (
        ("behavior" in failed and int(behavior_metrics.get("records") or 0) >= 1)
        or ("breadth" in failed and int(breadth_metrics.get("authors") or 0) >= 1)
    ):
        status = "needs_more_evidence"
    else:
        status = "rejected"
    return status, blockers, caveats


class PrivateRadarStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        stale_scan_after_seconds: float = 3 * 60,
    ):
        self.db_path = str(db_path)
        self.stale_scan_after_seconds = max(0.001, float(stale_scan_after_seconds))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=15, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA journal_mode=WAL")
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

    def ensure_schema(self):
        with self._connect() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS private_radar_scans (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                heartbeat_at TEXT,
                completed_at TEXT,
                status TEXT NOT NULL CHECK(status IN (
                    'running','complete','no_qualified_leads','failed'
                )),
                stage TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                panel_version TEXT NOT NULL,
                requested_panels_json TEXT NOT NULL,
                evidence_count INTEGER NOT NULL DEFAULT 0,
                candidate_count INTEGER NOT NULL DEFAULT 0,
                decisions_json TEXT NOT NULL DEFAULT '[]',
                limitations_json TEXT NOT NULL DEFAULT '[]',
                sources_json TEXT NOT NULL DEFAULT '[]',
                error_category TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_private_radar_one_running
                ON private_radar_scans(status) WHERE status='running';
            CREATE TABLE IF NOT EXISTS private_radar_evidence (
                run_id TEXT NOT NULL REFERENCES private_radar_scans(id),
                id TEXT NOT NULL,
                panel_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                external_id TEXT,
                url TEXT NOT NULL,
                author TEXT,
                text TEXT NOT NULL,
                created_at TEXT,
                observed_at TEXT NOT NULL,
                window_key TEXT,
                query TEXT,
                raw_json TEXT NOT NULL,
                PRIMARY KEY(run_id,id)
            );
            CREATE INDEX IF NOT EXISTS idx_private_radar_evidence_run
                ON private_radar_evidence(run_id,panel_id,window_key,id);
            CREATE TRIGGER IF NOT EXISTS private_radar_evidence_no_update
            BEFORE UPDATE ON private_radar_evidence BEGIN
                SELECT RAISE(ABORT,'private radar evidence is immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS private_radar_evidence_no_delete
            BEFORE DELETE ON private_radar_evidence BEGIN
                SELECT RAISE(ABORT,'private radar evidence is immutable');
            END;
            """)
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(private_radar_scans)")
            }
            if "heartbeat_at" not in columns:
                connection.execute(
                    "ALTER TABLE private_radar_scans ADD COLUMN heartbeat_at TEXT"
                )
            connection.execute(
                """UPDATE private_radar_scans
                   SET heartbeat_at=started_at
                   WHERE heartbeat_at IS NULL OR heartbeat_at=''"""
            )
            connection.commit()

    def create_scan_if_idle(self, panels: Sequence[Panel] | None = None) -> tuple[str, bool]:
        selected = list(panels or DEFAULT_PANELS)
        with self._transaction() as connection:
            running = connection.execute(
                """SELECT id,started_at,heartbeat_at
                   FROM private_radar_scans WHERE status='running' LIMIT 1"""
            ).fetchone()
            if running:
                heartbeat = datetime.fromisoformat(
                    str(running["heartbeat_at"] or running["started_at"]).replace("Z", "+00:00")
                )
                if heartbeat.tzinfo is None:
                    heartbeat = heartbeat.replace(tzinfo=timezone.utc)
                age_seconds = (
                    datetime.now(timezone.utc) - heartbeat.astimezone(timezone.utc)
                ).total_seconds()
                if age_seconds <= self.stale_scan_after_seconds:
                    return str(running["id"]), False
                connection.execute(
                    """UPDATE private_radar_scans SET completed_at=?,status='failed',
                       stage='failed',error_category='stale_scan_recovered'
                       WHERE id=? AND status='running'""",
                    (_utc_iso(), running["id"]),
                )
            run_id = uuid.uuid4().hex
            now = _utc_iso()
            connection.execute(
                """INSERT INTO private_radar_scans
                   (id,started_at,heartbeat_at,status,stage,progress,panel_version,requested_panels_json)
                   VALUES (?,?,?,'running','starting',0,?,?)""",
                (
                    run_id,
                    now,
                    now,
                    PANEL_VERSION,
                    _json([panel.panel_id for panel in selected]),
                ),
            )
        return run_id, True

    def update_progress(
        self, run_id: str, *, stage: str, progress: int,
        sources: Sequence[Mapping[str, Any]] | None = None,
    ):
        with self._transaction() as connection:
            cursor = connection.execute(
                """UPDATE private_radar_scans SET stage=?,progress=?,heartbeat_at=?,
                   sources_json=COALESCE(?,sources_json) WHERE id=? AND status='running'""",
                (
                    str(stage),
                    max(0, min(100, int(progress))),
                    _utc_iso(),
                    _json(list(sources)) if sources is not None else None,
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise PrivateRadarError("private radar scan is missing or finalized")

    def heartbeat_scan(self, run_id: str) -> bool:
        """Renew an active scan without reviving a terminal or reclaimed row."""
        with self._transaction() as connection:
            cursor = connection.execute(
                """UPDATE private_radar_scans SET heartbeat_at=?
                   WHERE id=? AND status='running'""",
                (_utc_iso(), run_id),
            )
            return cursor.rowcount == 1

    def add_evidence(self, run_id: str, evidence: Sequence[Mapping[str, Any]]):
        with self._transaction() as connection:
            run = connection.execute(
                "SELECT status FROM private_radar_scans WHERE id=?", (run_id,)
            ).fetchone()
            if not run or run["status"] != "running":
                raise PrivateRadarError("private radar scan is missing or finalized")
            for source in evidence:
                item = dict(source)
                item_id = str(item.get("id") or stable_evidence_id(item))
                url = str(item.get("url") or "").strip()
                text = str(item.get("text") or "").strip()
                if not url.startswith(("http://", "https://")) or not text:
                    continue
                connection.execute(
                    """INSERT OR IGNORE INTO private_radar_evidence
                       (run_id,id,panel_id,platform,external_id,url,author,text,created_at,
                        observed_at,window_key,query,raw_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        run_id, item_id, str(item.get("panel_id") or "unknown"),
                        str(item.get("platform") or "unknown"), item.get("external_id"),
                        url, item.get("author"), text[:6000], item.get("created_at"),
                        str(item.get("observed_at") or _utc_iso()), item.get("window_key"),
                        item.get("query"), _json(item),
                    ),
                )
            cursor = connection.execute(
                """UPDATE private_radar_scans SET heartbeat_at=?,evidence_count=(
                   SELECT COUNT(*) FROM private_radar_evidence WHERE run_id=?)
                   WHERE id=? AND status='running'""",
                (_utc_iso(), run_id, run_id),
            )
            if cursor.rowcount != 1:
                raise PrivateRadarError("private radar scan is missing or finalized")

    def evidence_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id,panel_id,platform,external_id,url,author,text,created_at,
                          observed_at,window_key,query,raw_json
                   FROM private_radar_evidence WHERE run_id=? ORDER BY rowid""",
                (run_id,),
            ).fetchall()
        values = []
        for row in rows:
            item = dict(row)
            try:
                raw = json.loads(str(item.pop("raw_json") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = {}
            item["engagement"] = (
                dict(raw.get("engagement"))
                if isinstance(raw.get("engagement"), Mapping)
                else {}
            )
            values.append(item)
        return values

    def complete_scan(
        self, run_id: str, decisions: Sequence[Mapping[str, Any]], *,
        limitations: Sequence[str], sources: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        values = [dict(value) for value in decisions]
        with self._transaction() as connection:
            evidence_rows = connection.execute(
                "SELECT id,panel_id FROM private_radar_evidence WHERE run_id=?",
                (run_id,),
            ).fetchall()
            available_evidence_ids = {str(row["id"]) for row in evidence_rows}
            evidence_panel_by_id = {
                str(row["id"]): str(row["panel_id"]) for row in evidence_rows
            }
            qualified = [
                value
                for value in values
                if is_supported_qualified(
                    value, available_evidence_ids, evidence_panel_by_id
                )
            ]
            has_incomplete_coverage = any(
                value.get("qualification_status") == "unknown_due_to_coverage"
                for value in values
            )
            if qualified:
                final_status, final_stage, error_category = "complete", "complete", None
            elif has_incomplete_coverage:
                final_status, final_stage, error_category = (
                    "failed", "failed", "coverage_incomplete"
                )
            else:
                final_status, final_stage, error_category = (
                    "no_qualified_leads", "complete", None
                )
            cursor = connection.execute(
                """UPDATE private_radar_scans SET completed_at=?,status=?,stage=?,
                   progress=100,candidate_count=?,decisions_json=?,limitations_json=?,
                   sources_json=COALESCE(?,sources_json),error_category=?
                   WHERE id=? AND status='running'""",
                (
                    _utc_iso(), final_status, final_stage, len(qualified), _json(values),
                    _json(list(limitations)), _json(list(sources)) if sources is not None else None,
                    error_category, run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise PrivateRadarError("private radar scan is missing or finalized")
        return self.get_scan(run_id)

    def fail_scan(self, run_id: str, error_category: str) -> dict[str, Any]:
        with self._transaction() as connection:
            cursor = connection.execute(
                """UPDATE private_radar_scans SET completed_at=?,status='failed',stage='failed',
                   error_category=? WHERE id=? AND status='running'""",
                (_utc_iso(), str(error_category), run_id),
            )
            if cursor.rowcount != 1:
                existing = connection.execute(
                    "SELECT id FROM private_radar_scans WHERE id=?", (run_id,)
                ).fetchone()
                if not existing:
                    raise PrivateRadarError("private radar scan is missing")
        result = self.get_scan(run_id)
        if result is None:  # Defensive: the row cannot disappear inside the transaction.
            raise PrivateRadarError("private radar scan is missing")
        return result

    def get_scan(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM private_radar_scans WHERE id=?", (run_id,)
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        for key in ("requested_panels", "decisions", "limitations", "sources"):
            result[key] = json.loads(result.pop(f"{key}_json"))
        return result

    def latest_attempt(self):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM private_radar_scans ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        return self.get_scan(row["id"]) if row else None

    def latest_qualified_scan(self):
        with self._connect() as connection:
            row = connection.execute(
                """SELECT id FROM private_radar_scans
                   WHERE status='complete' AND candidate_count>0 ORDER BY rowid DESC LIMIT 1"""
            ).fetchone()
        return self.get_scan(row["id"]) if row else None

    @staticmethod
    def _public_scan(scan):
        if not scan:
            return None
        return {key: scan.get(key) for key in (
            "id", "started_at", "completed_at", "status", "stage", "progress",
            "panel_version", "evidence_count", "candidate_count", "error_category",
        )}

    def public_payload(self) -> dict[str, Any]:
        attempt = self.latest_attempt()
        if attempt and attempt["status"] == "no_qualified_leads":
            data_scan = attempt
        elif attempt and attempt["status"] in {"running", "failed"}:
            data_scan = self.latest_qualified_scan()
        else:
            data_scan = attempt
        review_scan = (
            attempt
            if attempt and attempt.get("decisions") and attempt.get("status") != "running"
            else data_scan
        )
        coverage_scan = review_scan or data_scan
        if not coverage_scan:
            return {
                "items": [], "review_items": [],
                "trend_discovery": None,
                "last_attempt": self._public_scan(attempt), "data_scan": None,
                "review_scan": None, "displaying_previous_data": False,
                "coverage": {"summary": "No private investment scan has completed yet", "sources": []},
            }

        items = []
        if data_scan:
            qualified_evidence = {
                item["id"]: item for item in self.evidence_for_run(data_scan["id"])
            }
            evidence_panel_by_id = {
                evidence_id: str(item.get("panel_id") or "")
                for evidence_id, item in qualified_evidence.items()
            }
            for saved_decision in data_scan["decisions"]:
                decision, linked = review_decision_with_current_methodology(
                    saved_decision, qualified_evidence
                )
                if not decision or not is_supported_qualified(
                    decision, set(qualified_evidence), evidence_panel_by_id
                ):
                    continue
                items.append({**decision, "evidence": linked})

        review_evidence = {
            item["id"]: item for item in self.evidence_for_run(review_scan["id"])
        } if review_scan else {}
        review_panel_by_id = {
            evidence_id: str(item.get("panel_id") or "")
            for evidence_id, item in review_evidence.items()
        }
        review_items = []
        for saved_decision in (review_scan or {}).get("decisions", []):
            decision, linked = review_decision_with_current_methodology(
                saved_decision, review_evidence
            )
            if not decision or not linked or is_supported_qualified(
                decision, set(review_evidence), review_panel_by_id
            ):
                continue
            review_status, blocking_reasons, caveats = candidate_review_status(decision)
            review_items.append({
                **decision,
                "review_status": review_status,
                "blocking_reasons": blocking_reasons,
                "caveats": caveats,
                "evidence": linked,
            })
        review_items.sort(key=lambda item: (
            {"search_movement_only": 0, "needs_more_evidence": 1, "rejected": 2}.get(
                str(item.get("review_status")), 3
            ),
            str(item.get("label") or ""),
        ))
        trend_source = next(
            (
                source for source in coverage_scan["sources"]
                if source.get("stage") == "trend_discovery"
                and source.get("platform") == "google_trends"
            ),
            None,
        )
        trend_discovery = None if not trend_source else {
            "status": str(trend_source.get("status") or "unknown"),
            "observed_at": trend_source.get("observed_at"),
            "geographies": list(trend_source.get("geographies") or []),
            "candidates": [
                dict(value) for value in trend_source.get("candidates") or []
                if isinstance(value, Mapping)
            ],
        }
        discovery_sources = [
            source for source in coverage_scan["sources"]
            if source.get("stage") == "discovery"
            or (
                source.get("platform") == "x"
                and source.get("query_index") is not None
                and source.get("window_key") is None
            )
        ]
        initial_sources = [
            source for source in discovery_sources
            if source.get("platform") == "x"
            and source.get("query_index") is not None
            and source.get("window_key") is None
        ]
        funnel = {
            "panel_count": len({source.get("panel_id") for source in initial_sources}),
            "query_scopes": len(initial_sources),
            "complete_scopes": sum(
                source.get("status") == "complete" for source in initial_sources
            ),
            "capped_scopes": sum(
                bool((source.get("coverage") or {}).get("requested_limit_reached"))
                for source in initial_sources
            ),
            "reported_records": sum(int(source.get("count") or 0) for source in initial_sources),
        }
        platform_coverage = {}
        for source in discovery_sources:
            platform = str(source.get("platform") or "unknown")
            value = platform_coverage.setdefault(platform, {
                "scopes": 0,
                "complete": 0,
                "partial": 0,
                "failed": 0,
                "reported_records": 0,
            })
            value["scopes"] += 1
            status = str(source.get("status") or "failed")
            if status in {"complete", "partial"}:
                value[status] += 1
            else:
                value["failed"] += 1
            value["reported_records"] += int(source.get("count") or 0)
        coverage_summary = (
            f"{len(items)} trade-ready leads and {len(review_items)} reviewed subjects "
            f"from {coverage_scan['evidence_count']} stored evidence records"
        )
        if funnel["query_scopes"]:
            coverage_summary += (
                f"; X discovery checked {funnel['complete_scopes']}/{funnel['query_scopes']} query scopes"
            )
            if funnel["capped_scopes"]:
                coverage_summary += (
                    f"; {funnel['capped_scopes']} reached the per-query sample limit"
                )
        if platform_coverage:
            platforms_with_records = sum(
                value["reported_records"] > 0 for value in platform_coverage.values()
            )
            coverage_summary += (
                f"; discovery records found on {platforms_with_records}/{len(platform_coverage)} platforms"
            )
        return {
            "items": items,
            "review_items": review_items,
            "trend_discovery": trend_discovery,
            "last_attempt": self._public_scan(attempt),
            "data_scan": self._public_scan(data_scan),
            "review_scan": self._public_scan(review_scan),
            "displaying_previous_data": bool(attempt and data_scan and attempt["id"] != data_scan["id"]),
            "coverage": {
                "summary": coverage_summary,
                "sources": coverage_scan["sources"],
                "initial_funnel": funnel,
                "platforms": platform_coverage,
            },
        }


def _parse_model_json(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    value = json.loads(text.strip())
    if not isinstance(value, dict) or not isinstance(value.get("candidates"), list):
        raise PrivateRadarError("private radar model output is invalid")
    return value


async def propose_candidates(
    evidence: Sequence[Mapping[str, Any]], *,
    llm_call_fn: Callable[[str, str], Awaitable[str]],
    panels: Sequence[Panel],
    trend_candidates: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    records = [dict(item) for item in evidence]
    if not records:
        return [], ["No citable current social evidence was collected."]
    balanced_records = []
    for panel in panels:
        panel_records = [item for item in records if item.get("panel_id") == panel.panel_id]
        by_scope: dict[str, list[dict[str, Any]]] = {}
        for item in panel_records:
            scope_key = f"{item.get('platform') or 'unknown'}:{item.get('query') or ''}"
            by_scope.setdefault(scope_key, []).append(item)
        selected = []
        per_scope = max(
            1, MAX_DISCOVERY_RECORDS_PER_PANEL // max(1, len(by_scope))
        )
        for scope_records in by_scope.values():
            selected.extend(scope_records[:per_scope])
        selected_ids = {str(item.get("id")) for item in selected}
        if len(selected) < MAX_DISCOVERY_RECORDS_PER_PANEL:
            selected.extend(
                item for item in panel_records
                if str(item.get("id")) not in selected_ids
            )
        balanced_records.extend(selected[:MAX_DISCOVERY_RECORDS_PER_PANEL])
    payload = {
        "schema_version": SCAN_SCHEMA_VERSION,
        "panel_version": PANEL_VERSION,
        "panels": [dataclasses.asdict(panel) for panel in panels],
        "trend_candidates": [dict(value) for value in (trend_candidates or [])],
        "records": [{
            "id": item.get("id"), "panel_id": item.get("panel_id"),
            "platform": item.get("platform"), "author": item.get("author"),
            "text": str(item.get("text") or "")[:1200], "created_at": item.get("created_at"),
        } for item in balanced_records],
    }
    parsed = _parse_model_json(await llm_call_fn(_SYSTEM_PROMPT, _json(payload)))
    known_ids = {str(item.get("id")) for item in records}
    known_panel_by_id = {
        str(item.get("id")): str(item.get("panel_id") or "") for item in records
    }
    panel_ids = {panel.panel_id for panel in panels}
    accepted = []
    seen_panels = set()
    for raw in parsed["candidates"][:MAX_SHORTLIST_CANDIDATES]:
        if not isinstance(raw, Mapping):
            continue
        value = dict(raw)
        requested_ids = list(dict.fromkeys(
            str(eid) for eid in value.get("evidence_ids") or []
        ))
        candidate_panel = str(value.get("panel_id") or "")
        ids = [
            eid for eid in requested_ids
            if known_panel_by_id.get(eid) == candidate_panel
        ]
        anchors = [
            str(anchor).strip()
            for anchor in value.get("anchor_terms") or []
            if str(anchor).strip() and is_specific_anchor(anchor)
        ]
        if (
            value.get("panel_id") not in panel_ids
            or value.get("panel_id") in seen_panels
            or not str(value.get("label") or "").strip()
            or not ids
            or any(eid not in known_ids for eid in requested_ids)
            or not anchors
        ):
            continue
        value["evidence_ids"] = ids
        value["anchor_terms"] = anchors[:5]
        trajectory_query = " ".join(
            str(value.get("trajectory_query") or "").strip().split()
        )
        value["trajectory_query"] = (
            trajectory_query[:80] or derive_trajectory_query(value)
        )
        value["trajectory_query_reason"] = str(
            value.get("trajectory_query_reason")
            or "Derived from the candidate's specific product and behavior anchors."
        ).strip()[:300]
        accepted.append(value)
        seen_panels.add(value["panel_id"])
    limitations = [str(item)[:300] for item in parsed.get("limitations") or [] if isinstance(item, str)]
    return accepted, limitations


class PrivateRadarScanner:
    def __init__(
        self,
        store: PrivateRadarStore,
        collector,
        *,
        panels: Sequence[Panel] | None = None,
        llm_call_fn: Callable[[str, str], Awaitable[str]] | None = None,
        news_check_fn: Callable[[str, Sequence[str]], Awaitable[dict[str, Any]]] | None = None,
        trajectory_check_fn: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        movement_bundle_fn: Callable[[Sequence[Mapping[str, Any]]], Awaitable[list[dict[str, Any]]]] | None = None,
        heartbeat_interval_seconds: float = 30.0,
    ):
        self.store = store
        self.collector = collector
        self.panels = tuple(panels or DEFAULT_PANELS)
        self.llm_call_fn = llm_call_fn
        self.news_check_fn = news_check_fn
        self.trajectory_check_fn = trajectory_check_fn
        self.movement_bundle_fn = movement_bundle_fn
        self.heartbeat_interval_seconds = max(
            0.001, float(heartbeat_interval_seconds)
        )

    async def _heartbeat_loop(self, run_id: str) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            if not self.store.heartbeat_scan(run_id):
                return

    async def run(self, *, run_id: str | None = None):
        if run_id is None:
            run_id, created = self.store.create_scan_if_idle(self.panels)
            if not created:
                return self.store.get_scan(run_id)
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(run_id))
        sources = []
        limitations = []
        trend_candidates = []
        try:
            if hasattr(self.collector, "preflight"):
                self.store.update_progress(
                    run_id, stage="checking_mandatory_sources", progress=1
                )
                preflight = await self.collector.preflight()
                sources.extend(preflight.get("sources") or [])
                self.store.update_progress(
                    run_id,
                    stage=(
                        "preflight_complete" if preflight.get("ok")
                        else "preflight_failed"
                    ),
                    progress=3,
                    sources=sources,
                )
                if not preflight.get("ok"):
                    return self.store.fail_scan(
                        run_id,
                        str(preflight.get("error_category") or "preflight_failed"),
                    )
            if hasattr(self.collector, "collect_trend_discovery"):
                self.store.update_progress(
                    run_id, stage="discovering_google_trends", progress=4,
                    sources=sources,
                )
                trend_result = await self.collector.collect_trend_discovery()
                trend_candidates = list(trend_result.get("trend_candidates") or [])
                trend_sources = list(trend_result.get("sources") or [])
                sources.extend(trend_sources)
                self.store.add_evidence(
                    run_id, trend_result.get("evidence") or []
                )
                trend_receipt = next(
                    (
                        source for source in trend_sources
                        if source.get("stage") == "trend_discovery"
                        and source.get("platform") == "google_trends"
                    ),
                    {},
                )
                if (
                    trend_receipt.get("status") not in {"complete", "partial"}
                    or not trend_candidates
                ):
                    self.store.update_progress(
                        run_id, stage="trend_discovery_failed", progress=4,
                        sources=sources,
                    )
                    return self.store.fail_scan(
                        run_id, "trend_discovery_unavailable"
                    )
            self.store.update_progress(run_id, stage="collecting_current_evidence", progress=5)
            for index, panel in enumerate(self.panels):
                result = await self.collector.collect_discovery(panel)
                self.store.add_evidence(run_id, result.get("evidence") or [])
                sources.extend(result.get("sources") or [])
                self.store.update_progress(
                    run_id, stage=f"collecting_{panel.panel_id}",
                    progress=10 + int(30 * (index + 1) / len(self.panels)), sources=sources,
                )
            current_evidence = self.store.evidence_for_run(run_id)
            discovery_sources = [
                source for source in sources if source.get("stage") == "discovery"
            ]
            x_sources = [
                source for source in discovery_sources if source.get("platform") == "x"
            ]
            expected_x_scopes = sum(
                max(1, len(panel.x_query_slices)) for panel in self.panels
            )
            non_x_sources = [
                source for source in discovery_sources
                if source.get("platform") in NON_X_DISCOVERY_PLATFORMS
            ]
            expected_non_x_scopes = len(self.panels) * len(NON_X_DISCOVERY_PLATFORMS)
            x_incomplete = (
                len(x_sources) != expected_x_scopes
                or any(source.get("status") != "complete" for source in x_sources)
            )
            non_x_incomplete = (
                len(non_x_sources) != expected_non_x_scopes
                or any(
                    source.get("status") not in {"complete", "partial"}
                    or bool(source.get("error_category"))
                    for source in non_x_sources
                )
            )
            if x_incomplete or non_x_incomplete:
                raise PrivateRadarCoverageUnavailable(
                    "required discovery sources were unavailable or incomplete"
                )
            if self.llm_call_fn is None:
                from social_scraper.llm_client import call_llm

                async def model(system, user):
                    return await call_llm(system, user, max_tokens=5000, temperature=0.0)
            else:
                model = self.llm_call_fn
            proposals, model_limitations = await propose_candidates(
                current_evidence, llm_call_fn=model, panels=self.panels,
                trend_candidates=trend_candidates,
            )
            limitations.extend(model_limitations)
            movement_bundles: list[dict[str, Any]] = []
            if self.movement_bundle_fn is not None and proposals:
                try:
                    movement_bundles = list(
                        await self.movement_bundle_fn(proposals)
                    )
                except Exception as exc:
                    limitations.append(
                        f"Selectable Google Trends movement failed: {type(exc).__name__}."
                    )
            self.store.update_progress(run_id, stage="checking_history_and_coverage", progress=50)
            panel_by_id = {panel.panel_id: panel for panel in self.panels}
            decisions = []
            for index, proposal in enumerate(proposals):
                panel = panel_by_id[proposal["panel_id"]]
                corroboration = await self.collector.collect_corroboration(
                    panel, proposal["anchor_terms"]
                )
                self.store.add_evidence(run_id, corroboration.get("evidence") or [])
                sources.extend(corroboration.get("sources") or [])
                all_evidence = self.store.evidence_for_run(run_id)
                anchors = [
                    " ".join(re.sub(r"[^a-z0-9]+", " ", term.casefold()).split())
                    for term in proposal["anchor_terms"]
                ]
                evidence_ids = set(proposal["evidence_ids"])
                for item in all_evidence:
                    if item.get("panel_id") != proposal["panel_id"]:
                        continue
                    text = " ".join(
                        re.sub(
                            r"[^a-z0-9]+",
                            " ",
                            str(item.get("text") or "").casefold(),
                        ).split()
                    )
                    if any(anchor and anchor in text for anchor in anchors):
                        evidence_ids.add(item["id"])
                proposal = {**proposal, "evidence_ids": sorted(evidence_ids)}
                candidate_evidence = [
                    item for item in all_evidence
                    if item["id"] in evidence_ids
                    and item.get("panel_id") == proposal["panel_id"]
                    and item.get("window_key") in {None, "current"}
                ]
                trajectory_query = str(
                    proposal.get("trajectory_query")
                    or derive_trajectory_query(proposal)
                ).strip()
                movement_bundle = (
                    dict(movement_bundles[index])
                    if index < len(movement_bundles)
                    and isinstance(movement_bundles[index], Mapping)
                    else {}
                )
                default_geo = str(
                    movement_bundle.get("default_geo") or "WORLDWIDE"
                )
                default_horizon = str(
                    movement_bundle.get("default_horizon") or "3m"
                )
                trajectory = dict(
                    (((movement_bundle.get("series") or {}).get(default_geo) or {}).get(default_horizon) or {})
                )
                if not trajectory and self.trajectory_check_fn is None:
                    trajectory = {
                        "query": trajectory_query,
                        "source": "Google Trends",
                        "status": "failed",
                        "normalized": True,
                        "points": [],
                        "error_category": "not_configured",
                    }
                elif not trajectory:
                    try:
                        trajectory = await self.trajectory_check_fn(trajectory_query)
                    except Exception as exc:
                        trajectory = {
                            "query": trajectory_query,
                            "source": "Google Trends",
                            "status": "failed",
                            "normalized": True,
                            "points": [],
                            "error_category": type(exc).__name__,
                        }
                preliminary = qualify_candidate(
                    proposal,
                    evidence=candidate_evidence,
                    windows=[],
                    parity={
                        "level": "unknown",
                        "status": "not_checked_before_history",
                        "articles": [],
                    },
                )
                preliminary["trajectory"] = dict(trajectory)
                if movement_bundle:
                    preliminary["movement_bundle"] = movement_bundle
                preflight_gates = (
                    "specificity", "behavior", "evidence_quality", "persistence", "breadth", "investigability"
                )
                if any(
                    preliminary["gates"][name]["state"] == "fail"
                    for name in preflight_gates
                ):
                    decisions.append(preliminary)
                else:
                    historical = await self.collector.collect_windows(
                        panel, proposal["anchor_terms"]
                    )
                    self.store.add_evidence(
                        run_id, historical.get("evidence") or []
                    )
                    sources.extend(historical.get("sources") or [])
                    if self.news_check_fn is None:
                        parity = {
                            "level": "unknown",
                            "status": "not_checked",
                            "articles": [],
                        }
                    else:
                        parity = await self.news_check_fn(
                            proposal["label"], proposal["anchor_terms"]
                        )
                    decision = qualify_candidate(
                        proposal,
                        evidence=candidate_evidence,
                        windows=historical.get("windows") or [],
                        parity=parity,
                    )
                    decision["trajectory"] = dict(trajectory)
                    if movement_bundle:
                        decision["movement_bundle"] = movement_bundle
                    decisions.append(decision)
                self.store.update_progress(
                    run_id, stage="qualifying_candidates",
                    progress=55 + int(40 * (index + 1) / max(1, len(proposals))),
                    sources=sources,
                )
            return self.store.complete_scan(
                run_id, decisions, limitations=limitations, sources=sources
            )
        except asyncio.CancelledError:
            self.store.fail_scan(run_id, "cancelled")
            raise
        except Exception as exc:
            return self.store.fail_scan(run_id, type(exc).__name__)
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
