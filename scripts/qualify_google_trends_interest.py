"""Production-shaped qualification for the shared Google Trends interest route.

This script writes only a qualification artifact. It never advances a monitor,
dashboard, or latest pointer.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from social_scraper.source_connectors.google_trends_interest import (
    collect_interest_plan,
)

GHOST_QUERIES = ["ghost root beer energy drink", "ghost a&w root beer"]
SCALE_QUERIES = ["nike", "adidas", "apple", "samsung", "coca cola"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plan(cycle: int) -> list[dict]:
    return [
        {
            "request_key": f"cycle-{cycle}-canary-us",
            "query_basket": ["nike", "adidas"],
            "timeframe": "today 3-m",
            "geo": "US",
            "gprop": "web",
            "role": "production_shaped_canary",
        },
        {
            "request_key": f"cycle-{cycle}-ghost-us",
            "query_basket": GHOST_QUERIES,
            "timeframe": "today 3-m",
            "geo": "US",
            "gprop": "web",
            "role": "exact_monitor_basket",
        },
        {
            "request_key": f"cycle-{cycle}-ghost-worldwide",
            "query_basket": GHOST_QUERIES,
            "timeframe": "today 3-m",
            "geo": "",
            "gprop": "web",
            "role": "exact_monitor_basket",
        },
        {
            "request_key": f"cycle-{cycle}-max-width-us",
            "query_basket": SCALE_QUERIES,
            "timeframe": "today 3-m",
            "geo": "US",
            "gprop": "web",
            "role": "maximum_width_scale_probe",
        },
        {
            "request_key": f"cycle-{cycle}-max-width-worldwide",
            "query_basket": SCALE_QUERIES,
            "timeframe": "today 3-m",
            "geo": "",
            "gprop": "web",
            "role": "maximum_width_scale_probe",
        },
    ]


def qualifies(result: dict) -> bool:
    rows = int(result.get("rows_returned") or 0)
    values = result.get("returned_values")
    flags = result.get("isPartial_flags")
    return bool(
        result.get("status") == "complete"
        and result.get("requested_gprop") == "web"
        and result.get("effective_gprop") == "web_default"
        and rows >= 90
        and isinstance(values, dict)
        and len(values.get("dates") or []) == rows
        and isinstance(flags, list)
        and len(flags) == rows
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.cycles <= 3:
        raise ValueError("cycles must be between 1 and 3")
    output = args.output if args.output.is_absolute() else ROOT / args.output

    from trendspy import Trends
    from social_scraper.source_connectors.google_trends_interest import _request_delay

    trends = Trends(request_delay=_request_delay())
    started_at = utc_now()
    cycles = []
    all_qualified = True
    for cycle_number in range(1, args.cycles + 1):
        cycle_plan = plan(cycle_number)
        results = collect_interest_plan(
            cycle_plan,
            trends=trends,
            max_requests=5,
        )
        checks = {
            key: {
                "qualified": qualifies(result),
                "status": result.get("status"),
                "error_category": result.get("error_category"),
                "rows_returned": result.get("rows_returned"),
                "requested_gprop": result.get("requested_gprop"),
                "effective_gprop": result.get("effective_gprop"),
            }
            for key, result in results.items()
        }
        cycle_qualified = all(row["qualified"] for row in checks.values())
        all_qualified = all_qualified and cycle_qualified
        cycles.append({
            "cycle": cycle_number,
            "started_and_completed_in_order": True,
            "plan": cycle_plan,
            "results": results,
            "checks": checks,
            "qualified": cycle_qualified,
        })
        atomic_json(output, {
            "schema_version": "bounty-google-trends-qualification/1",
            "started_at": started_at,
            "updated_at": utc_now(),
            "status": "running" if cycle_number < args.cycles else (
                "qualified" if all_qualified else "failed"
            ),
            "cycles_requested": args.cycles,
            "cycles_completed": cycle_number,
            "provider_requests_completed": cycle_number * 5,
            "serialized": True,
            "monitor_or_dashboard_written": False,
            "cycles": cycles,
        })

    payload = json.loads(output.read_text(encoding="utf-8"))
    print(json.dumps({
        "status": payload["status"],
        "output": str(output),
        "sha256": sha256(output),
        "cycles_completed": payload["cycles_completed"],
        "provider_requests_completed": payload["provider_requests_completed"],
        "qualified_requests": sum(
            row["qualified"]
            for cycle in payload["cycles"]
            for row in cycle["checks"].values()
        ),
        "monitor_or_dashboard_written": payload["monitor_or_dashboard_written"],
    }, indent=2))
    return 0 if payload["status"] == "qualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
