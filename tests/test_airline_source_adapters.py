import asyncio
import json
from pathlib import Path

from social_scraper.source_connectors import (
    CapabilityCatalogue,
    CapabilityQuery,
    ConnectorOperation,
    CredentialResolver,
    ExecutionState,
    JsonReceiptStore,
    SourceExecutionRequest,
    SourceExecutor,
    build_bounty_source_catalogue,
    register_airline_official_sources,
)
from social_scraper.source_connectors.airline import (
    ARC_CSV_URL,
    CAA_PUNCTUALITY_INDEX,
    EUROCONTROL_SERIES_URL,
    build_arc_sales_connector,
    build_eurocontrol_connector,
    build_uk_caa_connector,
)
from social_scraper.source_connectors.official_http import OfficialHttpResponse


FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "source_connectors" / "airline"
MANIFEST = Path(__file__).resolve().parents[1] / "references" / "source-connector-registry-v1.json"


def _response(url: str, body: str | bytes, status_code: int = 200) -> OfficialHttpResponse:
    content = body.encode("utf-8") if isinstance(body, str) else body
    return OfficialHttpResponse(
        url=url,
        status_code=status_code,
        headers={"content-type": "text/plain"},
        content=content,
        observed_at="2026-09-05T11:40:00+00:00",
    )


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class MapFetcher:
    def __init__(self, mapping: dict[str, OfficialHttpResponse | Exception]):
        self.mapping = mapping
        self.calls = []

    async def __call__(self, url: str) -> OfficialHttpResponse:
        self.calls.append(url)
        value = self.mapping[url]
        if isinstance(value, Exception):
            raise value
        return value


def _executor(connector, tmp_path):
    catalogue = CapabilityCatalogue([connector])
    return SourceExecutor(
        catalogue,
        CredentialResolver(environment={}),
        JsonReceiptStore(tmp_path),
        max_attempts=1,
        retry_delay_seconds=0,
    )


def _arc_fetcher(body_name: str, status_code: int = 200) -> MapFetcher:
    return MapFetcher({
        ARC_CSV_URL: _response(ARC_CSV_URL, _fixture(body_name), status_code=status_code),
    })


def test_airline_sources_match_planning_manifest_metadata():
    catalogue = CapabilityCatalogue()
    source_ids = register_airline_official_sources(catalogue)
    manifest = {row["source_id"]: row for row in json.loads(MANIFEST.read_text(encoding="utf-8"))["sources"]}

    assert source_ids == ("arc_sales", "eurocontrol", "uk_caa")
    for source_id in source_ids:
        capability = catalogue.get(source_id).capability
        planned = manifest[source_id]
        assert capability.display_name == planned["display_name"]
        assert capability.geographies == tuple(planned["geographies"])
        assert set(capability.data_kinds) == set(planned["capabilities"])
        assert capability.cost.kind == planned["cost_tier"]
        assert capability.limitations == tuple(planned["limitations"])
        assert capability.preflight.query == planned["canary"]["query"]
        assert capability.preflight.geography == planned["canary"]["geography"]
        assert capability.credential_ref is None


def test_catalogue_selects_airline_ticket_sales_without_paid_sources():
    catalogue = build_bounty_source_catalogue()
    selected = catalogue.select(CapabilityQuery(
        operation=ConnectorOperation.COLLECT,
        data_kinds=frozenset({"airline.ticket_sales"}),
        geography="US",
        allow_paid=False,
    ))
    assert [item.capability.source_id for item in selected] == ["arc_sales"]


def test_arc_known_positive_canary_normalizes_sales_with_provenance(tmp_path):
    fetcher = _arc_fetcher("arc_records.csv")
    connector = build_arc_sales_connector(fetcher=fetcher)
    result = asyncio.run(_executor(connector, tmp_path).preflight("arc_sales"))
    evidence = result.receipt.evidence[0]
    attributes = evidence["attributes"]

    assert result.receipt.state is ExecutionState.COMPLETE
    assert evidence["external_id"] == "arc-monthly-2026-07-01"
    assert evidence["evidence_type"] == "airline.ticket_sales"
    assert attributes["source_date"] == "2026-07-01"
    assert attributes["geography"] == "US"
    assert attributes["url"]
    assert attributes["raw_payload_hash"]
    assert "settled" in attributes["metric_definition"].casefold()
    assert attributes["total_sales_usd"] == 9630724973.75
    assert fetcher.calls == [ARC_CSV_URL]


def test_arc_valid_no_result_is_empty_not_failure(tmp_path):
    connector = build_arc_sales_connector(fetcher=_arc_fetcher("arc_records.csv"))
    result = asyncio.run(_executor(connector, tmp_path).search(
        "arc_sales",
        SourceExecutionRequest(query="1999-01", geography="US"),
    ))
    assert result.receipt.state is ExecutionState.COMPLETE_EMPTY
    assert result.receipt.health_state == "healthy"
    assert result.receipt.evidence == ()


def test_arc_header_only_monthly_file_fails_known_positive_canary(tmp_path):
    connector = build_arc_sales_connector(fetcher=_arc_fetcher("arc_empty.csv"))
    result = asyncio.run(_executor(connector, tmp_path).preflight("arc_sales"))
    assert result.receipt.state is ExecutionState.SOURCE_UNAVAILABLE
    assert result.receipt.error_category == "preflight_known_positive_empty"


def test_arc_changed_schema_is_parser_failure(tmp_path):
    connector = build_arc_sales_connector(fetcher=_arc_fetcher("arc_changed_schema.csv"))
    result = asyncio.run(_executor(connector, tmp_path).search(
        "arc_sales",
        SourceExecutionRequest(query="current totals", geography="US"),
    ))
    assert result.receipt.state is ExecutionState.SOURCE_UNAVAILABLE
    assert result.receipt.health_state == "parser_error"
    assert result.receipt.error_category == "arc_csv_changed_schema"


def test_arc_http_403_is_source_failure_not_empty(tmp_path):
    connector = build_arc_sales_connector(fetcher=_arc_fetcher("arc_records.csv", status_code=403))
    result = asyncio.run(_executor(connector, tmp_path).search(
        "arc_sales",
        SourceExecutionRequest(query="current totals", geography="US"),
    ))
    assert result.receipt.state is ExecutionState.SOURCE_UNAVAILABLE
    assert result.receipt.health_state == "challenged"
    assert result.receipt.error_category == "http_403"


def test_eurocontrol_collect_hydrates_weekly_traffic(tmp_path):
    listing = EUROCONTROL_SERIES_URL
    week35 = "https://www.eurocontrol.int/publication/eurocontrol-european-aviation-overview-2026-week-35"
    fetcher = MapFetcher({
        listing: _response(listing, _fixture("eurocontrol_listing.html")),
        week35: _response(week35, _fixture("eurocontrol_week35.html")),
    })
    connector = build_eurocontrol_connector(fetcher=fetcher)
    result = asyncio.run(_executor(connector, tmp_path).collect(
        "eurocontrol",
        SourceExecutionRequest(
            query="European Aviation Overview",
            geography="EUROPE",
            time_filter="week",
            limit=1,
        ),
    ))
    evidence = result.receipt.evidence[0]
    attributes = evidence["attributes"]
    assert result.receipt.state is ExecutionState.COMPLETE
    assert evidence["external_id"] == "eurocontrol-week-2026-35"
    assert attributes["average_daily_flights"] == 36581
    assert attributes["arrival_punctuality_pct"] == 69.0
    assert attributes["source_date"] == "2026-W35"
    assert attributes["geography"] == "EUROPE"
    assert attributes["raw_payload_hash"]
    assert attributes["url"].endswith(".pdf")


def test_eurocontrol_unmatched_week_is_empty(tmp_path):
    listing = EUROCONTROL_SERIES_URL
    fetcher = MapFetcher({listing: _response(listing, _fixture("eurocontrol_listing.html"))})
    connector = build_eurocontrol_connector(fetcher=fetcher)
    result = asyncio.run(_executor(connector, tmp_path).search(
        "eurocontrol",
        SourceExecutionRequest(query="week 99", geography="EUROPE"),
    ))
    assert result.receipt.state is ExecutionState.COMPLETE_EMPTY
    assert result.receipt.health_state == "healthy"


def test_eurocontrol_changed_listing_is_parser_failure(tmp_path):
    listing = EUROCONTROL_SERIES_URL
    fetcher = MapFetcher({listing: _response(listing, _fixture("eurocontrol_changed_schema.html"))})
    connector = build_eurocontrol_connector(fetcher=fetcher)
    result = asyncio.run(_executor(connector, tmp_path).search(
        "eurocontrol",
        SourceExecutionRequest(query="European Aviation Overview", geography="EUROPE"),
    ))
    assert result.receipt.state is ExecutionState.SOURCE_UNAVAILABLE
    assert result.receipt.error_category == "eurocontrol_listing_changed_schema"


def test_uk_caa_collect_uses_latest_summary_csv(tmp_path):
    year_url = CAA_PUNCTUALITY_INDEX + "2026/"
    csv_url = "https://www.caa.co.uk/Documents/Download/26825/de96ee2c-f138-4043-b7d5-08a1edf7a5c8/1742"
    fetcher = MapFetcher({
        CAA_PUNCTUALITY_INDEX: _response(CAA_PUNCTUALITY_INDEX, _fixture("caa_index.html")),
        year_url: _response(year_url, _fixture("caa_year.html")),
        csv_url: _response(csv_url, _fixture("caa_summary.csv")),
    })
    connector = build_uk_caa_connector(fetcher=fetcher)
    result = asyncio.run(_executor(connector, tmp_path).collect(
        "uk_caa",
        SourceExecutionRequest(
            query="punctuality statistics",
            geography="GB",
            time_filter="month",
            limit=10,
        ),
    ))
    airports = {item["attributes"]["airport"] for item in result.receipt.evidence}
    assert result.receipt.state is ExecutionState.COMPLETE
    assert airports == {"HEATHROW", "GATWICK"}
    first = result.receipt.evidence[0]["attributes"]
    assert first["source_date"] == "202606"
    assert first["geography"] == "GB"
    assert first["raw_payload_hash"]
    assert first["url"] == csv_url
    assert "cancelled" in first["metric_definition"].casefold()


def test_uk_caa_search_no_match_is_empty(tmp_path):
    year_url = CAA_PUNCTUALITY_INDEX + "2026/"
    fetcher = MapFetcher({
        CAA_PUNCTUALITY_INDEX: _response(CAA_PUNCTUALITY_INDEX, _fixture("caa_index.html")),
        year_url: _response(year_url, _fixture("caa_year.html")),
    })
    connector = build_uk_caa_connector(fetcher=fetcher)
    result = asyncio.run(_executor(connector, tmp_path).search(
        "uk_caa",
        SourceExecutionRequest(query="199001", geography="GB"),
    ))
    assert result.receipt.state is ExecutionState.COMPLETE_EMPTY
    assert result.receipt.health_state == "healthy"


def test_uk_caa_changed_csv_schema_is_parser_failure(tmp_path):
    year_url = CAA_PUNCTUALITY_INDEX + "2026/"
    csv_url = "https://www.caa.co.uk/Documents/Download/26825/de96ee2c-f138-4043-b7d5-08a1edf7a5c8/1742"
    fetcher = MapFetcher({
        CAA_PUNCTUALITY_INDEX: _response(CAA_PUNCTUALITY_INDEX, _fixture("caa_index.html")),
        year_url: _response(year_url, _fixture("caa_year.html")),
        csv_url: _response(csv_url, _fixture("caa_changed_schema.csv")),
    })
    connector = build_uk_caa_connector(fetcher=fetcher)
    result = asyncio.run(_executor(connector, tmp_path).collect(
        "uk_caa",
        SourceExecutionRequest(query="punctuality statistics", geography="GB"),
    ))
    assert result.receipt.state is ExecutionState.SOURCE_UNAVAILABLE
    assert result.receipt.error_category == "caa_csv_changed_schema"


def test_uk_caa_summary_without_airport_totals_is_empty(tmp_path):
    year_url = CAA_PUNCTUALITY_INDEX + "2026/"
    csv_url = "https://www.caa.co.uk/Documents/Download/26825/de96ee2c-f138-4043-b7d5-08a1edf7a5c8/1742"
    fetcher = MapFetcher({
        CAA_PUNCTUALITY_INDEX: _response(CAA_PUNCTUALITY_INDEX, _fixture("caa_index.html")),
        year_url: _response(year_url, _fixture("caa_year.html")),
        csv_url: _response(csv_url, _fixture("caa_summary_empty.csv")),
    })
    connector = build_uk_caa_connector(fetcher=fetcher)
    result = asyncio.run(_executor(connector, tmp_path).collect(
        "uk_caa",
        SourceExecutionRequest(query="punctuality statistics", geography="GB"),
    ))
    assert result.receipt.state is ExecutionState.COMPLETE_EMPTY
