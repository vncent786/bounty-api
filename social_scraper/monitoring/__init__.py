"""
Trend monitoring layer for Bounty API.

Two complementary discovery modes:

1. BOTTOM-UP (zone monitoring):
   - Register "zones" = groups of 4-5 seed keywords per topic area
   - Collect across all platforms on a schedule (weekly default)
   - Cluster posts by semantic similarity using LLM
   - Diff clusters week-over-week to surface emerging sub-topics

2. TOP-DOWN (keyword discovery):
   - Scan Google Trends rising queries (free, unofficial)
   - Scan Reddit /r/all rising
   - Scan platform trending pages (YouTube, TikTok)
   - Feed discovered keywords as candidate zones

Together: top-down discovers what to watch, bottom-up tracks velocity.
"""

from .zones import ZoneRegistry, Zone
from .monitor import TrendMonitor, TrendReport
from .topdown import TopDownDiscovery, EmergingKeyword
from .storage import MonitoringStore

__all__ = [
    "ZoneRegistry", "Zone",
    "TrendMonitor", "TrendReport",
    "TopDownDiscovery", "EmergingKeyword",
    "MonitoringStore",
]
