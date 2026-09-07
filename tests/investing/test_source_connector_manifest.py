import json
from pathlib import Path

from social_scraper.source_connectors import CredentialResolver

ROOT = Path(__file__).parents[2]
MANIFEST = ROOT / "references" / "source-connector-registry-v1.json"


def load_manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_source_manifest_has_one_named_credential_profile_per_authenticated_source():
    manifest = load_manifest()
    profiles = manifest["credential_profiles"]
    sources = manifest["sources"]

    assert manifest["schema_version"] == "bounty-source-registry/1"
    assert len(sources) == 20
    assert len({row["source_id"] for row in sources}) == len(sources)
    assert all(row["auth_profile"] in profiles for row in sources)
    runtime_refs = set(CredentialResolver.with_bounty_defaults(environment={}).references())
    assert set(profiles) - {"none"} <= runtime_refs
    assert all(
        requirement["kind"] != "literal"
        for profile in profiles.values()
        for requirement in profile["requirements"]
    )


def test_source_manifest_covers_existing_social_and_non_consumer_purchase_sources():
    sources = {row["source_id"]: row for row in load_manifest()["sources"]}

    assert {"google_trends", "x_owned", "tiktok_owned", "instagram_owned", "reddit_owned", "youtube_public"} <= set(sources)
    assert "public_information_parity" in sources
    assert {"arc_sales", "eurocontrol", "uk_caa", "us_bts"} <= set(sources)
    assert {"gebiz", "eu_ted", "sam_gov", "canadabuys", "issuer_disclosures"} <= set(sources)
    assert "airline.ticket_sales" in sources["arc_sales"]["capabilities"]
    assert "procurement.awards" in sources["eu_ted"]["capabilities"]
    assert "company.orders" in sources["issuer_disclosures"]["capabilities"]


def test_paid_source_placeholders_are_disabled_until_approved():
    paid = [row for row in load_manifest()["sources"] if row["cost_tier"] == "paid"]

    assert {row["source_id"] for row in paid} == {"oag_paid", "forwardkeys_paid"}
    assert all(row["enabled"] is False for row in paid)


def test_planned_source_entries_are_not_marked_as_live_adapters():
    planned = [
        row for row in load_manifest()["sources"]
        if row["connector_version"].startswith("planned/")
    ]

    assert planned
    assert all(row["enabled"] is False for row in planned)


def test_every_source_declares_health_check_scope_cost_and_limits():
    for source in load_manifest()["sources"]:
        assert source["capabilities"]
        assert source["evidence_strengths"]
        assert source["geographies"]
        assert source["time_windows"]
        assert source["rate_limit_policy"]
        assert source["limitations"]
        if source["enabled"] is False and source.get("canary") is None:
            # Planned lane with no live adapter: no canary may be fabricated.
            assert source["connector_version"].startswith("planned/")
            continue
        assert source["canary"]["query"]
        assert source["canary"]["capability"] in source["capabilities"]
        assert source["canary"]["min_records"] >= 1
