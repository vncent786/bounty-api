"""Shared execution, retry, resume, receipt hashing, and secret redaction."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from .catalogue import CapabilityCatalogue
from .credentials import CredentialBundle, CredentialResolver, MissingCredentialError
from .models import (
    ConnectorOperation,
    ExecutionState,
    NormalizedEvidence,
    RawConnectorResult,
    RawState,
    SourceExecutionRequest,
)


_SCHEMA_VERSION = "bounty-source-receipt/1"
_SAFE_ERROR_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,119}$")
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
)
_RETRYABLE_STATES = {
    RawState.ERROR,
    RawState.RATE_LIMITED,
    RawState.PARSER_ERROR,
}
_RESUMABLE_TERMINAL_STATES = {
    ExecutionState.COMPLETE,
    ExecutionState.COMPLETE_EMPTY,
    ExecutionState.BOUNDED_PARTIAL,
}


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def payload_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _jsonable(value.to_dict())
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return f"<{type(value).__name__}>"


def redact_secrets(value: Any, secrets: tuple[str, ...]) -> Any:
    """Return JSON-safe receipt data with keyed and echoed secrets removed."""

    value = _jsonable(value)
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            folded = key.casefold()
            if any(part in folded for part in _SENSITIVE_KEY_PARTS):
                output[key] = "<redacted>"
            else:
                output[key] = redact_secrets(item, secrets)
        return output
    if isinstance(value, list):
        return [redact_secrets(item, secrets) for item in value]
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            redacted = re.sub(
                re.escape(secret),
                "<redacted>",
                redacted,
                flags=re.IGNORECASE,
            )
        return redacted
    return value


def _safe_error_category(
    value: str | None,
    secrets: tuple[str, ...] = (),
    default: str = "connector_error",
) -> str:
    redacted = redact_secrets(str(value or ""), secrets)
    normalized = str(redacted).strip().casefold().replace(" ", "_")
    return normalized if _SAFE_ERROR_RE.fullmatch(normalized) else default


@dataclass(frozen=True)
class AttemptReceipt:
    attempt: int
    raw_state: str
    returned_count: int
    error_category: str | None
    started_at: str
    finished_at: str
    payload_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AttemptReceipt":
        return cls(
            attempt=int(value["attempt"]),
            raw_state=str(value["raw_state"]),
            returned_count=int(value["returned_count"]),
            error_category=value.get("error_category"),
            started_at=str(value["started_at"]),
            finished_at=str(value["finished_at"]),
            payload_sha256=str(value["payload_sha256"]),
        )


@dataclass(frozen=True)
class SourceReceipt:
    operation_id: str
    source_id: str
    operation: str
    request: Mapping[str, Any]
    state: ExecutionState
    health_state: str
    error_category: str | None
    credential_ref: str | None
    preflight_attempts: tuple[AttemptReceipt, ...]
    attempts: tuple[AttemptReceipt, ...]
    evidence: tuple[dict[str, Any], ...]
    evidence_sha256: str
    source_payload_sha256: str
    next_cursor: str | None
    completed_at: str
    receipt_sha256: str = ""
    schema_version: str = _SCHEMA_VERSION

    def __post_init__(self) -> None:
        digest = self.compute_hash()
        if self.receipt_sha256 and self.receipt_sha256 != digest:
            raise ValueError("source receipt hash does not verify")
        object.__setattr__(self, "receipt_sha256", digest)

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operation_id": self.operation_id,
            "source_id": self.source_id,
            "operation": self.operation,
            "request": dict(self.request),
            "state": self.state.value,
            "health_state": self.health_state,
            "error_category": self.error_category,
            "credential_ref": self.credential_ref,
            "preflight_attempts": [item.to_dict() for item in self.preflight_attempts],
            "attempts": [item.to_dict() for item in self.attempts],
            "evidence": list(self.evidence),
            "evidence_sha256": self.evidence_sha256,
            "source_payload_sha256": self.source_payload_sha256,
            "next_cursor": self.next_cursor,
            "completed_at": self.completed_at,
        }

    def compute_hash(self) -> str:
        return payload_sha256(self._payload())

    def verify_hash(self) -> bool:
        return self.receipt_sha256 == self.compute_hash()

    def to_dict(self) -> dict[str, Any]:
        output = self._payload()
        output["receipt_sha256"] = self.receipt_sha256
        return output

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceReceipt":
        if value.get("schema_version") != _SCHEMA_VERSION:
            raise ValueError("unsupported source receipt schema")
        return cls(
            operation_id=str(value["operation_id"]),
            source_id=str(value["source_id"]),
            operation=str(value["operation"]),
            request=dict(value["request"]),
            state=ExecutionState(str(value["state"])),
            health_state=str(value["health_state"]),
            error_category=value.get("error_category"),
            credential_ref=value.get("credential_ref"),
            preflight_attempts=tuple(
                AttemptReceipt.from_dict(item)
                for item in value.get("preflight_attempts", ())
            ),
            attempts=tuple(
                AttemptReceipt.from_dict(item) for item in value.get("attempts", ())
            ),
            evidence=tuple(dict(item) for item in value.get("evidence", ())),
            evidence_sha256=str(value["evidence_sha256"]),
            source_payload_sha256=str(value["source_payload_sha256"]),
            next_cursor=value.get("next_cursor"),
            completed_at=str(value["completed_at"]),
            receipt_sha256=str(value["receipt_sha256"]),
            schema_version=str(value["schema_version"]),
        )


class JsonReceiptStore:
    """Atomic, hash-verified one-file-per-operation receipt store."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, operation_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", operation_id):
            raise ValueError("operation_id must be a sha256 digest")
        return self.root / f"{operation_id}.json"

    def save(self, receipt: SourceReceipt) -> None:
        if not receipt.verify_hash():
            raise ValueError("refusing to save an invalid source receipt")
        destination = self._path(receipt.operation_id)
        temporary = self.root / f".{receipt.operation_id}.{uuid.uuid4().hex}.tmp"
        serialized = json.dumps(
            receipt.to_dict(), ensure_ascii=False, sort_keys=True, indent=2
        )
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def load(self, operation_id: str) -> SourceReceipt | None:
        path = self._path(operation_id)
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        receipt = SourceReceipt.from_dict(value)
        if receipt.operation_id != operation_id or not receipt.verify_hash():
            raise ValueError("persisted source receipt does not verify")
        if receipt.evidence_sha256 != payload_sha256(list(receipt.evidence)):
            raise ValueError("persisted source evidence hash does not verify")
        return receipt


@dataclass(frozen=True)
class SourceExecutionResult:
    receipt: SourceReceipt
    resumed: bool = False


class SourceExecutor:
    """Execute any registered source with common preflight, retry, and receipts."""

    def __init__(
        self,
        catalogue: CapabilityCatalogue,
        credential_resolver: CredentialResolver,
        receipt_store: JsonReceiptStore,
        *,
        max_attempts: int = 3,
        retry_delay_seconds: float = 0.25,
        clock: Callable[[], datetime] | None = None,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative")
        self.catalogue = catalogue
        self.credential_resolver = credential_resolver
        self.receipt_store = receipt_store
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> str:
        return self._clock().isoformat()

    @staticmethod
    def _operation_id(
        source_id: str,
        operation: ConnectorOperation,
        request: SourceExecutionRequest,
        redaction_values: tuple[str, ...] = (),
    ) -> str:
        return payload_sha256({
            "schema_version": _SCHEMA_VERSION,
            "source_id": source_id,
            "operation": operation.value,
            "request": redact_secrets(request.identity_payload(), redaction_values),
        })

    async def preflight(
        self, source_id: str, *, resume: bool = False
    ) -> SourceExecutionResult:
        connector = self.catalogue.get(source_id)
        probe = connector.capability.preflight
        request = SourceExecutionRequest(
            query=probe.query,
            geography=probe.geography,
            time_filter=probe.time_filter,
            sort="latest",
            limit=probe.limit,
            options=probe.options,
        )
        return await self._execute(
            source_id, ConnectorOperation.PREFLIGHT, request, resume=resume
        )

    async def search(
        self,
        source_id: str,
        request: SourceExecutionRequest,
        *,
        resume: bool = True,
    ) -> SourceExecutionResult:
        return await self._execute(
            source_id, ConnectorOperation.SEARCH, request, resume=resume
        )

    async def collect(
        self,
        source_id: str,
        request: SourceExecutionRequest,
        *,
        resume: bool = True,
    ) -> SourceExecutionResult:
        return await self._execute(
            source_id, ConnectorOperation.COLLECT, request, resume=resume
        )

    async def _invoke(
        self,
        call,
        credentials: CredentialBundle,
    ) -> tuple[RawConnectorResult, tuple[AttemptReceipt, ...]]:
        attempts = []
        final = RawConnectorResult(
            records=(), state=RawState.ERROR, error_category="connector_error"
        )
        for attempt_number in range(1, self.max_attempts + 1):
            started_at = self._now()
            try:
                result = await call()
                if not isinstance(result, RawConnectorResult):
                    raise TypeError("connector returned the wrong result type")
                final = result
            except Exception as exc:
                final = RawConnectorResult(
                    records=(),
                    state=RawState.ERROR,
                    error_category=f"connector_exception:{type(exc).__name__.casefold()}",
                )
            finished_at = self._now()
            safe_records = redact_secrets(final.records, credentials.redaction_values)
            attempts.append(AttemptReceipt(
                attempt=attempt_number,
                raw_state=final.state.value,
                returned_count=len(final.records),
                error_category=(
                    _safe_error_category(
                        final.error_category, credentials.redaction_values
                    )
                    if final.error_category
                    else None
                ),
                started_at=started_at,
                finished_at=finished_at,
                payload_sha256=payload_sha256(safe_records),
            ))
            if final.state not in _RETRYABLE_STATES:
                break
            if attempt_number < self.max_attempts and self.retry_delay_seconds:
                await asyncio.sleep(self.retry_delay_seconds)
        return final, tuple(attempts)

    @staticmethod
    def _health_for(
        raw: RawConnectorResult, secrets: tuple[str, ...] = ()
    ) -> tuple[str, str | None]:
        error = (
            _safe_error_category(raw.error_category, secrets)
            if raw.error_category
            else None
        )
        if raw.state in {
            RawState.COMPLETE,
            RawState.PARTIAL,
            RawState.BOUNDED_PARTIAL,
        } and raw.records:
            return "healthy", error
        if raw.state is RawState.AUTH_ERROR:
            return "auth_error", error or "source_auth_error"
        if raw.state is RawState.RATE_LIMITED:
            return "rate_limited", error or "source_rate_limited"
        if raw.state is RawState.CHALLENGED:
            return "challenged", error or "source_challenged"
        if raw.state is RawState.PARSER_ERROR:
            return "parser_error", error or "source_parser_error"
        if raw.state is RawState.EMPTY:
            return "outage", error or "preflight_known_positive_empty"
        return "outage", error or "source_unavailable"

    def _make_receipt(
        self,
        *,
        operation_id: str,
        source_id: str,
        operation: ConnectorOperation,
        request: SourceExecutionRequest,
        state: ExecutionState,
        health_state: str,
        error_category: str | None,
        credential_ref: str | None,
        preflight_attempts: tuple[AttemptReceipt, ...] = (),
        attempts: tuple[AttemptReceipt, ...] = (),
        evidence: tuple[dict[str, Any], ...] = (),
        source_payload_sha256: str | None = None,
        next_cursor: str | None = None,
        redaction_values: tuple[str, ...] = (),
    ) -> SourceReceipt:
        receipt = SourceReceipt(
            operation_id=operation_id,
            source_id=source_id,
            operation=operation.value,
            request=redact_secrets(request.identity_payload(), redaction_values),
            state=state,
            health_state=health_state,
            error_category=error_category,
            credential_ref=credential_ref,
            preflight_attempts=preflight_attempts,
            attempts=attempts,
            evidence=evidence,
            evidence_sha256=payload_sha256(list(evidence)),
            source_payload_sha256=source_payload_sha256 or payload_sha256([]),
            next_cursor=next_cursor,
            completed_at=self._now(),
        )
        self.receipt_store.save(receipt)
        return receipt

    async def _execute(
        self,
        source_id: str,
        operation: ConnectorOperation,
        request: SourceExecutionRequest,
        *,
        resume: bool,
    ) -> SourceExecutionResult:
        connector = self.catalogue.get(source_id)
        capability = connector.capability
        if operation not in capability.operations:
            raise ValueError(f"{source_id} does not support {operation.value}")
        if not capability.supports_geography(request.geography):
            raise ValueError(f"{source_id} does not support geography {request.geography}")
        try:
            credentials = self.credential_resolver.resolve(capability.credential_ref)
        except MissingCredentialError as exc:
            operation_id = self._operation_id(source_id, operation, request)
            receipt = self._make_receipt(
                operation_id=operation_id,
                source_id=source_id,
                operation=operation,
                request=request,
                state=ExecutionState.CREDENTIAL_MISSING,
                health_state="credential_missing",
                error_category=str(exc),
                credential_ref=capability.credential_ref,
            )
            return SourceExecutionResult(receipt=receipt)

        operation_id = self._operation_id(
            source_id, operation, request, credentials.redaction_values
        )
        existing = None
        working_request = request
        prior_evidence: tuple[dict[str, Any], ...] = ()
        prior_attempts: tuple[AttemptReceipt, ...] = ()
        resumed_partial = False
        if resume:
            existing = self.receipt_store.load(operation_id)
            if existing is not None and existing.state in _RESUMABLE_TERMINAL_STATES:
                return SourceExecutionResult(receipt=existing, resumed=True)
            if (
                operation in {ConnectorOperation.SEARCH, ConnectorOperation.COLLECT}
                and existing is not None
                and existing.state is ExecutionState.PARTIAL
                and existing.next_cursor
            ):
                working_request = replace(request, cursor=existing.next_cursor)
                prior_evidence = existing.evidence
                prior_attempts = existing.attempts
                resumed_partial = True

        preflight_attempts: tuple[AttemptReceipt, ...] = ()
        if operation is ConnectorOperation.PREFLIGHT:
            raw, attempts = await self._invoke(
                lambda: connector.preflight(credentials), credentials
            )
            preflight_attempts = attempts
            health_state, error = self._health_for(
                raw, credentials.redaction_values
            )
            if health_state != "healthy":
                receipt = self._make_receipt(
                    operation_id=operation_id,
                    source_id=source_id,
                    operation=operation,
                    request=request,
                    state=ExecutionState.SOURCE_UNAVAILABLE,
                    health_state=health_state,
                    error_category=error,
                    credential_ref=capability.credential_ref,
                    attempts=attempts,
                    source_payload_sha256=attempts[-1].payload_sha256,
                    redaction_values=credentials.redaction_values,
                )
                return SourceExecutionResult(receipt=receipt)
        else:
            preflight_raw, preflight_attempts = await self._invoke(
                lambda: connector.preflight(credentials), credentials
            )
            health_state, error = self._health_for(
                preflight_raw, credentials.redaction_values
            )
            if health_state != "healthy":
                state = (
                    ExecutionState.PARTIAL
                    if prior_evidence
                    else ExecutionState.SOURCE_UNAVAILABLE
                )
                receipt = self._make_receipt(
                    operation_id=operation_id,
                    source_id=source_id,
                    operation=operation,
                    request=request,
                    state=state,
                    health_state=health_state,
                    error_category=error,
                    credential_ref=capability.credential_ref,
                    preflight_attempts=preflight_attempts,
                    attempts=prior_attempts,
                    evidence=prior_evidence,
                    source_payload_sha256=preflight_attempts[-1].payload_sha256,
                    next_cursor=working_request.cursor if prior_evidence else None,
                    redaction_values=credentials.redaction_values,
                )
                return SourceExecutionResult(receipt=receipt, resumed=resumed_partial)
            call = connector.search if operation is ConnectorOperation.SEARCH else connector.collect
            raw, attempts = await self._invoke(
                lambda: call(working_request, credentials), credentials
            )
            if prior_attempts:
                attempts = prior_attempts + tuple(
                    replace(item, attempt=len(prior_attempts) + index)
                    for index, item in enumerate(attempts, start=1)
                )

        health_state = "healthy"
        if raw.state is RawState.EMPTY:
            state = ExecutionState.COMPLETE if prior_evidence else ExecutionState.COMPLETE_EMPTY
            error = None
        elif raw.state is RawState.COMPLETE:
            state = ExecutionState.COMPLETE
            error = None
        elif raw.state is RawState.PARTIAL and raw.records:
            state = ExecutionState.PARTIAL
            error = (
                _safe_error_category(raw.error_category, credentials.redaction_values)
                if raw.error_category
                else None
            )
        elif raw.state is RawState.BOUNDED_PARTIAL and raw.records:
            state = ExecutionState.BOUNDED_PARTIAL
            error = (
                _safe_error_category(raw.error_category, credentials.redaction_values)
                if raw.error_category
                else None
            )
        else:
            health_state, error = self._health_for(
                raw, credentials.redaction_values
            )
            state = (
                ExecutionState.PARTIAL
                if prior_evidence
                else ExecutionState.SOURCE_UNAVAILABLE
            )

        evidence: tuple[dict[str, Any], ...] = prior_evidence
        if raw.records and state in {
            ExecutionState.COMPLETE,
            ExecutionState.PARTIAL,
            ExecutionState.BOUNDED_PARTIAL,
        }:
            normalized = list(prior_evidence)
            try:
                for record in raw.records:
                    item = connector.normalize(record, working_request)
                    if not isinstance(item, NormalizedEvidence):
                        raise TypeError("normalize returned the wrong result type")
                    if item.source_id != source_id:
                        raise ValueError("normalized evidence source_id mismatch")
                    normalized.append(
                        redact_secrets(item.to_dict(), credentials.redaction_values)
                    )
            except Exception:
                state = (
                    ExecutionState.PARTIAL
                    if prior_evidence
                    else ExecutionState.SOURCE_UNAVAILABLE
                )
                health_state = "parser_error"
                error = "normalization_error"
                normalized = list(prior_evidence)
            evidence = tuple(normalized)

        next_cursor = raw.next_cursor if state is ExecutionState.PARTIAL else None
        if state is ExecutionState.PARTIAL and prior_evidence and raw.state not in {
            RawState.PARTIAL,
            RawState.COMPLETE,
            RawState.EMPTY,
        }:
            next_cursor = working_request.cursor
        if next_cursor is not None:
            safe_cursor = redact_secrets(
                next_cursor, credentials.redaction_values
            )
            if safe_cursor != next_cursor:
                next_cursor = None
                state = ExecutionState.BOUNDED_PARTIAL
                error = "unsafe_resume_cursor"

        receipt = self._make_receipt(
            operation_id=operation_id,
            source_id=source_id,
            operation=operation,
            request=request,
            state=state,
            health_state=health_state,
            error_category=error,
            credential_ref=capability.credential_ref,
            preflight_attempts=preflight_attempts if operation is not ConnectorOperation.PREFLIGHT else (),
            attempts=attempts,
            evidence=evidence,
            source_payload_sha256=payload_sha256(
                [attempt.payload_sha256 for attempt in attempts]
            ),
            next_cursor=next_cursor,
            redaction_values=credentials.redaction_values,
        )
        return SourceExecutionResult(receipt=receipt, resumed=resumed_partial)
