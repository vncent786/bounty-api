import asyncio
from decimal import Decimal

import httpx
import pytest

import social_scraper.investing.free_company_sources as company_sources
from social_scraper.investing.free_company_sources import (
    check_public_news_implication,
    extract_passages,
    extract_regex_fact,
    extract_speaker_findings,
    extract_transcript_text,
    fetch_finnhub_transcript_findings,
    fetch_sec_company_sources,
    fetch_text_document,
    free_provider_registry,
    parse_gleif_resolution,
    parse_openfigi_resolution,
    select_exact_gleif,
    validate_public_https_url_syntax,
)


def test_free_provider_registry_does_not_claim_one_complete_global_database():
    providers = free_provider_registry()

    assert {item["provider"] for item in providers} >= {
        "GLEIF",
        "OpenFIGI",
        "SEC EDGAR",
        "filings.xbrl.org",
        "Issuer investor relations",
    }
    assert not any(item.get("complete_global_filings") is True for item in providers)
    assert all(item.get("cost") == "free" for item in providers)


def test_parse_gleif_resolution_preserves_lei_and_jurisdiction():
    payload = {
        "data": [{
            "id": "29DX7H14B9S6O3FD6V18",
            "attributes": {
                "entity": {
                    "legalName": {"name": "COSTCO WHOLESALE CORPORATION"},
                    "jurisdiction": "US-WA",
                    "status": "ACTIVE",
                }
            },
        }]
    }

    result = parse_gleif_resolution(payload)

    assert result == [{
        "lei": "29DX7H14B9S6O3FD6V18",
        "legal_name": "COSTCO WHOLESALE CORPORATION",
        "jurisdiction": "US-WA",
        "entity_status": "ACTIVE",
    }]


def test_gleif_resolution_keeps_only_exact_normalized_legal_name():
    records = [
        {"lei": "right", "legal_name": "T-MOBILE US, INC."},
        {"lei": "wrong", "legal_name": "INC Group Inc."},
    ]

    assert select_exact_gleif(records, "T-Mobile US, Inc.") == [records[0]]


def test_parse_openfigi_resolution_keeps_instrument_identity():
    payload = [{"data": [{
        "figi": "BBG000F6H8W8",
        "name": "COSTCO WHOLESALE CORP",
        "ticker": "COST",
        "exchCode": "US",
        "securityType2": "Common Stock",
        "shareClassFIGI": "BBG001S9KRQ7",
    }]}]

    result = parse_openfigi_resolution(payload)

    assert result == [{
        "figi": "BBG000F6H8W8",
        "share_class_figi": "BBG001S9KRQ7",
        "name": "COSTCO WHOLESALE CORP",
        "ticker": "COST",
        "exchange_code": "US",
        "security_type": "Common Stock",
    }]


def test_transcript_extractor_prefers_the_bounded_transcript_container():
    html = """
    <html><body>
      <script>Executive fake script payload</script>
      <nav>Membership fee navigation</nav>
      <div class="article-body transcript-content">
        <p><strong>Gary Millerchip:</strong> Paid Executive memberships reached 41.2 million.</p>
        <p>Renewal rates were 92.2% in the US and Canada.</p>
      </div>
    </body></html>
    """

    text = extract_transcript_text(html, source_domain="www.fool.com")

    assert "Paid Executive memberships reached 41.2 million" in text
    assert "fake script payload" not in text
    assert "navigation" not in text


def test_passages_are_bounded_deduplicated_and_keyword_grounded():
    text = (
        "Intro. Paid Executive memberships reached 41.2 million. "
        "Renewal rates were stable. Other discussion. "
        "Membership fee income increased because of upgrades. End."
    )

    passages = extract_passages(text, ("Executive memberships", "Membership fee"), radius=55)

    assert len(passages) == 2
    assert all(len(value) <= 160 for value in passages)
    assert "Executive memberships" in passages[0]
    assert "Membership fee" in passages[1]


def test_transcript_findings_keep_the_nearest_speaker_label():
    text = (
        "Operator: Welcome. Gary Millerchip: Membership income grew, driven by "
        "upgrades to executive memberships. Analyst: Thank you."
    )

    findings = extract_speaker_findings(text, ("upgrades to executive memberships",))

    assert findings == [{
        "keyword": "upgrades to executive memberships",
        "speaker": "Gary Millerchip",
        "text": "Membership income grew, driven by upgrades to executive memberships.",
    }]


def test_regex_fact_extraction_preserves_exact_source_passage():
    text = (
        "Membership Fees 2025 2024 2023 Membership fees $ 5,323 $ 4,828 $ 4,580. "
        "Membership fee revenue increased 10% in 2025."
    )

    fact = extract_regex_fact(
        text,
        pattern=r"Membership fees\s*\$\s*([\d,]+)",
        metric="membership_fees",
        issuer_id="lei:costco",
        source_id="sec:cost-20250831",
        period_start="2024-09-02",
        period_end="2025-08-31",
        currency="USD",
        unit="USD millions",
        scope_type="consolidated",
        source_locator="Membership Fees",
        verification_status="verified_primary",
    )

    assert fact.value == Decimal("5323")
    assert "Membership fees $ 5,323" in fact.exact_source_text
    assert fact.verification_status == "verified_primary"


def test_finnhub_transcript_without_user_key_fails_closed_without_network():
    result = asyncio.run(fetch_finnhub_transcript_findings(
        "COST",
        api_key=None,
        keywords=("Executive membership",),
    ))

    assert result["status"] == "unavailable"
    assert result["error_category"] == "api_key_not_supplied"
    assert result["findings"] == []


def test_private_and_reserved_source_urls_are_rejected():
    for url in (
        "https://127.0.0.1/report",
        "https://169.254.169.254/latest/meta-data",
        "https://localhost/report",
        "https://service.internal/report",
    ):
        with pytest.raises(ValueError):
            validate_public_https_url_syntax(url)


def test_user_source_redirect_to_private_address_is_blocked(monkeypatch):
    calls = []

    async def public_dns(_hostname, _port):
        return None

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"location": "https://169.254.169.254/latest/meta-data"},
            request=request,
        )

    monkeypatch.setattr(company_sources, "_resolve_public_host", public_dns)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_text_document(
                "https://public.example/report",
                source_type="user_supplied_primary_candidate",
                client=client,
            )

    result = asyncio.run(run())

    assert result["status"] == "unavailable"
    assert result["error_category"] == "ValueError"
    assert calls == ["https://public.example/report"]


def test_finnhub_empty_result_never_persists_query_string_key():
    secret = "SECRET-FINNHUB-KEY"

    def handler(request):
        return httpx.Response(200, json={"transcripts": []}, request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_finnhub_transcript_findings(
                "COST",
                api_key=secret,
                keywords=("Executive membership",),
                client=client,
            )

    result = asyncio.run(run())

    assert result["status"] == "empty"
    assert secret not in str(result)
    assert "token=" not in str(result)


def test_failed_annual_filing_retrieval_marks_sec_result_partial(monkeypatch):
    tickers = {
        "0": {"cik_str": 909832, "ticker": "COST", "title": "COSTCO WHOLESALE CORP /NEW"}
    }
    submissions = {
        "tickers": ["COST"],
        "filings": {"recent": {
            "accessionNumber": ["0000909832-25-000101"],
            "filingDate": ["2025-10-08"],
            "reportDate": ["2025-08-31"],
            "acceptanceDateTime": ["2025-10-08T01:16:06.000Z"],
            "form": ["10-K"],
            "primaryDocument": ["cost-20250831.htm"],
            "isXBRL": [1],
            "isInlineXBRL": [1],
        }},
    }

    def handler(request):
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json=tickers, request=request)
        if "/submissions/" in request.url.path:
            return httpx.Response(200, json=submissions, request=request)
        if "/companyfacts/" in request.url.path:
            return httpx.Response(200, json={"facts": {}}, request=request)
        raise AssertionError(str(request.url))

    async def unavailable_document(*_args, **_kwargs):
        return {
            "status": "unavailable",
            "source_type": "regulator_filing",
            "url": "https://www.sec.gov/Archives/filing.htm",
            "text": "",
        }

    monkeypatch.setattr(company_sources, "fetch_text_document", unavailable_document)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_sec_company_sources("COST", as_of="2026-08-29", client=client)

    result = asyncio.run(run())

    assert result["status"] == "partial"
    assert result["documents"][0]["status"] == "unavailable"
    assert "primary annual SEC filing document was unavailable" in " ".join(result["limitations"])


def test_failed_news_check_does_not_claim_sampled_coverage():
    async def run():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(503, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            return await check_public_news_implication(
                '"Costco Executive membership" incremental profit',
                client=client,
            )

    result = asyncio.run(run())

    assert result["status"] == "unavailable"
    assert result["coverage_class"] == "unavailable"
    assert result["articles"] == []
