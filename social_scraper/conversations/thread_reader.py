"""Optional connector contract for bounded comment and reply retrieval."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


_ALLOWED_STATUS = {
    "complete", "partial", "empty", "disabled", "unavailable", "error", "unsupported"
}


@dataclass(frozen=True)
class ThreadRecord:
    platform: str
    external_id: str
    record_type: str
    parent_external_id: str
    root_post_external_id: str
    depth: int
    text: str | None
    author_external_id: str | None = None
    author_username: str | None = None
    url: str | None = None
    published_at: str | None = None
    likes: int | None = None
    raw: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.record_type not in {"comment", "reply"}:
            raise ValueError("thread record must be comment or reply")
        if self.depth < 1:
            raise ValueError("thread record depth must be at least one")
        if not self.external_id or not self.parent_external_id or not self.root_post_external_id:
            raise ValueError("thread identities and parent relationship are required")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ThreadFetchResult:
    platform: str
    root_post_external_id: str
    status: str
    records: tuple[ThreadRecord, ...] = ()
    truncated: bool = False
    attempted_route: str = ""
    error_category: str | None = None
    platform_reported_total: int | None = None
    max_comments: int = 0
    max_depth: int = 0
    limitations: tuple[str, ...] = ()

    def __post_init__(self):
        if self.status not in _ALLOWED_STATUS:
            raise ValueError(f"invalid thread status: {self.status}")
        if self.max_comments < 0 or self.max_depth < 0:
            raise ValueError("thread bounds cannot be negative")
        if self.status in {"complete", "empty"} and self.error_category:
            raise ValueError("successful thread result cannot have an error category")

    @property
    def returned_count(self) -> int:
        return len(self.records)

    def to_dict(self) -> dict:
        value = asdict(self)
        value["records"] = [record.to_dict() for record in self.records]
        value["returned_count"] = self.returned_count
        return value


def unsupported_thread_result(platform: str, root_post_external_id: str, max_comments: int, max_depth: int) -> ThreadFetchResult:
    return ThreadFetchResult(
        platform=platform,
        root_post_external_id=root_post_external_id,
        status="unsupported",
        attempted_route="none",
        error_category="thread_reader_not_supported",
        max_comments=max_comments,
        max_depth=max_depth,
    )
