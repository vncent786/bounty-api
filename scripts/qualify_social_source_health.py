"""Source-only qualification for Bounty's required social platforms.

Runs production-shaped search + depth canaries and one additional bounded search
per platform. It writes only a qualification artifact, never monitor/dashboard
pointers or investment conclusions.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ghost_social_deepcheck import (  # noqa: E402
    _connector_for,
    _owned_tiktok_paths,
    _preflight_platform,
    configure_owned_social,
    load_environment,
)
from social_scraper.investing.owned_radar import OwnedRadarCollector  # noqa: E402

REQUIRED_PLATFORMS = ("x", "tiktok", "instagram", "reddit", "youtube")
CONNECTORS = {
    "x": "x_scweet",
    "tiktok": "authenticated",
    "instagram": "ig_auth_web",
    "reddit": "reddit_mobile_owned",
    "youtube": "ytdlp_free",
}
CANARIES = {
    "x": "nike",
    "tiktok": "nike",
    "instagram": "#nike",
    "reddit": "running shoes",
    "youtube": "iphone",
}
SCALE_QUERIES = {
    "x": "apple",
    "tiktok": "adidas",
    "instagram": "#adidas",
    "reddit": "coffee",
    "youtube": "samsung",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def classify_failure_layer(error: Any) -> str:
    value = str(error or "").strip().casefold()
    if not value:
        return "none"
    if "daily_budget" in value or "budget_exhausted" in value:
        return "local_budget"
    if "rate_limit" in value or "rate_limited" in value or value == "http_429":
        return "upstream_rate_limit"
    if "profile_busy" in value or "worker_busy" in value or "lock_timeout" in value:
        return "profile_concurrency"
    if any(token in value for token in ("auth", "credential", "session_expired", "login")):
        return "credential_or_session"
    if "timeout" in value:
        return "operation_timeout"
    if any(token in value for token in ("empty_response", "parser", "response_shape")):
        return "parser_or_response_shape"
    if any(token in value for token in ("complete_no_match", "query_empty", "no_match")):
        return "candidate_no_match"
    if "http_" in value or "network" in value or "unavailable" in value:
        return "upstream_or_network"
    return "connector_or_unknown"


def qualification_passed(
    cycles: list[Mapping[str, Any]], *, required_cycles: int = 2
) -> bool:
    if len(cycles) < required_cycles:
        return False
    return all(
        isinstance(cycle.get("platforms"), Mapping)
        and set(cycle["platforms"]) == set(REQUIRED_PLATFORMS)
        and all(
            row.get("preflight_status") == "healthy"
            and row.get("scale_status") == "complete"
            and row.get("failure_layer") == "none"
            for row in cycle["platforms"].values()
        )
        for cycle in cycles[:required_cycles]
    )


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _scale_search(connector, *, platform: str) -> dict[str, Any]:
    started_at = utc_now()
    try:
        result = await connector.search(
            SCALE_QUERIES[platform],
            count=10,
            time_filter="halfyear",
            sort="latest",
        )
        health = result.health.to_dict()
        if health.get("status") == "ok" and result.items:
            status = "complete"
            error = None
        elif health.get("status") == "ok":
            status = "explicit_empty"
            error = "known_positive_empty"
        else:
            status = "partial" if result.items else "failed"
            error = health.get("error") or "source_unhealthy"
        return {
            "platform": platform,
            "query": SCALE_QUERIES[platform],
            "started_at": started_at,
            "completed_at": utc_now(),
            "status": status,
            "returned_count": len(result.items),
            "connector": connector.connector_name,
            "health": health,
            "error_category": error,
        }
    except Exception as exc:
        error = str(getattr(exc, "error_category", "") or type(exc).__name__)
        return {
            "platform": platform,
            "query": SCALE_QUERIES[platform],
            "started_at": started_at,
            "completed_at": utc_now(),
            "status": "failed",
            "returned_count": 0,
            "connector": connector.connector_name,
            "error_category": error,
        }


async def _run_cycle(cycle_number: int) -> dict[str, Any]:
    collector = OwnedRadarCollector(trend_candidate_limit=1)
    connectors = {
        platform: _connector_for(
            collector, platform, CONNECTORS[platform]
        )
        for platform in REQUIRED_PLATFORMS
    }
    preflight_rows = await asyncio.gather(*(
        _preflight_platform(
            connectors[platform],
            platform=platform,
            query=CANARIES[platform],
        )
        for platform in REQUIRED_PLATFORMS
    ))
    preflight = {row["platform"]: row for row in preflight_rows}
    scale_rows = await asyncio.gather(*(
        _scale_search(connectors[platform], platform=platform)
        if preflight[platform].get("status") == "healthy"
        else asyncio.sleep(0, result={
            "platform": platform,
            "query": SCALE_QUERIES[platform],
            "status": "not_run_preflight_failed",
            "returned_count": 0,
            "connector": CONNECTORS[platform],
            "error_category": preflight[platform].get("error_category"),
        })
        for platform in REQUIRED_PLATFORMS
    ))
    scale = {row["platform"]: row for row in scale_rows}
    platforms = {}
    for platform in REQUIRED_PLATFORMS:
        error = (
            preflight[platform].get("error_category")
            if preflight[platform].get("status") != "healthy"
            else scale[platform].get("error_category")
            if scale[platform].get("status") != "complete"
            else None
        )
        platforms[platform] = {
            "preflight_status": preflight[platform].get("status"),
            "preflight_search_count": preflight[platform].get("returned_count"),
            "preflight_depth_state": (
                (preflight[platform].get("depth_canary") or {}).get("state")
            ),
            "scale_status": scale[platform].get("status"),
            "scale_search_count": scale[platform].get("returned_count"),
            "error_category": error,
            "failure_layer": classify_failure_layer(error),
            "connector": CONNECTORS[platform],
        }
    return {
        "cycle": cycle_number,
        "started_and_completed_in_order": True,
        "platforms": platforms,
        "preflight": preflight,
        "scale": scale,
    }


async def execute(output: Path, cycles_requested: int) -> int:
    load_environment(ROOT)
    profile, extension = _owned_tiktok_paths(None, None)
    configure_owned_social(
        tiktok_profile=profile,
        tiktok_extension=extension,
    )
    started_at = utc_now()
    cycles = []
    for cycle_number in range(1, cycles_requested + 1):
        cycle = await _run_cycle(cycle_number)
        cycles.append(cycle)
        passed = qualification_passed(cycles, required_cycles=cycles_requested)
        payload = {
            "schema_version": "bounty-social-source-health-qualification/1",
            "started_at": started_at,
            "updated_at": utc_now(),
            "required_platforms": list(REQUIRED_PLATFORMS),
            "cycles_requested": cycles_requested,
            "cycles_completed": len(cycles),
            "status": (
                "qualified" if passed
                else "running" if len(cycles) < cycles_requested
                else "failed"
            ),
            "monitor_or_dashboard_written": False,
            "cycles": cycles,
        }
        _atomic_json(output, payload)
    print(json.dumps({
        "status": payload["status"],
        "output": str(output),
        "sha256": _sha256(output),
        "cycles_completed": payload["cycles_completed"],
        "platforms": {
            platform: [
                cycle["platforms"][platform] for cycle in cycles
            ]
            for platform in REQUIRED_PLATFORMS
        },
        "monitor_or_dashboard_written": False,
    }, indent=2))
    return 0 if payload["status"] == "qualified" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.cycles <= 3:
        raise ValueError("cycles must be between 1 and 3")
    output = args.output if args.output.is_absolute() else ROOT / args.output
    return asyncio.run(execute(output, args.cycles))


if __name__ == "__main__":
    raise SystemExit(main())
