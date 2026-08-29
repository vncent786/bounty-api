import pytest

from scripts.build_costco_investment_dossier import (
    build_dossier_from_sources,
    render_markdown,
)


TEN_K_TEXT = """
Paid members are eligible to upgrade to an Executive membership in the U.S., for an
additional annual fee of $65. Executive members earn a 2% reward on qualified purchases.
The sales penetration of Executive members represented approximately 73.6% of worldwide
net sales in 2025. Total paid members 1 81,000 76,200 71,000. Executive members represented
38,700, 35,400, and 32,300 of total paid members in 2025, 2024, and 2023.
Membership Fees 2025 2024 2023 Membership fees $ 5,323 $ 4,828 $ 4,580.
REVENUE Net sales $ 269,912 $ 249,625 $ 237,710 Membership fees 5,323 4,828 4,580
Total revenue 275,235 254,453 242,290.
The 2% reward associated with Executive membership reduces net sales.
"""

TEN_Q_TEXT = """
Membership Fees 12 Weeks Ended 36 Weeks Ended May 10, 2026 May 11, 2025
May 10, 2026 May 11, 2025 Membership fees $ 1,373 $ 1,240 $ 4,057 $ 3,599.
Membership fee revenue increased 11% and 13% in the third quarter and first thirty-six
weeks of 2026, driven by new member sign-ups, membership fee increases and upgrades to
Executive Membership. At the end of the third quarter of 2026, our renewal rates were
92.2% in the U.S. and Canada and 89.7% worldwide.
"""


def _document(url, source_type, text, digest):
    return {
        "url": url,
        "requested_url": url,
        "source_type": source_type,
        "status": "complete",
        "http_status": 200,
        "content_sha256": digest,
        "text": text,
    }


def test_costco_dossier_keeps_direction_uncertain_and_economics_unestimated():
    payload = build_dossier_from_sources(
        created_at="2026-08-29T08:00:00+00:00",
        gleif=[{
            "lei": "29DX7H14B9S6O3FD6V18",
            "legal_name": "COSTCO WHOLESALE CORPORATION",
            "jurisdiction": "US-WA",
            "entity_status": "ACTIVE",
        }],
        instruments=[{
            "figi": "BBG000F6H8W8",
            "share_class_figi": "BBG001S9KRQ7",
            "name": "COSTCO WHOLESALE CORP",
            "ticker": "COST",
            "exchange_code": "US",
            "security_type": "Common Stock",
        }],
        ten_k=_document("https://sec.test/10-k", "regulator_filing", TEN_K_TEXT, "khash"),
        ten_q=_document("https://sec.test/10-q", "regulator_filing", TEN_Q_TEXT, "qhash"),
        official_webcast={
            "url": "https://costco.test/webcast",
            "source_type": "official_webcast_page",
            "status": "unavailable",
            "error_category": "HTTPStatusError",
            "text": "",
        },
        transcript={
            "source_name": "Public secondary transcript",
            "source_type": "secondary_public_transcript",
            "url": "https://transcript.test/costco",
            "status": "complete",
            "content_sha256": "thash",
            "passages": [
                "Upgrades to executive memberships contributed to membership income growth."
            ],
            "limitations": ["Verify against official audio."],
        },
        news_checks=[{
            "implication": "Costco Executive incremental economics",
            "status": "complete",
            "checked_source": "Google News RSS",
            "coverage_class": "sampled_public_news",
            "articles": [],
            "limitations": ["Not complete sell-side coverage."],
        }],
    )

    assert payload["status"] == "research_only"
    assert payload["direction"]["company_direction"] == "uncertain"
    assert payload["instrument_implementation"]["common_stock_eligible"] is True
    assert payload["instrument_implementation"]["options_required"] is False

    assessments = {item["assessment_id"]: item for item in payload["materiality"]}
    assert assessments["membership_fee_share_of_total_revenue"]["status"] == "exactly_quantified"
    assert assessments["executive_incremental_economics"]["status"] == "not_estimable"
    assert assessments["executive_incremental_economics"]["computed_value"] is None

    assert payload["derived_calculations"][0]["name"] == "us_consumer_reward_break_even_spend"
    assert payload["derived_calculations"][0]["value"] == "3250"
    assert payload["economic_bridge"]["total"] is None
    assert "reward_cost" in payload["economic_bridge"]["missing_terms"]
    assert payload["assumption_scenario_bridge"]["low_total"] is None
    assert payload["assumption_scenario_bridge"]["base_total"] is None
    assert payload["assumption_scenario_bridge"]["high_total"] is None
    assert "reward_cost" in payload["assumption_scenario_bridge"]["missing_terms"]
    assert payload["information_parity"]["status"] == "unknown_for_analyst_coverage"
    assert payload["single_complete_global_database"] is False


def test_costco_dossier_markdown_states_the_missing_economics_plainly():
    payload = build_dossier_from_sources(
        created_at="2026-08-29T08:00:00+00:00",
        gleif=[],
        instruments=[],
        ten_k=_document("https://sec.test/10-k", "regulator_filing", TEN_K_TEXT, "khash"),
        ten_q=_document("https://sec.test/10-q", "regulator_filing", TEN_Q_TEXT, "qhash"),
        official_webcast={"url": "https://costco.test/webcast", "status": "unavailable", "text": ""},
        transcript={"status": "unavailable", "passages": [], "limitations": []},
        news_checks=[],
    )

    markdown = render_markdown(payload)

    assert "Company direction: **uncertain**" in markdown
    assert "Executive incremental economics: **Not estimable**" in markdown
    assert "Options required: **No**" in markdown
    assert "No single free database" in markdown


def test_dossier_rejects_non_complete_primary_filing_even_if_text_looks_valid():
    unavailable_ten_k = _document(
        "https://sec.test/10-k", "regulator_filing", TEN_K_TEXT, "khash"
    )
    unavailable_ten_k["status"] = "unavailable"

    with pytest.raises(ValueError, match="complete primary filing"):
        build_dossier_from_sources(
            created_at="2026-08-29T08:00:00+00:00",
            gleif=[],
            instruments=[],
            ten_k=unavailable_ten_k,
            ten_q=_document("https://sec.test/10-q", "regulator_filing", TEN_Q_TEXT, "qhash"),
            official_webcast={"url": "https://costco.test/webcast", "status": "unavailable"},
            transcript={"status": "unavailable", "passages": []},
            news_checks=[],
        )


def test_unavailable_transcript_suppresses_stale_passages_and_failed_news_is_not_sampled():
    payload = build_dossier_from_sources(
        created_at="2026-08-29T08:00:00+00:00",
        gleif=[],
        instruments=[],
        ten_k=_document("https://sec.test/10-k", "regulator_filing", TEN_K_TEXT, "khash"),
        ten_q=_document("https://sec.test/10-q", "regulator_filing", TEN_Q_TEXT, "qhash"),
        official_webcast={"url": "https://costco.test/webcast", "status": "unavailable"},
        transcript={
            "status": "unavailable",
            "passages": ["Injected stale transcript passage."],
            "findings": [{"speaker": "Fake", "text": "Injected stale transcript passage."}],
            "limitations": [],
        },
        news_checks=[{
            "implication": "Costco Executive economics",
            "status": "unavailable",
            "articles": [],
            "limitations": ["Source failed."],
        }],
    )

    transcript = payload["transcript_research"]
    assert transcript["finding_status"] == "transcript_findings_unavailable"
    assert transcript["secondary_transcript"].get("passages") == []
    assert transcript["secondary_transcript"].get("findings") == []
    assert payload["information_parity"]["status"] == "unknown_news_unavailable"
    assert "could not be sampled" in payload["information_parity"]["conclusion"]
    assert "Injected stale" not in render_markdown(payload)
