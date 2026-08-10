"""Immutable operational budgets and per-stage usage receipts for Discovery."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar, Mapping


_ALLOWED_STAGES = frozenset({
    "observed",
    "screening",
    "root_probe",
    "deep_read",
    "horizontal_extraction",
    "lens_evaluation",
    "optional_enrichment",
})
_ALLOWED_STATUSES = frozenset({
    "not_checked",
    "complete",
    "empty",
    "partial",
    "unavailable",
    "failed",
    "skipped",
})
_STATUS_ALIASES = {"error": "failed", "success": "complete"}


def _nonnegative_integer(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")


def _datetime(value: datetime | str, name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    else:
        raise TypeError(f"{name} must be a datetime or ISO-8601 string")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ScanBudget:
    """Operational load guards for one scan, not claims of analytical optimality."""

    root_probe_candidates: int = 20
    deep_read_candidates: int = 5
    horizontal_llm_candidates: int = 5
    threads_per_platform: int = 2
    comments_per_thread: int = 20
    max_thread_depth: int = 2
    optional_enrichments: int = 0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            _nonnegative_integer(name, value)
        if self.max_thread_depth > 10:
            raise ValueError("max_thread_depth must be within the operational range 0..10")

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ScanBudget":
        return cls(**dict(value))


@dataclass(frozen=True)
class StageUsage:
    """Validated receipt for the work and calls performed by one pipeline stage."""

    ALLOWED_STAGES: ClassVar[frozenset[str]] = _ALLOWED_STAGES
    ALLOWED_STATUSES: ClassVar[frozenset[str]] = _ALLOWED_STATUSES

    discovery_run_id: str
    stage: str
    started_at: datetime | str
    completed_at: datetime | str
    candidates_considered: int = 0
    candidates_processed: int = 0
    records_returned: int = 0
    external_calls: int = 0
    llm_calls: int = 0
    cache_hits: int = 0
    status: str = "complete"
    error_category: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    tokens_estimated: bool = False
    duration_seconds: float = field(init=False)

    def __post_init__(self) -> None:
        run_id = str(self.discovery_run_id).strip()
        if not run_id:
            raise ValueError("discovery_run_id must not be empty")
        stage = str(self.stage).strip().casefold().replace("-", "_").replace(" ", "_")
        if stage not in _ALLOWED_STAGES:
            raise ValueError(f"unknown stage: {self.stage}")
        status = str(self.status).strip().casefold().replace("-", "_").replace(" ", "_")
        status = _STATUS_ALIASES.get(status, status)
        if status not in _ALLOWED_STATUSES:
            raise ValueError(f"unknown status: {self.status}")

        started = _datetime(self.started_at, "started_at")
        completed = _datetime(self.completed_at, "completed_at")
        if completed < started:
            raise ValueError("completed_at must not be before started_at")

        for name in (
            "candidates_considered", "candidates_processed", "records_returned",
            "external_calls", "llm_calls", "cache_hits",
        ):
            _nonnegative_integer(name, getattr(self, name))
        for name in ("input_tokens", "output_tokens"):
            value = getattr(self, name)
            if value is not None:
                _nonnegative_integer(name, value)
        if not isinstance(self.tokens_estimated, bool):
            raise TypeError("tokens_estimated must be a boolean")

        object.__setattr__(self, "discovery_run_id", run_id)
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "started_at", started)
        object.__setattr__(self, "completed_at", completed)
        object.__setattr__(self, "duration_seconds", (completed - started).total_seconds())

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["started_at"] = self.started_at.isoformat()
        value["completed_at"] = self.completed_at.isoformat()
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StageUsage":
        fields = dict(value)
        fields.pop("duration_seconds", None)
        fields.pop("id", None)
        return cls(**fields)
