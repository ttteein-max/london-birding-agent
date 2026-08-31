"""Phase 1.1 site-grounding gate regressions."""

from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

from app.biodiversity.models import (
    ExpeditionPlanStatus,
    ExpeditionRequest,
    PublicSiteSearchStatus,
    SiteEvidenceTier,
    SiteSearchAction,
    SourceFailure,
    ToolErrorCode,
)
from app.biodiversity.orchestration import BackendDependencies, ExpeditionBackend
from app.biodiversity.repositories import (
    FixtureOccurrenceRepository,
    FixturePostcodeRepository,
    FixtureTaxonomyRepository,
    FixtureWeatherRepository,
    SnapshotGreenSpaceRepository,
)


def request(
    bird: str, month: int, *, search_radius_km: float = 5.0
) -> ExpeditionRequest:
    return ExpeditionRequest(
        bird_input=bird,
        postcode="SW11 4NJ",
        target_local_date=date(2026, month, 15),
        duration_hours=2,
        search_radius_km=search_radius_km,
    )


def backend(
    green_spaces=SnapshotGreenSpaceRepository(),
    occurrences=FixtureOccurrenceRepository(),
) -> ExpeditionBackend:
    return ExpeditionBackend(
        BackendDependencies(
            postcode=FixturePostcodeRepository(),
            taxonomy=FixtureTaxonomyRepository(),
            occurrences=occurrences,
            weather=FixtureWeatherRepository(),
            green_spaces=green_spaces,
        )
    )


def test_common_woodpigeon_candidates_are_grounded_in_safe_cells() -> None:
    result = backend().run(request("Common woodpigeon", 6))
    assert result.plan.status == ExpeditionPlanStatus.candidate_plan_ready
    assert result.bundle.site_search.status == PublicSiteSearchStatus.success
    assert result.bundle.site_search.candidates
    allowed = {cell.cell_id for cell in result.bundle.occurrence.safe_map_cells}
    assert allowed
    assert all(
        candidate.associated_safe_cell_ids
        and set(candidate.associated_safe_cell_ids).issubset(allowed)
        and candidate.evidence_tier == SiteEvidenceTier.directly_grounded
        and candidate.source_geometry_type in {"Polygon", "MultiPolygon"}
        and candidate.distance_to_nearest_safe_cell_km == 0
        for candidate in result.bundle.site_search.candidates
    )
    assert result.bundle.site_search.contextual_sites
    assert all(
        candidate.evidence_tier
        in {SiteEvidenceTier.nearby_context, SiteEvidenceTier.ungrounded}
        and not candidate.associated_safe_cell_ids
        for candidate in result.bundle.site_search.contextual_sites
    )


@pytest.mark.parametrize(
    ("bird", "month", "expected_safe_cells"),
    [("House sparrow", 5, 8), ("Turdus iliacus", 1, 7)],
)
def test_refreshed_strong_fixtures_have_same_snapshot_safe_cells(
    bird: str, month: int, expected_safe_cells: int
) -> None:
    result = backend().run(request(bird, month))
    assert result.bundle.occurrence.outcome.value == "strong_map_evidence"
    assert len(result.bundle.occurrence.safe_map_cells) == expected_safe_cells
    assert result.bundle.site_search.status == PublicSiteSearchStatus.success
    assert result.bundle.site_search.candidates
    assert result.plan.status == ExpeditionPlanStatus.candidate_plan_ready


class NoSafeCellOccurrenceRepository:
    reference_year = 2026

    def __init__(self) -> None:
        self.fixture = FixtureOccurrenceRepository()

    def search(self, resolution, *, target_month: int):
        result = deepcopy(self.fixture.search(resolution, target_month=target_month))
        result["safe_map_cells"] = []
        return result


def test_synthetic_strong_evidence_without_safe_cells_is_still_suppressed() -> None:
    result = backend(occurrences=NoSafeCellOccurrenceRepository()).run(
        request("House sparrow", 5)
    )
    assert (
        result.bundle.site_search.status == PublicSiteSearchStatus.safe_map_unavailable
    )
    assert result.bundle.site_search.candidates == []
    assert result.bundle.site_search.contextual_sites
    assert all(
        site.evidence_tier == SiteEvidenceTier.ungrounded
        for site in result.bundle.site_search.contextual_sites
    )
    assert result.plan.status == ExpeditionPlanStatus.cannot_recommend_sites
    assert result.plan.suggested_actions == [SiteSearchAction.refresh_live_evidence]


def test_tiny_radius_is_no_suitable_sites_not_plan_ready() -> None:
    result = backend().run(request("Common woodpigeon", 6, search_radius_km=0.001))
    assert result.bundle.occurrence.safe_map_cells
    assert (
        result.bundle.site_search.status
        == PublicSiteSearchStatus.no_suitable_public_sites
    )
    assert result.bundle.site_search.candidates == []
    assert result.plan.status == ExpeditionPlanStatus.cannot_recommend_sites
    assert ToolErrorCode.no_suitable_public_sites in {
        error.code for error in result.bundle.tool_errors
    }
    assert "no_suitable_public_sites" in {
        constraint.code for constraint in result.bundle.constraints
    }


class FailingGreenSpaceRepository:
    def snapshot(self):
        raise SourceFailure(
            ToolErrorCode.missing_or_corrupt_fixture,
            "Synthetic site snapshot failure.",
            source="synthetic green-space source",
        )


class FailingOccurrenceRepository:
    reference_year = 2026

    def search(self, resolution, *, target_month: int):
        raise SourceFailure(
            ToolErrorCode.source_unavailable,
            "Synthetic occurrence source failure.",
            source="synthetic occurrence source",
            retryable=True,
        )


def test_occurrence_source_failure_has_only_retry_occurrence_action() -> None:
    result = backend(occurrences=FailingOccurrenceRepository()).run(
        request("Common woodpigeon", 6)
    )
    assert result.bundle.occurrence.outcome.value == "source_unavailable"
    assert result.bundle.site_search.candidates == []
    assert result.plan.suggested_actions == [SiteSearchAction.retry_occurrence_source]


def test_site_source_failure_cannot_be_plan_ready() -> None:
    result = backend(FailingGreenSpaceRepository()).run(request("Common woodpigeon", 6))
    assert result.bundle.site_search.status == PublicSiteSearchStatus.source_unavailable
    assert result.bundle.site_search.candidates == []
    assert result.plan.status == ExpeditionPlanStatus.cannot_recommend_sites
    assert ToolErrorCode.source_unavailable in {
        error.code for error in result.bundle.tool_errors
    }
    assert "site_source_unavailable" in {
        constraint.code for constraint in result.bundle.constraints
    }


def test_safe_map_unavailable_and_no_suitable_sites_are_distinct() -> None:
    safe_map_missing = backend(occurrences=NoSafeCellOccurrenceRepository()).run(
        request("House sparrow", 5)
    )
    radius_empty = backend().run(
        request("Common woodpigeon", 6, search_radius_km=0.001)
    )
    assert (
        safe_map_missing.bundle.site_search.status
        == PublicSiteSearchStatus.safe_map_unavailable
    )
    assert (
        radius_empty.bundle.site_search.status
        == PublicSiteSearchStatus.no_suitable_public_sites
    )
    assert (
        safe_map_missing.bundle.site_search.error.code
        != radius_empty.bundle.site_search.error.code
    )
