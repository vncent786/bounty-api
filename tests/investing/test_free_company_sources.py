import asyncio
from decimal import Decimal

import httpx

from social_scraper.investing.free_company_sources import (
    check_public_news_implication,
    extract_passages,
    extract_regex_fact,
    extract_speaker_findings,
    extract_transcript_text,
    free_provider_registry,
    parse_gleif_resolution,
    parse_openfigi_resolution,
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
