"""Shared LLM client for Bounty's enrichment and conversation analysis.

Providers:
- ``xai``: Grok through xAI's paid Responses API. This is the production
  synthesis path for investor-facing research.
- ``openai_compatible``: any explicitly configured OpenAI-compatible
  chat/completions API. No provider-specific credential fallback is allowed.
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
from typing import Literal


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


TaskClass = Literal["tagging", "triage", "investigation", "dossier"]
_TASK_CLASSES = {"tagging", "triage", "investigation", "dossier"}


def _provider(task_class: TaskClass | None = None) -> str:
    if task_class is not None:
        normalized = str(task_class).strip().lower()
        if normalized not in _TASK_CLASSES:
            raise RuntimeError(f"unsupported_llm_task: {normalized}")
        key = f"BOUNTY_LLM_{normalized.upper()}_PROVIDER"
        if key in os.environ:
            selected = os.getenv(key, "").strip().lower()
            if not selected:
                raise RuntimeError(f"llm_task_provider_blank: {normalized}")
            return selected
    return os.getenv("BOUNTY_LLM_PROVIDER", "openai_compatible").strip().lower()


async def call_llm(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int = 4000,
    temperature: float = 0.1,
    task_class: TaskClass | None = None,
) -> str:
    """Call the configured LLM and return plain assistant text."""
    provider = _provider(task_class)
    if provider == "codex_oauth":
        return await asyncio.to_thread(
            _call_codex_oauth,
            system_prompt,
            user_prompt,
            max_tokens,
        )
    if provider == "xai":
        return await _call_xai(
            system_prompt,
            user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    if provider == "glm":
        return await _call_glm(
            system_prompt,
            user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    if provider == "openai_compatible":
        return await _call_openai_compatible(
            system_prompt,
            user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    raise RuntimeError(f"unsupported_llm_provider: {provider}")


async def _call_xai(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int,
    temperature: float,
) -> str:
    """Call Grok through xAI's Responses API.

    Collection is deliberately outside this call. Grok receives a fixed,
    persisted evidence snapshot so a model or X-search outage cannot change the
    underlying corpus or trigger a raw-title fallback.
    """
    import httpx

    base_url = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1").strip().rstrip("/")
    api_key = os.getenv("XAI_API_KEY", "").strip()
    model = os.getenv("XAI_MODEL", "grok-4.6").strip() or "grok-4.6"
    if not api_key:
        raise RuntimeError("xai_not_configured: set XAI_API_KEY")

    timeout_seconds = float(os.getenv("BOUNTY_LLM_TIMEOUT_SECONDS", "300"))
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            f"{base_url}/responses",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "input": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
                "max_output_tokens": max_tokens,
                "store": False,
                "tools": [],
                "tool_choice": "none",
            },
        )
        response.raise_for_status()
        data = response.json()

    if data.get("error"):
        raise RuntimeError("xai_error_response")
    if data.get("status") != "completed":
        raise RuntimeError("xai_incomplete_response")

    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    parts = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)
    result = "\n".join(parts).strip()
    if not result:
        raise RuntimeError("xai_empty_response")
    return result


async def _call_glm(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int,
    temperature: float,
) -> str:
    """Call an explicitly selected GLM model with its own credential namespace."""
    import httpx

    base_url = os.getenv(
        "GLM_BASE_URL", "https://api.z.ai/api/paas/v4"
    ).strip().rstrip("/")
    api_key = os.getenv("ZAI_API_KEY", "").strip()
    model = os.getenv("GLM_MODEL", "").strip()
    if not api_key or not model:
        raise RuntimeError("glm_not_configured: set ZAI_API_KEY and GLM_MODEL")
    timeout_seconds = float(os.getenv("BOUNTY_LLM_TIMEOUT_SECONDS", "300"))
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
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
    if data.get("error"):
        raise RuntimeError("glm_error_response")
    try:
        choice = data["choices"][0]
        if choice.get("finish_reason") not in {None, "stop"}:
            raise RuntimeError("glm_incomplete_response")
        result = choice["message"]["content"]
    except RuntimeError:
        raise
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("glm_invalid_response") from exc
    if not isinstance(result, str) or not result.strip():
        raise RuntimeError("glm_empty_response")
    return result


async def _call_openai_compatible(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int,
    temperature: float,
) -> str:
    import httpx

    base_url = os.getenv("BOUNTY_LLM_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("BOUNTY_LLM_API_KEY", "").strip()
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
