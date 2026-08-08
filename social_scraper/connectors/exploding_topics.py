"""
Exploding Topics connector — extracts curated emerging trends via Playwright.

Exploding Topics curates growth-stage trends (not news events), making it
the highest-quality candidate generator for our discovery pipeline.

Data source: https://explodingtopics.com/topics (free, ~28 topics visible)
Method: Playwright renders the JS-heavy page, extracts topic links from the
mega menu dropdown.

Each topic includes: name, slug, growth %, search volume, category.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ExplodingTopic:
    """A single trend from Exploding Topics."""
    name: str
    slug: str
    url: str
    growth: str = ""        # e.g. "+5100%", "+99X"
    growth_value: int = 0   # numeric for sorting (5100, 9900 for 99X)
    volume: str = ""        # e.g. "4.4K", "110K", "1.22M"
    volume_value: int = 0   # numeric for sorting
    description: str = ""
    category: str = "all"
    discovered_at: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "slug": self.slug,
            "url": self.url,
            "growth": self.growth,
            "growth_value": self.growth_value,
            "volume": self.volume,
            "volume_value": self.volume_value,
            "description": self.description,
            "category": self.category,
            "discovered_at": self.discovered_at,
        }


def _parse_growth(growth_str: str) -> int:
    """Parse '+5100%' or '+99X' to a numeric value for sorting."""
    if not growth_str:
        return 0
    s = growth_str.replace("+", "").replace(",", "").strip()
    try:
        if s.endswith("X"):
            # 99X = 9900% equivalent
            return int(float(s[:-1]) * 100)
        elif s.endswith("%"):
            return int(float(s[:-1]))
        else:
            return int(float(s))
    except (ValueError, IndexError):
        return 0


def _parse_volume(volume_str: str) -> int:
    """Parse '4.4K', '110K', '1.22M' to numeric."""
    if not volume_str:
        return 0
    s = volume_str.replace(",", "").strip()
    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    try:
        if s and s[-1] in multipliers:
            return int(float(s[:-1]) * multipliers[s[-1]])
        return int(float(s))
    except (ValueError, IndexError):
        return 0


async def fetch_exploding_topics(timeout: int = 30) -> list[ExplodingTopic]:
    """
    Fetch trending topics from Exploding Topics free page.

    Returns list of ExplodingTopic objects sorted by growth_value descending.
    Uses Playwright to render the JS-heavy page.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright not installed — cannot fetch Exploding Topics")
        return []

    topics: list[ExplodingTopic] = []
    seen_slugs: set[str] = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()

        try:
            await page.goto(
                "https://explodingtopics.com/topics",
                timeout=timeout * 1000,
                wait_until="networkidle",
            )
            await page.wait_for_timeout(3000)

            # Extract topic data from the mega menu links
            raw_links = await page.evaluate(
                """() => {
                    const links = Array.from(
                        document.querySelectorAll('a[href*="/topic/"]')
                    ).filter(a => {
                        const href = a.getAttribute('href') || '';
                        const slug = href.split('/topic/')[1];
                        return slug && slug.length > 0
                            && !slug.startsWith('#')
                            && slug !== '';
                    });

                    return links.map(a => ({
                        text: a.textContent.trim(),
                        href: a.getAttribute('href'),
                    }));
                }"""
            )

            for link in raw_links:
                text = link.get("text", "")
                href = link.get("href", "")

                # Extract slug
                slug_match = re.search(r"/topic/([^?#]+)", href)
                if not slug_match:
                    continue
                slug = slug_match.group(1)
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)

                # Parse name: strip volume and growth from the link text
                # Format variants:
                #   "Remineralizing gum+5100%"
                #   "wolf haircut110KVolume+14%GrowthThe wolf haircut..."
                #   "SK Hynix550KVolume+99X+Growth..."

                # First, find the growth pattern
                growth_match = re.search(r"([+-]\d[\d,]*[X%])", text)
                growth_str = growth_match.group(1) if growth_match else ""

                # Find where the volume starts (digit followed by optional K/M/B + "Volume")
                vol_start = re.search(r"\d+(?:\.\d+)?[KMB]?\s*Volume", text)

                # Name is everything before the volume or growth, whichever comes first
                cut_points = []
                if vol_start:
                    cut_points.append(vol_start.start())
                if growth_match:
                    cut_points.append(growth_match.start())

                if cut_points:
                    name = text[: min(cut_points)].strip()
                else:
                    # No volume or growth found — take first words before a number
                    name_match = re.match(r"^([^\d]+)", text)
                    name = name_match.group(1).strip() if name_match else text[:50]

                # Clean name
                name = name.strip().rstrip("+").strip()
                if not name or len(name) < 2 or len(name) > 80:
                    continue
                if name.lower() in ("all topics", "topic", "view topic"):
                    continue

                # Extract volume (appears as "110KVolume" in text)
                vol_match = re.search(r"([\d.]+[KMB]?)\s*Volume", text)
                volume_str = vol_match.group(1) if vol_match else ""

                # Build full URL
                url = (
                    f"https://explodingtopics.com{href}"
                    if href.startswith("/")
                    else href
                )

                topics.append(
                    ExplodingTopic(
                        name=name,
                        slug=slug,
                        url=url,
                        growth=growth_str,
                        growth_value=_parse_growth(growth_str),
                        volume=volume_str,
                        volume_value=_parse_volume(volume_str),
                        category="all",
                        discovered_at=datetime.now(timezone.utc).isoformat(),
                    )
                )

            # Sort by growth value descending
            topics.sort(key=lambda t: t.growth_value, reverse=True)

            logger.info(
                f"Exploding Topics: extracted {len(topics)} topics "
                f"(top: {topics[0].name if topics else 'none'})"
            )

        except Exception as e:
            logger.warning(f"Exploding Topics fetch failed: {e}")
        finally:
            await browser.close()

    return topics
