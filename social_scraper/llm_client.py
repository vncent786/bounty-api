"""Shared LLM client for Bounty's enrichment and conversation analysis.

Providers:
- ``openai_compatible``: any OpenAI-compatible chat/completions API.
- ``codex_oauth``: temporary local-only adapter using the host's Hermes
  OpenAI Codex OAuth subscription through the Responses API.

The Codex adapter is intentionally isolated. It is suitable for local product
validation, not a production SaaS deployment. Production should use a normal
API credential and ``openai_compatible``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path


_CODEX_IMPORT_LOCK = threading.Lock()


@contextmanager
def _temporarily_unshadow_modules(*names: str):
    """Let an embedded dependency import its own top-level compatibility modules.

    Bounty loads ``crawlers.utils`` under the legacy top-level name ``utils``.
    The local Hermes Codex adapter also imports a different top-level ``utils``.
    Remove only those names for the duration of the embedded import, then put
    Bounty's modules back exactly as they were.
    """
    previous = {name: sys.modules.pop(name) for name in names if name in sys.modules}
    try:
        yield
    finally:
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(previous)


def _move_import_path_to_front(path: str) -> None:
    """Put one import root first, removing normalized duplicates."""
    target = os.path.normcase(os.path.abspath(path))
    sys.path[:] = [
        item for item in sys.path
        if os.path.normcase(os.path.abspath(item or os.curdir)) != target
    ]
    sys.path.insert(0, path)


def _provider() -> str:
    return os.getenv("BOUNTY_LLM_PROVIDER", "openai_compatible").strip().lower()


async def call_llm(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int = 4000,
    temperature: float = 0.1,
) -> str:
    """Call the configured LLM and return plain assistant text."""
    provider = _provider()
    if provider == "codex_oauth":
        return await asyncio.to_thread(
            _call_codex_oauth,
            system_prompt,
            user_prompt,
            max_tokens,
        )
    if provider == "openai_compatible":
        return await _call_openai_compatible(
            system_prompt,
            user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    raise RuntimeError(f"unsupported_llm_provider: {provider}")


async def _call_openai_compatible(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int,
    temperature: float,
) -> str:
    import httpx

    base_url = os.getenv("BOUNTY_LLM_BASE_URL", "").strip().rstrip("/")
    api_key = (
        os.getenv("BOUNTY_LLM_API_KEY")
        or os.getenv("ZAI_API_KEY", "")
    ).strip()
    model = os.getenv("BOUNTY_LLM_MODEL", "").strip()
    if not base_url or not api_key or not model:
        raise RuntimeError(
            "llm_not_configured: set BOUNTY_LLM_BASE_URL, "
            "BOUNTY_LLM_API_KEY, and BOUNTY_LLM_MODEL"
        )

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


def _call_codex_oauth(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
) -> str:
    """Use the local Hermes Codex OAuth pool and Responses API adapter.

    The credential pool refreshes expiring OAuth tokens and persists rotations
    safely. This deliberately does not copy OAuth tokens into Bounty's .env.
    """
    hermes_path = os.getenv(
        "BOUNTY_HERMES_AGENT_PATH",
        str(Path.home() / "AppData/Local/hermes/hermes-agent"),
    ).strip()
    # Move the embedded runtime to the front even if the host app imported it
    # earlier. Bounty prepends crawler compatibility paths during startup.
    # Leaving Hermes later in sys.path makes its top-level ``utils`` import
    # resolve to ``crawlers.utils`` instead.
    if hermes_path:
        _move_import_path_to_front(hermes_path)

    try:
        with _CODEX_IMPORT_LOCK, _temporarily_unshadow_modules("utils"):
            from openai import OpenAI
            from agent.auxiliary_client import (
                CodexAuxiliaryClient,
                _codex_cloudflare_headers,
            )
            from agent.credential_pool import load_pool
    except ImportError as exc:
        raise RuntimeError(
            "codex_oauth_unavailable: Hermes/OpenAI dependency import failed: "
            f"{exc.__class__.__name__}: {exc}"
        ) from exc

    entry = load_pool("openai-codex").select()
    if entry is None or not entry.access_token:
        raise RuntimeError(
            "codex_oauth_unavailable: no usable OpenAI Codex OAuth credential"
        )

    model = os.getenv("BOUNTY_LLM_MODEL", "gpt-5.4").strip() or "gpt-5.4"
    base_url = (
        os.getenv("BOUNTY_LLM_BASE_URL", "")
        .strip()
        .rstrip("/")
        or "https://chatgpt.com/backend-api/codex"
    )
    real_client = OpenAI(
        api_key=entry.access_token,
        base_url=base_url,
        default_headers=_codex_cloudflare_headers(entry.access_token),
        timeout=120.0,
    )
    client = CodexAuxiliaryClient(real_client, model)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=max_tokens,
    )
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("codex_oauth_empty_response")
    return content
