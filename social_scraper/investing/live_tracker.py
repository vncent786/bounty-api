"""Canonical live ledger for Bounty investment ideas.

The tracker gives every idea one primary state. Scheduled collection/review is a
separate monitoring activity, so a WATCH does not appear twice as "monitored".
All inputs are persisted artifacts; building the ledger makes zero provider calls.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

TRACKER_SCHEMA_VERSION = "bounty-investment-tracker/1"
PRIMARY_STATES = (
    "INVESTIGATING",
    "PURSUE",
    "WATCH",
    "TREND_NOTE",
    "STANDING_MONITOR",
    "REJECTED",
    "ARCHIVED",
)


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _decision_state(value: Any) -> str:
    decision = _text(value).upper()
    return {
        "PURSUE": "PURSUE",
        "WATCH": "WATCH",
        "TREND_NOTE": "TREND_NOTE",
        "REJECT": "REJECTED",
        "REJECTED": "REJECTED",
    }.get(decision, "INVESTIGATING")


def _signals(rows: Any) -> list[dict[str, Any]]:
    output = []
    for row in _as_list(rows):
        if not isinstance(row, dict):
            continue
        output.append({
            "query": _text(row.get("canonical_query") or row.get("query")),
            "rising": row.get("formatted_rising_growth") or row.get("formatted_growth"),
            "geography": row.get("geography") or row.get("countries"),
            "url": row.get("source_url"),
        })
    return output


def _instruments(paths: Any) -> list[str]:
    values: list[str] = []
    for path in _as_list(paths):
        if isinstance(path, str):
            values.append(path)
        elif isinstance(path, dict):
            value = _text(path.get("instrument") or path.get("ticker"))
            if value:
                values.append(value)
    return list(dict.fromkeys(values))


def _transition(plan: Any) -> dict[str, Any] | None:
    if not isinstance(plan, dict):
        return None
    aliases = {
        "missing_assertion": ("missing_assertion",),
        "resolution_source": ("resolution_source", "resolution_source_or_observable"),
        "next_check": ("next_check", "next_check_event_or_date"),
        "promotion_condition": ("promotion_condition",),
        "kill_condition": ("kill_condition",),
        "expiry": ("expiry", "expiry_event_or_date"),
    }
    result: dict[str, Any] = {}
    for target, sources in aliases.items():
        value = next((plan.get(source) for source in sources if plan.get(source)), None)
        result[target] = value
    return result


def _monitor_jobs(cron_jobs_path: Path | None) -> list[dict[str, Any]]:
    payload = _load(cron_jobs_path) if cron_jobs_path else None
    if not payload:
        return []
    rows = payload.get("jobs") if isinstance(payload.get("jobs"), list) else []
    keep_tokens = ("bounty", "ghost", "chewy", "kdp")
    output = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = _text(row.get("name"))
        if not any(token in name.casefold() for token in keep_tokens):
            continue
        schedule = row.get("schedule")
        if isinstance(schedule, dict):
            schedule_text = _text(schedule.get("display") or schedule.get("expr") or schedule.get("run_at"))
        else:
            schedule_text = _text(row.get("schedule_display") or schedule)
        output.append({
            "job_id": _text(row.get("id") or row.get("job_id")),
            "name": name,
            "enabled": bool(row.get("enabled")),
            "state": _text(row.get("state") or ("scheduled" if row.get("enabled") else "paused")).lower(),
            "schedule": schedule_text,
            "next_run_at": row.get("next_run_at"),
            "last_run_at": row.get("last_run_at"),
            "last_status": row.get("last_status"),
        })
    return output


def _monitor_summary(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    if not jobs:
        return {"status": "unscheduled", "jobs": []}
    if any(row["enabled"] and row["state"] in {"scheduled", "running"} for row in jobs):
        status = "active"
    elif all(row["state"] == "completed" for row in jobs):
        status = "completed"
    else:
        status = "paused"
    return {"status": status, "jobs": jobs}


def _artifact_receipt(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(root)).replace("\\", "/"),
        "sha256": _sha256(path),
        "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if path.exists() else None,
    }


def build_investment_tracker(
    repo_root: str | Path | None = None,
    *,
    cron_jobs_path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    artifacts = root / "artifacts"
    source_paths = {
        "historical": artifacts / "investing-dd" / "social-six-2026-09-03" / "comparison.json",
        "fresh": artifacts / "investing-discovery" / "sop-v2-2026-09-04T1135Z" / "fresh-run-comparison.json",
        "overnight": artifacts / "investing-discovery" / "overnight-2026-09-05" / "overnight-decisions.json",
        "watch_plans": artifacts / "investing-discovery" / "overnight-2026-09-05" / "watch-transition-plans.json",
        "round2": artifacts / "investing-discovery" / "expansion-2026-09-05" / "dd-round-2" / "dd-round-2.json",
        "round2_status": artifacts / "investing-discovery" / "expansion-2026-09-05" / "dd-round-2" / "status.json",
        "batch": artifacts / "investing-discovery" / "overnight-2026-09-05" / "frozen-candidate-batch.json",
        "monitor_state": root / "data" / "investing-watch-monitor-state.json",
    }
    loaded = {key: _load(path) for key, path in source_paths.items()}
    jobs_path = Path(cron_jobs_path) if cron_jobs_path else Path("D:/Hermes/cron/jobs.json")
    monitor_jobs = _monitor_jobs(jobs_path if jobs_path.exists() else None)
    watch_plan_by_id = {
        row.get("id"): row.get("transition_plan")
        for row in (loaded["watch_plans"] or {}).get("watches", [])
        if isinstance(row, dict)
    }
    ideas: dict[str, dict[str, Any]] = {}

    def add(item: dict[str, Any], *, replace: bool = True) -> None:
        idea_id = _text(item.get("idea_id"))
        if not idea_id:
            return
        item["idea_id"] = idea_id
        state = _text(item.get("primary_state")).upper()
        item["primary_state"] = state if state in PRIMARY_STATES else "INVESTIGATING"
        if replace or idea_id not in ideas:
            ideas[idea_id] = item

    # Historical items are kept for audit. The pre-v2 WATCH is archived because
    # it lacks the current finite transition contract.
    historical = loaded["historical"] or {}
    for row in historical.get("candidates", []):
        decision = _text(row.get("verdict")).upper()
        state = "ARCHIVED" if decision == "WATCH" else _decision_state(decision)
        add({
            "idea_id": f"historical::{_text(row.get('node_key') or row.get('candidate')).casefold()}",
            "title": row.get("candidate"),
            "primary_state": state,
            "instruments": [_text(row.get("potential_stock"))] if row.get("potential_stock") else [],
            "why": row.get("reason"),
            "next_check": row.get("next_check"),
            "kill_condition": row.get("invalidation"),
            "source_run": "social-six-2026-09-03",
            "source_artifact": "artifacts/investing-dd/social-six-2026-09-03/comparison.json",
            "updated_at": historical.get("built_at"),
            "monitoring": {"status": "unscheduled", "jobs": []},
            "notes": "Historical v1 result; archived WATCH requires v2 requalification." if state == "ARCHIVED" else None,
        })

    # Fresh run rejects remain visible. Its three WATCH items are superseded by
    # the richer overnight decision rows.
    fresh = loaded["fresh"] or {}
    for row in fresh.get("investigations", []):
        if _text(row.get("verdict")).upper() == "WATCH":
            continue
        add({
            "idea_id": f"fresh::{_text(row.get('key') or row.get('title')).casefold()}",
            "title": row.get("title"),
            "primary_state": _decision_state(row.get("verdict")),
            "instruments": _as_list(row.get("paths")),
            "why": row.get("headline"),
            "detail": row.get("summary"),
            "signals": [{"query": row.get("signal"), "url": url} for url in _as_list(row.get("trend_links"))],
            "source_run": fresh.get("run_id"),
            "source_artifact": "artifacts/investing-discovery/sop-v2-2026-09-04T1135Z/fresh-run-comparison.json",
            "updated_at": fresh.get("as_of"),
            "monitoring": {"status": "unscheduled", "jobs": []},
        })

    overnight = loaded["overnight"] or {}
    for row in overnight.get("investigations", []):
        state = _decision_state(row.get("decision"))
        plan = row.get("transition_plan") or watch_plan_by_id.get(row.get("id"))
        add({
            "idea_id": f"overnight::{row.get('id')}",
            "title": row.get("title"),
            "primary_state": state,
            "instruments": _instruments(row.get("paths")),
            "why": row.get("decision_basis") or row.get("verification") or row.get("what_changed"),
            "detail": row.get("what_changed"),
            "signals": _signals(row.get("signals")),
            "catalyst": row.get("catalyst"),
            "transition_plan": _transition(plan),
            "next_check": (_transition(plan) or {}).get("next_check") if plan else None,
            "kill_condition": (_transition(plan) or {}).get("kill_condition") if plan else row.get("invalidation"),
            "source_run": overnight.get("run_id"),
            "source_artifact": "artifacts/investing-discovery/overnight-2026-09-05/overnight-decisions.json",
            "updated_at": overnight.get("as_of_utc"),
            "monitoring": {"status": "unscheduled", "jobs": []},
        })

    round2 = loaded["round2"] or {}
    for row in round2.get("groups", []):
        plan = row.get("transition_plan")
        add({
            "idea_id": f"round2::{row.get('group_id')}",
            "title": row.get("title"),
            "primary_state": _decision_state(row.get("decision")),
            "instruments": _instruments(row.get("identities") or row.get("paths")),
            "why": row.get("decision_basis"),
            "detail": row.get("what_changed"),
            "signals": _signals(row.get("signals")),
            "catalyst": row.get("catalyst"),
            "transition_plan": _transition(plan),
            "next_check": (_transition(plan) or {}).get("next_check") if plan else None,
            "kill_condition": (_transition(plan) or {}).get("kill_condition") if plan else row.get("invalidation"),
            "source_run": round2.get("run_id"),
            "source_artifact": "artifacts/investing-discovery/expansion-2026-09-05/dd-round-2/dd-round-2.json",
            "updated_at": round2.get("as_of_utc"),
            "monitoring": {"status": "unscheduled", "jobs": []},
        })

    # Attach one shared bounded transition monitor to every current WATCH. This
    # keeps monitoring as an activity instead of duplicating the idea state.
    watch_jobs = [
        row for row in monitor_jobs
        if "watch transition" in row["name"].casefold()
    ]
    for item in ideas.values():
        if item.get("primary_state") == "WATCH":
            item["monitoring"] = _monitor_summary(watch_jobs)

    monitor_state = loaded["monitor_state"] or {}
    for row in monitor_state.get("watches", []):
        if not isinstance(row, dict):
            continue
        item = ideas.get(_text(row.get("idea_id")))
        if not item or item.get("primary_state") != "WATCH":
            continue
        item["monitoring"] = {
            **item.get("monitoring", {}),
            "last_checked_at": row.get("checked_at") or monitor_state.get("checked_at"),
            "last_result": _text(row.get("monitor_state") or "NO_CHANGE").upper(),
            "due_reason": row.get("due_reason"),
            "evidence_urls": _as_list(row.get("evidence_urls")),
        }
        if item["monitoring"]["last_result"] in {"PROMOTE_CANDIDATE", "KILL_CANDIDATE", "EXPIRED"}:
            item["transition_alert"] = {
                "state": item["monitoring"]["last_result"],
                "proposed_verdict": row.get("proposed_verdict"),
                "rationale": row.get("rationale"),
                "evidence_urls": _as_list(row.get("evidence_urls")),
            }

    # Known theses and calibration work live separately from blind discovery.
    ghost_jobs = [row for row in monitor_jobs if "ghost" in row["name"].casefold() or "kdp" in row["name"].casefold()]
    chewy_jobs = [row for row in monitor_jobs if "chewy" in row["name"].casefold()]
    add({
        "idea_id": "standing::ghost-aw-kdp",
        "title": "GHOST Energy x A&W / KDP",
        "primary_state": "STANDING_MONITOR",
        "instruments": ["NASDAQ:KDP"],
        "why": "User-supplied standing thesis and calibration case; it does not count as blind discovery yield.",
        "source_run": "standing-monitor",
        "updated_at": max((row.get("last_run_at") or "" for row in ghost_jobs), default=None),
        "monitoring": _monitor_summary(ghost_jobs),
    })
    add({
        "idea_id": "standing::chewy",
        "title": "Chewy",
        "primary_state": "STANDING_MONITOR",
        "instruments": ["NYSE:CHWY"],
        "why": "User-supplied standing thesis and depth-calibration case; it does not count as blind discovery yield.",
        "source_run": "standing-monitor",
        "updated_at": max((row.get("last_run_at") or "" for row in chewy_jobs), default=None),
        "monitoring": _monitor_summary(chewy_jobs),
    })

    # Backlog is tracked as a denominator, not dumped into the active-name list.
    batch = loaded["batch"] or {}
    denominator = batch.get("denominator") if isinstance(batch.get("denominator"), dict) else {}
    initial_excluded = int(denominator.get("excluded_queue_occurrences") or 0)
    consumed_round2 = int((round2.get("scope") or {}).get("exact_lineages") or 8 if round2 else 0)
    backlog_lineages = max(0, initial_excluded - consumed_round2)

    rows = sorted(
        ideas.values(),
        key=lambda row: (
            PRIMARY_STATES.index(row["primary_state"]),
            _text(row.get("title")).casefold(),
        ),
    )
    state_counts = Counter(row["primary_state"] for row in rows)
    generated = now or datetime.now(timezone.utc)
    source_receipts = [
        _artifact_receipt(path, root) for path in source_paths.values() if path.exists()
    ]
    return {
        "schema_version": TRACKER_SCHEMA_VERSION,
        "generated_at": generated.astimezone(timezone.utc).isoformat(),
        "status": "complete" if loaded["overnight"] and loaded["round2"] else "partial",
        "production_deployment": "unchanged",
        "taxonomy": {
            "primary_states": list(PRIMARY_STATES),
            "rule": "Each idea has one primary state. Monitoring is a separate activity attached to that idea.",
            "definitions": {
                "INVESTIGATING": "Research is active; no final verdict yet.",
                "PURSUE": "Current evidence supports an actionable asymmetric thesis.",
                "WATCH": "A real path survives with a finite promotion, kill and expiry plan.",
                "TREND_NOTE": "The observation is real but lacks a material listed path or differentiated catalyst.",
                "STANDING_MONITOR": "Known or user-supplied thesis tracked separately from blind discovery yield.",
                "REJECTED": "A core thesis, materiality, parity, event or implementation gate failed.",
                "ARCHIVED": "Historical result retained for audit but not active under the current contract.",
            },
        },
        "summary": {
            "primary_state_counts": {state: state_counts.get(state, 0) for state in PRIMARY_STATES},
            "backlog_lineages": backlog_lineages,
            "monitor_jobs": len(monitor_jobs),
            "active_monitor_jobs": sum(row["enabled"] and row["state"] in {"scheduled", "running"} for row in monitor_jobs),
            "paused_monitor_jobs": sum(row["state"] == "paused" for row in monitor_jobs),
            "trade_ready_now": state_counts.get("PURSUE", 0) > 0,
        },
        "ideas": rows,
        "backlog": {
            "primary_state": "BACKLOG",
            "lineages": backlog_lineages,
            "source_queue_occurrences": initial_excluded,
            "consumed_by_round2": consumed_round2,
            "note": "Backlog counts remain visible without flooding the active idea list with uninvestigated names.",
        },
        "monitor_jobs": monitor_jobs,
        "source_receipts": source_receipts,
    }
