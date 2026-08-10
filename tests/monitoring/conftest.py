"""Shared fixtures for monitoring tests."""

from __future__ import annotations

import pytest

from social_scraper.monitoring.zones import Zone, ZoneRegistry


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def registry(tmp_path):
    return ZoneRegistry(tmp_path / "monitoring.db")


@pytest.fixture
def sample_zone():
    return Zone(
        name="consumer-switching",
        description="Consumer switching behavior across competing products",
        keywords=["switched from alpha", "cancelled alpha", "buying beta instead"],
        platforms=["youtube", "reddit"],
        interval_hours=168,
        region="US",
    )
