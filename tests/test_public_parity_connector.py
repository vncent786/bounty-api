"""Shared-source tests for public information-parity collection."""

from __future__ import annotations

import asyncio

from social_scraper.source_connectors import (
    CapabilityCatalogue,
    CredentialResolver,
    ExecutionState,
    JsonReceiptStore,
    SourceExecutionRequest,
    SourceExecutor,
)
from social_scraper.source_connectors.official_http import OfficialHttpResponse
from social_scraper.source_connectors.public_parity import PublicParityConnector


def response(url: str, body: str, *, content_type: str = "text/html") -> OfficialHttpResponse:
    return OfficialHttpResponse(
        url=url,
        status_code=200,
        headers={"content-type": content_type},
        content=body.encode("utf-8"),
        observed_at="2026-09-06T12:00:00+00:00",
    )


def test_public_parity_connector_runs_through_shared_executor_and_preserves_receipts(tmp_path):
    calls: list[str] = []

    async def fetcher(url: str, **_kwargs):
        calls.append(url)
        return response(
            url,
            "<html><title>KDP investor relations</title><body>GHOST A&amp;W distribution update</body></html>",
        )

    connector = PublicParityConnector(
        canary_url="https://issuer.example/ir",
        fetcher=fetcher,
    )
    executor = SourceExecutor(
        CapabilityCatalogue([connector]),
        CredentialResolver(environment={}),
        JsonReceiptStore(tmp_path),
        max_attempts=1,
    )
    result = asyncio.run(executor.collect(
        "public_information_parity",
        SourceExecutionRequest(
            query="GHOST A&W",
            time_filter="latest_14_days",
            options={
                "lane": "official_ir",
                "sources": [{
                    "key": "issuer_release",
                    "url": "https://issuer.example/release",
                    "source_class": "official_company_ir",
                    "event_date": "2026-09-06",
                }],
                "identity_terms": ["ghost", "a&w"],
                "economic_terms": ["distribution", "sales"],
            },
            resume_key="fixture-official-ir",
        ),
        resume=False,
    ))

    assert result.receipt.state is ExecutionState.COMPLETE
    assert result.receipt.health_state == "healthy"
    assert result.receipt.verify_hash()
    assert calls == ["https://issuer.example/ir", "https://issuer.example/release"]
    item = result.receipt.evidence[0]
    assert item["url"] == "https://issuer.example/release"
    assert item["attributes"]["lane"] == "official_ir"
    assert item["attributes"]["exact_implication_match"] is True
    assert item["attributes"]["event_date"] == "2026-09-06"


def test_public_news_rss_keeps_discovery_links_and_never_marks_titles_as_verified_economics(tmp_path):
    rss = """<?xml version="1.0"?><rss><channel><item>
    <title>GHOST A&amp;W demand rises - Example Finance</title>
    <link>https://news.example/story</link>
    <pubDate>Sun, 06 Sep 2026 01:00:00 GMT</pubDate>
    <source>Example Finance</source>
    </item></channel></rss>"""

    async def fetcher(url: str, **_kwargs):
        if url == "https://issuer.example/ir":
            return response(url, "<title>IR</title><body>Keurig Dr Pepper</body>")
        return response(url, rss, content_type="application/rss+xml")

    connector = PublicParityConnector(
        canary_url="https://issuer.example/ir",
        fetcher=fetcher,
    )
    executor = SourceExecutor(
        CapabilityCatalogue([connector]),
        CredentialResolver(environment={}),
        JsonReceiptStore(tmp_path),
        max_attempts=1,
    )
    result = asyncio.run(executor.search(
        "public_information_parity",
        SourceExecutionRequest(
            query='"GHOST" "A&W" demand',
            geography="US",
            time_filter="latest_14_days",
            options={
                "lane": "qualifying_business_news",
                "rss_url": "https://news.example/rss",
                "identity_terms": ["ghost", "a&w"],
                "economic_terms": ["demand", "sales"],
            },
            resume_key="fixture-news",
        ),
        resume=False,
    ))

    assert result.receipt.state is ExecutionState.COMPLETE
    assert len(result.receipt.evidence) == 1
    item = result.receipt.evidence[0]
    assert item["url"] == "https://news.example/story"
    assert item["attributes"]["outlet"] == "Example Finance"
    assert item["attributes"]["exact_implication_match"] is False
    assert item["attributes"]["review_status"] == "requires_direct_article_review"


def test_document_terms_must_share_a_local_passage_before_exact_implication_match(tmp_path):
    async def fetcher(url: str, **_kwargs):
        if url == "https://issuer.example/ir":
            return response(url, "<title>IR</title><body>Keurig Dr Pepper</body>")
        separated = "GHOST " + ("navigation " * 200) + "A&W " + ("boilerplate " * 200) + "sales"
        return response(url, f"<html><body>{separated}</body></html>")

    connector = PublicParityConnector(
        canary_url="https://issuer.example/ir",
        fetcher=fetcher,
    )
    executor = SourceExecutor(
        CapabilityCatalogue([connector]),
        CredentialResolver(environment={}),
        JsonReceiptStore(tmp_path),
        max_attempts=1,
    )
    result = asyncio.run(executor.collect(
        "public_information_parity",
        SourceExecutionRequest(
            query="GHOST A&W",
            options={
                "lane": "official_ir",
                "sources": [{"key": "issuer", "url": "https://issuer.example/release"}],
                "identity_terms": ["ghost", "a&w"],
                "economic_terms": ["sales"],
            },
            resume_key="fixture-separated-terms",
        ),
        resume=False,
    ))

    attributes = result.receipt.evidence[0]["attributes"]
    assert attributes["identity_terms_matched"] == ["ghost", "a&w"]
    assert attributes["economic_terms_matched"] == ["sales"]
    assert attributes["exact_implication_match"] is False
    assert attributes["exact_implication_snippets"] == []


def test_document_matching_ignores_script_and_style_payloads(tmp_path):
    page = """
    <html><body><h1>GHOST Energy x A&amp;W Root Beer</h1>
    <p>A limited flavor collaboration.</p>
    <script>{"accessToken":"do-not-persist","sales":999}</script>
    <style>.ghost-a-w { content: "revenue"; }</style>
    <footer>KDP reports consolidated sales elsewhere.</footer></body></html>
    """

    async def fetcher(url: str, **_kwargs):
        if url == "https://issuer.example/ir":
            return response(url, "<title>IR</title><body>Keurig Dr Pepper</body>")
        return response(url, page)

    connector = PublicParityConnector(
        canary_url="https://issuer.example/ir",
        fetcher=fetcher,
    )
    executor = SourceExecutor(
        CapabilityCatalogue([connector]),
        CredentialResolver(environment={}),
        JsonReceiptStore(tmp_path),
        max_attempts=1,
    )
    result = asyncio.run(executor.collect(
        "public_information_parity",
        SourceExecutionRequest(
            query="GHOST A&W",
            options={
                "lane": "official_ir",
                "sources": [{"key": "issuer", "url": "https://issuer.example/release"}],
                "identity_terms": ["ghost", "a&w root beer"],
                "economic_terms": ["sales", "revenue"],
            },
            resume_key="fixture-script-exclusion",
        ),
        resume=False,
    ))

    item = result.receipt.evidence[0]
    assert "accessToken" not in (item.get("text") or "")
    assert item["attributes"]["exact_implication_match"] is False


def test_public_parity_connector_rejects_private_or_non_https_targets_before_network_use(tmp_path):
    calls: list[str] = []

    async def fetcher(url: str, **_kwargs):
        calls.append(url)
        return response(url, "ok")

    connector = PublicParityConnector(
        canary_url="https://issuer.example/ir",
        fetcher=fetcher,
    )
    executor = SourceExecutor(
        CapabilityCatalogue([connector]),
        CredentialResolver(environment={}),
        JsonReceiptStore(tmp_path),
        max_attempts=1,
    )
    result = asyncio.run(executor.collect(
        "public_information_parity",
        SourceExecutionRequest(
            query="unsafe",
            options={
                "lane": "official_ir",
                "sources": [{"key": "bad", "url": "http://127.0.0.1/private"}],
            },
            resume_key="fixture-unsafe",
        ),
        resume=False,
    ))

    assert result.receipt.state is ExecutionState.SOURCE_UNAVAILABLE
    assert result.receipt.error_category == "unsafe_source_url"
    assert calls == ["https://issuer.example/ir"]


def test_public_parity_connector_rejects_private_dns_before_network_use(tmp_path):
    calls: list[str] = []

    async def fetcher(url: str, **_kwargs):
        calls.append(url)
        return response(url, "ok")

    connector = PublicParityConnector(
        canary_url="https://issuer.example/ir",
        fetcher=fetcher,
        resolver=lambda host: [
            "93.184.216.34" if host == "issuer.example" else "10.0.0.7"
        ],
    )
    executor = SourceExecutor(
        CapabilityCatalogue([connector]),
        CredentialResolver(environment={}),
        JsonReceiptStore(tmp_path),
        max_attempts=1,
    )
    result = asyncio.run(executor.collect(
        "public_information_parity",
        SourceExecutionRequest(
            query="unsafe dns",
            options={
                "lane": "official_ir",
                "sources": [{"key": "bad", "url": "https://private.example/source"}],
            },
            resume_key="fixture-private-dns",
        ),
        resume=False,
    ))

    assert result.receipt.state is ExecutionState.SOURCE_UNAVAILABLE
    assert result.receipt.error_category == "unsafe_source_url"
    assert calls == ["https://issuer.example/ir"]


def test_public_parity_connector_validates_each_redirect_before_following(tmp_path):
    calls: list[str] = []

    async def fetcher(url: str, **_kwargs):
        calls.append(url)
        if url == "https://issuer.example/ir":
            return response(url, "canary")
        return OfficialHttpResponse(
            url=url,
            status_code=302,
            headers={"location": "https://private.example/secret"},
            content=b"",
            observed_at="2026-09-06T12:00:00+00:00",
        )

    connector = PublicParityConnector(
        canary_url="https://issuer.example/ir",
        fetcher=fetcher,
        resolver=lambda host: [
            "93.184.216.34" if host == "issuer.example" else "127.0.0.1"
        ],
    )
    executor = SourceExecutor(
        CapabilityCatalogue([connector]),
        CredentialResolver(environment={}),
        JsonReceiptStore(tmp_path),
        max_attempts=1,
    )
    result = asyncio.run(executor.collect(
        "public_information_parity",
        SourceExecutionRequest(
            query="unsafe redirect",
            options={
                "lane": "official_ir",
                "sources": [{
                    "key": "redirect",
                    "url": "https://issuer.example/redirect",
                }],
            },
            resume_key="fixture-private-redirect",
        ),
        resume=False,
    ))

    assert result.receipt.state is ExecutionState.SOURCE_UNAVAILABLE
    assert result.receipt.error_category == "unsafe_redirect_url"
    assert calls == ["https://issuer.example/ir", "https://issuer.example/redirect"]
