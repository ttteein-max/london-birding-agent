"""Strict Phase 1 domain contract tests."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.biodiversity.models import (
    ExpeditionRequest,
    ExpeditionPlan,
    OccurrenceCounts,
    OccurrenceEvidence,
    PublicSiteSearchResult,
    PublicSiteSearchStatus,
    EvidenceOutcome,
    EvidenceQualitySummary,
    SafeMapGeometry,
    StrictModel,
    WGS84Point,
)


def test_every_phase1_domain_model_forbids_unexpected_fields() -> None:
    assert StrictModel.__subclasses__()
    assert all(model.model_config.get("extra") == "forbid" for model in StrictModel.__subclasses__())


def test_site_search_and_plan_statuses_are_typed_enums() -> None:
    with pytest.raises(ValidationError):
        PublicSiteSearchResult(
            candidates=[], searched_radius_km=5, status="legacy_ok"
        )
    with pytest.raises(ValidationError):
        PublicSiteSearchResult(
            candidates=[],
            searched_radius_km=5,
            status=PublicSiteSearchStatus.success,
        )
    with pytest.raises(ValidationError):
        ExpeditionPlan(
            status="maybe_ready",
            target_bird="Avis test",
            target_date=date(2026, 6, 15),
            evidence_explanation="Invalid status should fail.",
        )


def test_request_requires_exactly_one_location_and_forbids_extra_fields() -> None:
    request = ExpeditionRequest(
        bird_input="  Common swift  ",
        postcode="SW11 4NJ",
        target_local_date=date(2026, 7, 1),
        duration_hours=2,
    )
    assert request.bird_input == "Common swift"
    assert request.seasonal_target_month == 7
    with pytest.raises(ValidationError):
        ExpeditionRequest(
            bird_input="Swift",
            target_local_date=date(2026, 7, 1),
            duration_hours=2,
        )
    with pytest.raises(ValidationError):
        ExpeditionRequest(
            bird_input="Swift",
            postcode="SW11 4NJ",
            start_point=WGS84Point(longitude=-0.1, latitude=51.5),
            target_local_date=date(2026, 7, 1),
            duration_hours=2,
        )
    with pytest.raises(ValidationError):
        ExpeditionRequest(
            bird_input="Swift",
            postcode="SW11 4NJ",
            target_local_date=date(2026, 7, 1),
            duration_hours=2,
            surprise=True,
        )


def test_target_month_override_is_explicit() -> None:
    request = ExpeditionRequest(
        bird_input="Turdus iliacus",
        postcode="SW11 4NJ",
        target_local_date=date(2026, 8, 31),
        target_month_override=1,
        duration_hours=1,
    )
    assert request.seasonal_target_month == 1


def test_occurrence_count_semantics_are_validated() -> None:
    with pytest.raises(ValidationError):
        OccurrenceCounts(
            server_match_count=10,
            sampled_count=10,
            deduplicated_count=9,
            exact_duplicates_removed=0,
            possible_duplicates_retained=0,
            retained_total_count=9,
            rejected_count=0,
            ranking_eligible_count=2,
        )


def test_non_strong_evidence_cannot_expose_hotspot_cells() -> None:
    with pytest.raises(ValidationError):
        OccurrenceEvidence(
            outcome=EvidenceOutcome.limited_contextual_evidence,
            reason="below_strong_gate",
            seasonal_target_month=7,
            seasonal_months=[6, 7, 8],
            year_window=(2021, 2026),
            counts=OccurrenceCounts(
                server_match_count=5,
                sampled_count=5,
                deduplicated_count=5,
                exact_duplicates_removed=0,
                possible_duplicates_retained=0,
                retained_total_count=5,
                rejected_count=0,
                ranking_eligible_count=5,
            ),
            quality=EvidenceQualitySummary(
                ranking_eligible_count=5,
                broad_zone_count=0,
                historical_context_count=0,
                unknown_uncertainty_count=0,
                spatial_cell_count=1,
                retained_dataset_count=1,
                ranking_dataset_count=1,
                dominant_ranking_dataset_share=1,
                dominant_basis_of_record_share=1,
            ),
            safe_map_cells=[
                {
                    "cell_id": "bng-1km-530-180",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-0.1, 51.5], [-0.09, 51.5], [-0.09, 51.51], [-0.1, 51.5]]],
                    },
                    "aggregated_record_count": 5,
                    "dataset_count": 1,
                    "evidence_quality": "ranking_eligible_aggregate",
                }
            ],
            deduplication_method="test hierarchy",
        )


def test_safe_polygon_must_be_closed() -> None:
    with pytest.raises(ValidationError):
        SafeMapGeometry(
            coordinates=[[[-0.1, 51.5], [-0.09, 51.5], [-0.09, 51.51], [-0.1, 51.51]]]
        )
