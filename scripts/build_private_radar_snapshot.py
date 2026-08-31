"""Build the sanitized phone-review snapshot from persisted private Radar evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apis.news_search import GOOGLE_NEWS_RSS, parse_google_news_rss
from social_scraper.investing.google_discovery import build_trend_context
from social_scraper.investing.private_radar import (
    PrivateRadarStore,
    build_opportunity_queue_items,
    candidate_review_status,
    is_supported_qualified,
    review_decision_with_current_methodology,
)
from social_scraper.investing.trajectory import (
    collect_movement_bundles,
    derive_trajectory_query,
    select_default_movement_query,
    trajectory_is_usable,
)


DEFAULT_DB = ROOT / "data" / "private_radar.db"
DEFAULT_OUTPUT = ROOT / "public" / "private-radar-snapshot.json"


async def _refresh_trend_context(candidates: list[dict[str, Any]]) -> None:
    """Refresh concise Google News context for persisted discovery candidates."""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        async def fetch(candidate: dict[str, Any]) -> None:
            keyword = str(candidate.get("keyword") or "").strip()
            articles = []
            error_category = None
            if keyword:
                try:
                    response = await client.get(
                        GOOGLE_NEWS_RSS.format(query=quote(f'"{keyword}"')),
                        headers={
                            "User-Agent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36"
                            )
                        },
                    )
                    response.raise_for_status()
                    articles = [{
                        "title": article.title,
                        "url": article.link,
                        "source": article.source,
                        "published_at": article.published,
                        "snippet": None,
                    } for article in parse_google_news_rss(response.text)[:3]]
                except Exception as exc:
                    error_category = type(exc).__name__
            candidate["context_articles"] = articles
            candidate["context_error_category"] = error_category
            candidate["context"] = build_trend_context(candidate)

        await asyncio.gather(*(fetch(candidate) for candidate in candidates))
    await _refine_trend_context_with_model(candidates)


async def _refine_trend_context_with_model(
    candidates: list[dict[str, Any]],
) -> None:
    """Use one bounded batch call to identify unfamiliar terms from supplied sources."""
    if not candidates:
        return
    try:
        from social_scraper.llm_client import call_llm

        payload = {"candidates": [{
            "keyword": candidate.get("keyword"),
            "categories": candidate.get("categories") or [],
            "related_queries": candidate.get("keyword_basket") or [],
            "articles": [{
                "title": article.get("title"),
                "source": article.get("source"),
                "url": article.get("url"),
            } for article in (candidate.get("context_articles") or [])[:3]],
        } for candidate in candidates]}
        system = (
            "Explain Google Trends terms for an international investor using ONLY the "
            "supplied categories, related queries, and article headlines. Return JSON only: "
            '{"contexts":[{"keyword":str,"what_it_is":str,"why_rising":str,'
            '"investing_angle":str,"evidence_urls":[str]}]}. Keep each prose field to '
            "35 words or fewer. Identify or translate unfamiliar names and foreign terms. "
            "Describe only catalysts supported by supplied headlines; otherwise say the "
            "catalyst is unclear. Never invent brand relationships or listed beneficiaries. "
            "evidence_urls must be a subset of supplied article URLs."
        )
        raw = await call_llm(
            system,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            max_tokens=1800,
            temperature=0.0,
        )
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
        parsed = json.loads(text.strip())
    except Exception:
        for candidate in candidates:
            candidate["context_mode"] = "deterministic_source_context"
        return

    by_keyword = {
        str(value.get("keyword") or "").casefold(): value
        for value in parsed.get("contexts") or []
        if isinstance(value, dict)
    }
    for candidate in candidates:
        value = by_keyword.get(str(candidate.get("keyword") or "").casefold())
        allowed_urls = {
            str(article.get("url"))
            for article in candidate.get("context_articles") or []
            if str(article.get("url") or "").startswith(("http://", "https://"))
        }
        evidence_urls = [
            str(url) for url in (value or {}).get("evidence_urls") or []
            if str(url) in allowed_urls
        ]
        fields = {
            name: " ".join(str((value or {}).get(name) or "").split())[:500]
            for name in ("what_it_is", "why_rising", "investing_angle")
        }
        if value and all(fields.values()) and (evidence_urls or not allowed_urls):
            candidate["context"] = {**fields, "source_urls": evidence_urls}
            candidate["context_mode"] = "source_grounded_batch_model"
        else:
            candidate["context_mode"] = "deterministic_source_context"


def _movement_bundle_reusable(bundle: Any) -> bool:
    if not isinstance(bundle, dict) or not bundle.get("query_options"):
        return False
    return not any(
        series.get("status") == "failed"
        for option in bundle.get("query_options") or []
        for horizons_by_geo in (option.get("series") or {}).values()
        for series in horizons_by_geo.values()
        if isinstance(series, dict)
    )


def _existing_movement_bundles(run_id: str) -> tuple[dict[str, dict], dict[str, dict]]:
    if not DEFAULT_OUTPUT.exists():
        return {}, {}
    try:
        payload = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    except Exception:
        return {}, {}
    if (payload.get("methodology_recheck") or {}).get("source_run_id") != run_id:
        return {}, {}
    trend = {
        str(item.get("keyword") or ""): dict(item.get("movement_bundle") or {})
        for item in (payload.get("trend_discovery") or {}).get("candidates") or []
        if item.get("keyword") and _movement_bundle_reusable(item.get("movement_bundle"))
    }
    decisions = {
        str(item.get("candidate_id") or ""): dict(item.get("movement_bundle") or {})
        for item in (payload.get("items") or []) + (payload.get("review_items") or [])
        if item.get("candidate_id") and _movement_bundle_reusable(item.get("movement_bundle"))
    }
    return trend, decisions


def _snapshot_evidence(item: dict[str, Any]) -> dict[str, Any]:
    platform = str(item.get("platform") or "unknown")
    source_url = str(item.get("url") or "")
    external_id = str(item.get("external_id") or "")
    display_url = source_url
    if platform == "x" and external_id.isdigit():
        display_url = (
            "https://platform.twitter.com/embed/Tweet.html?dnt=true&id="
            f"{external_id}"
        )
    return {
        "id": str(item.get("id") or ""),
        "platform": platform,
        "url": display_url,
        "source_url": source_url,
        "author": item.get("author"),
        "text": str(item.get("text") or "")[:500],
        "created_at": item.get("created_at"),
        "engagement": (
            dict(item.get("engagement"))
            if isinstance(item.get("engagement"), dict)
            else {}
        ),
    }


def _recheck_decisions(
    store: PrivateRadarStore,
    scan: dict[str, Any],
    *,
    trajectory_provider=None,
    movement_bundles: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence = store.evidence_for_run(scan["id"])
    evidence_by_id = {str(item["id"]): item for item in evidence}
    panel_by_id = {
        evidence_id: str(item.get("panel_id") or "")
        for evidence_id, item in evidence_by_id.items()
    }
    qualified = []
    reviewed = []
    for index, saved in enumerate(scan.get("decisions") or []):
        candidate_saved = dict(saved)
        if movement_bundles and index < len(movement_bundles):
            bundle = movement_bundles[index]
            if isinstance(bundle, dict):
                candidate_saved["movement_bundle"] = bundle
                default_geo = str(bundle.get("default_geo") or "WORLDWIDE")
                default_horizon = str(bundle.get("default_horizon") or "3m")
                candidate_saved["trajectory"] = dict(
                    (((bundle.get("series") or {}).get(default_geo) or {}).get(
                        default_horizon
                    ) or {})
                )
        if not isinstance(candidate_saved.get("trajectory"), dict):
            trajectory_query = str(
                candidate_saved.get("trajectory_query")
                or derive_trajectory_query(candidate_saved)
            ).strip()
            candidate_saved["trajectory"] = (
                trajectory_provider(trajectory_query)
                if trajectory_provider is not None
                else {
                    "query": trajectory_query,
                    "source": "Google Trends",
                    "status": "failed",
                    "normalized": True,
                    "points": [],
                    "error_category": "not_persisted",
                }
            )
        decision, linked_records = review_decision_with_current_methodology(
            candidate_saved, evidence_by_id
        )
        if (
            not decision
            or not linked_records
            or not trajectory_is_usable(decision.get("trajectory"))
        ):
            continue
        linked = [_snapshot_evidence(item) for item in linked_records]
        if is_supported_qualified(decision, set(evidence_by_id), panel_by_id):
            qualified.append({**decision, "evidence": linked})
            continue
        if not linked:
            continue
        review_status, blocking_reasons, caveats = candidate_review_status(decision)
        reviewed.append({
            **decision,
            "review_status": review_status,
            "blocking_reasons": blocking_reasons,
            "caveats": caveats,
            "evidence": linked,
        })
    reviewed.sort(key=lambda item: (
        {"search_movement_only": 0, "needs_more_evidence": 1, "rejected": 2}.get(
            str(item.get("review_status")), 3
        ),
        str(item.get("label") or ""),
    ))
    return qualified, reviewed


def _recheck_opportunities(
    store: PrivateRadarStore,
    scan: dict[str, Any],
) -> list[dict[str, Any]]:
    """Rebuild the opportunity lane from immutable evidence under current rules."""
    evidence = store.evidence_for_run(scan["id"])
    evidence_by_id = {str(item["id"]): item for item in evidence}
    rechecked = []
    decision_by_key = {}
    for saved in scan.get("decisions") or []:
        decision, linked = review_decision_with_current_methodology(
            dict(saved), evidence_by_id
        )
        if decision and linked:
            if not isinstance(decision.get("trajectory"), dict):
                trajectory_query = str(
                    decision.get("trajectory_query")
                    or derive_trajectory_query(decision)
                ).strip()
                decision["trajectory"] = {
                    "query": trajectory_query,
                    "source": "Google Trends",
                    "status": "failed",
                    "normalized": True,
                    "points": [],
                    "error_category": "not_persisted",
                }
            if not isinstance(decision.get("movement_bundle"), dict):
                decision["movement_bundle"] = {
                    "source": "Google Trends",
                    "status": "not_persisted",
                    "query_options": [],
                    "series": {},
                }
            rechecked.append(decision)
            decision_by_key[str(decision.get("candidate_id") or "")] = decision
    opportunities = build_opportunity_queue_items(rechecked, evidence_by_id)
    values = []
    for item in opportunities:
        evidence_ids = [str(value) for value in item.get("evidence_ids") or []]
        linked = [
            _snapshot_evidence(evidence_by_id[evidence_id])
            for evidence_id in evidence_ids
            if evidence_id in evidence_by_id
        ]
        if len(linked) != len(evidence_ids) or not linked:
            continue
        source_decision = decision_by_key.get(
            str(item.get("opportunity_key") or ""),
            {},
        )
        values.append({
            key: value for key, value in item.items()
            if key not in {"payload"}
        } | {
            "evidence": linked,
            "trajectory": dict(source_decision.get("trajectory") or {}),
            "movement_bundle": dict(source_decision.get("movement_bundle") or {}),
        })
    return values


def build_snapshot(
    db_path: Path,
    *,
    movement_provider=None,
    refresh_external: bool = False,
) -> dict[str, Any]:
    store = PrivateRadarStore(db_path)
    scan = store.latest_attempt()
    if not scan or scan.get("status") not in {"complete", "no_qualified_leads"}:
        raise RuntimeError("a successful terminal private Radar scan is required")
    payload = store.public_payload()
    trend_discovery = payload.get("trend_discovery") or {}
    trend_candidates = [
        dict(value) for value in trend_discovery.get("candidates") or []
        if isinstance(value, dict)
    ]
    for candidate in trend_candidates:
        if not isinstance(candidate.get("context"), dict):
            candidate["context"] = build_trend_context(candidate)
            candidate["context_mode"] = "deterministic_persisted_context"
    saved_decisions = [
        dict(value) for value in scan.get("decisions") or []
        if isinstance(value, dict)
    ]
    movement_inputs = [
        {
            **candidate,
            "trajectory_query": str(candidate.get("keyword") or "").strip(),
        }
        for candidate in trend_candidates
    ] + saved_decisions
    movement_bundles: list[dict[str, Any] | None] = []
    for value in movement_inputs:
        bundle = value.get("movement_bundle")
        movement_bundles.append(
            select_default_movement_query(dict(bundle))
            if isinstance(bundle, dict) and bundle.get("query_options")
            else None
        )
    missing_indices = [
        index for index, bundle in enumerate(movement_bundles) if bundle is None
    ]
    if refresh_external and missing_indices:
        provider = movement_provider or collect_movement_bundles
        fresh = list(provider([movement_inputs[index] for index in missing_indices]))
        if len(fresh) != len(missing_indices):
            raise RuntimeError("Google movement backfill returned an incomplete bundle set")
        for index, bundle in zip(missing_indices, fresh):
            movement_bundles[index] = select_default_movement_query(dict(bundle))
    resolved_bundles = [
        dict(bundle) if isinstance(bundle, dict) else {}
        for bundle in movement_bundles
    ]
    trend_count = len(trend_candidates)
    for candidate, bundle in zip(
        trend_candidates, resolved_bundles[:trend_count]
    ):
        if bundle:
            candidate["movement_bundle"] = bundle
    if payload.get("trend_discovery") is not None:
        payload["trend_discovery"]["candidates"] = trend_candidates
    qualified, reviewed = _recheck_decisions(
        store,
        scan,
        movement_bundles=resolved_bundles[trend_count:],
    )
    opportunities = _recheck_opportunities(store, scan)
    if not qualified and not reviewed and not opportunities:
        raise RuntimeError(
            "the latest scan has no cited subjects or opportunity investigations"
        )
    payload["items"] = qualified
    payload["review_items"] = reviewed
    payload["opportunity_queue"] = opportunities
    payload["review_scan"] = store._public_scan(scan)
    payload["coverage"]["summary"] = (
        f"{len(qualified)} trade-ready leads, {len(opportunities)} active investigations, "
        f"and {len(reviewed)} reviewed subjects "
        f"from {scan['evidence_count']} stored evidence records; "
        + payload["coverage"]["summary"].split(";", 1)[-1].strip()
    )
    payload["coverage"]["sources"] = []
    payload["snapshot_observed_at"] = scan.get("completed_at") or scan.get("started_at")
    payload["snapshot_mode"] = "read_only"
    payload["methodology_recheck"] = {
        "performed": True,
        "source_run_id": scan["id"],
        "new_social_collection": False,
        "google_context_refreshed": False,
        "google_movement_refreshed": bool(refresh_external),
        "google_movement_reused_from_scan": True,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.getenv("BOUNTY_PRIVATE_RADAR_DB") or DEFAULT_DB),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_snapshot(args.db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "qualified": len(payload["items"]),
        "reviewed": len(payload["review_items"]),
        "statuses": [item["review_status"] for item in payload["review_items"]],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
