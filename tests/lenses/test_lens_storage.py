import sqlite3

import pytest

from social_scraper.lenses.compiler import FEATURE_SOURCE_MAP, compile_lens
from social_scraper.lenses.core import REGISTERED_FEATURES
from social_scraper.lenses.storage import ConflictError, LensStore, ValidationError


def lens_spec(feature_key="novelty"):
    return {
        "objective": "Find novel opportunities",
        "criteria": [{
            "criterion_id": "novelty_score",
            "label": "Novelty",
            "feature_key": feature_key,
            "mode": "score",
            "weight": 1.0,
            "missing_policy": "keep_unknown",
        }],
    }


def test_create_edit_duplicate_and_archive_are_versioned(tmp_path):
    store = LensStore(tmp_path / "discovery.db")
    created = store.create_lens("acme", "Opportunity radar", "Research", lens_spec())
    assert created["latest_version"]["version"] == 1
    assert created["latest_version"]["compiled_requirements"]["required_depth"] == "candidate"

    edited = store.create_lens_version(
        "acme", created["id"], lens_spec("behavior_evidence")
    )
    assert edited["version"] == 2
    assert store.get_lens_version("acme", created["id"], 1)["spec"] == lens_spec()
    assert edited["compiled_requirements"]["required_depth"] == "horizontal_analysis"

    copied = store.duplicate_lens("acme", created["id"], name="Opportunity radar copy")
    assert copied["id"] != created["id"]
    assert copied["latest_version"]["version"] == 1
    assert copied["latest_version"]["spec"] == edited["spec"]

    archived = store.archive_lens("acme", created["id"])
    assert archived["archived_at"]
    assert [row["id"] for row in store.list_lenses("acme")] == [copied["id"]]
    assert store.get_lens("acme", created["id"], include_archived=True)["archived_at"]


def test_custom_enum_field_and_compiler_depth(tmp_path):
    store = LensStore(tmp_path / "discovery.db")
    field = store.create_custom_field(
        "acme", key="purchase_intent", label="Purchase intent", description="Intent band",
        data_type="enum", source_stage="custom_extraction", extraction_mode="llm",
        definition={"values": ["low", "medium", "high"]},
    )
    assert field["definition"]["values"] == ["low", "medium", "high"]
    compiled = compile_lens(lens_spec("purchase_intent"), [field])
    assert compiled["required_depth"] == "custom_extraction"
    assert compiled["feature_sources"]["purchase_intent"] == "custom_extraction"

    with pytest.raises(ValidationError, match="enum values"):
        store.create_custom_field(
            "acme", key="bad_enum", label="Bad", data_type="enum",
            source_stage="candidate", extraction_mode="deterministic", definition={},
        )
    with pytest.raises(ValidationError, match="safe snake_case"):
        store.create_custom_field(
            "acme", key="bad-key; DROP TABLE x", label="Bad", data_type="string",
            source_stage="candidate", extraction_mode="deterministic", definition={},
        )
    with pytest.raises(ConflictError):
        store.create_custom_field(
            "acme", key="purchase_intent", label="Duplicate", data_type="string",
            source_stage="candidate", extraction_mode="deterministic", definition={},
        )


@pytest.mark.parametrize(("feature", "depth"), [
    ("novelty", "candidate"),
    ("independent_voices", "root_probe"),
    ("behavior_evidence", "horizontal_analysis"),
    ("company_exposure", "optional_enrichment"),
])
def test_compiler_maps_registered_features_to_real_source_depth(feature, depth):
    assert compile_lens(lens_spec(feature))["required_depth"] == depth
    assert set(FEATURE_SOURCE_MAP) == REGISTERED_FEATURES


def test_crud_has_no_discovery_usage_and_calls_no_sources(tmp_path, monkeypatch):
    db_path = tmp_path / "discovery.db"
    from social_scraper.discovery.storage import DiscoveryStore
    DiscoveryStore(db_path)
    store = LensStore(db_path)

    # CRUD has no source/LLM dependency to patch or invoke.
    field = store.create_custom_field(
        "acme", key="verified", label="Verified", data_type="boolean",
        source_stage="root_probe", extraction_mode="signal_aggregation", definition={},
    )
    lens = store.create_lens("acme", "Verification", "", lens_spec("verified"))
    store.get_lens("acme", lens["id"])
    store.list_custom_fields("acme")
    store.archive_custom_field("acme", field["id"])

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT count(*) FROM discovery_stage_usage").fetchone()[0] == 0
