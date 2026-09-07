"""Build the zero-call public snapshot consumed by the live investment tracker."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from social_scraper.investing.live_tracker import build_investment_tracker

OUTPUT = ROOT / "data" / "investing-tracker-snapshot.json"
payload = build_investment_tracker(ROOT)
trends_path = ROOT / "data" / "investing-tracker-trends.json"
if trends_path.is_file():
    trends = json.loads(trends_path.read_text(encoding="utf-8"))
    payload["trend_release_metadata"] = {
        "methodology": trends["methodology"],
        "built_at": trends["built_at"],
        "requested_series": trends["requested_series"],
        "terminal_series": trends["terminal_series"],
        "preflight_status": trends["preflight"]["status"],
    }
OUTPUT.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    encoding="utf-8",
)
print(json.dumps({
    "output": str(OUTPUT),
    "status": payload["status"],
    "summary": payload["summary"],
    "idea_count": len(payload["ideas"]),
    "sha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
}, indent=2))
