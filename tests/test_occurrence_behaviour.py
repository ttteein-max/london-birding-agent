"""Stable synthetic occurrence retrieval and evidence-tier tests."""

from __future__ import annotations

from typing import Any

from app.feasibility.occurrence import (
    RetrievalPolicy,
    classify_evidence,
    retrieve_occurrences,
    sanitise_occurrence,
    validate_occurrence_counts,
)
from app.feasibility.taxonomy import TaxonomyOutcome

SQUARE_LONDON = {
    "type": "Polygon",
    "coordinates": [[[-1, 51], [1, 51], [1, 52], [-1, 52], [-1, 51]]],
}


def raw_record(
    key: int,
    *,
    uncertainty: int | None,
    longitude: float = -0.1,
    latitude: float = 51.5,
    dataset: str = "dataset-a",
    issues: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "decimalLongitude": longitude,
        "decimalLatitude": latitude,
        "coordinateUncertaintyInMeters": uncertainty,
        "occurrenceStatus": "PRESENT",
        "issues": issues or [],
        "eventDate": "2025-06-15",
        "year": 2025,
        "month": 6,
        "datasetKey": dataset,
        "basisOfRecord": "HUMAN_OBSERVATION",
        "license": "https://creativecommons.org/licenses/by/4.0/",
    }


def test_location_quality_controls_permitted_use() -> None:
    cases = [(1_000, "strong", "spatial_ranking", True), (1_001, "weak", "broad_zone_only", False), (5_000, "weak", "broad_zone_only", False), (5_001, "context_only", "historical_context_only", False), (None, "unknown", "audit_only", False)]
    for index, (uncertainty, quality, use, ranking) in enumerate(cases):
        result = sanitise_occurrence(
            raw_record(index, uncertainty=uncertainty),
            london_boundary=SQUARE_LONDON,
            cell_secret=b"test-secret" * 4,
        )
        assert result["location_quality"] == quality
        assert result["evidence_use"] == use
        assert result["ranking_eligible"] is ranking
        assert (result["spatial_cell_1km_ref"] is not None) is ranking
        assert "decimalLatitude" not in result and "decimalLongitude" not in result


def test_fatal_issue_and_outside_boundary_are_rejected() -> None:
    fatal = sanitise_occurrence(
        raw_record(1, uncertainty=100, issues=["ZERO_COORDINATE"]),
        london_boundary=SQUARE_LONDON,
        cell_secret=b"x" * 32,
    )
    outside = sanitise_occurrence(
        raw_record(2, uncertainty=100, longitude=2),
        london_boundary=SQUARE_LONDON,
        cell_secret=b"x" * 32,
    )
    assert fatal["rejection_reasons"] == ["fatal_geospatial_issue"]
    assert outside["rejection_reasons"] == ["outside_greater_london_boundary"]
    assert not fatal["retained"] and not outside["retained"]


def test_one_to_four_sparse_records_are_insufficient_not_site_recommendation() -> None:
    records = [
        sanitise_occurrence(
            raw_record(index, uncertainty=5_001),
            london_boundary=SQUARE_LONDON,
            cell_secret=b"x" * 32,
        )
        for index in range(3)
    ]
    assert classify_evidence(records) == "insufficient_evidence"
    assert all(record["evidence_use"] == "historical_context_only" for record in records)
    assert all(not record["ranking_eligible"] for record in records)


def test_five_sparse_records_can_be_limited_context() -> None:
    records = [
        sanitise_occurrence(
            raw_record(index, uncertainty=5_001),
            london_boundary=SQUARE_LONDON,
            cell_secret=b"x" * 32,
        )
        for index in range(5)
    ]
    assert classify_evidence(records) == "limited_contextual_evidence"


def test_strong_gate_uses_only_ranking_eligible_records() -> None:
    ranking = [
        {
            "retained": True,
            "ranking_eligible": True,
            "spatial_cell_1km_ref": f"cell-{index % 5}",
            "dataset_key": f"dataset-{index % 2}",
        }
        for index in range(50)
    ]
    context = [
        {
            "retained": True,
            "ranking_eligible": False,
            "spatial_cell_1km_ref": None,
            "dataset_key": "context-dataset",
        }
        for _ in range(1_000)
    ]
    assert classify_evidence(ranking + context) == "strong_map_evidence"
    assert classify_evidence(ranking[:49] + context) == "limited_contextual_evidence"


def test_bounded_paging_deduplication_and_count_semantics() -> None:
    resolution = TaxonomyOutcome(
        outcome="resolved",
        original_input="Synthetic bird",
        normalised_input="synthetic bird",
        scientific_name="Avis synthetica",
        canonical_name="Avis synthetica",
        accepted_taxon_key=123,
        rank="SPECIES",
        taxonomic_status="ACCEPTED",
        match_method="synthetic",
        rationale="Synthetic accepted test taxon.",
    )
    pages = {
        0: [raw_record(1, uncertainty=500), raw_record(2, uncertainty=2_000)],
        2: [raw_record(2, uncertainty=2_000), raw_record(3, uncertainty=None)],
    }

    def fetcher(url: str, params: dict[str, Any] | None) -> tuple[Any, str]:
        supplied = params or {}
        offset = supplied["offset"]
        return (
            {
                "count": 4,
                "results": pages[offset],
                "endOfRecords": offset == 2,
            },
            f"synthetic://occurrence?offset={offset}",
        )

    result = retrieve_occurrences(
        resolution,
        london_boundary=SQUARE_LONDON,
        policy=RetrievalPolicy(page_size=2, max_pages=3, request_budget=2, target_month=1),
        fetch_json=fetcher,
        current_year=2026,
        cell_secret=b"x" * 32,
    )
    counts = result["counts"]
    assert result["retrieval"]["pages_requested"] == 2
    assert result["retrieval"]["year_window"] == [2021, 2026]
    assert result["retrieval"]["seasonal_months"] == [1, 2, 12]
    assert counts["server_match_count"] == 4
    assert counts["sampled_count"] == 4
    assert counts["deduplicated_count"] == 3
    assert counts["duplicates_removed"] == 1
    assert counts["ranking_eligible_count"] == 1
    assert counts["weak_count"] == 1
    assert counts["unknown_uncertainty_count"] == 1
    assert validate_occurrence_counts(counts) == []


def test_provenance_count_validator_rejects_inconsistent_meanings() -> None:
    invalid = {
        "server_match_count": 100,
        "sampled_count": 10,
        "deduplicated_count": 10,
        "duplicates_removed": 2,
        "ranking_eligible_count": 4,
        "weak_count": 1,
        "context_only_count": 1,
        "unknown_uncertainty_count": 1,
        "rejected_count": 2,
        "retained_total_count": 8,
        "rejection_counts_by_reason": {"outside_greater_london_boundary": 2},
    }
    errors = validate_occurrence_counts(invalid)
    assert "sampled_count - duplicates_removed must equal deduplicated_count" in errors
    assert "location-quality counts must sum to retained_total_count" in errors
