"""Shared public HTTPS fetch for official statistical sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

import httpx


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class OfficialFetchError(Exception):
    """Network-level failure before a parseable HTTP response exists."""

    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


@dataclass(frozen=True)
class OfficialHttpResponse:
    url: str
    status_code: int
    headers: Mapping[str, str]
    content: bytes
    observed_at: str

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    @property
    def content_type(self) -> str:
        return str(self.headers.get("content-type") or "").casefold()


async def fetch_public(
    url: str,
    *,
    timeout: float = 30.0,
    headers: Mapping[str, str] | None = None,
    follow_redirects: bool = True,
) -> OfficialHttpResponse:
    """Fetch a public URL. Timeouts and transport errors stay distinct from HTTP status."""

    observed_at = datetime.now(timezone.utc).isoformat()
    try:
        request_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        request_headers.update(dict(headers or {}))
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=follow_redirects,
            headers=request_headers,
        ) as client:
            response = await client.get(url)
    except httpx.TimeoutException as exc:
        raise OfficialFetchError("timeout") from exc
    except httpx.HTTPError as exc:
        raise OfficialFetchError("network_error") from exc
    return OfficialHttpResponse(
        url=str(response.url),
        status_code=int(response.status_code),
        headers={str(key).casefold(): str(value) for key, value in response.headers.items()},
        content=response.content or b"",
        observed_at=observed_at,
    )


def classify_http_status(status_code: int) -> tuple[str, str] | None:
    """Map a non-success status to (raw_state_value, error_category). None means parse the body."""

    if 200 <= status_code < 300:
        return None
    if status_code == 429:
        return "rate_limited", "http_429"
    if status_code in {401, 407}:
        return "auth_error", f"http_{status_code}"
    if status_code in {403, 451}:
        return "challenged", f"http_{status_code}"
    if status_code == 404:
        return "error", "http_404"
    if status_code >= 500:
        return "error", f"http_{status_code}"
    return "error", f"http_{status_code}"
