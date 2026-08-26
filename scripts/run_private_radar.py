"""Run one owned private investment Radar scan from the residential worker."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=False)


def _allowed() -> bool:
    enabled = os.getenv("BOUNTY_OWNED_SOCIAL_WORKER", "").strip().lower()
    return enabled in {"1", "true", "yes", "on"} and not any(
        os.getenv(name) for name in (
            "RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID"
        )
    )


async def main() -> int:
    if not _allowed():
        print(json.dumps({
            "status": "refused",
            "reason": "owned_residential_worker_required",
        }))
        return 2

    from social_scraper.investing.owned_radar import build_private_scanner
    from social_scraper.investing.private_radar import PrivateRadarStore

    path = Path(
        os.getenv("BOUNTY_PRIVATE_RADAR_DB", str(ROOT / "data" / "private_radar.db"))
    )
    if not path.is_absolute():
        path = ROOT / path
    store = PrivateRadarStore(path)
    result = await build_private_scanner(store).run()
    print(json.dumps({
        "status": result.get("status"),
        "run_id": result.get("id"),
        "stage": result.get("stage"),
        "progress": result.get("progress"),
        "evidence_count": result.get("evidence_count"),
        "candidate_count": result.get("candidate_count"),
        "error_category": result.get("error_category"),
        "db_path": str(path),
    }))
    return 1 if result.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
