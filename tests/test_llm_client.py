import asyncio
import sys
from types import ModuleType

import httpx
import pytest

from social_scraper.llm_client import (
    _move_import_path_to_front,
    _temporarily_unshadow_modules,
    call_llm,
)


def test_temporarily_unshadow_modules_restores_host_module(monkeypatch):
    host_utils = ModuleType("utils")
    host_utils.origin = "bounty"
    imported_utils = ModuleType("utils")
    imported_utils.origin = "hermes"
    monkeypatch.setitem(sys.modules, "utils", host_utils)

    with _temporarily_unshadow_modules("utils"):
        assert "utils" not in sys.modules
        sys.modules["utils"] = imported_utils
        assert sys.modules["utils"] is imported_utils

    assert sys.modules["utils"] is host_utils


def test_move_import_path_to_front_reorders_existing_path(tmp_path, monkeypatch):
    embedded = tmp_path / "embedded"
    embedded.mkdir()
    other = str(tmp_path / "other")
    monkeypatch.setattr(sys, "path", [other, str(embedded), ""])

    _move_import_path_to_front(str(embedded))

    assert sys.path[0] == str(embedded)
    assert sys.path.count(str(embedded)) == 1
    assert sys.path[1:] == [other, ""]


def test_xai_provider_uses_paid_responses_api_and_parses_output(monkeypatch):
    import json

    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "error": None,
                "output": [{
                    "type": "message",
                    "content": [{"type": "output_text", "text": "qualified lead"}],
                }],
            },
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    class TestClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", TestClient)
    monkeypatch.setenv("BOUNTY_LLM_PROVIDER", "xai")
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    monkeypatch.delenv("XAI_BASE_URL", raising=False)
    monkeypatch.delenv("XAI_MODEL", raising=False)

    result = asyncio.run(call_llm("system", "evidence", max_tokens=321))

    assert result == "qualified lead"
    assert captured["url"] == "https://api.x.ai/v1/responses"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["payload"]["model"] == "grok-4.6"
    assert captured["payload"]["max_output_tokens"] == 321
    assert captured["payload"]["input"][1]["content"] == "evidence"
    assert captured["payload"]["store"] is False
    assert captured["payload"]["tools"] == []
    assert captured["payload"]["tool_choice"] == "none"


def test_xai_incomplete_response_fails_even_when_partial_text_exists(monkeypatch):
    def handler(_request):
        return httpx.Response(
            200,
            json={
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "error": None,
                "output": [{
                    "type": "message",
                    "content": [{"type": "output_text", "text": "truncated"}],
                }],
            },
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    class TestClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", TestClient)
    monkeypatch.setenv("BOUNTY_LLM_PROVIDER", "xai")
    monkeypatch.setenv("XAI_API_KEY", "test-key")

    with pytest.raises(RuntimeError, match="xai_incomplete_response"):
        asyncio.run(call_llm("system", "evidence"))


def test_xai_error_payload_fails_closed(monkeypatch):
    def handler(_request):
        return httpx.Response(
            200,
            json={"status": "incomplete", "error": {"code": "provider_error"}},
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    class TestClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", TestClient)
    monkeypatch.setenv("BOUNTY_LLM_PROVIDER", "xai")
    monkeypatch.setenv("XAI_API_KEY", "test-key")

    with pytest.raises(RuntimeError, match="xai_error_response"):
        asyncio.run(call_llm("system", "evidence"))


def test_openai_compatible_does_not_fall_back_to_zai_key(monkeypatch):
    monkeypatch.setenv("BOUNTY_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("BOUNTY_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("BOUNTY_LLM_MODEL", "example")
    monkeypatch.delenv("BOUNTY_LLM_API_KEY", raising=False)
    monkeypatch.setenv("ZAI_API_KEY", "must-not-be-used")

    with pytest.raises(RuntimeError, match="llm_not_configured"):
        asyncio.run(call_llm("system", "evidence"))


def test_xai_provider_fails_closed_without_api_key(monkeypatch):
    monkeypatch.setenv("BOUNTY_LLM_PROVIDER", "xai")
    monkeypatch.delenv("XAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="xai_not_configured"):
        asyncio.run(call_llm("system", "evidence"))
