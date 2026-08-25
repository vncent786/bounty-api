"""Persisted, citation-gated social-first discovery for the Investing Radar."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator, Mapping, Sequence

from social_scraper.discovery.triage import _has_public_source_url


SOCIAL_PULSE_SCHEMA_VERSION = "social-pulse/1"
SOCIAL_PROBE_VERSION = "investing-behaviour/1"
SOCIAL_PLATFORMS = ("reddit", "youtube", "tiktok", "instagram", "x")
ALLOWED_BEHAVIOURS = {
    "purchase", "adoption", "switching", "shortage", "rejection",
    "pain_point", "price_change", "workaround", "other",
}
DEFAULT_REDDIT_SCOPES = (
    "BuyItForLife", "technology", "Futurology", "HomeImprovement",
    "SkincareAddiction",
)


_SYSTEM_PROMPT = """Identify concrete investable-behaviour subjects from supplied social records. Return only JSON: {"candidates":[{"label":str,"behaviour_type":str,"summary":str,"why_investigate":str,"evidence_ids":[str]}],"limitations":[str]}. Allowed behaviour_type values: purchase, adoption, switching, shortage, rejection, pain_point, price_change, workaround, other. A candidate must describe a specific product, service, technology, material, problem, or changing behaviour, not a generic phrase such as news or viral. Use only supplied evidence IDs. Do not name companies unless the supplied record names them. Do not infer revenue, stock impact, demographics, causality, or market materiality. Prefer repeated independent voices or cross-platform support, but retain a genuinely concrete single-source lead and make that limitation explicit."""


class SocialPulseError(RuntimeError):
    pass


def _utc_iso(value: datetime | str | None = None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _mapping(item: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(item):
        return dataclasses.asdict(item)
    if isinstance(item, Mapping):
        return dict(item)
    if callable(getattr(item, "to_dict", None)):
        return dict(item.to_dict())
    raise SocialPulseError("social evidence must be a mapping or data object")


def _metric(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _normalise_evidence(platform: str, item: Any, observed_at: str) -> dict[str, Any] | None:
    raw = _mapping(item)
    url = str(raw.get("url") or "").strip()
    text = str(raw.get("text") or raw.get("title") or "").strip()
    if not text or not _has_public_source_url(url):
        return None
    author = raw.get("author")
    if isinstance(author, Mapping):
        author_name = str(author.get("username") or author.get("name") or "").strip()
    else:
        author_name = str(raw.get("author_username") or author or "").strip()
    engagement = raw.get("engagement") if isinstance(raw.get("engagement"), Mapping) else {}
    evidence_id = uuid.uuid4().hex
    return {
        "id": evidence_id,
        "platform": platform,
        "external_id": str(raw.get("post_id") or raw.get("external_id") or "").strip() or None,
        "url": url,
        "author": author_name or None,
        "text": text[:4000],
        "created_at": raw.get("created_at") or raw.get("published_at"),
        "observed_at": observed_at,
        "views": _metric(raw.get("views") if raw.get("views") is not None else engagement.get("views")),
        "likes": _metric(raw.get("likes") if raw.get("likes") is not None else engagement.get("likes")),
        "comments": _metric(raw.get("comments") if raw.get("comments") is not None else engagement.get("comments")),
        "shares": _metric(raw.get("shares") if raw.get("shares") is not None else engagement.get("shares")),
        "raw": raw,
    }


def _parse_model_json(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    parsed = json.loads(text.strip())
    if not isinstance(parsed, dict):
        raise ValueError("social candidate response must be an object")
    return parsed


def _fallback_behaviour(text: str) -> str:
    value = text.casefold()
    rules = (
        ("shortage", ("sold out", "out of stock", "can't find", "cannot find", "restock")),
        ("switching", ("switched to", "switching to", "replaced my", "moved from")),
        ("purchase", ("i bought", "we bought", "purchased", "ordered", "buying")),
        ("rejection", ("banned", "bans ", "ban ", "boycott", "opposition", "objecting", "stopped using")),
        ("price_change", ("discount", "% off", "price increase", "price hike", "cheaper")),
        ("pain_point", ("problem", "issue", "doesn't work", "not working", "struggling", "pain", "acne")),
        ("adoption", ("started using", "now use", "installed", "new treatment", "adopting")),
        ("workaround", ("workaround", "hack", "temporary fix", "instead of")),
    )
    for behaviour, phrases in rules:
        if any(phrase in value for phrase in phrases):
            return behaviour
    return "other"


def _deterministic_fallback_candidates(
    evidence: Sequence[Mapping[str, Any]], max_candidates: int
) -> list[dict[str, Any]]:
    """Return source-native leads when optional LLM synthesis is unavailable."""
    grouped: dict[str, list[dict[str, Any]]] = {platform: [] for platform in SOCIAL_PLATFORMS}
    for item in evidence:
        grouped.setdefault(str(item.get("platform")), []).append(dict(item))
    ordered: list[dict[str, Any]] = []
    while len(ordered) < max_candidates and any(grouped.values()):
        for platform in SOCIAL_PLATFORMS:
            values = grouped.get(platform) or []
            if values and len(ordered) < max_candidates:
                ordered.append(values.pop(0))
    candidates = []
    seen: set[str] = set()
    for item in ordered:
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if not text:
            continue
        first_sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
        label = first_sentence[:157].rstrip(" ,;:-")
        if len(first_sentence) > 157:
            label += "..."
        key = label.casefold()
        if not label or key in seen:
            continue
        platform = str(item.get("platform") or "unknown")
        metrics = {
            field: int(item[field]) for field in ("views", "likes", "comments", "shares")
            if item.get(field) is not None
        }
        metric_copy = ", ".join(f"{name}={value:,}" for name, value in metrics.items())
        candidates.append({
            "label": label,
            "behaviour_type": _fallback_behaviour(text),
            "summary": text[:500],
            "why_investigate": (
                f"Source-native {platform} lead retained without model synthesis"
                + (f"; observed {metric_copy}." if metric_copy else ".")
            ),
            "evidence_ids": [str(item["id"])],
            "voice_count": 1,
            "platform_count": 1,
            "platforms": [platform],
            "support_type": "single_source_early",
            "engagement_by_platform": {platform: metrics},
            "extraction_mode": "deterministic_fallback",
        })
        seen.add(key)
    return candidates


async def extract_social_candidates(
    evidence: Sequence[Mapping[str, Any]],
    *,
    llm_call_fn: Callable[[str, str], Awaitable[str]] | None = None,
    max_candidates: int = 8,
) -> dict[str, Any]:
    usable = [dict(item) for item in evidence if item.get("id") and item.get("url")]
    if not usable:
        return {
            "status": "insufficient_evidence",
            "candidates": [],
            "limitations": ["No citable social records were collected."],
            "error_category": None,
        }

    def fallback(error_category: str, limitation: str) -> dict[str, Any]:
        candidates = _deterministic_fallback_candidates(usable, max_candidates)
        return {
            "status": "supported_fallback" if candidates else "analysis_unavailable",
            "candidates": candidates,
            "limitations": [limitation],
            "error_category": error_category,
        }

    if llm_call_fn is None:
        from social_scraper.llm_client import call_llm

        async def llm_call_fn(system: str, user: str) -> str:
            return await call_llm(system, user, max_tokens=4000, temperature=0.0)

    alias_to_id: dict[str, str] = {}
    model_records = []
    by_id = {str(item["id"]): item for item in usable}
    for index, item in enumerate(usable, start=1):
        alias = f"E{index}"
        alias_to_id[alias] = str(item["id"])
        model_records.append({
            "id": alias,
            "platform": item.get("platform"),
            "author": item.get("author"),
            "text": str(item.get("text") or "")[:1200],
            "created_at": item.get("created_at"),
            "engagement": {
                key: item.get(key) for key in ("views", "likes", "comments", "shares")
                if item.get(key) is not None
            },
        })
    try:
        raw = await llm_call_fn(
            _SYSTEM_PROMPT,
            _json({
                "schema_version": SOCIAL_PULSE_SCHEMA_VERSION,
                "max_candidates": max_candidates,
                "records": model_records,
            }),
        )
    except Exception:
        return fallback(
            "provider_error",
            "Model synthesis was unavailable; showing source-native leads without cross-record clustering.",
        )
    try:
        parsed = _parse_model_json(raw)
    except Exception:
        return fallback(
            "parse_error",
            "Model output could not be parsed; showing source-native leads without cross-record clustering.",
        )

    raw_candidates = parsed.get("candidates")
    if not isinstance(raw_candidates, list):
        return fallback(
            "schema_error",
            "Model output had an invalid schema; showing source-native leads without cross-record clustering.",
        )

    accepted = []
    seen_labels: set[str] = set()
    rejected = 0
    for raw_candidate in raw_candidates:
        if not isinstance(raw_candidate, Mapping):
            rejected += 1
            continue
        label = str(raw_candidate.get("label") or "").strip()
        behaviour = str(raw_candidate.get("behaviour_type") or "").strip()
        aliases = raw_candidate.get("evidence_ids")
        if (
            not label or len(label) > 160 or behaviour not in ALLOWED_BEHAVIOURS
            or not isinstance(aliases, list) or not aliases
            or any(alias not in alias_to_id for alias in aliases)
        ):
            rejected += 1
            continue
        key = " ".join(label.casefold().split())
        if key in seen_labels:
            continue
        evidence_ids = list(dict.fromkeys(alias_to_id[alias] for alias in aliases))
        records = [by_id[evidence_id] for evidence_id in evidence_ids]
        voices = {
            f"{item.get('platform')}:{item.get('author') or item.get('external_id') or item['id']}"
            for item in records
        }
        platforms = sorted({str(item.get("platform")) for item in records})
        engagement_by_platform: dict[str, dict[str, int]] = {}
        for item in records:
            platform = str(item.get("platform"))
            metrics = engagement_by_platform.setdefault(platform, {})
            for field in ("views", "likes", "comments", "shares"):
                if item.get(field) is not None:
                    metrics[field] = metrics.get(field, 0) + int(item[field])
        support = (
            "cross_platform" if len(platforms) > 1
            else "repeated_voices" if len(voices) > 1
            else "single_source_early"
        )
        accepted.append({
            "label": label,
            "behaviour_type": behaviour,
            "summary": str(raw_candidate.get("summary") or "").strip()[:700],
            "why_investigate": str(raw_candidate.get("why_investigate") or "").strip()[:700],
            "evidence_ids": evidence_ids,
            "voice_count": len(voices),
            "platform_count": len(platforms),
            "platforms": platforms,
            "support_type": support,
            "engagement_by_platform": engagement_by_platform,
        })
        seen_labels.add(key)
        if len(accepted) >= max_candidates:
            break

    support_order = {"cross_platform": 0, "repeated_voices": 1, "single_source_early": 2}
    accepted.sort(key=lambda item: (
        support_order.get(item["support_type"], 3),
        -item["voice_count"],
        -item["platform_count"],
        item["label"].casefold(),
    ))
    limitations = [
        str(item)[:300] for item in parsed.get("limitations", []) if isinstance(item, str)
    ]
    if rejected:
        limitations.append(f"{rejected} uncitable or invalid candidate outputs were rejected.")
    return {
        "status": "supported" if accepted else "insufficient_evidence",
        "candidates": accepted,
        "limitations": list(dict.fromkeys(limitations)),
        "error_category": None,
    }


class SocialPulseStore:
    """Additive SQLite store for immutable social discovery evidence."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
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
            CREATE TABLE IF NOT EXISTS investing_social_pulse_runs (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL CHECK(status IN (
                    'running','complete','partial','empty','analysis_unavailable','failed'
                )),
                probe_version TEXT NOT NULL,
                requested_platforms_json TEXT NOT NULL,
                evidence_count INTEGER NOT NULL DEFAULT 0,
                candidate_count INTEGER NOT NULL DEFAULT 0,
                candidates_json TEXT NOT NULL DEFAULT '[]',
                limitations_json TEXT NOT NULL DEFAULT '[]',
                analysis_error_category TEXT
            );
            CREATE TABLE IF NOT EXISTS investing_social_pulse_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES investing_social_pulse_runs(id),
                platform TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('complete','partial','empty','unavailable','failed')),
                observed_at TEXT NOT NULL,
                evidence_count INTEGER NOT NULL,
                error_category TEXT,
                UNIQUE(run_id, platform)
            );
            CREATE TABLE IF NOT EXISTS investing_social_pulse_evidence (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES investing_social_pulse_runs(id),
                platform TEXT NOT NULL,
                external_id TEXT,
                url TEXT NOT NULL,
                author TEXT,
                text TEXT NOT NULL,
                created_at TEXT,
                observed_at TEXT NOT NULL,
                views INTEGER,
                likes INTEGER,
                comments INTEGER,
                shares INTEGER,
                raw_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_social_pulse_runs_started
                ON investing_social_pulse_runs(started_at, id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_social_pulse_one_running
                ON investing_social_pulse_runs(status) WHERE status='running';
            CREATE INDEX IF NOT EXISTS idx_social_pulse_evidence_run
                ON investing_social_pulse_evidence(run_id, platform, id);
            CREATE TRIGGER IF NOT EXISTS investing_social_pulse_evidence_no_update
            BEFORE UPDATE ON investing_social_pulse_evidence BEGIN
                SELECT RAISE(ABORT, 'social pulse evidence is immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS investing_social_pulse_evidence_no_delete
            BEFORE DELETE ON investing_social_pulse_evidence BEGIN
                SELECT RAISE(ABORT, 'social pulse evidence is immutable');
            END;
            """)
            connection.commit()

    def create_run(self, platforms: Sequence[str] = SOCIAL_PLATFORMS) -> str:
        selected = list(dict.fromkeys(str(platform).strip() for platform in platforms))
        if not selected or any(platform not in SOCIAL_PLATFORMS for platform in selected):
            raise SocialPulseError("requested social platforms are invalid")
        run_id = uuid.uuid4().hex
        with self._transaction() as connection:
            running = connection.execute(
                "SELECT id FROM investing_social_pulse_runs WHERE status='running' LIMIT 1"
            ).fetchone()
            if running:
                raise SocialPulseError(f"social pulse run {running['id']} is already running")
            connection.execute(
                """INSERT INTO investing_social_pulse_runs
                   (id, started_at, status, probe_version, requested_platforms_json)
                   VALUES (?, ?, 'running', ?, ?)""",
                (run_id, _utc_iso(), SOCIAL_PROBE_VERSION, _json(selected)),
            )
        return run_id

    def create_run_if_idle(
        self, platforms: Sequence[str] = SOCIAL_PLATFORMS
    ) -> tuple[str, bool]:
        selected = list(dict.fromkeys(str(platform).strip() for platform in platforms))
        if not selected or any(platform not in SOCIAL_PLATFORMS for platform in selected):
            raise SocialPulseError("requested social platforms are invalid")
        with self._transaction() as connection:
            running = connection.execute(
                "SELECT id FROM investing_social_pulse_runs WHERE status='running' LIMIT 1"
            ).fetchone()
            if running:
                return str(running["id"]), False
            run_id = uuid.uuid4().hex
            connection.execute(
                """INSERT INTO investing_social_pulse_runs
                   (id, started_at, status, probe_version, requested_platforms_json)
                   VALUES (?, ?, 'running', ?, ?)""",
                (run_id, _utc_iso(), SOCIAL_PROBE_VERSION, _json(selected)),
            )
        return run_id, True

    def fail_stale_run(self, run_id: str, error_category: str) -> dict[str, Any]:
        with self._transaction() as connection:
            cursor = connection.execute(
                """UPDATE investing_social_pulse_runs
                   SET completed_at=?, status='failed', analysis_error_category=?
                   WHERE id=? AND status='running'""",
                (_utc_iso(), str(error_category), run_id),
            )
            if cursor.rowcount != 1:
                raise SocialPulseError("social pulse run is missing or finalized")
        result = self.get_run(run_id)
        assert result is not None
        return result

    def record_source(
        self,
        run_id: str,
        platform: str,
        *,
        status: str,
        items: Sequence[Any] = (),
        error_category: str | None = None,
        observed_at: datetime | str | None = None,
    ) -> dict[str, Any]:
        if platform not in SOCIAL_PLATFORMS or status not in {
            "complete", "partial", "empty", "unavailable", "failed"
        }:
            raise SocialPulseError("invalid social source outcome")
        stamp = _utc_iso(observed_at)
        evidence = []
        for item in list(items)[:20]:
            normalised = _normalise_evidence(platform, item, stamp)
            if normalised:
                evidence.append(normalised)
        if evidence and status in {"empty", "unavailable", "failed"}:
            status = "partial" if error_category else "complete"
        if not evidence and status == "complete":
            status = "empty"
        if status in {"complete", "empty"}:
            error_category = None
        elif not error_category:
            raise SocialPulseError("failed/unavailable source requires error_category")
        with self._transaction() as connection:
            run = connection.execute(
                "SELECT status FROM investing_social_pulse_runs WHERE id=?", (run_id,)
            ).fetchone()
            if not run or run["status"] != "running":
                raise SocialPulseError("social pulse run is missing or finalized")
            connection.execute(
                """INSERT INTO investing_social_pulse_sources
                   (run_id, platform, status, observed_at, evidence_count, error_category)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, platform, status, stamp, len(evidence), error_category),
            )
            for item in evidence:
                connection.execute(
                    """INSERT INTO investing_social_pulse_evidence
                       (id, run_id, platform, external_id, url, author, text, created_at,
                        observed_at, views, likes, comments, shares, raw_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item["id"], run_id, platform, item["external_id"], item["url"],
                        item["author"], item["text"], item["created_at"], item["observed_at"],
                        item["views"], item["likes"], item["comments"], item["shares"],
                        _json(item["raw"]),
                    ),
                )
            connection.execute(
                """UPDATE investing_social_pulse_runs
                   SET evidence_count=(SELECT COUNT(*) FROM investing_social_pulse_evidence WHERE run_id=?)
                   WHERE id=?""",
                (run_id, run_id),
            )
        return {"platform": platform, "status": status, "evidence_count": len(evidence)}

    def evidence_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, platform, external_id, url, author, text, created_at,
                          observed_at, views, likes, comments, shares
                   FROM investing_social_pulse_evidence WHERE run_id=?
                   ORDER BY platform, id""",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def complete_run(self, run_id: str, extraction: Mapping[str, Any]) -> dict[str, Any]:
        candidates = list(extraction.get("candidates") or [])
        analysis_status = str(extraction.get("status") or "analysis_unavailable")
        with self._transaction() as connection:
            run = connection.execute(
                "SELECT * FROM investing_social_pulse_runs WHERE id=?", (run_id,)
            ).fetchone()
            if not run or run["status"] != "running":
                raise SocialPulseError("social pulse run is missing or finalized")
            sources = connection.execute(
                "SELECT status FROM investing_social_pulse_sources WHERE run_id=?",
                (run_id,),
            ).fetchall()
            statuses = [row["status"] for row in sources]
            requested = json.loads(run["requested_platforms_json"])
            checked_sources = sum(status in {"complete", "partial", "empty"} for status in statuses)
            has_source_gaps = len(statuses) < len(requested) or any(
                status in {"partial", "failed", "unavailable"} for status in statuses
            )
            if analysis_status == "analysis_unavailable":
                final_status = "analysis_unavailable"
            elif candidates:
                final_status = "partial" if has_source_gaps else "complete"
            elif not run["evidence_count"]:
                if checked_sources == len(requested):
                    final_status = "empty"
                elif checked_sources:
                    final_status = "partial"
                else:
                    final_status = "failed"
            else:
                final_status = "partial" if has_source_gaps else "empty"
            connection.execute(
                """UPDATE investing_social_pulse_runs
                   SET completed_at=?, status=?, candidate_count=?, candidates_json=?,
                       limitations_json=?, analysis_error_category=? WHERE id=?""",
                (
                    _utc_iso(), final_status, len(candidates), _json(candidates),
                    _json(list(extraction.get("limitations") or [])),
                    extraction.get("error_category"), run_id,
                ),
            )
        result = self.get_run(run_id)
        assert result is not None
        return result

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            run = connection.execute(
                "SELECT * FROM investing_social_pulse_runs WHERE id=?", (run_id,)
            ).fetchone()
            if not run:
                return None
            sources = connection.execute(
                """SELECT platform, status, observed_at, evidence_count, error_category
                   FROM investing_social_pulse_sources WHERE run_id=? ORDER BY id""",
                (run_id,),
            ).fetchall()
        result = dict(run)
        for field in ("requested_platforms", "candidates", "limitations"):
            result[field] = json.loads(result.pop(f"{field}_json"))
        result["sources"] = [dict(row) for row in sources]
        return result

    def latest_attempt(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM investing_social_pulse_runs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        return self.get_run(row["id"]) if row else None

    def latest_data_run(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT id FROM investing_social_pulse_runs
                   WHERE completed_at IS NOT NULL AND candidate_count > 0
                   ORDER BY rowid DESC LIMIT 1"""
            ).fetchone()
        return self.get_run(row["id"]) if row else None

    def public_payload(self) -> dict[str, Any]:
        attempt = self.latest_attempt()
        data_run = self.latest_data_run()
        if not data_run:
            return {
                "items": [],
                "last_attempt": _public_run(attempt),
                "data_run": None,
                "coverage": _coverage(attempt, None),
            }
        evidence = {item["id"]: item for item in self.evidence_for_run(data_run["id"])}
        items = []
        for index, candidate in enumerate(data_run["candidates"], start=1):
            linked = [evidence[eid] for eid in candidate.get("evidence_ids", []) if eid in evidence]
            public_evidence = [{
                key: item.get(key) for key in (
                    "id", "platform", "url", "author", "text", "created_at",
                    "observed_at", "views", "likes", "comments", "shares",
                )
            } for item in linked]
            items.append({"id": f"{data_run['id']}:{index}", **candidate, "evidence": public_evidence})
        return {
            "items": items,
            "last_attempt": _public_run(attempt),
            "data_run": _public_run(data_run),
            "coverage": _coverage(attempt, data_run),
        }


def _public_run(run: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not run:
        return None
    return {key: run.get(key) for key in (
        "id", "started_at", "completed_at", "status", "probe_version",
        "evidence_count", "candidate_count", "analysis_error_category",
    )}


def _coverage(attempt: Mapping[str, Any] | None, data_run: Mapping[str, Any] | None) -> dict[str, Any]:
    source_run = attempt or data_run
    sources = list(source_run.get("sources") or []) if source_run else []
    return {
        "sources": [{
            "platform": source.get("platform"),
            "status": source.get("status"),
            "evidence_count": source.get("evidence_count"),
        } for source in sources],
        "summary": (
            f"{sum(source.get('status') in {'complete', 'partial', 'empty'} for source in sources)} "
            f"of {len(source_run.get('requested_platforms') or [])} social sources checked"
            if source_run else "No social collection has completed yet"
        ),
        "displaying_previous_data": bool(
            attempt and data_run and attempt.get("id") != data_run.get("id")
        ),
    }


SourceFetcher = Callable[[], Awaitable[dict[str, Any]]]


class SocialPulseCollector:
    def __init__(
        self,
        store: SocialPulseStore,
        source_fetchers: Mapping[str, SourceFetcher],
        *,
        llm_call_fn: Callable[[str, str], Awaitable[str]] | None = None,
    ):
        self.store = store
        self.source_fetchers = dict(source_fetchers)
        self.llm_call_fn = llm_call_fn

    async def run(self, *, run_id: str | None = None) -> dict[str, Any]:
        if run_id is None:
            run_id = self.store.create_run()
        else:
            existing = self.store.get_run(run_id)
            if not existing or existing["status"] != "running":
                raise SocialPulseError("social pulse run is missing or finalized")
        tasks = {
            platform: asyncio.create_task(self.source_fetchers[platform]())
            for platform in SOCIAL_PLATFORMS if platform in self.source_fetchers
        }
        try:
            for platform in SOCIAL_PLATFORMS:
                if platform not in tasks:
                    self.store.record_source(
                        run_id, platform, status="unavailable",
                        error_category="source_not_configured",
                    )
                    continue
                try:
                    result = await tasks[platform]
                    self.store.record_source(
                        run_id,
                        platform,
                        status=str(result.get("status") or "failed"),
                        items=list(result.get("items") or []),
                        error_category=result.get("error_category"),
                        observed_at=result.get("observed_at"),
                    )
                except Exception as exc:
                    self.store.record_source(
                        run_id, platform, status="failed",
                        error_category=type(exc).__name__,
                    )
        finally:
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
        evidence = self.store.evidence_for_run(run_id)
        extraction = await extract_social_candidates(
            _balanced_evidence(evidence), llm_call_fn=self.llm_call_fn
        )
        return self.store.complete_run(run_id, extraction)


def _balanced_evidence(evidence: Sequence[Mapping[str, Any]], per_platform: int = 12) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {platform: [] for platform in SOCIAL_PLATFORMS}
    for item in evidence:
        grouped.setdefault(str(item.get("platform")), []).append(dict(item))
    selected = []
    for platform in SOCIAL_PLATFORMS:
        rows = grouped.get(platform, [])
        rows.sort(key=lambda item: (
            item.get("comments") or 0,
            item.get("likes") or 0,
            item.get("views") or 0,
            item.get("created_at") or "",
        ), reverse=True)
        selected.extend(rows[:per_platform])
    return selected


async def build_default_social_fetchers() -> dict[str, SourceFetcher]:
    """Build bounded fetchers around the existing registered connectors."""
    from apis.social_search_api import build_default_broker
    from social_scraper.connectors.reddit_mobile import RedditMobileConnector
    from social_scraper.connectors.youtube import YouTubeConnector

    broker = build_default_broker(route_timeout_seconds=90)

    async def reddit_fetch() -> dict[str, Any]:
        result = await RedditMobileConnector(max_subreddits=5).search_with_options(
            "", count=30, time_filter="week", sort="latest",
            options={"subreddits": list(DEFAULT_REDDIT_SCOPES)},
        )
        return _connector_payload(result)

    async def youtube_fetch() -> dict[str, Any]:
        connector = YouTubeConnector()
        queries = ("products everyone is buying 2026", "why I switched products 2026")
        items = []
        statuses = []
        for query in queries:
            result = await connector.search(query, count=6, time_filter="month", sort="views")
            statuses.append(result.health.status)
            items.extend(result.items)
        has_failure = any(status not in {"ok", "complete"} for status in statuses)
        return {
            "status": ("partial" if has_failure else "complete") if items else ("failed" if "error" in statuses else "empty"),
            "items": items,
            "error_category": (
                "partial_source_coverage" if items and has_failure
                else None if items else "youtube_discovery_unavailable"
            ),
            "observed_at": _utc_iso(),
        }

    def broker_fetch(platform: str, keyword: str) -> SourceFetcher:
        async def fetch() -> dict[str, Any]:
            result = await broker.search(
                keyword=keyword,
                platforms=[platform],
                count=12,
                time_filter="week",
                sort="hot",
                region="US",
            )
            items = [item for item in result.get("items", []) if item.get("platform") == platform]
            health = next((
                entry for entry in result.get("source_health", [])
                if entry.get("platform") == platform
            ), {})
            health_status = str(health.get("status") or "")
            return {
                "status": (
                    "partial" if health_status not in {"ok", "complete"} else "complete"
                ) if items else ("failed" if health_status == "error" else "empty"),
                "items": items,
                "error_category": (
                    health.get("error") or "partial_source_coverage"
                    if items and health_status not in {"ok", "complete"}
                    else None if items else (
                        health.get("error") or f"{platform}_discovery_unavailable"
                    )
                ),
                "observed_at": health.get("fetched_at") or _utc_iso(),
            }
        return fetch

    return {
        "reddit": reddit_fetch,
        "youtube": youtube_fetch,
        "tiktok": broker_fetch("tiktok", "tiktok made me buy it"),
        "instagram": broker_fetch("instagram", "tiktok made me buy it"),
        "x": broker_fetch("x", '"I switched to" OR "sold out" OR "can\'t find"'),
    }


def _connector_payload(result: Any) -> dict[str, Any]:
    items = list(getattr(result, "items", []) or [])
    health = getattr(result, "health", None)
    status = getattr(health, "status", None)
    return {
        "status": (
            "partial" if status not in {"ok", "complete"} else "complete"
        ) if items else ("failed" if status == "error" else "empty"),
        "items": items,
        "error_category": (
            getattr(health, "error", None) or "partial_source_coverage"
            if items and status not in {"ok", "complete"}
            else None if items else (getattr(health, "error", None) or "source_empty")
        ),
        "observed_at": getattr(health, "fetched_at", None) or _utc_iso(),
    }
