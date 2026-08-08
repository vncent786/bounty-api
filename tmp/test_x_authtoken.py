"""Test X with auth_token cookie directly via curl_cffi."""
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from curl_cffi import requests as curl_requests

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
DOMAIN = "x.com"

FEATURES = {
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}

SEARCH_URL = f"https://{DOMAIN}/i/api/graphql/flaR-PUMshxFWZWPNpq4zA/SearchTimeline"


async def test():
    auth_token = os.getenv("BOUNTY_X_AUTH_TOKEN", "").strip()
    print(f"auth_token: {auth_token[:15]}...")

    session = curl_requests.Session(impersonate="chrome124")

    # Step 1: Get ct0 (CSRF token) by visiting x.com with auth_token set
    session.cookies.set("auth_token", auth_token, domain=f".{DOMAIN}")

    print("Fetching x.com home for ct0...")
    resp = session.get(
        f"https://{DOMAIN}/",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        },
        timeout=15,
    )
    print(f"  Homepage: {resp.status_code}")

    ct0 = None
    for cookie in session.cookies.jar:
        if cookie.name == "ct0":
            ct0 = cookie.value
            break

    print(f"ct0: {ct0[:20] if ct0 else 'NONE'}...")

    if not ct0:
        print("FAILED: No ct0 cookie. auth_token may be invalid or expired.")
        return

    # Step 2: Search
    print("\nSearching 'ozempic weight loss'...")
    variables = json.dumps({
        "rawQuery": "ozempic weight loss",
        "count": 10,
        "querySource": "typed_query",
        "product": "Latest",
    })
    features = json.dumps(FEATURES)

    search_resp = session.get(
        SEARCH_URL,
        params={"variables": variables, "features": features},
        headers={
            "Authorization": f"Bearer {BEARER_TOKEN}",
            "X-Twitter-Auth-Type": "OAuth2Session",
            "X-Twitter-Active-User": "yes",
            "X-Csrf-Token": ct0,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
            "Referer": f"https://{DOMAIN}/search?q=ozempic",
        },
        timeout=15,
    )
    print(f"Search: {search_resp.status_code}")

    if search_resp.status_code == 200:
        data = search_resp.json()
        instructions = (
            data.get("data", {})
            .get("search_by_raw_query", {})
            .get("search_timeline", {})
            .get("timeline", {})
            .get("instructions", [])
        )
        tweets_found = 0
        for instruction in instructions:
            entries = instruction.get("entries", [])
            for entry in entries:
                content = entry.get("content", {})
                tweet_results = (
                    content.get("itemContent", {})
                    .get("tweet_results", {})
                    .get("result", {})
                )
                if not tweet_results or "legacy" not in tweet_results:
                    continue

                legacy = tweet_results.get("legacy", {})
                user = (
                    tweet_results.get("core", {})
                    .get("user_results", {})
                    .get("result", {})
                )
                user_legacy = user.get("legacy", {})

                tweets_found += 1
                text = legacy.get("full_text", "").replace("\n", " ")
                views = tweet_results.get("views", {}).get("count", "?")

                print(f"\n  [{tweets_found}] @{user_legacy.get('screen_name', '?')}")
                print(f"      Likes: {legacy.get('favorite_count')} | RTs: {legacy.get('retweet_count')} | Replies: {legacy.get('reply_count')} | Views: {views}")
                print(f"      Text: {text[:120]}")

        print(f"\nTotal tweets: {tweets_found}")

        # Save cookies for the connector
        cookie_out = Path(__file__).resolve().parents[1] / "data" / "x_cookies.json"
        cookies = {c.name: c.value for c in session.cookies.jar}
        cookie_out.parent.mkdir(parents=True, exist_ok=True)
        cookie_out.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        print(f"\nCookies saved to {cookie_out}")
    else:
        print(f"Error: {search_resp.text[:500]}")


if __name__ == "__main__":
    asyncio.run(test())
