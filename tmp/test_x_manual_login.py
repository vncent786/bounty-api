"""Test: use auth_token cookie directly with curl_cffi to hit X GraphQL."""
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlencode

from dotenv import load_dotenv
from curl_cffi import requests as curl_requests

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# The public bearer token (same for all web clients, hardcoded in twikit/constants.py)
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

SEARCH_TIMELINE_URL = f"https://{DOMAIN}/i/api/graphql/flaR-PUMshxFWZWPNpq4zA/SearchTimeline"


async def test_search():
    # Step 1: Get a session with curl_cffi (Chrome TLS fingerprint)
    session = curl_requests.Session(impersonate="chrome124")

    # Step 2: Hit x.com homepage to get initial cookies (csrftoken, etc.)
    print("Step 1: Fetching x.com homepage for cookies...")
    home_resp = session.get(
        f"https://{DOMAIN}/",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        },
        timeout=15,
    )
    print(f"  Homepage: {home_resp.status_code}")

    # Get ct0 (CSRF token) from cookies
    ct0 = None
    for cookie in session.cookies.jar:
        if cookie.name == "ct0":
            ct0 = cookie.value
            break

    if not ct0:
        print("  WARNING: No ct0 cookie found in homepage response")

    # Step 3: Set the auth_token cookie manually
    auth_token = os.getenv("BOUNTY_X_AUTH_TOKEN", "").strip()
    if not auth_token:
        # Try login-based approach: use username/password to get auth_token
        username = os.getenv("BOUNTY_X_USERNAME", "").strip()
        password = os.getenv("BOUNTY_X_PASSWORD", "").strip()

        if not username or not password:
            print("ERROR: Need either BOUNTY_X_AUTH_TOKEN or BOUNTY_X_USERNAME + BOUNTY_X_PASSWORD")
            return

        print(f"\nStep 2: Logging in as @{username}...")
        # Use curl_cffi to perform login via X's flow
        login_resp = session.post(
            f"https://{DOMAIN}/i/api/1.1/on/p/task/v1",
            json={
                "input_flow_data": {
                    "flow_context": {
                        "debug_overrides": {},
                        "start_location": {"location": "splash_screen"},
                    }
                },
                "subtask_versions": {},
            },
            headers={
                "Authorization": f"Bearer {BEARER_TOKEN}",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
                "X-Twitter-Active-User": "yes",
                "X-Twitter-Auth-Type": "OAuth2Client",
                **({"X-Csrf-Token": ct0} if ct0 else {}),
            },
            timeout=15,
        )
        print(f"  Login init: {login_resp.status_code}")
        login_data = login_resp.json()
        flow_token = login_data.get("flow_token")
        print(f"  Flow token: {flow_token[:50] if flow_token else 'None'}...")

        # Continue login flow with credentials
        login_resp2 = session.post(
            f"https://{DOMAIN}/i/api/1.1/on/p/task/v1",
            json={
                "flow_token": flow_token,
                "subtask_inputs": [{
                    "subtask_id": "LoginJsInstrumentationSubtask",
                    "js_instrumentation": {
                        "response": "{}",
                        "link": "next_link",
                    },
                }],
            },
            headers={
                "Authorization": f"Bearer {BEARER_TOKEN}",
                "Content-Type": "application/json",
                "X-Twitter-Active-User": "yes",
                "X-Twitter-Auth-Type": "OAuth2Client",
                **({"X-Csrf-Token": ct0} if ct0 else {}),
            },
            timeout=15,
        )
        print(f"  JS subtask: {login_resp2.status_code}")
        data2 = login_resp2.json()
        flow_token = data2.get("flow_token", flow_token)

        # Submit credentials
        login_resp3 = session.post(
            f"https://{DOMAIN}/i/api/1.1/on/p/task/v1",
            json={
                "flow_token": flow_token,
                "subtask_inputs": [{
                    "subtask_id": "LoginEnterUserIdentifierSSO",
                    "settings_list": {
                        "setting_responses": [{
                            "response_data": {
                                "text_data": {"result": username},
                            },
                        }],
                        "link": "next_link",
                    },
                }],
            },
            headers={
                "Authorization": f"Bearer {BEARER_TOKEN}",
                "Content-Type": "application/json",
                "X-Twitter-Active-User": "yes",
                "X-Twitter-Auth-Type": "OAuth2Client",
                **({"X-Csrf-Token": ct0} if ct0 else {}),
            },
            timeout=15,
        )
        print(f"  Username: {login_resp3.status_code}")
        data3 = login_resp3.json()
        flow_token = data3.get("flow_token", flow_token)

        # Submit password
        login_resp4 = session.post(
            f"https://{DOMAIN}/i/api/1.1/on/p/task/v1",
            json={
                "flow_token": flow_token,
                "subtask_inputs": [{
                    "subtask_id": "LoginEnterPassword",
                    "enter_password": {
                        "password": password,
                        "link": "next_link",
                    },
                }],
            },
            headers={
                "Authorization": f"Bearer {BEARER_TOKEN}",
                "Content-Type": "application/json",
                "X-Twitter-Active-User": "yes",
                "X-Twitter-Auth-Type": "OAuth2Client",
                **({"X-Csrf-Token": ct0} if ct0 else {}),
            },
            timeout=15,
        )
        print(f"  Password: {login_resp4.status_code}")
        data4 = login_resp4.json()

        # Check if we got auth_token
        for cookie in session.cookies.jar:
            if cookie.name == "auth_token":
                auth_token = cookie.value
                print(f"  Got auth_token: {auth_token[:20]}...")
                break

        if not auth_token:
            print(f"  Login response: {json.dumps(data4, indent=2)[:500]}")
            # Check for errors
            if "errors" in data4:
                print(f"  Errors: {data4['errors']}")
            return

    # Set auth_token cookie
    session.cookies.set("auth_token", auth_token, domain=f".{DOMAIN}")

    # Refresh ct0 after auth
    ct0 = None
    for cookie in session.cookies.jar:
        if cookie.name == "ct0":
            ct0 = cookie.value
            break

    if not ct0:
        # Try getting a fresh ct0
        print("\nGetting fresh ct0...")
        verify_resp = session.post(
            f"https://api.{DOMAIN}/1.1/account/verify_credentials.json",
            headers={
                "Authorization": f"Bearer {BEARER_TOKEN}",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            timeout=15,
        )
        for cookie in session.cookies.jar:
            if cookie.name == "ct0":
                ct0 = cookie.value
                break

    print(f"\nct0: {ct0[:20] if ct0 else 'NONE'}...")

    # Step 4: Search!
    print("\nStep 3: Searching 'ozempic weight loss'...")
    variables = json.dumps({
        "rawQuery": "ozempic weight loss",
        "count": 10,
        "querySource": "typed_query",
        "product": "Latest",
    })
    features = json.dumps(FEATURES)

    search_resp = session.get(
        SEARCH_TIMELINE_URL,
        params={"variables": variables, "features": features},
        headers={
            "Authorization": f"Bearer {BEARER_TOKEN}",
            "Content-Type": "application/json",
            "X-Twitter-Auth-Type": "OAuth2Session",
            "X-Twitter-Active-User": "yes",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
            **({"X-Csrf-Token": ct0} if ct0 else {}),
        },
        timeout=15,
    )
    print(f"Search response: {search_resp.status_code}")

    if search_resp.status_code == 200:
        data = search_resp.json()
        # Extract tweets from the nested response
        instructions = (
            data.get("data", {})
            .get("search_by_raw_query", {})
            .get("search_timeline", {})
            .get("timeline", {})
            .get("instructions", [])
        )
        tweet_count = 0
        for instruction in instructions:
            entries = instruction.get("entries", [])
            for entry in entries:
                content = entry.get("content", {})
                if content.get("entryType") == "TimelineTimelineItem":
                    tweet_results = (
                        content.get("itemContent", {})
                        .get("tweet_results", {})
                        .get("result", {})
                    )
                    if tweet_results:
                        legacy = tweet_results.get("legacy", {})
                        user = tweet_results.get("core", {}).get("user_results", {}).get("result", {})
                        user_legacy = user.get("legacy", {})

                        tweet_count += 1
                        text = legacy.get("full_text", "").replace("\n", " ")
                        print(f"\n  [{tweet_count}] @{user_legacy.get('screen_name', '?')}")
                        print(f"      Likes: {legacy.get('favorite_count')} | RTs: {legacy.get('retweet_count')} | Replies: {legacy.get('reply_count')}")
                        print(f"      Text: {text[:120]}")

        print(f"\nTotal tweets found: {tweet_count}")
    else:
        print(f"Error: {search_resp.text[:500]}")


if __name__ == "__main__":
    asyncio.run(test_search())
