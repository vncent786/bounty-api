"""Evidence-preserving public-information parity classification."""

from __future__ import annotations

from urllib.parse import urlparse


_ALLOWED_TIERS = {"niche", "mainstream", "company"}
_ALLOWED_HEALTHY = {"complete", "success", "empty"}


def _safe_headline(item: dict) -> dict | None:
    url = str(item.get("url") or "")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    tier = str(item.get("source_tier") or "").lower()
    return {
        "title": str(item.get("title") or "")[:500],
        "url": url,
        "published_at": item.get("published_at"),
        "source": item.get("source"),
        "source_tier": tier if tier in _ALLOWED_TIERS else "unknown",
    }


def assess_market_awareness(
    *,
    earliest_observed_at: str | None,
    retrieved_at: str,
    headlines: list[dict],
    price_context: list[dict] | None = None,
    source_health: list[dict] | None = None,
    company_acknowledged: bool = False,
) -> dict:
    """Classify awareness from supplied evidence; never fetch or infer missing data."""
    source_health = source_health or []
    clean_headlines = [
        value for value in (_safe_headline(item) for item in headlines)
        if value is not None
    ]
    statuses = {str(item.get("status") or "").lower() for item in source_health}
    healthy = bool(statuses & _ALLOWED_HEALTHY)
    tiers = {item["source_tier"] for item in clean_headlines}
    if company_acknowledged or "company" in tiers:
        classification = "company_acknowledged"
    elif "mainstream" in tiers:
        classification = "mainstream_coverage"
    elif "niche" in tiers:
        classification = "niche_coverage"
    elif not clean_headlines and healthy:
        classification = "social_only"
    else:
        classification = "unknown"

    clean_prices = []
    for item in price_context or []:
        clean_prices.append({
            "ticker": item.get("ticker"),
            "window": item.get("window"),
            "return_pct": item.get("return_pct"),
            "as_of": item.get("as_of"),
            "source_url": item.get("source_url"),
            "interpretation": "context_not_causation",
        })
    limitations = []
    if not healthy:
        limitations.append("News-source coverage was not confirmed complete.")
    if clean_prices:
        limitations.append("Price movement is context and does not prove causality or absorption.")
    return {
        "classification": classification,
        "earliest_observed_at": earliest_observed_at,
        "retrieved_at": retrieved_at,
        "headlines": clean_headlines,
        "price_context": clean_prices,
        "source_health": source_health,
        "limitations": limitations,
    }
