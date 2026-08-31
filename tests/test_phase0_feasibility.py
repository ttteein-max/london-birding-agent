"""Offline acceptance tests for the Phase 0 London biodiversity evidence gate."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from app.feasibility.core import (
    FIXTURE_DIR,
    MAP_VIABILITY_RULE,
    PROJECT_ROOT,
    canonical_sha256,
    evaluate_occurrence_fixture,
    file_sha256,
    load_fixture_bundle,
    validate_provenance,
)
from scripts.generate_osm_snapshot import process_elements


def test_fixture_schema_and_payload_checksums_are_valid() -> None:
    fixtures = load_fixture_bundle()
    assert set(fixtures) == {"taxonomy", "occurrences", "postcode", "weather"}
    for fixture in fixtures.values():
        assert fixture["schema_version"] == 1
        assert validate_provenance(fixture["provenance"]) == []
        assert canonical_sha256(fixture["payload"]) == fixture["provenance"]["checksum_sha256"]

    assert fixtures["postcode"]["payload"]["postcode"] == "SW11 4NJ"
    assert fixtures["weather"]["payload"]["daily"]["time"]


def test_deterministic_filtering_counts_match_saved_provenance() -> None:
    fixture = load_fixture_bundle()["occurrences"]
    taxa = fixture["payload"]["taxa"]
    retained = sum(sum(record["retained"] for record in taxon["records"]) for taxon in taxa)
    raw = sum(taxon["raw_server_count"] for taxon in taxa)
    assert fixture["provenance"]["record_counts"] == {"before": raw, "after": retained}
    assert [sum(record["retained"] for record in taxon["records"]) for taxon in taxa] == [
        295,
        294,
        294,
        5,
        0,
    ]


def test_map_viability_gate_passes_three_taxa() -> None:
    assert MAP_VIABILITY_RULE == {
        "minimum_retained_records": 50,
        "minimum_spatial_cells_1km": 5,
        "minimum_datasets": 2,
    }
    results = [
        evaluate_occurrence_fixture(taxon)
        for taxon in load_fixture_bundle()["occurrences"]["payload"]["taxa"]
    ]
    assert [result["status"] for result in results[:3]] == ["map_viable"] * 3
    assert all(result["retained_count"] >= 50 for result in results[:3])
    assert all(result["spatial_cells_1km"] >= 5 for result in results[:3])
    assert all(result["dataset_count"] >= 2 for result in results[:3])


def test_ambiguous_taxonomy_requires_human_selection() -> None:
    ambiguous = load_fixture_bundle()["taxonomy"]["payload"]["ambiguous"]
    assert ambiguous["input"] == "robin"
    assert ambiguous["direct_match"]["rank"] != "SPECIES"
    assert ambiguous["resolution_status"] == "human_selection_required"
    assert {candidate["canonical_name"] for candidate in ambiguous["candidates"]} == {
        "Erithacus rubecula",
        "Turdus migratorius",
        "Erithacus komadori",
    }


def test_sparse_and_no_evidence_taxa_fail_safely() -> None:
    taxa = load_fixture_bundle()["occurrences"]["payload"]["taxa"]
    sparse, none = (evaluate_occurrence_fixture(taxon) for taxon in taxa[-2:])
    assert sparse["input"] == "Corncrake"
    assert sparse["raw_server_count"] == 5
    assert sparse["status"] == "insufficient_evidence"
    assert none["input"] == "Great auk"
    assert none["raw_server_count"] == 0
    assert none["status"] == "insufficient_evidence"


def test_occurrence_fixture_audits_quality_and_hides_locations() -> None:
    records = [
        record
        for taxon in load_fixture_bundle()["occurrences"]["payload"]["taxa"]
        for record in taxon["records"]
    ]
    required = {
        "record_ref",
        "has_coordinate",
        "occurrence_status",
        "issues",
        "coordinate_uncertainty_metres",
        "observation_or_event_date",
        "dataset_key",
        "basis_of_record",
        "record_licence",
        "spatial_cell_1km",
        "media",
        "retained",
    }
    assert records and all(required.issubset(record) for record in records)
    assert all("decimalLatitude" not in record and "decimalLongitude" not in record for record in records)
    assert all(
        record["spatial_cell_1km"] is None or record["spatial_cell_1km"].startswith("cell-ref:")
        for record in records
    )
    assert all(
        {"identifier_ref", "type", "licence", "reusable"}.issubset(media)
        for record in records
        for media in record["media"]
    )


def test_osm_snapshot_provenance_and_access_policy() -> None:
    provenance_paths = sorted((PROJECT_ROOT / "data" / "osm").glob("*.provenance.json"))
    assert len(provenance_paths) == 1
    provenance = json.loads(provenance_paths[0].read_text(encoding="utf-8"))
    assert validate_provenance(provenance) == []
    snapshot = provenance_paths[0].with_name(provenance_paths[0].name.replace(".provenance.json", ".geojson"))
    collection = json.loads(snapshot.read_text(encoding="utf-8"))
    assert file_sha256(snapshot) == provenance["checksum_sha256"]
    assert len(collection["features"]) == provenance["feature_count"] == 3364
    assert collection["attribution"] == "© OpenStreetMap contributors"
    assert all(
        feature["properties"]["source_tags"].get("access") not in {"private", "no"}
        for feature in collection["features"]
    )
    assert any(
        feature["properties"]["access_certainty"] == "unspecified"
        for feature in collection["features"]
    )


def test_osm_processing_excludes_private_and_does_not_infer_missing_access() -> None:
    elements = [
        {"type": "way", "id": 1, "center": {"lat": 51.5, "lon": -0.1}, "tags": {"name": "Private", "leisure": "park", "access": "private"}},
        {"type": "way", "id": 2, "center": {"lat": 51.51, "lon": -0.11}, "tags": {"name": "Unknown", "leisure": "park"}},
        {"type": "way", "id": 3, "center": {"lat": 51.52, "lon": -0.12}, "tags": {"name": "Yes", "leisure": "park", "access": "yes"}},
    ]
    features, counts = process_elements(elements)
    assert counts == {"raw": 3, "private_or_no_access": 1, "missing_centre": 0, "retained": 2}
    certainty = {feature["properties"]["source_tags"]["name"]: feature["properties"]["access_certainty"] for feature in features}
    assert certainty == {"Unknown": "unspecified", "Yes": "explicit_public"}


def test_default_feasibility_path_does_not_require_api_keys() -> None:
    source = (PROJECT_ROOT / "scripts" / "phase0_feasibility.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    environment_reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}
    ]
    assert environment_reads == []
    assert "OPENAI_API_KEY" not in source
    assert not (FIXTURE_DIR / ".env").exists()
