"""
Proxy configuration for Bounty social scrapers.

Secrets are read from environment variables only. Do not hardcode proxy
credentials or paste them into chat logs.

Supported env vars:
- BOUNTY_PROXY_SERVER: proxy server URL, e.g. http://geo.iproyal.com:12321
- BOUNTY_PROXY_USERNAME: optional proxy username
- BOUNTY_PROXY_PASSWORD: optional proxy password
"""

from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlsplit, urlunsplit


def build_playwright_proxy() -> Optional[dict]:
    """Return Playwright-compatible proxy config from environment.

    Playwright expects:
        {"server": "http://host:port", "username": "...", "password": "..."}

    If no server is configured, return None so browsers launch direct.
    """
    server = os.getenv("BOUNTY_PROXY_SERVER", "").strip()
    if not server:
        return None

    proxy = {"server": server}

    username = os.getenv("BOUNTY_PROXY_USERNAME", "").strip()
    password = os.getenv("BOUNTY_PROXY_PASSWORD", "").strip()

    if username:
        proxy["username"] = username
    if password:
        proxy["password"] = password

    return proxy


def proxy_health_summary() -> dict:
    """Return redacted proxy config status for health/debug output."""
    proxy = build_playwright_proxy()
    if not proxy:
        return {
            "configured": False,
            "server": None,
            "username_configured": False,
            "password_configured": False,
        }

    server = proxy.get("server") or ""
    parsed = urlsplit(server)
    try:
        port = parsed.port
    except ValueError:
        port = None
        parsed = urlsplit("")
    if parsed.scheme in {"http", "https", "socks5", "socks5h"} and parsed.hostname:
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = f"{host}:{port}" if port else host
        server = urlunsplit((parsed.scheme, netloc, "", "", ""))
    else:
        server = "redacted-invalid-proxy-url"

    return {
        "configured": True,
        "server": server,
        "username_configured": bool(proxy.get("username")),
        "password_configured": bool(proxy.get("password")),
    }
