from datetime import datetime, timezone
from decimal import Decimal

import pytest

from social_scraper.investing.free_company_sources import (
    build_sec_filing_rows,
    extract_sec_revenue_fact,
    sec_filing_url,
    select_latest_sec_filing,
    select_sec_ticker,
)


def test_select_sec_ticker_requires_exact_ticker():
    payload = {
        "0": {"cik_str": 909832, "ticker": "COST", "title": "COSTCO WHOLESALE CORP /NEW"},
        "1": {"cik_str": 1283699, "ticker": "TMUS", "title": "T-Mobile US, Inc."},
    }

    assert select_sec_ticker(payload, " cost ") == {
        "cik": "0000909832",
        "archive_cik": "909832",
        "ticker": "COST",
        "title": "COSTCO WHOLESALE CORP /NEW",
    }
    assert select_sec_ticker(payload, "COS") is None


def test_sec_filing_rows_preserve_parallel_array_identity_and_build_url():
    submissions = {
        "filings": {"recent": {
            "accessionNumber": ["0000909832-25-000101", "0000909832-26-000051"],
            "filingDate": ["2025-10-08", "2026-06-03"],
            "reportDate": ["2025-08-31", "2026-05-10"],
            "acceptanceDateTime": ["2025-10-08T01:16:06.000Z", "2026-06-03T20:43:00.000Z"],
            "form": ["10-K", "10-Q"],
            "primaryDocument": ["cost-20250831.htm", "cost-20260510.htm"],
            "isXBRL": [1, 1],
            "isInlineXBRL": [1, 1],
        }}
    }

    rows = build_sec_filing_rows(submissions)
    annual = select_latest_sec_filing(rows, ("10-K", "20-F"), as_of="2026-08-29")

    assert annual["accession_number"] == "0000909832-25-000101"
    assert annual["report_date"] == "2025-08-31"
    assert sec_filing_url("0000909832", annual) == (
        "https://www.sec.gov/Archives/edgar/data/909832/"
        "000090983225000101/cost-20250831.htm"
    )


def test_sec_filing_selection_respects_as_of_and_does_not_use_future_filing():
    rows = [
        {
            "accession_number": "future",
            "filing_date": "2026-09-01",
            "report_date": "2026-08-01",
            "acceptance_datetime": "2026-09-01T12:00:00Z",
            "form": "10-Q",
            "primary_document": "future.htm",
        },
        {
            "accession_number": "available",
            "filing_date": "2026-06-01",
            "report_date": "2026-05-01",
            "acceptance_datetime": "2026-06-01T12:00:00Z",
            "form": "10-Q",
            "primary_document": "available.htm",
        },
    ]

    selected = select_latest_sec_filing(rows, ("10-Q",), as_of="2026-08-29")

    assert selected["accession_number"] == "available"


def test_companyfacts_revenue_is_accession_and_as_of_matched():
    filing = {
        "accession_number": "0000909832-25-000101",
        "filing_date": "2025-10-08",
        "report_date": "2025-08-31",
        "acceptance_datetime": "2025-10-08T01:16:06.000Z",
        "form": "10-K",
        "primary_document": "cost-20250831.htm",
    }
    payload = {
        "facts": {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "label": "Revenue",
                "description": "Total consolidated revenue.",
                "units": {"USD": [
                    {
                        "start": "2024-09-02", "end": "2025-08-31",
                        "val": 275235000000,
                        "accn": "0000909832-25-000101",
                        "form": "10-K", "filed": "2025-10-08", "fp": "FY",
                    },
                    {
                        "start": "2024-09-02", "end": "2025-08-31",
                        "val": 999999000000,
                        "accn": "later-restatement",
                        "form": "10-K/A", "filed": "2026-09-01", "fp": "FY",
                    },
                ]},
            }
        }}
    }

    fact = extract_sec_revenue_fact(
        payload,
        filing,
        issuer_id="lei:costco",
        as_of="2026-08-29",
        source_url="https://sec.test/cost-20250831.htm",
    )

    assert fact.value == Decimal("275235000000")
    assert fact.currency == "USD"
    assert fact.period_end == "2025-08-31"
    assert fact.source_locator.endswith("RevenueFromContractWithCustomerExcludingAssessedTax")
    assert fact.verification_status == "verified_primary"


def test_companyfacts_missing_exact_accession_is_missing_not_best_guess():
    filing = {
        "accession_number": "wanted",
        "filing_date": "2025-10-08",
        "report_date": "2025-08-31",
        "acceptance_datetime": "2025-10-08T01:16:06.000Z",
        "form": "10-K",
        "primary_document": "annual.htm",
    }
    payload = {
        "facts": {"us-gaap": {"Revenues": {
            "label": "Revenue", "description": "Revenue",
            "units": {"USD": [{
                "start": "2024-01-01", "end": "2024-12-31", "val": 100,
                "accn": "different", "form": "10-K", "filed": "2025-01-01",
            }]},
        }}}
    }

    assert extract_sec_revenue_fact(
        payload,
        filing,
        issuer_id="lei:test",
        as_of="2026-08-29",
        source_url="https://sec.test/annual.htm",
    ) is None


def test_companyfacts_wrong_period_for_same_accession_is_missing():
    filing = {
        "accession_number": "same-accession",
        "filing_date": "2026-04-01",
        "report_date": "2025-12-31",
        "acceptance_datetime": "2026-04-01T00:00:00Z",
        "form": "10-K",
        "primary_document": "annual.htm",
    }
    payload = {"facts": {"us-gaap": {"Revenues": {
        "label": "Revenue", "description": "Revenue",
        "units": {"USD": [{
            "start": "2024-01-01", "end": "2024-12-31", "val": 100,
            "accn": "same-accession", "form": "10-K", "filed": "2026-04-01",
        }]},
    }}}}

    assert extract_sec_revenue_fact(
        payload,
        filing,
        issuer_id="lei:test",
        as_of="2026-08-29",
        source_url="https://sec.test/annual.htm",
    ) is None


def test_companyfacts_supports_accession_matched_ifrs_revenue():
    filing = {
        "accession_number": "ifrs-annual",
        "filing_date": "2026-04-01",
        "report_date": "2025-12-31",
        "acceptance_datetime": "2026-04-01T00:00:00Z",
        "form": "20-F",
        "primary_document": "annual.htm",
    }
    payload = {"facts": {"ifrs-full": {"Revenue": {
        "label": "Revenue", "description": "Consolidated revenue",
        "units": {"TWD": [{
            "start": "2025-01-01", "end": "2025-12-31", "val": 500,
            "accn": "ifrs-annual", "form": "20-F", "filed": "2026-04-01",
        }]},
    }}}}

    fact = extract_sec_revenue_fact(
        payload,
        filing,
        issuer_id="lei:foreign",
        as_of="2026-08-29",
        source_url="https://sec.test/annual.htm",
    )

    assert fact.value == Decimal("500")
    assert fact.currency == "TWD"
    assert fact.source_locator == "SEC CompanyFacts ifrs-full:Revenue"
