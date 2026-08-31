"""Phase 1 occurrence privacy, deduplication and quality tests."""

from __future__ import annotations

import json
from typing import Any

from app.biodiversity.models import (
    EvidenceOutcome,
    ResolvedTaxon,
    TaxonStatus,
)
from app.biodiversity.tools import search_occurrences
from app.feasibility.occurrence import (
    RetrievalPolicy,
    deduplicate_occurrences,
    retrieve_occurrences,
)
from app.feasibility.taxonomy import TaxonomyOutcome


SQUARE_LONDON = {
    "type": "Polygon",
    "coordinates": [[[-1, 51], [1, 51], [1, 52], [-1, 52], [-1, 51]]],
}


def record(
    key: int,
    *,
    longitude: float = -0.2,
    dataset: str = "dataset-a",
    occurrence_id: str | None = None,
    uncertainty: int | None = 100,
) -> dict[str, Any]:
    return {
        "key": key,
        "occurrenceID": occurrence_id,
        "taxonKey": 123,
        "eventDate": f"2025-06-{(key % 27) + 1:02d}T08:{key % 60:02d}:00",
        "year": 2025,
        "month": 6,
        "decimalLongitude": longitude,
        "decimalLatitude": 51.5,
        "coordinateUncertaintyInMeters": uncertainty,
        "datasetKey": dataset,
        "basisOfRecord": "HUMAN_OBSERVATION",
        "occurrenceStatus": "PRESENT",
        "issues": [],
        "license": "https://creativecommons.org/licenses/by/4.0/",
    }


def test_deduplication_hierarchy_and_cautious_retention() -> None:
    repeated_key = [record(1), record(1)]
    deduped, audit = deduplicate_occurrences(repeated_key)
    assert len(deduped) == 1
    assert audit["removed_by_method"] == {"gbif_key": 1}

    same_occurrence = [record(1, occurrence_id="publisher:42"), record(2, occurrence_id="publisher:42")]
    deduped, audit = deduplicate_occurrences(same_occurrence)
    assert len(deduped) == 1
    assert audit["removed_by_method"] == {"occurrence_id": 1}

    same_cell_distinct_observations = [record(10), record(11)]
    deduped, _ = deduplicate_occurrences(same_cell_distinct_observations)
    assert len(deduped) == 2

    no_identifiers = [
        {key: value for key, value in record(20).items() if key not in {"key", "occurrenceID"}},
        {key: value for key, value in record(20).items() if key not in {"key", "occurrenceID"}},
    ]
    deduped, audit = deduplicate_occurrences(no_identifiers)
    assert len(deduped) == 1
    assert audit["removed_by_method"] == {"deterministic_fingerprint": 1}


def test_safe_map_is_aggregated_and_contains_no_occurrence_coordinates() -> None:
    records = [
        record(index, longitude=-0.24 + (index % 5) * 0.025, dataset="a" if index < 45 else "b")
        for index in range(50)
    ]

    def fetcher(url: str, params: dict[str, Any] | None) -> tuple[Any, str]:
        return {"count": 50, "results": records, "endOfRecords": True}, "synthetic://gbif"

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
        rationale="Synthetic accepted taxon.",
    )
    result = retrieve_occurrences(
        resolution,
        london_boundary=SQUARE_LONDON,
        policy=RetrievalPolicy(page_size=50, max_pages=1, request_budget=1, target_month=6),
        fetch_json=fetcher,
        current_year=2026,
        cell_secret=b"x" * 32,
    )
    assert result["evidence_outcome"] == "strong_map_evidence"
    assert len(result["safe_map_cells"]) >= 5
    public_json = json.dumps(result["safe_map_cells"])
    assert "decimalLatitude" not in public_json
    assert "decimalLongitude" not in public_json
    assert "record_ref" not in public_json
    assert all(cell["record_count"] >= 3 for cell in result["safe_map_cells"])
    assert "dataset_concentration_warning" in result["quality_summary"]["warnings"]


def test_one_live_shaped_record_has_isolated_records_reason() -> None:
    one = [record(1, uncertainty=None)]

    def fetcher(url: str, params: dict[str, Any] | None) -> tuple[Any, str]:
        return {"count": 1, "results": one, "endOfRecords": True}, "synthetic://gbif"

    resolution = TaxonomyOutcome(
        outcome="resolved",
        original_input="Rare bird",
        normalised_input="rare bird",
        scientific_name="Avis rara",
        canonical_name="Avis rara",
        accepted_taxon_key=123,
        rank="SPECIES",
        taxonomic_status="ACCEPTED",
        match_method="synthetic",
        rationale="Synthetic accepted taxon.",
    )
    result = retrieve_occurrences(
        resolution,
        london_boundary=SQUARE_LONDON,
        policy=RetrievalPolicy(page_size=1, max_pages=1, request_budget=1, target_month=5),
        fetch_json=fetcher,
        current_year=2026,
        cell_secret=b"x" * 32,
    )
    assert result["evidence_outcome"] == "insufficient_evidence"
    assert result["evidence_reason"] == "isolated_records_only"
    assert result["safe_map_cells"] == []


class StubOccurrenceRepository:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw

    def search(self, resolution: TaxonomyOutcome, *, target_month: int) -> dict[str, Any]:
        assert target_month == 7
        return self.raw


def test_unknown_uncertainty_warning_explains_large_count() -> None:
    stored = []
    for index in range(10):
        stored.append(
            {
                "retained": True,
                "ranking_eligible": index < 2,
                "location_quality": "strong" if index < 2 else "unknown",
                "dataset_key": "a",
                "basis_of_record": "HUMAN_OBSERVATION",
                "spatial_cell_1km_ref": f"cell-{index}" if index < 2 else None,
            }
        )
    raw = {
        "evidence_outcome": "limited_contextual_evidence",
        "records": stored,
        "counts": {
            "server_match_count": 10,
            "sampled_count": 10,
            "deduplicated_count": 10,
            "duplicates_removed": 0,
            "ranking_eligible_count": 2,
            "weak_count": 0,
            "context_only_count": 0,
            "unknown_uncertainty_count": 8,
            "rejected_count": 0,
            "retained_total_count": 10,
        },
        "retrieval": {"seasonal_months": [6, 7, 8], "year_window": [2021, 2026]},
    }
    taxon = ResolvedTaxon(
        status=TaxonStatus.resolved,
        original_input="Synthetic",
        normalised_input="synthetic",
        accepted_taxon_key=123,
        scientific_name="Avis synthetica",
        canonical_name="Avis synthetica",
        rank="SPECIES",
        taxonomic_status="ACCEPTED",
        resolution_method="synthetic",
        rationale="Resolved for test.",
    )
    evidence = search_occurrences(taxon, target_month=7, repository=StubOccurrenceRepository(raw))
    assert evidence.outcome == EvidenceOutcome.limited_contextual_evidence
    assert "coordinate_quality_limited" in evidence.quality.warnings
    assert any("large retained count" in item for item in evidence.limitations)
    assert evidence.safe_map_cells == []
