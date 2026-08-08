"""
Trajectory analysis engine — breakout detection from Google Trends time series.

Uses pytrends interest_over_time to fetch 7-day search interest data for
candidate keywords, then computes velocity to classify them as:
- BREAKOUT: sudden spike (recent > 3x baseline)
- RISING: steady increase (>50% above baseline)
- STABLE: no significant change
- DECLINING: decreasing interest

This addresses the core gap: we can't distinguish "broke out today" from
"trending for 3 weeks" without trajectory data.

Rate limiting: Google Trends returns 429 if hit too fast. We throttle to
1 request per 3 seconds with retry/backoff.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TrajectoryResult:
    """Velocity/breakout analysis for a single keyword."""
    keyword: str
    status: str  # BREAKOUT | RISING | STABLE | DECLINING | UNKNOWN
    velocity_pct: float  # percent change from early to recent
    current_value: int   # most recent non-partial value (0-100 scale)
    peak_value: int      # peak in the window
    baseline_value: int  # average of first half
    recent_value: int    # average of second half
    trajectory_shape: str  # sparkline-like text representation
    timeframe: str = "now 7-d"
    geo: str = "US"
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "keyword": self.keyword,
            "status": self.status,
            "velocity_pct": round(self.velocity_pct, 1),
            "current_value": self.current_value,
            "peak_value": self.peak_value,
            "baseline_value": self.baseline_value,
            "recent_value": self.recent_value,
            "trajectory_shape": self.trajectory_shape,
            "timeframe": self.timeframe,
            "geo": self.geo,
            "error": self.error,
        }


SPARK_CHARS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list[float]) -> str:
    """Create a text sparkline from values."""
    if not values:
        return ""
    max_val = max(values) if max(values) > 0 else 1
    step = max(len(values) // 20, 1)
    sampled = values[::step]
    return "".join(SPARK_CHARS[min(7, int(v / max_val * 7))] for v in sampled)


def _analyze_series(values: list[float]) -> dict:
    """Analyze a time series for velocity and breakout patterns."""
    if not values or len(values) < 4:
        return {
            "velocity_pct": 0,
            "current_value": 0,
            "peak_value": max(values) if values else 0,
            "baseline_value": 0,
            "recent_value": 0,
            "status": "UNKNOWN",
            "shape": "",
        }

    # Remove last value if it's partial (Google Trends marks isPartial=True)
    # We assume the caller has handled this

    mid = len(values) // 2
    baseline = sum(values[:mid]) / max(mid, 1)
    recent = sum(values[mid:]) / max(len(values) - mid, 1)

    current = int(values[-1])
    peak = int(max(values))

    velocity = ((recent - baseline) / max(baseline, 1)) * 100

    # Classify
    if baseline > 0 and recent > baseline * 3:
        status = "BREAKOUT"
    elif velocity > 50:
        status = "RISING"
    elif velocity < -30:
        status = "DECLINING"
    elif baseline > 0 and abs(velocity) < 30:
        status = "STABLE"
    else:
        status = "STABLE"

    return {
        "velocity_pct": velocity,
        "current_value": current,
        "peak_value": peak,
        "baseline_value": int(baseline),
        "recent_value": int(recent),
        "status": status,
        "shape": _sparkline(values),
    }


async def analyze_trajectory(
    keyword: str,
    geo: str = "US",
    timeframe: str = "now 7-d",
    max_retries: int = 3,
) -> TrajectoryResult:
    """
    Fetch Google Trends interest_over_time for a keyword and analyze velocity.

    Handles 429 rate limiting with exponential backoff.
    """
    for attempt in range(max_retries):
        try:
            from pytrends.request import TrendReq  # noqa: F401 — used in _fetch

            # Run pytrends in a thread (it's synchronous)
            result = await asyncio.to_thread(
                _fetch_interest_over_time, keyword, geo, timeframe
            )

            if result is None:
                # Rate limited or error — backoff
                wait = 3 * (2 ** attempt)
                logger.info(
                    f"pytrends rate limited for '{keyword}', "
                    f"backing off {wait}s (attempt {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(wait)
                continue

            values, is_partial = result

            # Remove partial values from the end
            clean_values = [
                v for v, p in zip(values, is_partial) if not p
            ]
            if not clean_values:
                clean_values = values[:-1]  # at least remove the last

            analysis = _analyze_series(clean_values)

            return TrajectoryResult(
                keyword=keyword,
                status=analysis["status"],
                velocity_pct=analysis["velocity_pct"],
                current_value=analysis["current_value"],
                peak_value=analysis["peak_value"],
                baseline_value=analysis["baseline_value"],
                recent_value=analysis["recent_value"],
                trajectory_shape=analysis["shape"],
                timeframe=timeframe,
                geo=geo,
            )

        except Exception as e:
            wait = 3 * (2 ** attempt)
            logger.warning(
                f"Trajectory analysis error for '{keyword}' "
                f"(attempt {attempt + 1}): {e}"
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(wait)

    return TrajectoryResult(
        keyword=keyword, status="UNKNOWN",
        velocity_pct=0, current_value=0, peak_value=0,
        baseline_value=0, recent_value=0, trajectory_shape="",
        error=f"Failed after {max_retries} retries",
    )


def _fetch_interest_over_time(
    keyword: str, geo: str, timeframe: str
) -> Optional[tuple[list[float], list[bool]]]:
    """
    Synchronous pytrends call. Returns (values, is_partial) or None on error.
    """
    try:
        from pytrends.request import TrendReq

        pytrends = TrendReq(hl="en-US", tz=360)
        pytrends.build_payload([keyword], timeframe=timeframe, geo=geo)
        df = pytrends.interest_over_time()

        if len(df) == 0 or keyword not in df.columns:
            return None

        values = df[keyword].tolist()
        is_partial = (
            df["isPartial"].tolist() if "isPartial" in df.columns
            else [False] * len(values)
        )

        return values, is_partial

    except Exception as e:
        error_name = type(e).__name__
        if "429" in str(e) or "TooManyRequests" in error_name:
            return None  # signal rate limit
        logger.warning(f"interest_over_time failed for '{keyword}': {e}")
        return None


async def analyze_batch(
    keywords: list[str],
    geo: str = "US",
    timeframe: str = "now 7-d",
    throttle_seconds: float = 3.0,
    max_keywords: int = 20,
) -> list[TrajectoryResult]:
    """
    Analyze trajectory for multiple keywords with throttling.

    Processes sequentially with a delay between calls to avoid 429s.
    Limits to max_keywords to bound execution time.
    """
    keywords = keywords[:max_keywords]
    results: list[TrajectoryResult] = []

    for i, kw in enumerate(keywords):
        if i > 0:
            await asyncio.sleep(throttle_seconds)

        result = await analyze_trajectory(kw, geo=geo, timeframe=timeframe)
        results.append(result)

        logger.info(
            f"Trajectory {i + 1}/{len(keywords)}: '{kw}' = {result.status} "
            f"(velocity {result.velocity_pct:+.0f}%)"
        )

    return results
