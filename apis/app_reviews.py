"""
App Reviews API — App Store review intelligence for agents.

Zero-fixed-cost endpoint using Apple's public customer review RSS JSON feed.
Useful for product research, competitor monitoring, subscription app due
diligence, sentiment snapshots, and feature complaint mining.

Important: only returns Apple-provided review records. No sentiment score is
fabricated; lightweight topic flags are deterministic keyword matches.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Path, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/reviews", tags=["App Reviews"])

APPLE_REVIEWS_URL = "https://itunes.apple.com/{country}/rss/customerreviews/id={app_id}/sortby=mostrecent/json"
USER_AGENT = "BountyAPI/2.0 (+https://bountyapi.com)"
TOPIC_KEYWORDS = {
    "pricing": ["price", "expensive", "subscription", "trial", "billing", "refund", "paywall"],
    "bugs": ["bug", "crash", "freeze", "broken", "error", "glitch", "not working"],
    "ux": ["confusing", "design", "interface", "ui", "ux", "hard to use", "annoying"],
    "performance": ["slow", "lag", "loading", "speed", "battery"],
    "features": ["feature", "wish", "missing", "add", "please"],
    "support": ["support", "customer service", "help", "response"],
}


class AppReview(BaseModel):
    id: str
    rating: int
    title: str
    author: Optional[str] = None
    version: Optional[str] = None
    updated_at: Optional[str] = None
    content: str
    url: Optional[str] = None
    source: str = "Apple App Store customer reviews"
    topic_flags: list[str] = Field(default_factory=list)


class AppReviewsResponse(BaseModel):
    country: str
    app_id: str
    app_name: Optional[str]
    app_url: Optional[str]
    generated_at: str
    count: int
    average_rating_sample: Optional[float]
    rating_distribution_sample: dict[str, int]
    topic_counts: dict[str, int]
    reviews: list[AppReview]
    source: dict[str, str]
    notes: list[str]


def _label(obj: dict, key: str, default=None):
    value = obj.get(key, default)
    if isinstance(value, dict):
        return value.get("label", default)
    return value


def _attr(obj: dict, key: str, default=None):
    value = obj.get(key, {})
    if isinstance(value, dict):
        return value.get("attributes", {}).get("label", default)
    return default


def _topic_flags(text: str) -> list[str]:
    lower = text.lower()
    flags = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in lower for keyword in keywords):
            flags.append(topic)
    return flags


@router.get("/app/{country}/{app_id}", response_model=AppReviewsResponse)
async def app_reviews(
    country: str = Path(..., min_length=2, max_length=2, description="Two-letter App Store country code, e.g. us, sg, gb"),
    app_id: str = Path(..., min_length=3, max_length=32, description="Numeric App Store app ID"),
    limit: int = Query(25, ge=1, le=50, description="Maximum recent reviews to return"),
    min_rating: Optional[int] = Query(None, ge=1, le=5, description="Optional minimum star rating filter"),
    max_rating: Optional[int] = Query(None, ge=1, le=5, description="Optional maximum star rating filter"),
):
    """Fetch recent App Store reviews for an app ID and country."""
    country = country.lower()
    url = APPLE_REVIEWS_URL.format(country=country, app_id=app_id)

    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    feed = data.get("feed", {}) if isinstance(data, dict) else {}
    entries = feed.get("entry", [])
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        entries = []

    # Apple's first entry can be app metadata, not a review.
    app_name = None
    app_url = None
    reviews: list[AppReview] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if "im:rating" not in entry:
            app_name = app_name or _label(entry, "im:name") or _label(feed.get("title", {}), "label")
            links = entry.get("link")
            if isinstance(links, dict):
                app_url = links.get("attributes", {}).get("href")
            continue

        try:
            rating = int(_label(entry, "im:rating"))
        except Exception:
            continue
        if min_rating is not None and rating < min_rating:
            continue
        if max_rating is not None and rating > max_rating:
            continue

        title = str(_label(entry, "title", "")).strip()
        content = str(_label(entry, "content", "")).strip()
        links = entry.get("link")
        review_url = None
        if isinstance(links, dict):
            review_url = links.get("attributes", {}).get("href")

        flags = _topic_flags(" ".join([title, content]))
        reviews.append(AppReview(
            id=str(_label(entry, "id", "")),
            rating=rating,
            title=title,
            author=_label(entry.get("author", {}), "name") if isinstance(entry.get("author"), dict) else None,
            version=_attr(entry, "im:version"),
            updated_at=_label(entry, "updated"),
            content=content,
            url=review_url,
            topic_flags=flags,
        ))
        if len(reviews) >= limit:
            break

    ratings = [r.rating for r in reviews]
    distribution = Counter(str(r.rating) for r in reviews)
    topic_counter: Counter[str] = Counter()
    for review in reviews:
        topic_counter.update(review.topic_flags)

    return AppReviewsResponse(
        country=country,
        app_id=app_id,
        app_name=app_name,
        app_url=app_url,
        generated_at=datetime.now(timezone.utc).isoformat(),
        count=len(reviews),
        average_rating_sample=round(sum(ratings) / len(ratings), 2) if ratings else None,
        rating_distribution_sample=dict(sorted(distribution.items())),
        topic_counts=dict(sorted(topic_counter.items())),
        reviews=reviews,
        source={"name": "Apple App Store customer reviews RSS", "url": url},
        notes=[
            "Sample reflects Apple's recent RSS feed for the requested country/app ID, not lifetime App Store ratings.",
            "Topic flags are deterministic keyword matches, not model-generated sentiment.",
            "No missing ratings, versions, authors, or content are filled in.",
        ],
    )
