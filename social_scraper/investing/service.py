"""Upstream-free read service for persisted investing Radar data."""

from __future__ import annotations

from typing import Any

from .storage import InvestingRadarStore


class InvestingRadarService:
    """API-independent read boundary; every method is SQLite-only."""

    def __init__(self, store: InvestingRadarStore) -> None:
        self.store = store

    def get_sweep(self, sweep_id: str) -> dict[str, Any] | None:
        return self.store.get_sweep(sweep_id)

    def latest_sweep(self) -> dict[str, Any] | None:
        return self.store.latest_sweep()

    def latest_data_sweep(self) -> dict[str, Any] | None:
        return self.store.latest_data_sweep()

    def list_radar(
        self,
        limit: int = 100,
        country: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.store.list_radar(limit=limit, country=country, category=category)

    def get_candidate(self, candidate_id: int | str) -> dict[str, Any] | None:
        return self.store.get_candidate(candidate_id)
