"""Validate or seed scoped Reddit reliability canaries.

Dry-run is the default. Pass --apply to write the query registry. These canaries
measure source health and freshness across several legitimate user segments;
they are not investment signals.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from social_scraper.connectors.reddit_arctic import SUBREDDIT_RE  # noqa: E402
from social_scraper.storage import ObservationStore  # noqa: E402

CONFIG_PATH = PROJECT_ROOT / "social_scraper" / "config" / "reddit_canaries.json"
VALID_FILTERS = {"1day", "week", "month", "halfyear"}


def load_canaries():
    records = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        raise ValueError("Canary configuration must be a non-empty list")
    names = set()
    for record in records:
        name = str(record.get("name", "")).strip()
        keyword = str(record.get("keyword", "")).strip()
        subreddits = record.get("subreddits", [])
        if not name or name in names or not keyword:
            raise ValueError("Canary names must be unique and keywords non-empty")
        if not isinstance(subreddits, list) or not 1 <= len(subreddits) <= 5:
            raise ValueError(f"{name}: requires one to five subreddits")
        if any(not isinstance(value, str) or not SUBREDDIT_RE.fullmatch(value) for value in subreddits):
            raise ValueError(f"{name}: invalid subreddit")
        if record.get("time_filter") not in VALID_FILTERS:
            raise ValueError(f"{name}: invalid time_filter")
        if not isinstance(record.get("interval_minutes"), int) or record["interval_minutes"] < 30:
            raise ValueError(f"{name}: interval must be at least 30 minutes")
        names.add(name)
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--db", default=str(PROJECT_ROOT / "data" / "social_observations.db"))
    args = parser.parse_args()
    records = load_canaries()
    output = {"validated": len(records), "applied": 0, "canaries": [row["name"] for row in records]}
    if args.apply:
        store = ObservationStore(args.db)
        now = datetime.now(timezone.utc)
        for record in records:
            store.upsert_query(
                record["keyword"],
                ["reddit"],
                "",
                record["interval_minutes"],
                now,
                platform_options={
                    "reddit": {"subreddits": record["subreddits"]},
                    "_search": {"time_filter": record["time_filter"]},
                },
            )
        output["applied"] = len(records)
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
