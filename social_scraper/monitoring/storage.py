"""
Monitoring store — thin wrapper for the zone registry SQLite database.
Keeps monitoring data separate from raw observation storage.
"""

from .zones import ZoneRegistry

class MonitoringStore(ZoneRegistry):
    """Same as ZoneRegistry — alias for clarity in the monitoring context."""
    pass
