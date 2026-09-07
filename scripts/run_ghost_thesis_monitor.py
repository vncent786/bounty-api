"""Durable daily public-information-parity runner for the GHOST/KDP monitor."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from social_scraper.investing.standing_monitor import (
    build_public_parity_observation,
    load_sensor_framework,
)
from social_scraper.source_connectors import (
    AuthMode,
    CapabilityCatalogue,
    ConnectorOperation,
    CostProfile,
    CredentialResolver,
    JsonReceiptStore,
    NormalizedEvidence,
    PreflightProbe,
    PublicParityConnector,
    RateLimitPolicy,
    RawConnectorResult,
    RawState,
    SourceCapability,
    SourceExecutionRequest,
    SourceExecutor,
    TimeRangeCapability,
)
from social_scraper.source_connectors.official_http import OfficialFetchError, classify_http_status, fetch_public


LANES = (
    "official_ir",
    "regulator_filings",
    "earnings_calls",
    "official_product_context",
    "qualifying_business_news",
    "sell_side_public_mentions",
)
ECONOMIC_TERMS = (
    "sales", "revenue", "demand", "volume", "distribution", "replenish",
    "restock", "sell-through", "repeat purchase", "margin", "guidance",
    "earnings", "market share", "price target", "estimate", "upgrade", "downgrade",
)
EXCLUDED_OUTLET_HINTS = (
    "drinkghost", "walmart", "amazon", "sporked", "tiktok", "instagram", "youtube",
)
_SCRIPT_BLOCK_RE = re.compile(r"<(script|style|noscript|template)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _plain_text(value: str) -> str:
    """Extract visible prose without persisting script/style payloads or page tokens."""

    source = value or ""
    try:
        if "<" not in source or ">" not in source:
            return re.sub(r"\s+", " ", html.unescape(source)).strip()
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(source, "html.parser")
        for node in soup(("script", "style", "noscript", "template")):
            node.decompose()
        source = soup.get_text(" ")
    except Exception:
        source = _SCRIPT_BLOCK_RE.sub(" ", source)
        source = _TAG_RE.sub(" ", source)
    return re.sub(r"\s+", " ", html.unescape(source)).strip()


def _identity_match(text: str) -> bool:
    folded = text.casefold().replace("&amp;", "&")
    has_ghost = "ghost" in folded
    has_aw = "a&w" in folded or "a & w" in folded or "aw root beer" in folded
    return has_ghost and has_aw


def _exact_implication_match(text: str) -> bool:
    """Require product identity and economics in the same sentence-sized passage."""

    folded = re.sub(r"\s+", " ", text.casefold().replace("&amp;", "&"))
    passages = re.split(r"(?<=[.!?])\s+|\s*[;|]\s*", folded)
    for passage in passages:
        if (
            _identity_match(passage)
            and any(term in passage for term in ECONOMIC_TERMS)
        ):
            return True
    return False


def _outlet_from_title(title: str) -> str | None:
    if " - " not in title:
        return None
    return title.rsplit(" - ", 1)[-1].strip() or None


def _pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
        from io import BytesIO
        return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
    except Exception:
        return ""


@dataclass
class PublicParityLaneConnector:
    lane: str
    lane_config: Mapping[str, Any]

    def __post_init__(self) -> None:
        sources = list(self.lane_config.get("sources") or ())
        queries = list(self.lane_config.get("rss_queries") or ())
        if not sources and not queries:
            raise ValueError(f"{self.lane} needs sources or rss_queries")
        canary_url = str((sources[0] if sources else {}).get("url") or "")
        canary_query = str(queries[0] if queries else self.lane)
        self.capability = SourceCapability(
            source_id=f"public_parity_{self.lane}",
            display_name=self.lane.replace("_", " ").title(),
            data_kinds=frozenset({"issuer.public_information_parity"}),
            geographies=("*",),
            time_range=TimeRangeCapability(max_lookback_days=None, notes="Public web history varies by route."),
            auth_mode=AuthMode.NONE,
            credential_ref=None,
            cost=CostProfile(kind="free", amount_per_request="0"),
            rate_limit=RateLimitPolicy(requests=None, window_seconds=None, concurrency=1, notes="Low-rate daily monitor."),
            limitations=("Public web only; paywalled and private research is outside scope.",),
            preflight=PreflightProbe(
                query=canary_query,
                limit=1,
                options={"canary_url": canary_url, "lane": self.lane},
            ),
            operations=frozenset(ConnectorOperation),
        )

    async def preflight(self, credentials: Any) -> RawConnectorResult:
        sources = list(self.lane_config.get("sources") or ())
        if sources:
            return await self._fetch_sources(sources[:1], canary=True)
        queries = list(self.lane_config.get("rss_queries") or ())
        return await self._fetch_rss(queries[:1], canary=True)

    async def search(self, request: SourceExecutionRequest, credentials: Any) -> RawConnectorResult:
        sources = list(request.options.get("sources") or ())
        queries = list(request.options.get("rss_queries") or ())
        if sources:
            return await self._fetch_sources(sources)
        return await self._fetch_rss(queries)

    collect = search

    async def _fetch_sources(self, sources: list[Mapping[str, Any]], *, canary: bool = False) -> RawConnectorResult:
        records = []
        for item in sources:
            try:
                response = await fetch_public(str(item.get("url") or ""))
            except OfficialFetchError as exc:
                return RawConnectorResult(records=(), state=RawState.ERROR, error_category=exc.category)
            classified = classify_http_status(response.status_code)
            if classified:
                state_value, error = classified
                return RawConnectorResult(records=(), state=RawState(state_value), error_category=error)
            content_type = response.content_type
            text = _pdf_text(response.content) if "pdf" in content_type else response.text
            records.append({
                "external_id": str(item.get("url")),
                "url": response.url,
                "title": item.get("title"),
                "published_at": item.get("event_date"),
                "text": _plain_text(text)[:250000],
                "outlet": "Keurig Dr Pepper" if self.lane != "official_product_context" else "GHOST",
                "event_date": item.get("event_date"),
            })
        return RawConnectorResult(
            records=tuple(records),
            state=RawState.COMPLETE if records else RawState.EMPTY,
            source_observed_at=datetime.now(timezone.utc).isoformat(),
            metadata={"canary": canary},
        )

    async def _fetch_rss(self, queries: list[str], *, canary: bool = False) -> RawConnectorResult:
        records: dict[str, dict[str, Any]] = {}
        for query in queries:
            url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
            try:
                response = await fetch_public(url)
            except OfficialFetchError as exc:
                return RawConnectorResult(records=(), state=RawState.ERROR, error_category=exc.category)
            classified = classify_http_status(response.status_code)
            if classified:
                state_value, error = classified
                return RawConnectorResult(records=(), state=RawState(state_value), error_category=error)
            try:
                root = ET.fromstring(response.content)
            except ET.ParseError:
                return RawConnectorResult(records=(), state=RawState.PARSER_ERROR, error_category="rss_parse_error")
            for item in root.findall(".//item")[:20]:
                title = _plain_text(item.findtext("title") or "")
                link = _plain_text(item.findtext("link") or "")
                description = _plain_text(item.findtext("description") or "")
                key = link or title
                if not key:
                    continue
                records[key] = {
                    "external_id": key,
                    "url": link or None,
                    "title": title,
                    "published_at": _plain_text(item.findtext("pubDate") or "") or None,
                    "text": f"{title}. {description}".strip(),
                    "outlet": _outlet_from_title(title),
                    "query": query,
                }
        values = tuple(records.values())
        return RawConnectorResult(
            records=values,
            state=RawState.COMPLETE if values else RawState.EMPTY,
            source_observed_at=datetime.now(timezone.utc).isoformat(),
            metadata={"canary": canary},
        )

    def normalize(self, record: Any, request: SourceExecutionRequest) -> NormalizedEvidence:
        value = dict(record)
        text = f"{value.get('title') or ''} {value.get('text') or ''}".strip()
        exact_match = _exact_implication_match(text)
        outlet = str(value.get("outlet") or "").strip() or None
        qualifying = (
            self.lane in {"qualifying_business_news", "sell_side_public_mentions"}
            and exact_match
            and not any(hint in str(outlet or "").casefold() for hint in EXCLUDED_OUTLET_HINTS)
        )
        management = self.lane in {"official_ir", "regulator_filings", "earnings_calls"} and exact_match
        return NormalizedEvidence(
            source_id=self.capability.source_id,
            external_id=str(value.get("external_id")),
            evidence_type="issuer.public_information_parity",
            observed_at=datetime.now(timezone.utc).isoformat(),
            url=value.get("url"),
            title=value.get("title"),
            text=text[:4000] or None,
            published_at=value.get("published_at"),
            geography="US",
            attributes={
                "lane": self.lane,
                "outlet": outlet,
                "event_date": value.get("event_date"),
                "query": value.get("query"),
                "exact_implication_match": exact_match,
                "direct_article_verified": self.lane not in {"qualifying_business_news", "sell_side_public_mentions"},
                "qualifying": qualifying,
                "management_attribution": management,
                "passage": text[:500] or None,
            },
        )


def load_monitor_config(root: str | Path) -> dict[str, Any]:
    path = Path(root) / "references" / "ghost-standing-monitor-v1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "bounty-standing-monitor/1":
        raise ValueError("unsupported GHOST standing-monitor config")
    return value


def build_execution_requests(config: Mapping[str, Any], *, observed_at: str) -> dict[str, SourceExecutionRequest]:
    parity = config.get("public_information_parity") or {}
    day = observed_at[:10]
    requests = {}
    for lane in LANES:
        lane_config = parity.get(lane) or {}
        options = {
            "lane": lane,
            "identity_terms": list(config.get("identity_terms") or ("ghost", "a&w root beer")),
            "economic_terms": list(config.get("economic_terms") or ECONOMIC_TERMS),
        }
        if lane == "regulator_filings":
            sources = list(lane_config.get("sources") or ())
            options["submissions_url"] = lane_config.get("submissions_url") or (
                sources[0].get("url") if sources else None
            )
            options["sec_cik"] = str(lane_config.get("sec_cik") or "")
            options["recent_forms"] = list(
                lane_config.get("recent_forms") or ("8-K", "10-K", "10-Q", "DEF 14A")
            )
            options["window_days"] = int(config.get("window_days") or 14)
        elif lane_config.get("sources"):
            options["sources"] = list(lane_config["sources"])
        if lane_config.get("rss_queries"):
            options["rss_queries"] = list(lane_config["rss_queries"])
        requests[lane] = SourceExecutionRequest(
            query=str(config.get("exact_implication") or ""),
            geography="US",
            time_filter=f"latest_{int(config.get('window_days') or 14)}_calendar_days",
            sort="latest",
            limit=100,
            options=options,
            resume_key=f"{config.get('monitor_id')}:{day}:{lane}",
        )
    return requests


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _atomic_append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_bytes() if path.exists() else b""
    line = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8") + b"\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(existing + line)
    os.replace(temporary, path)


async def execute(root: Path, observed_at: str) -> dict[str, Any]:
    config = load_monitor_config(root)
    framework = load_sensor_framework(root)
    run_id = observed_at.replace(":", "").replace("+", "_")
    run_dir = root / "artifacts" / "dd" / "ghost-kdp" / "parity-runs" / run_id
    store = JsonReceiptStore(run_dir / "source-receipts")
    canary_url = str(
        config.get("canary_url")
        or config["public_information_parity"]["official_ir"]["sources"][0]["url"]
    )
    source_id = "public_information_parity"
    catalogue = CapabilityCatalogue([PublicParityConnector(canary_url=canary_url)])
    executor = SourceExecutor(
        catalogue,
        CredentialResolver.with_bounty_defaults(base_path=root),
        store,
        max_attempts=2,
        retry_delay_seconds=1,
    )
    requests = build_execution_requests(config, observed_at=observed_at)

    receipts = {}
    for lane in LANES:
        if lane in {"qualifying_business_news", "sell_side_public_mentions"}:
            result = await executor.search(source_id, requests[lane], resume=False)
        else:
            result = await executor.collect(source_id, requests[lane], resume=False)
        receipts[lane] = result.receipt.to_dict()
    observation = build_public_parity_observation(
        framework, config, receipts, observed_at=observed_at
    )
    observation["writer"] = {
        "writer_id": "scripts/run_ghost_thesis_monitor.py",
        "writer_version": 1,
        "owns_latest_pointer": "artifacts/dd/ghost-kdp/coverage_latest.json",
    }
    observation["run_id"] = run_id
    observation["receipt_paths"] = {
        lane: str((run_dir / "source-receipts" / f"{receipt['operation_id']}.json").relative_to(root).as_posix())
        for lane, receipt in receipts.items()
    }
    observation["official_source_receipts"] = [
        event for lane in ("official_ir", "earnings_calls", "official_product_context")
        for event in observation["lanes"][lane]["events"]
    ]
    observation["sec_source_receipts"] = observation["lanes"]["regulator_filings"]["events"]
    observation["media_search_receipts"] = (
        observation["lanes"]["qualifying_business_news"]["events"]
        + observation["lanes"]["sell_side_public_mentions"]["events"]
    )
    _atomic_json(run_dir / "parity.json", observation)
    ghost = root / "artifacts" / "dd" / "ghost-kdp"
    _atomic_json(ghost / "coverage_latest.json", observation)
    _atomic_append_jsonl(ghost / "coverage_history.jsonl", observation)
    return observation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--observed-at", default=None)
    args = parser.parse_args()
    root = Path(args.repo_root).resolve()
    lock = root / "artifacts" / "dd" / "ghost-kdp" / "ghost_thesis_monitor.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print(json.dumps({"status": "blocked", "reason": "monitor_already_running"}))
        return 2
    observed_at = args.observed_at or datetime.now(timezone.utc).isoformat()
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        result = asyncio.run(execute(root, observed_at))
        print(json.dumps({
            "status": "complete" if result["parity_state"] != "SOURCE_FAILURE" else "source_failure",
            "observed_at": result["observed_at"],
            "parity_state": result["parity_state"],
            "qualifying_public_items": result["qualifying_independent_business_financial_outlet_count"],
            "management_attribution": result["management_acknowledges_a_and_w_economics"],
            "public_sell_side_mentions_discovered": result["sell_side_public_mentions"]["checked_count"],
            "public_sell_side_mentions_directly_read": result["sell_side_public_mentions"]["retrieved_count"],
            "paywalled_research_checked": False,
            "automatic_trade_action": False,
        }, indent=2))
        return 0 if result["parity_state"] != "SOURCE_FAILURE" else 1
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
