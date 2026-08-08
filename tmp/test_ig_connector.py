"""Test Instagram and YouTube connectors end-to-end."""
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from social_scraper.connectors.instagram_graphql import InstagramConnector


async def test_ig():
    print("=== Instagram Authenticated Connector Test ===")
    conn = InstagramConnector()

    health = await conn.health_check()
    print(f"Health: {health.status} | {health.coverage}")

    print("\nSearching 'dopaminedetox' (20 posts)...")
    result = await conn.search("dopaminedetox", count=20)
    print(f"Status: {result.health.status}")
    print(f"Items: {result.health.items_returned}")
    print(f"Latency: {result.health.latency_ms}ms")
    print(f"Tag media count: {result.health.coverage.get('tag_media_count', '?')}")
    if result.health.error:
        print(f"Error: {result.health.error}")

    for i, item in enumerate(result.items[:5]):
        text = (item.text or "").replace("\n", " ")
        print(f"\n  [{i+1}] @{item.author_username}")
        print(f"      Likes: {item.likes} | Comments: {item.comments} | Views: {item.views}")
        print(f"      Created: {item.created_at}")
        print(f"      Text: {text[:100]}" if len(text) > 100 else f"      Text: {text}")
        print(f"      URL: {item.url}")

    # Verify cookies were saved
    cookie_file = Path(__file__).resolve().parents[1] / "data" / "ig_cookies.json"
    print(f"\nCookie file exists: {cookie_file.exists()}")


if __name__ == "__main__":
    asyncio.run(test_ig())
