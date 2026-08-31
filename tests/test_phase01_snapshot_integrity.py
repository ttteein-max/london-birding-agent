"""Dated snapshot integrity tests; product behaviour is tested elsewhere."""

from __future__ import annotations

import ast
import json

from app.feasibility.core import (
    FIXTURE_DIR,
    PROJECT_ROOT,
    canonical_sha256,
    file_sha256,
    load_fixture_bundle,
    validate_provenance,
)
from app.feasibility.occurrence import validate_occurrence_counts
from scripts.generate_osm_snapshot import process_elements


def test_committed_fixture_schema_and_dated_checksums() -> None:
    fixtures = load_fixture_bundle()
    assert fixtures["taxonomy"]["schema_version"] == 2
    assert fixtures["occurrences"]["schema_version"] == 2
    for fixture in fixtures.values():
        assert (
            validate_provenance(
                fixture["provenance"], schema_version=fixture["schema_version"]
            )
            == []
        )
        assert (
            canonical_sha256(fixture["payload"])
            == fixture["provenance"]["checksum_sha256"]
        )
        assert fixture["provenance"]["snapshot_version"] == "2026-08-31"


def test_occurrence_snapshot_counts_are_internally_consistent() -> None:
    fixture = load_fixture_bundle()["occurrences"]
    assert validate_occurrence_counts(fixture["provenance"]["record_counts"]) == []
    for result in fixture["payload"]["results"]:
        if result["counts"]:
            assert validate_occurrence_counts(result["counts"]) == []


def test_strong_fixture_safe_maps_come_from_the_same_hardened_snapshot() -> None:
    fixture = load_fixture_bundle()["occurrences"]
    by_input = {item["input"]: item for item in fixture["payload"]["results"]}
    assert len(by_input["House sparrow"]["safe_map_cells"]) == 8
    assert len(by_input["Turdus iliacus"]["safe_map_cells"]) == 7
    assert "safe_map_coverage_gaps" not in fixture["provenance"]
    assert fixture["payload"]["metric_grid"]["application_safe_map"] == {
        "geometry_crs": "EPSG:4326",
        "aggregation_crs": "EPSG:27700",
        "cell_size_metres": 1000,
        "minimum_records_per_exposed_cell": 3,
        "only_for_outcome": "strong_map_evidence",
        "individual_coordinates_persisted": False,
    }


def test_snapshots_never_store_precise_occurrence_coordinates() -> None:
    source = (FIXTURE_DIR / "gbif-occurrences-london.json").read_text(encoding="utf-8")
    assert "decimalLatitude" not in source
    assert "decimalLongitude" not in source
    assert "cell_secret" not in source
    for result in load_fixture_bundle()["occurrences"]["payload"]["results"]:
        for record in result["records"]:
            assert record["spatial_cell_1km_ref"] is None or record[
                "spatial_cell_1km_ref"
            ].startswith("bng-1000m-ref:")


def test_osm_and_boundary_snapshot_file_checksums_without_eternal_counts() -> None:
    for directory in (
        PROJECT_ROOT / "data" / "osm",
        PROJECT_ROOT / "data" / "boundaries",
    ):
        provenance_paths = sorted(directory.glob("*.provenance.json"))
        assert provenance_paths
        for provenance_path in provenance_paths:
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            artifact = provenance_path.with_name(
                provenance_path.name.replace(".provenance.json", ".geojson")
            )
            assert file_sha256(artifact) == provenance["checksum_sha256"]
            assert provenance["snapshot_version"] == "2026-08-31"
    osm_provenance = json.loads(
        next((PROJECT_ROOT / "data" / "osm").glob("*.provenance.json")).read_text()
    )
    osm = json.loads(
        next((PROJECT_ROOT / "data" / "osm").glob("*.geojson")).read_text()
    )
    assert len(osm["features"]) == osm_provenance["feature_count"]
    assert osm_provenance["polygon_footprint_count"] > 0
    assert (
        osm_provenance["polygon_footprint_count"]
        + osm_provenance["point_fallback_count"]
        == osm_provenance["feature_count"]
    )


def test_osm_access_policy_is_behavioural_not_snapshot_count_based() -> None:
    elements = [
        {
            "type": "way",
            "id": 1,
            "center": {"lat": 51.5, "lon": -0.1},
            "tags": {"name": "Private", "leisure": "park", "access": "private"},
        },
        {
            "type": "way",
            "id": 2,
            "center": {"lat": 51.51, "lon": -0.11},
            "geometry": [
                {"lat": 51.50, "lon": -0.12},
                {"lat": 51.50, "lon": -0.10},
                {"lat": 51.52, "lon": -0.10},
                {"lat": 51.52, "lon": -0.12},
                {"lat": 51.50, "lon": -0.12},
            ],
            "tags": {"name": "Unknown", "leisure": "park"},
        },
        {
            "type": "way",
            "id": 3,
            "center": {"lat": 51.52, "lon": -0.12},
            "tags": {"name": "Yes", "leisure": "park", "access": "yes"},
        },
    ]
    features, counts = process_elements(elements)
    assert counts["private_or_no_access"] == 1
    certainty = {
        feature["properties"]["source_tags"]["name"]: feature["properties"][
            "access_certainty"
        ]
        for feature in features
    }
    assert certainty == {"Unknown": "unspecified", "Yes": "explicit_public"}
    unknown = next(
        feature
        for feature in features
        if feature["properties"]["source_tags"]["name"] == "Unknown"
    )
    assert unknown["geometry"]["type"] == "Polygon"


def test_default_validation_has_no_api_key_or_environment_dependency() -> None:
    source = (PROJECT_ROOT / "scripts" / "phase0_feasibility.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    environment_reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}
    ]
    assert environment_reads == []
    assert "OPENAI_API_KEY" not in source
    assert not (FIXTURE_DIR / ".env").exists()
