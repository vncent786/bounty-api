"""Test X/Twitter connector with real credentials."""
import asyncio
import os
import sys
from pathlib import Path

# Load .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from social_scraper.connectors.x_graphql import XConnector


async def test_x():
    print("=== X/Twitter Connector Test ===")
    conn = XConnector()

    # Health check
    health = await conn.health_check()
    print(f"Health: {health.status}")
    if health.error:
        print(f"Error: {health.error}")
        return

    # Search test
    print("\nSearching 'ozempic weight loss' (10 tweets)...")
    result = await conn.search("ozempic weight loss", count=10, sort="new")
    print(f"Status: {result.health.status}")
    print(f"Items returned: {result.health.items_returned}")
    print(f"Latency: {result.health.latency_ms}ms")
    if result.health.error:
        print(f"Error: {result.health.error}")

    for i, item in enumerate(result.items[:5]):
        print(f"\n  [{i+1}] @{item.author_username}")
        print(f"      Likes: {item.likes} | Comments: {item.comments} | RTs: {item.shares} | Views: {item.views}")
        print(f"      Created: {item.created_at}")
        text = item.text.replace("\n", " ")
        print(f"      Text: {text[:120]}..." if len(text) > 120 else f"      Text: {text}")
        print(f"      URL: {item.url}")

    # Verify cookies were saved
    cookie_file = Path(__file__).resolve().parents[1] / "data" / "x_cookies.json"
    print(f"\nCookie file: {cookie_file}")
    print(f"Cookie file exists: {cookie_file.exists()}")


if __name__ == "__main__":
    asyncio.run(test_x())
