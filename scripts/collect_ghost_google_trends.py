"""Deterministic daily Google Trends collector for the GHOST standing monitor."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
from typing import Any, Mapping
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from social_scraper.source_connectors.google_trends_interest import (  # noqa: E402
    collect_interest_plan,
)

SCHEMA_VERSION = "ghost-google-trends-observation/1"
WRITER_ID = "scripts/collect_ghost_google_trends.py"
WRITER_VERSION = 1
SGT = ZoneInfo("Asia/Singapore")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_timestamp(value: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    return parsed


def _sgt_day(value: str) -> str:
    return _parse_timestamp(value).astimezone(SGT).date().isoformat()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _atomic_append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_bytes() if path.exists() else b""
    run_id = str(payload.get("run_id") or "")
    if run_id:
        for line in existing.splitlines():
            try:
                if str(json.loads(line).get("run_id") or "") == run_id:
                    return
            except (json.JSONDecodeError, AttributeError):
                continue
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8") + b"\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(existing + encoded)
    os.replace(temporary, path)


def _query_windows(result: Mapping[str, Any], queries: list[str]) -> dict[str, Any]:
    if result.get("status") != "complete":
        return {
            "status": "SOURCE_FAILURE",
            "error_category": result.get("error_category"),
            "requested_gprop": result.get("requested_gprop"),
            "effective_gprop": result.get("effective_gprop"),
            "returned_values": None,
            "isPartial_flags": None,
            "latest_complete_date": None,
        }
    values = result.get("returned_values")
    dates = list((values or {}).get("dates") or [])
    flags = result.get("isPartial_flags")
    if not isinstance(values, Mapping) or not isinstance(flags, list) or len(flags) != len(dates):
        return {
            "status": "SOURCE_FAILURE",
            "error_category": "INVALID_PARTIAL_FLAGS",
            "requested_gprop": result.get("requested_gprop"),
            "effective_gprop": result.get("effective_gprop"),
            "returned_values": None,
            "isPartial_flags": None,
            "latest_complete_date": None,
        }
    for query in queries:
        if not isinstance(values.get(query), list) or len(values[query]) != len(dates):
            return {
                "status": "SOURCE_FAILURE",
                "error_category": "INVALID_QUERY_SERIES",
                "requested_gprop": result.get("requested_gprop"),
                "effective_gprop": result.get("effective_gprop"),
                "returned_values": None,
                "isPartial_flags": None,
                "latest_complete_date": None,
            }

    complete = [index for index, is_partial in enumerate(flags) if not is_partial]
    latest = complete[-7:]
    prior = complete[-14:-7]
    latest_values = {query: [values[query][index] for index in latest] for query in queries}
    prior_values = {query: [values[query][index] for index in prior] for query in queries}
    ratios = {}
    means = {}
    query_status = {}
    for query in queries:
        recent_mean = statistics.mean(latest_values[query]) if latest else None
        prior_mean = statistics.mean(prior_values[query]) if prior else None
        ratios[query] = (
            recent_mean / prior_mean
            if recent_mean is not None and prior_mean not in {None, 0}
            else None
        )
        means[query] = {"latest_7_mean": recent_mean, "prior_7_mean": prior_mean}
        nonzero = sum(
            value is not None and int(value) > 0
            for index, value in enumerate(values[query])
            if index in complete
        )
        query_status[query] = {
            "nonzero_points": nonzero,
            "sparse": nonzero < 8,
            "status": "usable_for_comparison" if len(latest) == 7 and len(prior) == 7 and nonzero >= 8 else "BUILDING_BASELINE",
        }
    return {
        "status": "complete",
        "requested_gprop": result.get("requested_gprop"),
        "effective_gprop": result.get("effective_gprop"),
        "returned_values": deepcopy(values),
        "isPartial_flags": list(flags),
        "rows_returned": len(dates),
        "complete_rows": len(complete),
        "latest_complete_date": dates[complete[-1]] if complete else None,
        "partial_dates_excluded": [date for date, is_partial in zip(dates, flags) if is_partial],
        "latest_7_dates": [dates[index] for index in latest],
        "prior_7_dates": [dates[index] for index in prior],
        "latest_7_values": latest_values,
        "prior_7_values": prior_values,
        "latest_to_prior_ratio": ratios,
        "means": means,
        "query_status": query_status,
        "source_result": deepcopy(dict(result)),
    }


def build_search_observation(
    config: Mapping[str, Any],
    geography_results: Mapping[str, Mapping[str, Any]],
    *,
    observed_at: str,
    contract_sha256: str,
    preflight: Mapping[str, Any],
) -> dict[str, Any]:
    _parse_timestamp(observed_at)
    search_config = config.get("search_attention") or {}
    queries = [str(query) for query in search_config.get("queries") or []]
    expected_geographies = [
        str(value) for value in search_config.get("geographies") or ("US", "WORLDWIDE")
    ]
    geographies = {
        geography: _query_windows(geography_results.get(geography) or {}, queries)
        for geography in expected_geographies
    }
    complete = bool(
        preflight.get("status") == "complete"
        and preflight.get("effective_gprop") == "web_default"
        and set(geographies) == set(expected_geographies)
        and all(
            row.get("status") == "complete"
            and row.get("effective_gprop") == "web_default"
            for row in geographies.values()
        )
    )
    ratios = [
        ratio
        for row in geographies.values()
        for ratio in (row.get("latest_to_prior_ratio") or {}).values()
        if ratio is not None
    ]
    comparison = (
        "SOURCE_FAILURE" if not complete
        else "not_falling" if ratios and all(value >= 1 for value in ratios)
        else "mixed_or_softening" if ratios
        else "BUILDING_BASELINE"
    )
    latest_dates = [
        row.get("latest_complete_date") for row in geographies.values()
        if row.get("latest_complete_date")
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at": observed_at,
        "observation_day_sgt": _sgt_day(observed_at),
        "monitor_id": config.get("monitor_id"),
        "candidate": config.get("candidate"),
        "source": "Google Trends",
        "route": "trendspy_interest_over_time",
        "timeframe": str(search_config.get("timeframe") or "today 3-m"),
        "query_basket": queries,
        "geography_scope": expected_geographies,
        "requested_gprop": "web",
        "effective_gprop": "web_default",
        "status": "complete" if complete else "SOURCE_FAILURE",
        "state": "SEARCH_BUILDING_BASELINE",
        "current_comparison": comparison,
        "latest_complete_date": min(latest_dates) if latest_dates else None,
        "geographies": geographies,
        "preflight": deepcopy(dict(preflight)),
        "contract_sha256": contract_sha256,
        "writer": {
            "writer_id": WRITER_ID,
            "writer_version": WRITER_VERSION,
        },
        "automatic_trade_action": False,
    }


def should_reuse_daily_success(
    value: Mapping[str, Any] | None,
    *,
    observed_at: str,
    contract_sha256: str,
) -> bool:
    if not isinstance(value, Mapping):
        return False
    writer = value.get("writer") if isinstance(value.get("writer"), Mapping) else {}
    return bool(
        value.get("schema_version") == SCHEMA_VERSION
        and value.get("status") == "complete"
        and value.get("observation_day_sgt") == _sgt_day(observed_at)
        and value.get("contract_sha256") == contract_sha256
        and writer.get("writer_id") == WRITER_ID
        and writer.get("writer_version") == WRITER_VERSION
    )


def persist_observation(root: Path, observation: Mapping[str, Any], *, run_id: str) -> dict[str, Path]:
    value = deepcopy(dict(observation))
    value["run_id"] = run_id
    ghost = root / "artifacts/dd/ghost-kdp"
    run_path = ghost / "trends-runs" / f"{run_id}.json"
    attempt_path = ghost / "trends_attempt_latest.json"
    success_path = ghost / "trends_latest.json"
    history_path = ghost / "trends_history.jsonl"
    if run_path.exists():
        existing = json.loads(run_path.read_text(encoding="utf-8"))
        if existing != value:
            raise ValueError("immutable Google Trends run ID already exists with different content")
    else:
        _atomic_json(run_path, value)
    _atomic_json(attempt_path, value)
    _atomic_append_jsonl(history_path, value)
    if value.get("status") == "complete":
        _atomic_json(success_path, value)
    return {
        "run": run_path,
        "attempt_latest": attempt_path,
        "success_latest": success_path,
        "history": history_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, default=ROOT / "references/ghost-standing-monitor-v1.json")
    parser.add_argument("--observed-at")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    root = args.repo_root.resolve()
    config_path = args.config if args.config.is_absolute() else root / args.config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    contract_hash = sha256(config_path)
    observed_at = args.observed_at or datetime.now(timezone.utc).isoformat()
    run_id = _parse_timestamp(observed_at).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ghost = root / "artifacts/dd/ghost-kdp"
    latest_path = ghost / "trends_latest.json"
    if not args.force and latest_path.exists():
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
        if should_reuse_daily_success(
            latest, observed_at=observed_at, contract_sha256=contract_hash
        ):
            print(json.dumps({
                "status": "cached_complete",
                "provider_requests": 0,
                "observed_at": latest.get("observed_at"),
                "latest_complete_date": latest.get("latest_complete_date"),
            }, indent=2))
            return 0

    lock = ghost / "ghost_google_trends.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print(json.dumps({"status": "blocked", "reason": "google_trends_collector_already_running"}))
        return 2
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        from trendspy import Trends
        from social_scraper.source_connectors.google_trends_interest import _request_delay

        search_config = config.get("search_attention") or {}
        queries = list(search_config.get("queries") or [])
        trends = Trends(request_delay=_request_delay())
        plan = [
            {
                "request_key": "preflight",
                "query_basket": ["nike", "adidas"],
                "timeframe": "today 3-m",
                "geo": "US",
                "gprop": "web",
            },
            *[
                {
                    "request_key": geography,
                    "query_basket": queries,
                    "timeframe": str(search_config.get("timeframe") or "today 3-m"),
                    "geo": "" if geography == "WORLDWIDE" else geography,
                    "gprop": str(search_config.get("requested_gprop") or "web"),
                }
                for geography in search_config.get("geographies") or ("US", "WORLDWIDE")
            ],
        ]
        results = collect_interest_plan(plan, trends=trends, max_requests=3)
        observation = build_search_observation(
            config,
            {key: value for key, value in results.items() if key != "preflight"},
            observed_at=observed_at,
            contract_sha256=contract_hash,
            preflight=results["preflight"],
        )
        paths = persist_observation(root, observation, run_id=run_id)
        print(json.dumps({
            "status": observation["status"],
            "provider_requests": len(plan),
            "observed_at": observation["observed_at"],
            "observation_day_sgt": observation["observation_day_sgt"],
            "latest_complete_date": observation["latest_complete_date"],
            "geographies": {
                key: {
                    "status": value.get("status"),
                    "rows_returned": value.get("rows_returned"),
                    "latest_complete_date": value.get("latest_complete_date"),
                    "effective_gprop": value.get("effective_gprop"),
                }
                for key, value in observation["geographies"].items()
            },
            "run_artifact": str(paths["run"]),
        }, indent=2))
        return 0 if observation["status"] == "complete" else 1
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
