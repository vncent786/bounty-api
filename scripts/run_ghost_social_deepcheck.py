"""Authenticated, multi-query GHOST Energy x A&W social deep-check.

This monitor-specific collector fixes the 2026-09-06 shallow check: it runs the
entire frozen product query basket on X, TikTok, and Instagram, applies one exact
object matcher, and reads comments/replies for every retained original post.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from social_scraper.base import ConnectorResult, SocialItem  # noqa: E402
from social_scraper.conversations.thread_reader import ThreadFetchResult  # noqa: E402
from social_scraper.investing.owned_radar import OwnedRadarCollector  # noqa: E402


DEFAULT_CONTRACT = (
    ROOT / "references" / "ghost-social-deepcheck-contract-v1.json"
)
DEFAULT_PLATFORMS = ("x", "tiktok", "instagram")
TERMINAL_QUERY_STATES = {"complete_relevant", "complete_no_match", "empty"}
FAILED_THREAD_STATES = {"failed", "unavailable", "error", "unsupported"}


def _git_common_repository_root(root: Path) -> Path | None:
    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=str(root),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    common_dir = Path(result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = root / common_dir
    return common_dir.resolve().parent


def load_environment(root: Path) -> tuple[Path, ...]:
    checkout_root = root.resolve()
    common_root = _git_common_repository_root(checkout_root)
    roots = (
        [common_root, checkout_root]
        if common_root is not None and common_root != checkout_root
        else [checkout_root]
    )
    loaded = []
    for repository_root in roots:
        env_path = repository_root / ".env"
        if env_path.is_file() and env_path not in loaded:
            load_dotenv(env_path, override=False)
            loaded.append(env_path)
    return tuple(loaded)


def configure_owned_social(
    *,
    tiktok_profile: Path | None,
    tiktok_extension: Path | None,
) -> None:
    for name, path in (
        ("BOUNTY_TIKTOK_PROFILE_PATH", tiktok_profile),
        ("BOUNTY_TIKTOK_EXTENSION_PATH", tiktok_extension),
    ):
        if path is None or not path.exists():
            raise ValueError(f"{name} does not exist: {path}")
        os.environ[name] = str(path.resolve())
    if not os.getenv("BOUNTY_IG_COOKIE_PATH", "").strip():
        common_root = _git_common_repository_root(ROOT)
        shared_cookie = (
            common_root / "data" / "ig_cookies.json"
            if common_root is not None else None
        )
        if shared_cookie is None or not shared_cookie.is_file():
            raise ValueError("BOUNTY_IG_COOKIE_PATH is not configured")
        os.environ["BOUNTY_IG_COOKIE_PATH"] = str(shared_cookie.resolve())
    os.environ["BOUNTY_OWNED_SOCIAL_WORKER"] = "1"


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_contract(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "bounty-ghost-social-deepcheck-contract/1":
        raise ValueError("unsupported GHOST social deep-check contract")
    platforms = payload.get("platforms") or {}
    if set(platforms) != set(DEFAULT_PLATFORMS):
        raise ValueError("contract must contain exactly X, TikTok, and Instagram")
    for platform in DEFAULT_PLATFORMS:
        row = platforms[platform]
        if not row.get("connector") or not row.get("canary"):
            raise ValueError(f"missing route or canary for {platform}")
        queries = row.get("queries") or []
        if len(queries) < 2 or len(queries) != len(set(queries)):
            raise ValueError(f"{platform} requires a unique multi-query basket")
    return payload


def _identity_text(item: SocialItem | dict[str, Any]) -> str:
    if isinstance(item, SocialItem):
        author_username = item.author_username
        author_display_name = item.author_display_name
        text = item.text
        hashtags = item.hashtags
    else:
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        author_username = str(author.get("username") or item.get("author_username") or "")
        author_display_name = str(
            author.get("display_name") or item.get("author_display_name") or ""
        )
        text = str(item.get("text") or "")
        hashtags = item.get("hashtags") or []
    return " ".join(
        [
            str(text or ""),
            str(author_username or ""),
            str(author_display_name or ""),
            " ".join(str(value) for value in hashtags),
        ]
    ).casefold()


def exact_object_match(item: SocialItem | dict[str, Any]) -> tuple[bool, list[str]]:
    """Match only the exact GHOST/A&W product identity, never a broad category."""
    identity = _identity_text(item)
    compact = re.sub(r"[^a-z0-9]+", "", identity)
    brand_identity = "ghostenergy" in compact or "ghostlifestyle" in compact
    ghost_aw_phrase = bool(
        re.search(
            r"\bghost(?:\s+energy)?\s*(?:x|and|&)?\s*a\s*(?:&|and)\s*w\b",
            identity,
        )
        or re.search(r"\ba\s*(?:&|and)\s*w\s+root\s+beer\s+ghost\b", identity)
    )
    ghost_root_beer_phrase = bool(
        re.search(r"\bghost(?:\s+energy)?\s+root\s+beer\b", identity)
    )
    has_aw_or_root_beer = bool(
        re.search(r"\ba\s*(?:&|and)\s*w\b", identity)
        or "awrootbeer" in compact
        or "aandwrootbeer" in compact
        or "rootbeer" in compact
    )
    reasons = [
        name
        for name, present in (
            ("ghost_energy_or_lifestyle_identity", brand_identity),
            ("ghost_a_and_w_product_phrase", ghost_aw_phrase),
            ("ghost_root_beer_product_phrase", ghost_root_beer_phrase),
        )
        if present
    ]
    return bool(
        ghost_aw_phrase
        or ghost_root_beer_phrase
        or (brand_identity and has_aw_or_root_beer)
    ), reasons


def classify_query_result(
    platform: str,
    query: str,
    result: ConnectorResult,
    exact_count: int,
) -> tuple[str, str | None]:
    """Keep explicit empty, semantic no-match, and source failure separate."""
    health = result.health
    if health.status == "error" or health.error and health.error not in {
        "tiktok_query_empty"
    }:
        return "failed", health.error or f"{platform}_source_error"
    if result.items:
        return (
            ("complete_relevant", None)
            if exact_count > 0
            else ("complete_no_match", None)
        )
    if platform == "x" and health.status == "ok" and not health.error:
        return "empty", None
    if platform == "tiktok" and (
        health.status == "partial"
        and health.error == "tiktok_query_empty"
        and health.coverage.get("page_state") == "query_empty"
        and int(health.coverage.get("api_payloads_parsed") or 0) > 0
        and int(health.coverage.get("parsed_items_before_dedup") or 0) == 0
    ):
        return "empty", None
    if platform == "instagram" and (
        query.startswith("#")
        and health.status == "partial"
        and not health.error
        and health.coverage.get("route") == "hashtag_web_info"
        and health.coverage.get("tag_media_count") == 0
    ):
        return "empty", None
    return "failed", health.error or "ambiguous_zero_no_explicit_source_state"


def normalized_thread_state(thread: ThreadFetchResult) -> str:
    if thread.status == "partial" and thread.records and not thread.error_category:
        return "bounded_partial"
    if thread.status in {"complete", "empty"} and not thread.error_category:
        return thread.status
    return thread.status if thread.status in FAILED_THREAD_STATES else "failed"


def _connector_for(
    collector: OwnedRadarCollector,
    platform: str,
    expected_name: str,
):
    matches = [
        route.connector
        for route in collector.broker.iter_routes()
        if route.connector.platform == platform
        and route.connector.connector_name == expected_name
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one {platform}/{expected_name} route, found {len(matches)}"
        )
    return matches[0]


def _thread_receipt(
    thread: ThreadFetchResult,
    *,
    root_url: str,
    canary: bool = False,
) -> dict[str, Any]:
    comments = sum(record.record_type == "comment" for record in thread.records)
    replies = sum(record.record_type == "reply" for record in thread.records)
    return {
        "root_post_external_id": thread.root_post_external_id,
        "root_url": root_url,
        "state": normalized_thread_state(thread),
        "source_status": thread.status,
        "returned_count": len(thread.records),
        "comments": comments,
        "replies": replies,
        "platform_reported_total": thread.platform_reported_total,
        "cap": thread.max_comments,
        "max_depth": thread.max_depth,
        "truncated": bool(thread.truncated),
        "attempted_route": thread.attempted_route,
        "error_category": thread.error_category,
        "limitations": list(thread.limitations or ()),
        "records": [] if canary else [record.to_dict() for record in thread.records],
    }


async def _preflight_platform(
    connector,
    *,
    platform: str,
    query: str,
) -> dict[str, Any]:
    started_at = utc_now()
    try:
        result = await connector.search(
            query,
            # Latest X results often have no replies in the first ten rows.
            # Inspect a bounded 60-row canary so the reply route is exercised
            # against a positive-comment root rather than misdiagnosed as down.
            count=60 if platform == "x" else 10,
            time_filter="halfyear",
            sort="latest",
        )
    except Exception as exc:
        return {
            "platform": platform,
            "status": "failed",
            "query": query,
            "started_at": started_at,
            "completed_at": utc_now(),
            "returned_count": 0,
            "error_category": type(exc).__name__,
        }
    health = result.health.to_dict()
    if health.get("status") != "ok" or not result.items:
        return {
            "platform": platform,
            "status": "failed",
            "query": query,
            "started_at": started_at,
            "completed_at": utc_now(),
            "returned_count": len(result.items),
            "health": health,
            "error_category": health.get("error") or f"{platform}_canary_empty",
        }
    ranked = sorted(
        result.items,
        key=lambda item: (
            isinstance(item.comments, int) and item.comments > 0,
            item.comments or 0,
            item.likes or 0,
        ),
        reverse=True,
    )
    root = ranked[0]
    try:
        thread = await connector.fetch_thread(root, max_comments=12, max_depth=2)
        depth = _thread_receipt(thread, root_url=root.url, canary=True)
    except Exception as exc:
        depth = {
            "root_post_external_id": root.post_id,
            "root_url": root.url,
            "state": "failed",
            "returned_count": 0,
            "comments": 0,
            "replies": 0,
            "error_category": type(exc).__name__,
        }
    reported_comments = root.comments if isinstance(root.comments, int) else None
    depth_healthy = bool(
        depth.get("state") in {"complete", "empty", "bounded_partial"}
        and not depth.get("error_category")
        and (
            int(depth.get("returned_count") or 0) > 0
            or (depth.get("state") == "empty" and reported_comments in {None, 0})
        )
    )
    return {
        "platform": platform,
        "status": "healthy" if depth_healthy else "failed",
        "query": query,
        "started_at": started_at,
        "completed_at": utc_now(),
        "connector": connector.connector_name,
        "returned_count": len(result.items),
        "health": health,
        "depth_canary": depth,
        "error_category": None if depth_healthy else (
            depth.get("error_category") or f"{platform}_depth_not_readable"
        ),
    }


async def _collect_platform(
    connector,
    *,
    platform: str,
    queries: list[str],
    root_cap: int,
    comment_cap: int,
    max_depth: int,
    time_filter: str,
    sort: str,
) -> dict[str, Any]:
    query_receipts: list[dict[str, Any]] = []
    raw_roots: dict[str, SocialItem] = {}
    exact_roots: dict[str, dict[str, Any]] = {}

    for query in queries:
        started_at = utc_now()
        try:
            result = await connector.search(
                query,
                count=root_cap,
                time_filter=time_filter,
                sort=sort,
            )
        except Exception as exc:
            query_receipts.append({
                "query": query,
                "started_at": started_at,
                "completed_at": utc_now(),
                "status": "failed",
                "provider_rows": 0,
                "exact_rows": 0,
                "semantic_no_match_rows": 0,
                "error_category": type(exc).__name__,
            })
            continue

        exact_items: list[SocialItem] = []
        for item in result.items:
            raw_roots[item.post_id] = item
            matched, reasons = exact_object_match(item)
            if not matched:
                continue
            exact_items.append(item)
            stored = exact_roots.setdefault(
                item.post_id,
                {"item": item, "matched_queries": [], "match_reasons": []},
            )
            if len(item.text or "") > len(stored["item"].text or ""):
                stored["item"] = item
            stored["matched_queries"].append(query)
            stored["match_reasons"] = sorted(
                set(stored["match_reasons"]) | set(reasons)
            )
        status, error = classify_query_result(
            platform, query, result, len(exact_items)
        )
        query_receipts.append({
            "query": query,
            "started_at": started_at,
            "completed_at": utc_now(),
            "status": status,
            "provider_rows": len(result.items),
            "exact_rows": len(exact_items),
            "semantic_no_match_rows": len(result.items) - len(exact_items),
            "provider_row_ids": [item.post_id for item in result.items],
            "exact_row_ids": [item.post_id for item in exact_items],
            "health": result.health.to_dict(),
            "error_category": error,
        })

    roots_payload = []
    thread_reads = []
    for post_id in sorted(exact_roots):
        stored = exact_roots[post_id]
        item = stored["item"]
        root = item.to_dict()
        root.update({
            "record_type": "root",
            "external_id": item.post_id,
            "root_post_external_id": item.post_id,
            "matched_queries": list(dict.fromkeys(stored["matched_queries"])),
            "exact_object_match_reasons": stored["match_reasons"],
            "content_origin": "unknown_commercial_relationship",
            "counts_as_independent_behavior": False,
            "selected_connector": connector.connector_name,
        })
        roots_payload.append(root)
        try:
            thread = await connector.fetch_thread(
                item,
                max_comments=comment_cap,
                max_depth=max_depth,
            )
            thread_reads.append(_thread_receipt(thread, root_url=item.url))
        except Exception as exc:
            thread_reads.append({
                "root_post_external_id": item.post_id,
                "root_url": item.url,
                "state": "failed",
                "source_status": "error",
                "returned_count": 0,
                "comments": 0,
                "replies": 0,
                "platform_reported_total": item.comments,
                "cap": comment_cap,
                "max_depth": max_depth,
                "truncated": False,
                "attempted_route": connector.connector_name,
                "error_category": type(exc).__name__,
                "limitations": [],
                "records": [],
            })

    query_states = Counter(row["status"] for row in query_receipts)
    thread_states = Counter(row["state"] for row in thread_reads)
    query_complete = all(
        row["status"] in TERMINAL_QUERY_STATES for row in query_receipts
    )
    depth_complete = all(
        row["state"] not in FAILED_THREAD_STATES
        for row in thread_reads
    )
    raw_ids = set(raw_roots)
    exact_ids = set(exact_roots)
    return {
        "platform": platform,
        "connector": connector.connector_name,
        "status": "complete_bounded" if query_complete and depth_complete else "partial_resume_required",
        "queries": query_receipts,
        "query_state_counts": dict(sorted(query_states.items())),
        "root_cap_per_query": root_cap,
        "provider_rows_sum": sum(row["provider_rows"] for row in query_receipts),
        "unique_provider_roots": len(raw_ids),
        "unique_exact_roots": len(exact_ids),
        "unique_semantic_no_match_roots": len(raw_ids - exact_ids),
        "exact_roots": roots_payload,
        "thread_reads": thread_reads,
        "thread_state_counts": dict(sorted(thread_states.items())),
        "comments_returned": sum(row.get("comments", 0) for row in thread_reads),
        "replies_returned": sum(row.get("replies", 0) for row in thread_reads),
        "all_exact_roots_thread_attempted": len(thread_reads) == len(exact_ids),
        "all_queries_terminal": query_complete,
        "all_thread_reads_terminal": depth_complete,
        "source_limit": (
            "Ranked bounded authenticated search. Counts cover only the frozen query "
            "basket and caps; they are not platform-wide totals."
        ),
    }


def _owned_tiktok_paths(
    profile: Path | None,
    extension: Path | None,
) -> tuple[Path | None, Path | None]:
    common_root = _git_common_repository_root(ROOT)
    if common_root is not None:
        if profile is None:
            candidate = common_root / ".browser_profiles" / "tiktok_real"
            profile = candidate if candidate.exists() else None
        if extension is None:
            candidate = common_root / ".browser_profiles" / "tiktok_proxy_ext"
            extension = candidate if candidate.exists() else None
    return profile, extension


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--platform",
        action="append",
        choices=DEFAULT_PLATFORMS,
        help="Targeted recovery platform. Repeat for multiple; default is all three.",
    )
    parser.add_argument("--tiktok-profile", type=Path)
    parser.add_argument("--tiktok-extension", type=Path)
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    contract_path = args.contract.resolve()
    contract = load_contract(contract_path)
    selected_platforms = list(dict.fromkeys(args.platform or DEFAULT_PLATFORMS))
    load_environment(ROOT)
    profile, extension = _owned_tiktok_paths(
        args.tiktok_profile,
        args.tiktok_extension,
    )
    configure_owned_social(
        tiktok_profile=profile,
        tiktok_extension=extension,
    )
    collector = OwnedRadarCollector(trend_candidate_limit=1)
    connectors = {
        platform: _connector_for(
            collector,
            platform,
            contract["platforms"][platform]["connector"],
        )
        for platform in selected_platforms
    }
    observed_at = utc_now()
    output = args.output
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
        output = (
            ROOT
            / "artifacts"
            / "dd"
            / "ghost-kdp"
            / "conversation-runs"
            / f"deepcheck-{stamp}.json"
        )
    elif not output.is_absolute():
        output = ROOT / output

    preflight_rows = await asyncio.gather(*(
        _preflight_platform(
            connectors[platform],
            platform=platform,
            query=contract["platforms"][platform]["canary"],
        )
        for platform in selected_platforms
    ))
    preflight = {row["platform"]: row for row in preflight_rows}
    failed_preflight = [
        platform
        for platform, row in preflight.items()
        if row.get("status") != "healthy"
    ]
    base_payload = {
        "schema_version": "bounty-ghost-social-deepcheck/1",
        "observed_at": observed_at,
        "completed_at": utc_now(),
        "candidate": contract["candidate"],
        "contract": str(contract_path.relative_to(ROOT)).replace("\\", "/"),
        "contract_sha256": sha256(contract_path),
        "collector_script_sha256": sha256(Path(__file__)),
        "selected_platforms": selected_platforms,
        "window": contract["window"],
        "preflight": preflight,
        "old_method_defect": contract["old_method_defect"],
        "recovery_procedure": contract["recovery_procedure"],
    }
    if failed_preflight:
        payload = {
            **base_payload,
            "status": "blocked_source_preflight",
            "failed_preflight_platforms": failed_preflight,
            "platform_results": {},
            "summary": {
                "unique_exact_roots": None,
                "comments_returned": None,
                "replies_returned": None,
            },
        }
        atomic_write_json(output, payload)
        print(json.dumps({
            "output": str(output),
            "sha256": sha256(output),
            "status": payload["status"],
            "failed_preflight_platforms": failed_preflight,
        }, indent=2))
        return 2

    window = contract["window"]
    platform_rows = await asyncio.gather(*(
        _collect_platform(
            connectors[platform],
            platform=platform,
            queries=list(contract["platforms"][platform]["queries"]),
            root_cap=int(window["root_cap_per_query"]),
            comment_cap=int(window["comment_reply_cap_per_root"]),
            max_depth=int(window["max_reply_depth"]),
            time_filter=str(window["time_filter"]),
            sort=str(window["sort"]),
        )
        for platform in selected_platforms
    ))
    platform_results = {row["platform"]: row for row in platform_rows}
    status = (
        "complete_bounded"
        if all(row["status"] == "complete_bounded" for row in platform_rows)
        else "partial_resume_required"
    )
    payload = {
        **base_payload,
        "completed_at": utc_now(),
        "status": status,
        "failed_preflight_platforms": [],
        "platform_results": platform_results,
        "summary": {
            "unique_exact_roots": sum(
                row["unique_exact_roots"] for row in platform_rows
            ),
            "unique_exact_roots_by_platform": {
                row["platform"]: row["unique_exact_roots"] for row in platform_rows
            },
            "comments_returned": sum(
                row["comments_returned"] for row in platform_rows
            ),
            "replies_returned": sum(
                row["replies_returned"] for row in platform_rows
            ),
            "all_queries_terminal": all(
                row["all_queries_terminal"] for row in platform_rows
            ),
            "all_exact_roots_thread_attempted": all(
                row["all_exact_roots_thread_attempted"] for row in platform_rows
            ),
            "all_thread_reads_terminal": all(
                row["all_thread_reads_terminal"] for row in platform_rows
            ),
            "claim_boundary": (
                "Exact counts are observations from the frozen authenticated query "
                "basket within explicit caps, not platform-wide totals."
            ),
        },
    }
    atomic_write_json(output, payload)
    print(json.dumps({
        "output": str(output),
        "sha256": sha256(output),
        "status": status,
        **payload["summary"],
    }, indent=2))
    return 0 if status == "complete_bounded" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
