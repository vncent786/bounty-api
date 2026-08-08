"""Test all connectors through the broker."""
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apis.social_search_api import build_default_broker


async def test_all():
    broker = build_default_broker()
    print(f"Platforms: {sorted(broker.list_platforms())}")

    # Test each platform
    for platform in ["youtube", "x", "instagram", "reddit"]:
        print(f"\n{'='*50}")
        print(f"Testing {platform}: 'ozempic weight loss'")
        result = await broker.search("ozempic weight loss", platforms=[platform], count=5)
        h = result.get("platform_results", {}).get(platform, {})
        print(f"  Status: {h.get('status')}")
        print(f"  Connector: {h.get('selected_connector')}")
        items = result.get("items", [])
        print(f"  Items: {len(items)}")
        # Find items for this platform
        platform_items = [i for i in items if i.get("platform") == platform]
        for i, item in enumerate(platform_items[:3]):
            text = (item.get("text", "") or "").replace("\n", " ")
            eng = item.get("engagement", {})
            print(f"  [{i+1}] @{item.get('author', {}).get('username', '?')} | likes={eng.get('likes')} | {text[:80]}")


if __name__ == "__main__":
    asyncio.run(test_all())
