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


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _availability_counts(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    value = snapshot or {}
    summary = value.get("summary") if isinstance(value.get("summary"), dict) else {}
    records = value.get("records") if isinstance(value.get("records"), list) else []
    available = int(summary.get("orderable") or 0)
    out_of_stock = int(summary.get("out_of_stock") or 0)
    not_listed = int(summary.get("not_listed_at_store") or 0)
    unverified = summary.get("unavailable")
    if unverified is None:
        unverified = sum(
            isinstance(row, dict)
            and row.get("status") not in {"orderable", "out_of_stock", "not_listed_at_store"}
            for row in records
        )
    return {
        "observed_at": value.get("observed_at"),
        "coverage": _text(value.get("coverage_status") or "unknown").lower(),
        "available": available,
        "out_of_stock": out_of_stock,
        "not_listed": not_listed,
        "unverified": int(unverified or 0),
    }


def _daily_complete_availability(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[str, dict[str, Any]] = {}
    for row in rows:
        observed_at = _text(row.get("observed_at"))
        if row.get("coverage_status") != "complete" or len(observed_at) < 10:
            continue
        day = observed_at[:10]
        prior = by_day.get(day)
        if prior is None or _text(row.get("observed_at")) > _text(prior.get("observed_at")):
            by_day[day] = row
    return [by_day[day] for day in sorted(by_day)]


def _store_rows(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    output = []
    for row in (snapshot or {}).get("records") or []:
        if not isinstance(row, dict):
            continue
        requested = row.get("requested_location") if isinstance(row.get("requested_location"), dict) else {}
        status = _text(row.get("status") or "unverified").lower()
        label = {
            "orderable": "Available",
            "out_of_stock": "Out of stock",
            "not_listed_at_store": "Not listed",
            "unavailable_location_unverified": "Wrong store returned",
            "unavailable_error": "Collection failed",
            "unavailable_challenge": "Verification challenge",
            "availability_unknown": "Availability unclear",
        }.get(status, "Unverified")
        output.append({
            "store_id": _text(requested.get("store_id")),
            "metro": _text(requested.get("metro") or "Store"),
            "postal_code": _text(requested.get("postal_code")),
            "status": status,
            "label": label,
            "observed_at": row.get("observed_at"),
        })
    return output


def _newly_available_stores(previous: dict[str, Any] | None, current: dict[str, Any] | None) -> list[str]:
    prior = {
        row.get("record_key"): row
        for row in (previous or {}).get("records") or []
        if isinstance(row, dict)
    }
    names = []
    for row in (current or {}).get("records") or []:
        if not isinstance(row, dict) or row.get("status") != "orderable":
            continue
        old = prior.get(row.get("record_key"))
        if not old or old.get("status") != "out_of_stock":
            continue
        requested = row.get("requested_location") if isinstance(row.get("requested_location"), dict) else {}
        names.append(_text(requested.get("metro") or requested.get("store_id") or "One store"))
    return list(dict.fromkeys(names))


def _search_ratios(search: dict[str, Any] | None, geography: str = "US") -> dict[str, float]:
    value = search or {}
    geo = (value.get("geographies") or {}).get(geography)
    if not isinstance(geo, dict):
        return {}
    direct = geo.get("latest_to_prior_ratio")
    if isinstance(direct, dict):
        return {
            _text(query): float(ratio)
            for query, ratio in direct.items()
            if query and isinstance(ratio, (int, float))
        }
    queries = geo.get("queries")
    if not isinstance(queries, dict) and any(
        isinstance(metrics, dict)
        and isinstance(metrics.get("latest_to_prior_ratio", metrics.get("ratio")), (int, float))
        for metrics in geo.values()
    ):
        queries = geo
    if isinstance(queries, dict):
        output = {}
        for query, metrics in queries.items():
            if not isinstance(metrics, dict):
                continue
            ratio = metrics.get("latest_to_prior_ratio")
            if not isinstance(ratio, (int, float)):
                ratio = metrics.get("ratio")
            if query and isinstance(ratio, (int, float)):
                output[_text(query)] = float(ratio)
        return output
    return {}


def _rolling_seven_day_change_series(
    returned_values: dict[str, Any] | None,
    queries: list[str],
    partial_flags: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Derive rolling 7d-vs-prior-7d changes inside one normalized request.

    A point is omitted when any date in its 14-day window is partial, a query
    value is missing/non-numeric, or the prior seven-day mean is zero. Missing
    points remain gaps; nothing is interpolated.
    """
    source = returned_values or {}
    dates = source.get("dates") if isinstance(source.get("dates"), list) else []
    partial = partial_flags if isinstance(partial_flags, list) else (
        source.get("isPartial_flags") if isinstance(source.get("isPartial_flags"), list) else []
    )
    if len(dates) < 14:
        return []
    if len(partial) != len(dates):
        partial = [False] * len(dates)
    output = []
    for index in range(13, len(dates)):
        window_start = index - 13
        if any(bool(value) for value in partial[window_start:index + 1]):
            continue
        changes: dict[str, float] = {}
        latest_means: dict[str, float] = {}
        prior_means: dict[str, float] = {}
        for query in queries:
            values = source.get(query)
            if not isinstance(values, list) or len(values) != len(dates):
                continue
            latest = values[index - 6:index + 1]
            prior = values[index - 13:index - 6]
            if not all(isinstance(value, (int, float)) for value in latest + prior):
                continue
            latest_mean = sum(float(value) for value in latest) / 7
            prior_mean = sum(float(value) for value in prior) / 7
            if prior_mean <= 0:
                continue
            changes[query] = round(((latest_mean / prior_mean) - 1) * 100, 4)
            latest_means[query] = latest_mean
            prior_means[query] = prior_mean
        if changes:
            output.append({
                "date": _text(dates[index])[:10],
                "changes_pct": changes,
                "latest_7_mean": latest_means,
                "prior_7_mean": prior_means,
                "source_window_days": 14,
            })
    return output


def _verified_search_values(
    search: dict[str, Any],
    geography: str = "US",
    observed_at: str | None = None,
) -> tuple[dict[str, Any] | None, str | None, str | None, list[Any] | None, str | None]:
    geographies = search.get("geographies") if isinstance(search.get("geographies"), dict) else {}
    current = geographies.get(geography) if isinstance(geographies.get(geography), dict) else {}
    current_values = current.get("returned_values") if isinstance(current.get("returned_values"), dict) else None
    if current_values:
        return (
            current_values,
            observed_at or search.get("observed_at"),
            current.get("latest_complete_date") or search.get("latest_complete_date"),
            current.get("isPartial_flags") if isinstance(current.get("isPartial_flags"), list) else None,
            _text(current.get("status") or search.get("data_status") or search.get("status") or "complete").lower(),
        )
    last_verified = search.get("last_verified_observation") if isinstance(search.get("last_verified_observation"), dict) else {}
    verified_geographies = last_verified.get("geographies") if isinstance(last_verified.get("geographies"), dict) else {}
    verified = verified_geographies.get(geography) if isinstance(verified_geographies.get(geography), dict) else {}
    verified_values = verified.get("returned_values") if isinstance(verified.get("returned_values"), dict) else None
    return (
        verified_values,
        last_verified.get("observed_at"),
        verified.get("latest_complete_date"),
        verified.get("isPartial_flags") if isinstance(verified.get("isPartial_flags"), list) else None,
        _text(verified.get("status") or "complete").lower() if verified_values else None,
    )


def _ghost_monitor_dashboard(
    root: Path,
    *,
    include_private_position: bool = False,
    monitor_jobs: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    ghost = root / "artifacts" / "dd" / "ghost-kdp"
    walmart_latest_path = ghost / "walmart_native_latest.json"
    walmart_history_path = ghost / "walmart_native_history.jsonl"
    attention_latest_path = ghost / "attention_latest.json"
    attention_history_path = ghost / "attention_history.jsonl"
    coverage_latest_path = ghost / "coverage_latest.json"
    coverage_history_path = ghost / "coverage_history.jsonl"
    if not any(path.exists() for path in (
        walmart_latest_path, walmart_history_path, attention_latest_path,
        attention_history_path, coverage_latest_path, coverage_history_path,
    )):
        return None

    walmart_latest = _load(walmart_latest_path) or {}
    complete_daily = _daily_complete_availability(_load_jsonl(walmart_history_path))
    last_complete = complete_daily[-1] if complete_daily else None
    previous_complete = complete_daily[-2] if len(complete_daily) > 1 else None
    latest_attempt_counts = _availability_counts(walmart_latest)
    last_complete_counts = _availability_counts(last_complete)
    visible_snapshot = (
        walmart_latest
        if walmart_latest.get("coverage_status") == "complete"
        else last_complete
    )
    current_counts = _availability_counts(visible_snapshot)
    availability_history = [
        {"date": _text(row.get("observed_at"))[:10], **_availability_counts(row)}
        for row in complete_daily
    ]
    newly_available = _newly_available_stores(previous_complete, last_complete)
    if newly_available:
        headline = (
            f"{', '.join(newly_available)} restocked; the latest full six-store reading "
            f"was {last_complete_counts['available']} available and "
            f"{last_complete_counts['out_of_stock']} out of stock."
        )
    elif last_complete:
        headline = (
            f"Latest full six-store reading: {last_complete_counts['available']} available, "
            f"{last_complete_counts['out_of_stock']} out of stock."
        )
    else:
        headline = "Walmart monitoring has no complete six-store reading yet."

    attention_latest = _load(attention_latest_path) or {}
    attention_rows = _load_jsonl(attention_history_path)
    search_latest = attention_latest.get("search_attention") if isinstance(attention_latest.get("search_attention"), dict) else {}
    search_history = []
    for row in attention_rows:
        search = row.get("search_attention") if isinstance(row.get("search_attention"), dict) else None
        ratios = _search_ratios(search)
        if not search or not ratios:
            continue
        search_history.append({
            "observed_at": row.get("observed_at"),
            "status": _text(search.get("status") or "complete").lower(),
            "state": _text(search.get("state") or "SEARCH_BUILDING_BASELINE").upper(),
            "ratios": ratios,
        })
    latest_ratios = _search_ratios(search_latest)
    if not latest_ratios and search_history:
        latest_ratios = dict(search_history[-1].get("ratios") or {})
    query_basket = [
        _text(value) for value in _as_list(
            search_latest.get("query_basket") or list(latest_ratios)
        ) if _text(value)
    ]
    (
        verified_values,
        verified_search_at,
        verified_complete_date,
        verified_partial_flags,
        verified_search_status,
    ) = _verified_search_values(search_latest, observed_at=attention_latest.get("observed_at"))
    rolling_search_change = _rolling_seven_day_change_series(
        verified_values,
        query_basket,
        verified_partial_flags,
    )
    if verified_search_status:
        visible_search_status = verified_search_status
    elif search_history and latest_ratios:
        visible_search_status = _text(search_history[-1].get("status") or "complete").lower()
        verified_search_at = verified_search_at or search_history[-1].get("observed_at")
    else:
        visible_search_status = _text(search_latest.get("status") or "unknown").lower()
    comparison = _text(search_latest.get("current_comparison")).casefold()
    if "not_falling" in comparison or (latest_ratios and all(value >= 1 for value in latest_ratios.values())):
        search_read = "Elevated, not falling in the usable US comparison."
    elif "fall" in comparison or "cool" in comparison:
        search_read = "Search attention is weakening and needs review."
    else:
        search_read = "Building a comparable search baseline."

    conversation = attention_latest.get("conversation_attention") if isinstance(attention_latest.get("conversation_attention"), dict) else {}
    conversation_by_day: dict[str, tuple[str, dict[str, Any]]] = {}
    for row in attention_rows:
        historical = row.get("conversation_attention") if isinstance(row.get("conversation_attention"), dict) else None
        if not historical:
            continue
        historical_origin = historical.get("origin_review") if isinstance(historical.get("origin_review"), dict) else {}
        historical_raw = historical_origin.get("raw_exact_roots_by_platform")
        historical_qualifying = historical_origin.get("qualifying_independent_roots_by_platform")
        historical_comments = historical.get("comments_replies_by_platform")
        if not isinstance(historical_comments, dict):
            historical_comments = {}
        if not isinstance(historical_raw, dict) or not isinstance(historical_qualifying, dict):
            continue
        historical_canaries = historical.get("platform_canary_matrix") if isinstance(historical.get("platform_canary_matrix"), dict) else {}
        historical_queries = historical.get("candidate_platform_queries") if isinstance(historical.get("candidate_platform_queries"), dict) else {}
        canaries_healthy = len(historical_canaries) == 5 and all(
            isinstance(value, dict) and _text(value.get("status")).lower() == "healthy"
            for value in historical_canaries.values()
        )
        queries_terminal = len(historical_queries) == 5 and all(
            isinstance(value, dict)
            and _text(value.get("candidate_query_status")).lower()
            in {"complete", "complete_relevant", "complete_no_match", "empty"}
            for value in historical_queries.values()
        )
        terminal_states = {"complete", "complete_relevant", "complete_no_match", "empty"}
        successful_platforms = [
            platform for platform in ("x", "tiktok", "instagram", "reddit", "youtube")
            if isinstance(historical_canaries.get(platform), dict)
            and _text(historical_canaries[platform].get("status")).lower() == "healthy"
            and isinstance(historical_queries.get(platform), dict)
            and (
                _text(historical_queries[platform].get("candidate_query_status")).lower() in terminal_states
                or (
                    _text(historical_queries[platform].get("candidate_query_status")).lower() == "partial"
                    and int(historical_raw.get(platform) or 0) > 0
                )
            )
        ]
        point = {
            "observed_at": row.get("observed_at"),
            "exact_roots": sum(int(historical_raw.get(platform) or 0) for platform in successful_platforms),
            "qualifying_roots": sum(int(historical_qualifying.get(platform) or 0) for platform in successful_platforms),
            "captured_comments_replies": sum(int(historical_comments.get(platform) or 0) for platform in successful_platforms),
            "exact_roots_by_platform": {
                platform: int(historical_raw.get(platform) or 0)
                for platform in ("x", "tiktok", "instagram", "reddit", "youtube")
            },
            "successful_platforms": successful_platforms,
            "source_state": _text(historical.get("operational_state") or "unknown").lower(),
            "comparable": bool(canaries_healthy and queries_terminal),
        }
        day = _text(row.get("observed_at"))[:10]
        if len(day) < 10:
            continue
        score = _text(row.get("observed_at"))
        prior = conversation_by_day.get(day)
        if prior is None or score >= prior[0]:
            conversation_by_day[day] = (score, point)
    conversation_history = [conversation_by_day[day][1] for day in sorted(conversation_by_day)]
    sentiment_source = conversation.get("sentiment") if isinstance(conversation.get("sentiment"), dict) else {}
    canaries = conversation.get("platform_canary_matrix") if isinstance(conversation.get("platform_canary_matrix"), dict) else {}
    queries = conversation.get("candidate_platform_queries") if isinstance(conversation.get("candidate_platform_queries"), dict) else {}
    origin = conversation.get("origin_review") if isinstance(conversation.get("origin_review"), dict) else {}
    raw_by_platform = origin.get("raw_exact_roots_by_platform") if isinstance(origin.get("raw_exact_roots_by_platform"), dict) else {}
    qualifying_by_platform = origin.get("qualifying_independent_roots_by_platform") if isinstance(origin.get("qualifying_independent_roots_by_platform"), dict) else {}
    comments_by_platform = conversation.get("comments_replies_by_platform") if isinstance(conversation.get("comments_replies_by_platform"), dict) else {}
    reviewed_comments_by_platform = conversation.get("reviewed_product_relevant_comments_replies_by_platform") if isinstance(conversation.get("reviewed_product_relevant_comments_replies_by_platform"), dict) else {}
    platform_rows: dict[str, dict[str, Any]] = {}
    for platform in ("x", "tiktok", "instagram", "reddit", "youtube"):
        canary = canaries.get(platform) if isinstance(canaries.get(platform), dict) else {}
        query = queries.get(platform) if isinstance(queries.get(platform), dict) else {}
        platform_rows[platform] = {
            "health": _text(canary.get("status") or "unknown").lower(),
            "query_status": _text(query.get("candidate_query_status") or "not run").lower(),
            "exact_roots": int(raw_by_platform.get(platform) or query.get("observed_exact_roots") or 0),
            "qualifying_roots": int(qualifying_by_platform.get(platform) or 0),
            "captured_comments_replies": int(comments_by_platform.get(platform) or query.get("captured_comments_replies") or 0),
            "reviewed_product_relevant_comments_replies": (
                int(reviewed_comments_by_platform.get(platform))
                if isinstance(reviewed_comments_by_platform.get(platform), (int, float))
                else None
            ),
        }
    retry_paths = sorted((ghost / "conversation-runs").glob("tiktok-targeted-retry-*.json"))
    tiktok_retry = _load(retry_paths[-1]) if retry_paths else None
    retry_source = tiktok_retry.get("source") if isinstance((tiktok_retry or {}).get("source"), dict) else {}
    retry_observed_at = _text((tiktok_retry or {}).get("observed_at"))
    attention_observed_at = _text(attention_latest.get("observed_at"))
    current_tiktok = platform_rows["tiktok"]
    current_tiktok_terminal = (
        current_tiktok["health"] == "healthy"
        and current_tiktok["query_status"]
        in {"complete", "complete_relevant", "complete_no_match", "empty"}
    )
    retry_is_newer = bool(
        retry_observed_at
        and (not attention_observed_at or retry_observed_at > attention_observed_at)
    )
    if (
        retry_source.get("status") in {"complete", "empty"}
        and not retry_source.get("error_category")
        and (retry_is_newer or not current_tiktok_terminal)
    ):
        platform_rows["tiktok"].update({
            "health": "healthy",
            "query_status": _text(retry_source.get("status")).lower(),
            "exact_roots": int(retry_source.get("count") or 0),
            "recovered_at": (tiktok_retry or {}).get("observed_at"),
        })

    completed_platform_names = [
        platform for platform, row in platform_rows.items()
        if row["health"] == "healthy"
        and (
            row["query_status"] in {"complete", "complete_relevant", "complete_no_match", "empty"}
            or (row["query_status"] == "partial" and row["exact_roots"] > 0)
        )
    ]
    visible_platform_rows = {platform: platform_rows[platform] for platform in completed_platform_names}
    observed_conversation_count = sum(row["exact_roots"] for row in visible_platform_rows.values())
    captured_comments_replies = sum(row["captured_comments_replies"] for row in visible_platform_rows.values())
    reviewed_product_relevant_comments_replies = int(conversation.get("reviewed_product_relevant_comments_replies") or 0)
    completed_platform_count = len(completed_platform_names)
    supplied_sentiment = sentiment_source.get("counts") if isinstance(sentiment_source.get("counts"), dict) else {}
    sentiment_counts = {
        bucket: max(0, int(supplied_sentiment.get(bucket) or 0))
        for bucket in ("positive", "negative", "neutral", "mixed")
    }
    classified_sentiment = sum(sentiment_counts.values())
    explicitly_unclassified = supplied_sentiment.get("unclassified")
    sentiment_counts["unclassified"] = (
        max(0, int(explicitly_unclassified))
        if isinstance(explicitly_unclassified, (int, float))
        else max(0, observed_conversation_count - classified_sentiment)
    )
    sentiment = {
        **sentiment_source,
        "status": _text(sentiment_source.get("status") or "not_collected").lower(),
        "role": "secondary_context_only",
        "counts": sentiment_counts,
        "sample_denominator": int(
            sentiment_source.get("sample_denominator")
            or sentiment_source.get("total_exact_roots")
            or observed_conversation_count
        ),
        "note": _text(sentiment_source.get("note")) or (
            "Positive, negative, neutral and mixed reactions all remain context; "
            "unclassified posts are not relabeled."
        ),
    }
    conversation_headline = (
        f"{observed_conversation_count} exact posts plus {captured_comments_replies} comments/replies "
        f"observed across {completed_platform_count} successful platform reads."
    )

    coverage = _load(coverage_latest_path) or {}
    coverage_history = []
    for row in _load_jsonl(coverage_history_path):
        coverage_history.append({
            "observed_at": row.get("observed_at"),
            "state": _text(row.get("parity_state") or "unknown").upper(),
            "qualifying_outlets": int(row.get("qualifying_independent_business_financial_outlet_count") or 0),
            "management_acknowledged": bool(row.get("management_acknowledges_a_and_w_economics")),
        })
    official_sources = coverage.get("official_source_receipts") if isinstance(coverage.get("official_source_receipts"), list) else []
    sec_sources = coverage.get("sec_source_receipts") if isinstance(coverage.get("sec_source_receipts"), list) else []
    media_searches = coverage.get("media_search_receipts") if isinstance(coverage.get("media_search_receipts"), list) else []
    transcript_checked = any(
        "transcript" in _text(source.get("source_class") or source.get("key")).casefold()
        for source in official_sources if isinstance(source, dict)
    )
    source_paths = [
        path for path in (
            walmart_latest_path, walmart_history_path, attention_latest_path,
            attention_history_path, coverage_latest_path, coverage_history_path,
            retry_paths[-1] if retry_paths else None,
        ) if isinstance(path, Path) and path.exists()
    ]
    observed_values = [
        _text(value) for value in (
            walmart_latest.get("observed_at"),
            (last_complete or {}).get("observed_at"),
            attention_latest.get("observed_at"),
            coverage.get("observed_at"),
            (tiktok_retry or {}).get("observed_at"),
        ) if value
    ]
    search_state = _text(search_latest.get("state") or "SEARCH_BUILDING_BASELINE").upper()
    conversation_state = _text(conversation.get("state") or "CONVERSATION_BUILDING_BASELINE").upper()
    coverage_state = _text(coverage.get("parity_state") or "unknown").upper()
    comparable_conversation_points = max(
        int(conversation.get("comparable_scheduled_runs") or 0),
        sum(bool(row.get("comparable")) for row in conversation_history),
    )
    triggered_by = []
    if search_state in {"SEARCH_SOFTENING", "SEARCH_COOLING_REVIEW"}:
        triggered_by.append("search attention fell through its frozen review rule")
    if conversation_state in {"CONVERSATION_SOFTENING", "CONVERSATION_COOLING_REVIEW"}:
        triggered_by.append("independent conversation volume fell through its frozen review rule")
    if coverage_state == "EXTENSIVE_COVERAGE_REVIEW":
        triggered_by.append("financial coverage or management acknowledgment reached the review threshold")
    if triggered_by:
        thesis_status = "EXIT_REVIEW"
        thesis_read = "Exit review triggered: " + "; ".join(triggered_by) + "."
    elif int(conversation.get("comparable_scheduled_runs") or 0) < 2:
        thesis_status = "BUILDING_BASELINE"
        search_phrase = (
            "US search remains elevated"
            if search_read.startswith("Elevated")
            else "search direction remains incomplete"
        )
        coverage_phrase = (
            "financial coverage remains niche"
            if coverage_state == "NICHE_ONLY"
            else "financial coverage is broadening but remains below the review threshold"
            if coverage_state == "EARLY_BUSINESS_COVERAGE"
            else "financial-coverage status remains incomplete"
        )
        thesis_read = (
            f"No exit-review trigger. {search_phrase}; the latest observed conversation sample "
            f"contains {observed_conversation_count} exact posts; {coverage_phrase}."
        )
    else:
        thesis_status = "CONTINUE_MONITORING"
        thesis_read = "No exit-review trigger. Search, conversation momentum and financial coverage remain inside the frozen monitoring rules."
    if conversation_state == "CONVERSATION_COOLING_REVIEW":
        conversation_read = "Independent conversation volume crossed the cooling threshold; human exit review is required."
    elif conversation_state == "CONVERSATION_SOFTENING":
        conversation_read = "Independent conversation volume is softening; watch the next comparable run."
    elif int(conversation.get("comparable_scheduled_runs") or 0) < 2:
        conversation_read = (
            f"Latest observed sample: {observed_conversation_count} exact posts and "
            f"{captured_comments_replies} comments/replies across successful sources. "
            "Positive and negative reactions both count toward buzz."
        )
    else:
        conversation_read = "No verified conversation-volume decline trigger."
    payload = {
        "as_of": max(observed_values, default=None),
        "headline": headline,
        "thesis_realization": {
            "status": thesis_status,
            "current_read": thesis_read,
            "exit_review_triggered": bool(triggered_by),
            "triggered_by": triggered_by,
        },
        "availability": {
            "state": _text(((last_complete or {}).get("restock_monitor") or {}).get("availability_state") or "building_baseline").lower(),
            "current": current_counts,
            "latest_attempt": latest_attempt_counts,
            "last_complete": last_complete_counts,
            "history": availability_history,
            "stores": _store_rows(visible_snapshot),
            "newly_available_stores": newly_available,
            "broad_restock_at": 4,
            "full_restock_at": 6,
        },
        "search": {
            "state": _text(search_latest.get("state") or "SEARCH_BUILDING_BASELINE").upper(),
            "status": visible_search_status,
            "current_read": search_read,
            "latest_complete_date": verified_complete_date or search_latest.get("latest_complete_date"),
            "last_successful_observed_at": verified_search_at,
            "ratios": latest_ratios,
            "history": search_history,
            "rolling_seven_day_change": rolling_search_change,
            "query_basket": query_basket,
            "source_health": {
                "latest_attempt_status": _text(search_latest.get("status") or "unknown").lower(),
                "visible_series_uses_last_verified": not bool(_search_ratios(search_latest)),
            },
        },
        "conversations": {
            "state": _text(conversation.get("state") or "CONVERSATION_BUILDING_BASELINE").upper(),
            "headline": conversation_headline,
            "current_read": conversation_read,
            "comparable_runs": int(conversation.get("comparable_scheduled_runs") or 0),
            "comparable_history_points": comparable_conversation_points,
            "coverage_note": (
                "Observed volume is shown from successful sources. Coverage is not yet stable enough "
                "for a like-for-like momentum claim."
                if comparable_conversation_points < 2
                else "Observed volume has at least two comparable monitoring points."
            ),
            "platforms": visible_platform_rows,
            "successful_platforms": completed_platform_names,
            "exact_roots": observed_conversation_count,
            "captured_comments_replies": captured_comments_replies,
            "reviewed_product_relevant_comments_replies": reviewed_product_relevant_comments_replies,
            "qualifying_roots": sum(row["qualifying_roots"] for row in visible_platform_rows.values()),
            "history": conversation_history,
            "sentiment": sentiment,
        },
        "street_coverage": {
            "state": _text(coverage.get("parity_state") or "unknown").upper(),
            "qualifying_outlets": int(coverage.get("qualifying_independent_business_financial_outlet_count") or 0),
            "management_acknowledged": bool(coverage.get("management_acknowledges_a_and_w_economics")),
            "observed_at": coverage.get("observed_at"),
            "history": coverage_history,
            "source_checks": {
                "official_sources": len(official_sources),
                "sec_filings": len(sec_sources),
                "news_queries": len(media_searches),
                "earnings_call_or_transcript_checked": transcript_checked,
            },
        },
        "source_receipts": {
            "upstream_calls": 0,
            "artifacts": [_artifact_receipt(path, root) for path in source_paths],
            "operational_attempts": [
                {
                    "source": "Walmart six-store availability",
                    "observed_at": latest_attempt_counts.get("observed_at"),
                    "status": latest_attempt_counts.get("coverage"),
                    "usable": latest_attempt_counts.get("coverage") == "complete",
                },
                {
                    "source": "Google search attention",
                    "observed_at": attention_latest.get("observed_at"),
                    "status": _text(search_latest.get("status") or "unknown").lower(),
                    "usable": bool(
                        _text(search_latest.get("status")).lower() != "source_failure"
                        and (_search_ratios(search_latest) or verified_values)
                    ),
                },
                *[
                    {
                        "source": f"{platform.title()} conversation check",
                        "observed_at": attention_latest.get("observed_at"),
                        "status": row["query_status"],
                        "usable": False,
                    }
                    for platform, row in platform_rows.items()
                    if platform not in completed_platform_names
                ],
            ],
        },
    }
    if include_private_position:
        payload["exit_monitor"] = _ghost_exit_monitor(root, payload, monitor_jobs or [])
    return payload


EXIT_MONITOR_SCHEMA_VERSION = "bounty-ghost-exit-monitor-view/1"
EXIT_REVIEW_STATES = (
    "NO_EXIT_TRIGGER_VERIFIED",
    "HUMAN_EXIT_REVIEW_REQUIRED",
    "THESIS_INVALIDATION_REVIEW",
    "INFORMATION_PARITY_REVIEW",
    "EXPIRY_REVIEW",
    "DATA_INCOMPLETE",
)


def _date_part(value: Any) -> str | None:
    text = _text(value)
    return text[:10] if len(text) >= 10 else None


def _days_between(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    try:
        from datetime import date

        beginning = date.fromisoformat(start)
        finish = date.fromisoformat(end)
    except ValueError:
        return None
    return (finish - beginning).days


def _exact_option_quote(market: dict[str, Any], expiry: str, strike: float) -> dict[str, Any] | None:
    options = market.get("options") if isinstance(market.get("options"), dict) else {}
    expiries = options.get("expiries") if isinstance(options.get("expiries"), dict) else {}
    node = expiries.get(expiry) if isinstance(expiries.get(expiry), dict) else {}
    quotes = node.get("atm_quotes") if isinstance(node.get("atm_quotes"), dict) else {}
    call = quotes.get("C") if isinstance(quotes.get("C"), dict) else None
    suffix = f"C{int(round(strike * 1000)):08d}"
    if call and _text(call.get("option")).upper().endswith(suffix):
        return call
    return None


def _ghost_exit_monitor(
    root: Path,
    dashboard: dict[str, Any] | None,
    monitor_jobs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Build the private hold/exit decision layer for the GHOST standing monitor.

    Frozen review rules come from the persisted audited contract; every current
    state is recomputed from persisted artifacts. The result carries the exact
    user-reported option position, so it must only travel through the
    authenticated tracker API and never into committed snapshots or public
    static assets. Returns None (fail closed) when the position contract or the
    audited contract is absent.
    """
    workstream = root / "artifacts" / "workstreams" / "chewy-ghost-monitoring"
    contract = _load(workstream / "exit-monitor-minimum-view-v1.json")
    status = _load(workstream / "status.json")
    market = _load(root / "artifacts" / "dd" / "ghost-kdp" / "market_snapshot.json")
    if not contract or not status:
        return None
    position_contract = status.get("position_contract") if isinstance(status.get("position_contract"), dict) else None
    if not position_contract:
        return None
    states = status.get("states") if isinstance(status.get("states"), dict) else {}
    receipts = status.get("scheduler_receipts") if isinstance(status.get("scheduler_receipts"), dict) else {}
    dashboard = dashboard or {}
    availability = dashboard.get("availability") if isinstance(dashboard.get("availability"), dict) else {}
    search = dashboard.get("search") if isinstance(dashboard.get("search"), dict) else {}
    conversations = dashboard.get("conversations") if isinstance(dashboard.get("conversations"), dict) else {}
    coverage = dashboard.get("street_coverage") if isinstance(dashboard.get("street_coverage"), dict) else {}

    market_as_of = _date_part(market.get("as_of")) if market else None
    as_of_candidates = [
        _text(value) for value in (
            dashboard.get("as_of"),
            status.get("updated_at"),
            market.get("as_of") if market else None,
        ) if value
    ]
    as_of = max(as_of_candidates) if as_of_candidates else None
    as_of_date = _date_part(as_of)

    expiry = _text(position_contract.get("expiry"))
    days_to_expiry = _days_between(as_of_date, expiry)
    strike = position_contract.get("strike_usd")
    premium_per_unit = position_contract.get("premium_per_underlying_unit_usd")
    contracts = int(position_contract.get("contracts") or 0)
    multiplier = int(position_contract.get("contract_multiplier") or 0)
    premium_paid = position_contract.get("premium_paid_usd")
    breakeven = (
        round(float(strike) + float(premium_per_unit), 2)
        if isinstance(strike, (int, float)) and isinstance(premium_per_unit, (int, float))
        else None
    )

    # --- Recompute the exit-review trigger matrix from persisted states. ---
    current_availability = availability.get("current") if isinstance(availability.get("current"), dict) else {}
    last_complete = availability.get("last_complete") if isinstance(availability.get("last_complete"), dict) else {}
    latest_coverage = _text(current_availability.get("coverage") or "unknown").lower()
    coverage_state = _text(coverage.get("state") or "unknown").upper()
    search_state = _text(search.get("state") or "SEARCH_BUILDING_BASELINE").upper()
    conversation_state = _text(conversations.get("state") or "CONVERSATION_BUILDING_BASELINE").upper()
    quote = _exact_option_quote(market or {}, expiry, float(strike)) if market and isinstance(strike, (int, float)) else None
    quote_current = bool(quote and market_as_of and market_as_of == as_of_date)
    user_loss_status = _text(position_contract.get("maximum_acceptable_loss_status") or "not_set_by_user")
    user_loss = position_contract.get("maximum_acceptable_loss")

    triggers_met: list[dict[str, Any]] = []
    triggers_not_met: list[dict[str, Any]] = []
    if coverage_state == "EXTENSIVE_COVERAGE_REVIEW":
        triggers_met.append({
            "trigger": "information_parity",
            "evidence": "At least three qualifying independent outlets in 14 days, or explicit KDP management attribution.",
            "observed_at": coverage.get("observed_at"),
        })
    else:
        triggers_not_met.append({
            "trigger": "information_parity",
            "status": "not_met",
            "evidence": f"{int(coverage.get('qualifying_outlets') or 0)} qualifying outlets; management acknowledgment "
                        f"{'present' if coverage.get('management_acknowledged') else 'absent'}.",
            "observed_at": coverage.get("observed_at"),
            "next_check": (receipts.get("attention_and_parity_watch") or {}).get("next_run_at") if isinstance(receipts.get("attention_and_parity_watch"), dict) else None,
        })
    cooling = [
        row for row in (
            {"trigger": "search_attention_cooling", "state": search_state,
             "observed_at": search.get("latest_complete_date") or search.get("status")},
            {"trigger": "conversation_cooling", "state": conversation_state,
             "observed_at": coverage.get("observed_at")},
        )
        if row["state"] in {"SEARCH_COOLING_REVIEW", "CONVERSATION_COOLING_REVIEW"}
    ]
    invalidation = contract.get("thesis_invalidation_review") if isinstance(contract.get("thesis_invalidation_review"), dict) else {}
    invalidation_triggers = [
        _text(row) for row in _as_list(invalidation.get("human_review_triggers")) if _text(row)
    ]
    triggers_not_met.append({
        "trigger": "thesis_invalidation",
        "status": "not_met",
        "evidence": f"None of the {len(invalidation_triggers)} persisted invalidation tests is verified by current evidence.",
        "observed_at": as_of,
        "next_check": "Every scheduled monitor run re-tests availability, repeat demand and coverage evidence.",
    })
    for row in cooling:
        triggers_met.append({
            "trigger": row["trigger"],
            "evidence": "The frozen cooling review rule was crossed on the latest comparable reading.",
            "observed_at": row["observed_at"],
        })
    expiry_met = days_to_expiry is not None and days_to_expiry <= 0
    (triggers_met if expiry_met else triggers_not_met).append({
        "trigger": "expiry",
        "status": "met" if expiry_met else "not_met",
        "evidence": f"{days_to_expiry if days_to_expiry is not None else 'Unknown'} calendar days to the {expiry} hard latest date"
                    + ("; the option has reached its expiry review." if expiry_met else "."),
        "observed_at": as_of,
        "next_check": "Position handling at expiry is a human decision; no automatic action exists.",
    })
    (triggers_not_met if user_loss is None else triggers_met).append({
        "trigger": "user_risk_boundary",
        "status": "not_set" if user_loss is None else "met",
        "evidence": "The user has not set a maximum acceptable loss, so the price-loss alert stays disabled."
                    if user_loss is None else "The position is at or beyond the user-set maximum acceptable loss.",
        "observed_at": as_of,
        "next_check": "Set a maximum acceptable loss to arm this alert; it still only requests human review.",
    })

    # --- Critical data gaps, each named and dated. ---
    data_gaps: list[dict[str, Any]] = []
    if latest_coverage != "complete":
        failed = int(current_availability.get("unverified") or 0)
        data_gaps.append({
            "gap": "current_retailer_availability",
            "detail": f"The latest six-store Walmart attempt on {_date_part(current_availability.get('observed_at'))} returned source failures for "
                      f"{failed} store rows; current availability is unknown, not out of stock. The last complete panel is from "
                      f"{_date_part(last_complete.get('observed_at'))}.",
        })
    if not quote_current:
        data_gaps.append({
            "gap": "current_option_quote",
            "detail": f"No verified current KDP or exact-contract quote exists for {_date_part(as_of) or 'this decision'}; the last exact quote is from "
                      f"{market_as_of or 'no snapshot'} and remains dated context only.",
        })
    if user_loss is None:
        data_gaps.append({
            "gap": "user_loss_limit",
            "detail": "The user has not set a maximum acceptable loss; this disables only the price-loss alert.",
        })

    if expiry_met:
        review_state = "EXPIRY_REVIEW"
        reason = f"The position reached its {expiry} hard latest date; expiry handling requires human review. No automatic trade action is permitted."
    elif coverage_state == "EXTENSIVE_COVERAGE_REVIEW":
        review_state = "INFORMATION_PARITY_REVIEW"
        reason = "Financial coverage or management acknowledgment crossed the exact information-parity threshold; human exit review is required. This is not an automatic sale."
    elif cooling:
        review_state = "HUMAN_EXIT_REVIEW_REQUIRED"
        reason = "Exit review triggered: " + "; ".join(
            str(row["trigger"]).replace("_", " ") for row in cooling
        ) + ". Human review only; no automatic trade action is permitted."
    elif data_gaps:
        review_state = "DATA_INCOMPLETE"
        reason = " ".join(row["detail"] for row in data_gaps) + " No exit call is made from incomplete data; no automatic trade action is permitted."
    else:
        review_state = "NO_EXIT_TRIGGER_VERIFIED"
        reason = "No exit-review trigger is verified and no critical data gap is open. Continue the standing monitor; human review only."

    # --- Four-link thesis chain (availability recomputed; the rest persist). ---
    contract_chain = contract.get("thesis_chain") if isinstance(contract.get("thesis_chain"), dict) else {}
    contract_checks = {
        _text(row.get("check")): row
        for row in _as_list(contract_chain.get("required_checks"))
        if isinstance(row, dict) and _text(row.get("check"))
    }
    availability_state = _text(availability.get("state") or "building_baseline")
    newly_available = _as_list(availability.get("newly_available_stores"))
    if latest_coverage != "complete":
        availability_check_state = "unresolved_current_source_failure"
        availability_evidence = (
            f"The last complete panel on {_date_part(last_complete.get('observed_at'))} showed "
            f"{int(last_complete.get('available') or 0)} orderable and {int(last_complete.get('out_of_stock') or 0)} out-of-stock stores"
            + (f" after {', '.join(newly_available)} replenished" if newly_available else "")
            + f"; the {_date_part(current_availability.get('observed_at'))} attempt returned source-failed rows."
        )
    else:
        availability_check_state = availability_state
        availability_evidence = (
            f"The latest complete panel on {_date_part(current_availability.get('observed_at'))} showed "
            f"{int(current_availability.get('available') or 0)} orderable and {int(current_availability.get('out_of_stock') or 0)} out-of-stock stores"
            + (f" after {', '.join(newly_available)} replenished." if newly_available else ".")
        )
    thesis_rows = [{
        "check": "availability_and_replenishment",
        "label": "Availability and replenishment",
        "current_state": availability_check_state,
        "current_evidence": availability_evidence,
        "source": "artifacts/dd/ghost-kdp/walmart_native_latest.json and walmart_native_history.jsonl",
        "observed_at": current_availability.get("observed_at") or last_complete.get("observed_at"),
    }]
    for key, label, observed_at in (
        ("repeat_purchase", "Repeat purchase", contract.get("audited_at")),
        ("incremental_vs_cannibalized_demand", "Incremental vs cannibalized demand", contract.get("audited_at")),
        ("kdp_materiality_and_expectations", "KDP materiality and expectations", coverage.get("observed_at")),
    ):
        row = contract_checks.get(key) or {}
        thesis_rows.append({
            "check": key,
            "label": label,
            "current_state": _text(row.get("current_state") or "not_evaluated"),
            "current_evidence": _text(row.get("current_evidence") or "No persisted evaluation is available."),
            "source": _text(row.get("source") or "artifacts/dd/ghost-kdp/dossier.md"),
            "observed_at": observed_at,
        })

    parity = contract.get("information_parity_review") if isinstance(contract.get("information_parity_review"), dict) else {}
    risk = contract.get("expiry_and_risk_review") if isinstance(contract.get("expiry_and_risk_review"), dict) else {}
    missing_boundaries = [_text(row) for row in _as_list(risk.get("missing_user_boundaries")) if _text(row)]

    # --- Source health: latest attempt, last complete, job vs source success. ---
    native_receipt = receipts.get("native_walmart_panel") if isinstance(receipts.get("native_walmart_panel"), dict) else {}
    attention_receipt = receipts.get("attention_and_parity_watch") if isinstance(receipts.get("attention_and_parity_watch"), dict) else {}
    barclays_receipt = receipts.get("kdp_barclays_update") if isinstance(receipts.get("kdp_barclays_update"), dict) else {}
    root_beer_state = states.get("root_beer_product") if isinstance(states.get("root_beer_product"), dict) else {}
    attention_state = states.get("search_and_conversation_attention") if isinstance(states.get("search_and_conversation_attention"), dict) else {}
    parity_state = states.get("street_and_headline_parity") if isinstance(states.get("street_and_headline_parity"), dict) else {}
    ghost_brand = states.get("ghost_brand") if isinstance(states.get("ghost_brand"), dict) else {}

    def _sensor(name, role, *, observed_state, operational_state, latest_attempt_at, latest_attempt_result,
                last_complete_at, last_complete_result, next_run_at, source_gap):
        return {
            "sensor": name,
            "role": role,
            "observed_state": _text(observed_state or "unknown"),
            "operational_state": _text(operational_state or "unknown"),
            "latest_attempt_at": latest_attempt_at,
            "latest_attempt_result": _text(latest_attempt_result or "unknown"),
            "last_complete_at": last_complete_at,
            "last_complete_result": _text(last_complete_result or "none yet"),
            "next_run_at": next_run_at,
            "source_gap": _text(source_gap or "none"),
        }

    coverage_history_rows = coverage.get("history") if isinstance(coverage.get("history"), list) else []
    coverage_last_row = coverage_history_rows[-1] if coverage_history_rows and isinstance(coverage_history_rows[-1], dict) else {}
    sensors = [
        _sensor(
            "walmart_native_panel", "Retailer availability",
            observed_state=availability_state,
            operational_state=(root_beer_state.get("operational_state") or latest_coverage),
            latest_attempt_at=current_availability.get("observed_at"),
            latest_attempt_result=f"{latest_coverage}; {int(current_availability.get('unverified') or 0)} of "
                                   f"{int(current_availability.get('available') or 0) + int(current_availability.get('out_of_stock') or 0) + int(current_availability.get('not_listed') or 0) + int(current_availability.get('unverified') or 0)} stores source-failed",
            last_complete_at=last_complete.get("observed_at"),
            last_complete_result=f"complete; {int(last_complete.get('available') or 0)} orderable, "
                                 f"{int(last_complete.get('out_of_stock') or 0)} out of stock",
            next_run_at=native_receipt.get("next_run_at"),
            source_gap="The latest attempt produced no usable store rows; current availability is unknown, not depleted."
            if latest_coverage != "complete" else "none",
        ),
        _sensor(
            "search_attention", "Google search direction",
            observed_state=search_state,
            operational_state=attention_state.get("operational_state"),
            latest_attempt_at=dashboard.get("as_of"),
            latest_attempt_result=_text(search.get("status") or "unknown"),
            last_complete_at=search.get("latest_complete_date"),
            last_complete_result="complete; both basket queries above their prior-seven-day means",
            next_run_at=attention_receipt.get("next_run_at"),
            source_gap=_text(attention_state.get("operational_state") or ""),
        ),
        _sensor(
            "conversation_coverage", "Five-platform social depth",
            observed_state=conversation_state,
            operational_state=attention_state.get("operational_state"),
            latest_attempt_at=dashboard.get("as_of"),
            latest_attempt_result=f"comparable runs {int(conversations.get('comparable_runs') or 0)}",
            last_complete_at=None,
            last_complete_result="none yet; a full five-platform comparable run is still required",
            next_run_at=attention_receipt.get("next_run_at"),
            source_gap="Conversation direction stays unmeasured until two comparable five-platform runs exist.",
        ),
        _sensor(
            "street_parity", "Exact business coverage",
            observed_state=coverage_state,
            operational_state=parity_state.get("operational_state"),
            latest_attempt_at=coverage.get("observed_at"),
            latest_attempt_result=f"{int(coverage.get('qualifying_outlets') or 0)} qualifying outlets",
            last_complete_at=coverage_last_row.get("observed_at"),
            last_complete_result=f"state {coverage_state}",
            next_run_at=attention_receipt.get("next_run_at"),
            source_gap=_text(parity_state.get("operational_state") or ""),
        ),
    ]
    job_source_separation = {
        "job": "native_walmart_panel",
        "job_last_run_at": native_receipt.get("last_run_at"),
        "job_last_status": _text(native_receipt.get("last_status") or "unknown"),
        "job_completed": True,
        "source_success": latest_coverage == "complete",
        "note": "The scheduled collection job completed operationally, but every store source observation failed. "
                "Script completion is not source success; the last complete reading stays the evidence of record.",
    }
    monitor_job_states = [
        {"job": "legacy_supporting_store_panel", "state": "paused", "next_run_at": None,
         "note": "Superseded by the native route; remains paused."},
        {"job": "native_walmart_panel", "state": "scheduled", "next_run_at": native_receipt.get("next_run_at"),
         "note": job_source_separation["note"]},
        {"job": "kdp_barclays_update", "state": "scheduled",
         "next_run_at": barclays_receipt.get("run_at") or ghost_brand.get("scheduled_at"),
         "note": "One-shot event update for the KDP Barclays conference."},
        {"job": "trends_conversations_and_parity_watch", "state": "scheduled", "next_run_at": attention_receipt.get("next_run_at"),
         "note": "Daily search, conversation and exact-implication coverage watch through 2027-04-16."},
    ]

    last_verified = None
    if market and quote:
        last_verified = {
            "as_of": market.get("as_of"),
            "status_for_current_exit_decision": "current" if quote_current else "stale",
            "underlying_close": market.get("regular_market_price"),
            "underlying_close_date": (market.get("six_month_end") or {}).get("date") if isinstance(market.get("six_month_end"), dict) else None,
            "option_symbol": quote.get("option"),
            "option_bid": quote.get("bid"),
            "option_ask": quote.get("ask"),
            "option_spread": round(float(quote["ask"]) - float(quote["bid"]), 4) if isinstance(quote.get("ask"), (int, float)) and isinstance(quote.get("bid"), (int, float)) else None,
            "open_interest": quote.get("open_interest"),
            "volume": quote.get("volume"),
            "implied_volatility": quote.get("iv"),
            "source_url": (market.get("options") or {}).get("source_url") if isinstance(market.get("options"), dict) else None,
        }
    current_market = {
        "underlying_bid": None, "underlying_ask": None,
        "option_bid": None, "option_ask": None, "option_last": None,
        "option_market_value": None, "unrealized_pnl": None,
        "intrinsic_value": None, "extrinsic_value": None,
        "implied_volatility": None, "delta": None, "theta": None,
        "status": "verified_current" if quote_current else "unavailable_no_verified_current_quote",
        "as_of": as_of_date if quote_current else None,
    }
    if quote_current and isinstance(market.get("regular_market_price"), (int, float)) and isinstance(strike, (int, float)):
        current_market["option_bid"] = quote.get("bid")
        current_market["option_ask"] = quote.get("ask")
        current_market["implied_volatility"] = quote.get("iv")
        current_market["intrinsic_value"] = round(max(0.0, float(market["regular_market_price"]) - float(strike)), 4)

    exit_paths = [
        path for path in (
            workstream / "exit-monitor-minimum-view-v1.json",
            workstream / "status.json",
            root / "artifacts" / "dd" / "ghost-kdp" / "market_snapshot.json",
        ) if path.exists()
    ]
    return {
        "schema_version": EXIT_MONITOR_SCHEMA_VERSION,
        "classification": "sensitive_internal_do_not_publish_or_commit",
        "as_of": as_of,
        "decision_banner": {
            "review_state": review_state,
            "allowed_review_states": list(EXIT_REVIEW_STATES),
            "as_of": as_of,
            "plain_english_reason": reason,
            "exit_review_triggers_met": triggers_met,
            "exit_review_triggers_not_met": triggers_not_met,
            "critical_data_gaps": data_gaps,
            "human_review_only": True,
            "automatic_trade_action": False,
        },
        "position_and_clock": {
            "underlying": position_contract.get("underlying"),
            "instrument_type": position_contract.get("instrument_type"),
            "option_right": position_contract.get("option_right"),
            "strike_usd": strike,
            "expiry": expiry,
            "contracts": contracts,
            "contract_multiplier": multiplier,
            "underlying_units": int(position_contract.get("underlying_units") or contracts * multiplier or 0),
            "entry_date": position_contract.get("entry_date"),
            "premium_per_underlying_unit_usd": premium_per_unit,
            "premium_paid_usd": premium_paid,
            "at_expiry_premium_breakeven_usd": breakeven,
            "at_expiry_premium_breakeven_formula": "strike_usd + premium_per_underlying_unit_usd",
            "days_to_expiry": days_to_expiry,
            "days_to_expiry_as_of": as_of_date,
            "next_catalyst": ghost_brand.get("next_check"),
            "next_catalyst_scheduled_at": ghost_brand.get("scheduled_at"),
            "contractual_max_loss_usd": position_contract.get("contractual_long_call_max_loss_usd"),
            "user_maximum_acceptable_loss_usd": user_loss,
            "user_maximum_acceptable_loss_status": user_loss_status,
            "loss_alert_enabled": bool(position_contract.get("user_defined_loss_threshold_alert_enabled")),
            "intended_horizon": (position_contract.get("intended_horizon") or {}).get("description") if isinstance(position_contract.get("intended_horizon"), dict) else None,
            "source": "artifacts/workstreams/chewy-ghost-monitoring/status.json#position_contract",
        },
        "thesis_chain": {
            "focal_proposition": _text(contract_chain.get("focal_proposition")),
            "checks": thesis_rows,
            "display_rule": _text(contract_chain.get("display_rule")),
        },
        "invalidation_review": {
            "triggers": invalidation_triggers,
            "verified_count": 0,
            "automation_rule": _text(invalidation.get("automation_rule")),
            "source": _text(invalidation.get("source")),
        },
        "information_parity": {
            "exact_implication": _text(parity.get("exact_implication")),
            "current_state": coverage_state,
            "qualifying_outlets": int(coverage.get("qualifying_outlets") or 0),
            "management_acknowledgment": bool(coverage.get("management_acknowledged")),
            "observed_at": coverage.get("observed_at"),
            "human_review_trigger": _text(parity.get("human_review_trigger")),
            "rules": [_text(row) for row in _as_list(parity.get("rules")) if _text(row)],
        },
        "market_and_option_context": {
            "currency_rule": "A quote counts as current only when its as-of date matches the decision as-of date; older quotes stay visible as dated context and never stand in for current P&L.",
            "current": current_market,
            "last_verified_snapshot": last_verified,
            "greeks_note": "Delta, theta and extrinsic value have no collected source; they stay blank rather than estimated.",
        },
        "risk_boundaries": {
            "missing_user_boundaries": missing_boundaries,
            "rule": _text(risk.get("missing_boundary_rule")),
            "maximum_acceptable_loss": "Not set by user" if user_loss is None else user_loss,
            "pre_expiry_close_or_roll_policy": "Not set by user",
            "time_decay_review_threshold": "Not set by user",
            "loss_alert_enabled": False if user_loss is None else True,
        },
        "data_health": {
            "sensors": sensors,
            "job_source_separation": job_source_separation,
            "monitor_job_states": monitor_job_states,
        },
        "source_receipts": {
            "upstream_calls": 0,
            "artifacts": [_artifact_receipt(path, root) for path in exit_paths],
        },
    }


def build_investment_tracker(
    repo_root: str | Path | None = None,
    *,
    cron_jobs_path: str | Path | None = None,
    now: datetime | None = None,
    include_private_position: bool = False,
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
        "tracker_trends": root / "data" / "investing-tracker-trends.json",
        "persistence_rescore": artifacts / "investing-discovery" / "corrected-rerun-2026-09-05" / "existing-bank-rescore.json",
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

    persistence_by_title = {
        _text(row.get("idea")): row
        for row in ((loaded["persistence_rescore"] or {}).get("current_watch_list") or {}).get("rows", [])
        if isinstance(row, dict)
    }
    for item in ideas.values():
        persistence = persistence_by_title.get(_text(item.get("title")))
        if persistence:
            raw_state = _text(persistence.get("persistence_state")).lower()
            item["signal_state"] = (
                "COLLAPSED_NO_NEW_ENTRY"
                if raw_state in {"collapsed", "collapsed_event_spike"}
                else "DECAYING_NO_NEW_ENTRY"
                if raw_state == "stable_or_unclear_not_current"
                else "UNVERIFIED"
            )
            item["active_trend"] = False
            item["persistence_treatment"] = persistence.get("recommended_treatment")

    tracker_trends = loaded["tracker_trends"] or {}
    for row in tracker_trends.get("items", []):
        if not isinstance(row, dict):
            continue
        item = ideas.get(_text(row.get("idea_id")))
        if not item:
            continue
        trend_bundle = row.get("search_trends") if isinstance(row.get("search_trends"), dict) else {}
        trend_state = trend_bundle.get("classification") if isinstance(trend_bundle.get("classification"), dict) else {}
        theme_state = row.get("theme_assessment") if isinstance(row.get("theme_assessment"), dict) else {}
        item["search_trends"] = trend_bundle
        item["signal_state"] = _text(trend_state.get("state") or item.get("signal_state") or "UNVERIFIED").upper()
        item["active_trend"] = bool(theme_state.get("active"))
        item["theme_assessment"] = theme_state
        item["trend_geography"] = row.get("geography_label") or row.get("geography")
        item["economic_confirmation_required"] = row.get("economic_confirmation_required")
        item["geography_limit"] = row.get("geography_limit")

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
    ghost_dashboard = _ghost_monitor_dashboard(root)
    ghost_updated = max(
        [row.get("last_run_at") or "" for row in ghost_jobs]
        + ([_text(ghost_dashboard.get("as_of"))] if ghost_dashboard else []),
        default=None,
    )
    add({
        "idea_id": "standing::ghost-aw-kdp",
        "title": "GHOST Energy x A&W / KDP",
        "primary_state": "STANDING_MONITOR",
        "instruments": ["NASDAQ:KDP"],
        "why": "User-supplied standing thesis and calibration case; it does not count as blind discovery yield.",
        "detail": ghost_dashboard.get("headline") if ghost_dashboard else None,
        "next_check": "Track exact Walmart replenishment, search attention, independent conversations and Street coverage.",
        "source_run": "standing-monitor",
        "source_artifact": "artifacts/dd/ghost-kdp/attention_latest.json" if ghost_dashboard else None,
        "updated_at": ghost_updated,
        "monitoring": _monitor_summary(ghost_jobs),
        "monitor_dashboard": ghost_dashboard,
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
    signal_counts = Counter(_text(row.get("signal_state") or "NOT_APPLICABLE").upper() for row in rows)
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
            "decision_queue": sum(row["primary_state"] in {"PURSUE", "INVESTIGATING"} for row in rows),
            "signal_state_counts": dict(signal_counts),
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
