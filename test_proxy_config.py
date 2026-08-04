import os

from social_scraper.proxy_config import build_playwright_proxy, proxy_health_summary


def test_build_playwright_proxy_from_env(monkeypatch):
    monkeypatch.setenv("BOUNTY_PROXY_SERVER", "http://proxy.example.com:1234")
    monkeypatch.setenv("BOUNTY_PROXY_USERNAME", "user")
    monkeypatch.setenv("BOUNTY_PROXY_PASSWORD", "pass")

    assert build_playwright_proxy() == {
        "server": "http://proxy.example.com:1234",
        "username": "user",
        "password": "pass",
    }


def test_build_playwright_proxy_without_credentials(monkeypatch):
    monkeypatch.setenv("BOUNTY_PROXY_SERVER", "http://proxy.example.com:1234")
    monkeypatch.delenv("BOUNTY_PROXY_USERNAME", raising=False)
    monkeypatch.delenv("BOUNTY_PROXY_PASSWORD", raising=False)

    assert build_playwright_proxy() == {
        "server": "http://proxy.example.com:1234",
    }


def test_build_playwright_proxy_returns_none_when_not_configured(monkeypatch):
    monkeypatch.delenv("BOUNTY_PROXY_SERVER", raising=False)
    monkeypatch.delenv("BOUNTY_PROXY_USERNAME", raising=False)
    monkeypatch.delenv("BOUNTY_PROXY_PASSWORD", raising=False)

    assert build_playwright_proxy() is None


def test_proxy_health_summary_redacts_secret(monkeypatch):
    monkeypatch.setenv("BOUNTY_PROXY_SERVER", "http://proxy.example.com:1234")
    monkeypatch.setenv("BOUNTY_PROXY_USERNAME", "user")
    monkeypatch.setenv("BOUNTY_PROXY_PASSWORD", "super-secret")

    summary = proxy_health_summary()

    assert summary["configured"] is True
    assert summary["server"] == "http://proxy.example.com:1234"
    assert summary["username_configured"] is True
    assert "super-secret" not in str(summary)


def test_proxy_health_summary_removes_embedded_url_credentials(monkeypatch):
    monkeypatch.setenv("BOUNTY_PROXY_SERVER", "http://embedded-user:embedded-pass@proxy.example.com:1234")
    monkeypatch.delenv("BOUNTY_PROXY_USERNAME", raising=False)
    monkeypatch.delenv("BOUNTY_PROXY_PASSWORD", raising=False)

    summary = proxy_health_summary()

    assert summary["server"] == "http://proxy.example.com:1234"
    assert "embedded-user" not in str(summary)
    assert "embedded-pass" not in str(summary)


def test_proxy_health_summary_redacts_unparseable_server(monkeypatch):
    monkeypatch.setenv("BOUNTY_PROXY_SERVER", "embedded-user:embedded-pass@proxy.example.com:1234")

    summary = proxy_health_summary()

    assert summary["server"] == "redacted-invalid-proxy-url"
    assert "embedded-user" not in str(summary)
    assert "embedded-pass" not in str(summary)


def test_proxy_health_summary_handles_invalid_port(monkeypatch):
    monkeypatch.setenv("BOUNTY_PROXY_SERVER", "http://user:pass@proxy.example.com:notaport")

    summary = proxy_health_summary()

    assert summary["server"] == "redacted-invalid-proxy-url"
