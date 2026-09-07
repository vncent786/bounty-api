"""Capability catalogue and deterministic connector selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .models import ConnectorOperation, SourceCapability, SourceConnector


@dataclass(frozen=True)
class CapabilityQuery:
    operation: ConnectorOperation
    data_kinds: frozenset[str] = field(default_factory=frozenset)
    geography: str = ""
    lookback_days: int | None = None
    allow_paid: bool = True

    def __post_init__(self) -> None:
        if self.lookback_days is not None and self.lookback_days < 0:
            raise ValueError("lookback_days cannot be negative")


class CapabilityCatalogue:
    """Register a source once, then select it by declared capability."""

    def __init__(self, connectors: Iterable[SourceConnector] | None = None):
        self._connectors: dict[str, SourceConnector] = {}
        for connector in connectors or ():
            self.register(connector)

    def register(self, connector: SourceConnector) -> None:
        capability = getattr(connector, "capability", None)
        if not isinstance(capability, SourceCapability):
            raise TypeError("connector must expose a SourceCapability")
        source_id = capability.source_id
        if source_id in self._connectors:
            raise ValueError(f"source already registered: {source_id}")
        required_methods = ("preflight", "search", "collect", "normalize")
        missing = [name for name in required_methods if not callable(getattr(connector, name, None))]
        if missing:
            raise TypeError(f"connector is missing methods: {', '.join(missing)}")
        self._connectors[source_id] = connector

    def get(self, source_id: str) -> SourceConnector:
        try:
            return self._connectors[source_id]
        except KeyError as exc:
            raise KeyError(f"unknown source: {source_id}") from exc

    def list_capabilities(self) -> tuple[SourceCapability, ...]:
        return tuple(
            connector.capability
            for connector in sorted(
                self._connectors.values(),
                key=lambda item: (item.capability.priority, item.capability.source_id),
            )
        )

    def describe(self) -> list[dict]:
        return [capability.to_dict() for capability in self.list_capabilities()]

    def select(self, query: CapabilityQuery) -> tuple[SourceConnector, ...]:
        selected = []
        for connector in self._connectors.values():
            capability = connector.capability
            if query.operation not in capability.operations:
                continue
            if query.data_kinds and not query.data_kinds.issubset(capability.data_kinds):
                continue
            if not capability.supports_geography(query.geography):
                continue
            if not capability.time_range.supports(query.lookback_days):
                continue
            if not query.allow_paid and capability.cost.kind != "free":
                continue
            selected.append(connector)
        selected.sort(key=lambda item: (item.capability.priority, item.capability.source_id))
        return tuple(selected)
