"""Isolated subprocess entry point for killable Camoufox operations."""

import json
import sys

from social_scraper.connectors.reddit_camoufox import (
    hydrate_reddit_post,
    scan_reddit_feeds,
)


def main():
    request = json.loads(sys.stdin.read())
    operation = request.get("operation")
    payload = request.get("payload") or {}
    try:
        if operation == "feed":
            data = scan_reddit_feeds(
                payload["subreddits"],
                payload.get("keyword", ""),
                int(payload.get("count", 20)),
                payload.get("time_filter", ""),
                payload.get("sort", ""),
            )
        elif operation == "post":
            data = hydrate_reddit_post(
                payload["url"],
                int(payload.get("comment_limit", 20)),
            )
        else:
            raise ValueError("Unsupported Camoufox operation")
        result = {"ok": True, "data": data}
    except Exception as exc:
        error = (
            "camoufox_verification_challenge"
            if str(exc) == "Reddit verification challenge"
            else "camoufox_worker_failed"
        )
        result = {"ok": False, "error": error}
    print("RESULT_JSON:" + json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
