"""
News Search API — Aggregate news from free RSS sources.

Zero upstream cost: uses Google News RSS (no API key, no auth).
Replaces NewsAPI ($449/mo minimum for commercial use).

Agents pay per-call to get clean structured news results without
managing RSS feeds, parsing XML, or dealing with rate limits.
"""

import re
import asyncio
from datetime import datetime, timezone
from urllib.parse import quote, urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional
import xml.etree.ElementTree as ET

router = APIRouter(prefix="/news", tags=["News Search"])

# ============================================================
# Models
# ============================================================

class NewsArticle(BaseModel):
    title: str
    link: str
    source: str = Field(..., description="Publication name")
    published: Optional[str] = Field(None, description="ISO timestamp or raw date string")
    description: Optional[str] = None
    image: Optional[str] = None


class NewsSearchResult(BaseModel):
    query: str
    fetched_at: str
    total_results: int
    articles: list[NewsArticle]


# ============================================================
# Helpers
# ============================================================

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

# Additional high-quality RSS feeds for broader coverage
EXTRA_FEEDS = {
    "tech": [
        "https://hnrss.org/frontpage",  # Hacker News
    ],
}


def clean_html(text: str) -> str:
    """Strip HTML tags from RSS descriptions."""
    if not text:
        return text
    soup = BeautifulSoup(text, "lxml")
    # Remove script/style
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    return soup.get_text(strip=True)


def extract_source_from_google_link(url: str) -> tuple[str, str]:
    """Google News wraps article URLs. Extract the real URL and source name."""
    # Google News links look like:
    # https://news.google.com/rss/articles/CBM... - the source is in the feed <source> tag
    # But the URL itself may contain the source domain
    parsed = urlparse(url)
    if "news.google.com" in parsed.netloc:
        # Try to extract source from query params
        params = parse_qs(parsed.query)
        if "url" in params:
            real_url = params["url"][0]
            domain = urlparse(real_url).netloc.removeprefix("www.")
            return real_url, domain
        return url, "Google News"
    domain = parsed.netloc.removeprefix("www.")
    return url, domain


def parse_google_news_rss(xml_text: str) -> list[NewsArticle]:
    """Parse Google News RSS feed into structured articles."""
    articles = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return articles

    # RSS feeds: rss/channel/item
    channel = root.find("channel")
    if channel is None:
        return articles

    for item in channel.findall("item")[:30]:  # Cap at 30
        title_elem = item.find("title")
        link_elem = item.find("link")
        pub_date_elem = item.find("pubDate")
        desc_elem = item.find("description")
        source_elem = item.find("source")

        title = title_elem.text if title_elem is not None and title_elem.text else ""
        link = link_elem.text if link_elem is not None and link_elem.text else ""

        # Google News titles often end with " - Source Name"
        source_name = ""
        if source_elem is not None and source_elem.text:
            source_name = source_elem.text
        elif " - " in title:
            parts = title.rsplit(" - ", 1)
            title = parts[0]
            source_name = parts[1]

        published = pub_date_elem.text if pub_date_elem is not None else None

        description = None
        image = None
        if desc_elem is not None and desc_elem.text:
            desc_html = desc_elem.text
            # Extract image from description HTML
            soup = BeautifulSoup(desc_html, "lxml")
            img_tag = soup.find("img")
            if img_tag and img_tag.get("src"):
                image = img_tag["src"]
            description = clean_html(desc_html)

        # Clean up the link (Google News redirect)
        real_link, domain = extract_source_from_google_link(link)
        if not source_name:
            source_name = domain

        articles.append(NewsArticle(
            title=title.strip(),
            link=real_link,
            source=source_name.strip(),
            published=published,
            description=description[:300] if description else None,
            image=image,
        ))

    return articles


async def fetch_google_news(client: httpx.AsyncClient, query: str) -> list[NewsArticle]:
    """Fetch and parse Google News RSS for a query."""
    url = GOOGLE_NEWS_RSS.format(query=quote(query))
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    try:
        resp = await client.get(url, headers=headers, timeout=10.0, follow_redirects=True)
        resp.raise_for_status()
        return parse_google_news_rss(resp.text)
    except Exception as e:
        print(f"[news] Google News RSS error: {e}")
        return []


# ============================================================
# Main endpoint
# ============================================================

@router.get("/search", response_model=NewsSearchResult)
async def search_news(
    q: str = Query(..., description="Search query", min_length=2, max_length=200),
    limit: int = Query(20, description="Max results", ge=1, le=50),
):
    """
    Search news articles by keyword or topic.

    Aggregates from Google News and other free RSS sources.
    Returns structured JSON with title, source, URL, publish date, summary, and image.

    No API key needed, no subscription. Pay per call.
    """
    start = datetime.now(timezone.utc)

    async with httpx.AsyncClient() as client:
        articles = await fetch_google_news(client, q)

        # Also check Hacker News for tech queries
        if any(word in q.lower() for word in ["tech", "startup", "ai", "crypto", "software", "developer", "code", "app"]):
            try:
                resp = await client.get("https://hnrss.org/frontpage", timeout=8.0)
                if resp.status_code == 200:
                    hn_articles = parse_google_news_rss(resp.text)
                    # Filter HN articles by query keywords
                    q_lower = q.lower()
                    for a in hn_articles:
                        if q_lower in a.title.lower() or q_lower in (a.description or "").lower():
                            a.source = "Hacker News"
                            articles.append(a)
            except Exception:
                pass

    # Deduplicate by title similarity
    seen_titles = set()
    unique = []
    for a in articles:
        # Normalize for dedup
        norm = re.sub(r'[^a-z0-9]', '', a.title.lower())[:60]
        if norm not in seen_titles:
            seen_titles.add(norm)
            unique.append(a)

    # Truncate to limit
    unique = unique[:limit]

    return NewsSearchResult(
        query=q,
        fetched_at=start.isoformat(),
        total_results=len(unique),
        articles=unique,
    )
