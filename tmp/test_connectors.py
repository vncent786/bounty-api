"""Test Instagram and X connectors locally."""
import asyncio
import sys
sys.path.insert(0, ".")

from social_scraper.connectors.instagram_graphql import InstagramConnector


async def test_ig():
    print("=== Instagram Connector Test ===")
    conn = InstagramConnector()
    
    # Health check
    health = await conn.health_check()
    print(f"Health: {health.status} | {health.coverage}")
    
    # Search test
    print("\nSearching 'dopaminedetox' (should return hashtag posts)...")
    result = await conn.search("dopaminedetox", count=10)
    print(f"Status: {result.health.status}")
    print(f"Items returned: {result.health.items_returned}")
    print(f"Latency: {result.health.latency_ms}ms")
    if result.health.error:
        print(f"Error: {result.health.error}")
    
    for i, item in enumerate(result.items[:5]):
        print(f"\n  [{i+1}] @{item.author_username}")
        print(f"      Likes: {item.likes} | Comments: {item.comments}")
        print(f"      Created: {item.created_at}")
        print(f"      Text: {item.text[:100]}..." if len(item.text) > 100 else f"      Text: {item.text}")
        print(f"      URL: {item.url}")


if __name__ == "__main__":
    asyncio.run(test_ig())
