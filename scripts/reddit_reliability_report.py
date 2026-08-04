"""Summarize measured Reddit reliability from named canary collection runs."""

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "social_scraper" / "config" / "reddit_canaries.json"


def canary_signatures():
    records = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    output = {}
    for row in records:
        options = {
            "_search": {"time_filter": row["time_filter"]},
            "reddit": {"subreddits": sorted({value.lower() for value in row["subreddits"]})},
        }
        signature = json.dumps(options, sort_keys=True, separators=(",", ":"))
        output[(row["keyword"], signature)] = row["name"]
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--hours", type=int, default=168)
    args = parser.parse_args()
    if args.hours < 1:
        raise SystemExit("--hours must be positive")

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()
    path = Path(args.db)
    if not path.exists():
        raise SystemExit(f"Database not found: {path}")
    signatures = canary_signatures()

    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """
            SELECT query, options_json, collected_at, raw_response_json
            FROM collection_runs
            WHERE collected_at >= ?
            ORDER BY collected_at
            """,
            (cutoff,),
        ).fetchall()

    canaries = {
        name: {
            "runs": 0,
            "runs_with_items": 0,
            "error_runs": 0,
            "newest_created_at_present": 0,
        }
        for name in signatures.values()
    }
    connectors = defaultdict(lambda: {
        "attempts": 0,
        "ok": 0,
        "partial": 0,
        "error": 0,
        "skipped": 0,
        "items_returned": 0,
        "timed_attempts": 0,
        "latency_ms_total": 0,
    })

    for query, options_json, _collected_at, raw_json in rows:
        name = signatures.get((query, options_json))
        if name is None:
            continue
        response = json.loads(raw_json)
        reddit_items = [item for item in response.get("items", []) if item.get("platform") == "reddit"]
        reddit_result = (response.get("platform_results", {}) or {}).get("reddit", {})
        row = canaries[name]
        row["runs"] += 1
        if reddit_items:
            row["runs_with_items"] += 1
        if reddit_result.get("status") == "error":
            row["error_runs"] += 1
        if (reddit_result.get("data_quality", {}) or {}).get("newest_created_at"):
            row["newest_created_at_present"] += 1

        for health in response.get("source_health", []):
            if health.get("platform") != "reddit":
                continue
            connector = connectors[health.get("connector", "unknown")]
            connector["attempts"] += 1
            status = health.get("status", "error")
            connector[status if status in {"ok", "partial", "error", "skipped"} else "error"] += 1
            connector["items_returned"] += int(health.get("items_returned") or 0)
            if status != "skipped":
                connector["timed_attempts"] += 1
                connector["latency_ms_total"] += int(health.get("latency_ms") or 0)

    connector_output = {}
    for name, values in sorted(connectors.items()):
        timed = values["timed_attempts"]
        connector_output[name] = {
            **{
                key: value
                for key, value in values.items()
                if key not in {"latency_ms_total", "timed_attempts"}
            },
            "average_latency_ms": round(values["latency_ms_total"] / timed) if timed else None,
        }

    gate_reasons = []
    if args.hours < 168:
        gate_reasons.append("measurement_window_under_168_hours")
    for name, values in canaries.items():
        if values["runs"] < 10:
            gate_reasons.append(f"{name}:fewer_than_10_runs")
            continue
        if values["error_runs"]:
            gate_reasons.append(f"{name}:error_runs_present")
        if values["runs_with_items"] / values["runs"] < 0.9:
            gate_reasons.append(f"{name}:item_coverage_below_90_percent")
        if values["newest_created_at_present"] != values["runs_with_items"]:
            gate_reasons.append(f"{name}:missing_freshness_timestamps")

    print(json.dumps({
        "window_hours": args.hours,
        "canaries": canaries,
        "connectors": connector_output,
        "sla_ready": not gate_reasons,
        "gate_reasons": gate_reasons,
    }, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
