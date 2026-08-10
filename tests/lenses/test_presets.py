import asyncio

from apis import dashboard_api
from social_scraper.lenses.presets import get_lens_preset, list_lens_presets


def test_horizontal_explorer_is_neutral_default_surface():
    preset = get_lens_preset("horizontal-explorer")
    assert preset.suggested_signal_kinds == ()
    assert preset.optional_enrichments == ()


def test_investing_product_and_marketing_are_peer_lenses():
    presets = {item["preset_id"]: item for item in list_lens_presets()}
    assert {
        "horizontal-explorer",
        "investing-social-arbitrage",
        "product-opportunity",
        "marketing-intelligence",
    } == set(presets)
    assert "company_exposure" in presets["investing-social-arbitrage"]["optional_enrichments"]
    assert "workaround" in presets["product-opportunity"]["suggested_signal_kinds"]
    assert "objection" in presets["marketing-intelligence"]["suggested_signal_kinds"]
    assert presets["marketing-intelligence"]["requires_monitored_subject"] is True


def test_presets_api_keeps_horizontal_explorer_as_neutral_default(monkeypatch):
    monkeypatch.delenv("DASHBOARD_ADMIN_TOKEN", raising=False)
    result = asyncio.run(dashboard_api.discovery_lens_presets())
    assert result["default_preset_id"] == "horizontal-explorer"
    assert len(result["presets"]) == 4
