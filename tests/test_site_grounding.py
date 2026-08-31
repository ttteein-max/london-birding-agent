"""Phase 1.1 site-grounding gate regressions."""

from __future__ import annotations

from datetime import date

import pytest

from app.biodiversity.models import (
    ExpeditionPlanStatus,
    ExpeditionRequest,
    PublicSiteSearchStatus,
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


def request(bird: str, month: int, *, search_radius_km: float = 5.0) -> ExpeditionRequest:
    return ExpeditionRequest(
        bird_input=bird,
        postcode="SW11 4NJ",
        target_local_date=date(2026, month, 15),
        duration_hours=2,
        search_radius_km=search_radius_km,
    )


def backend(green_spaces=SnapshotGreenSpaceRepository()) -> ExpeditionBackend:
    return ExpeditionBackend(
        BackendDependencies(
            postcode=FixturePostcodeRepository(),
            taxonomy=FixtureTaxonomyRepository(),
            occurrences=FixtureOccurrenceRepository(),
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
        for candidate in result.bundle.site_search.candidates
    )


@pytest.mark.parametrize(
    ("bird", "month"),
    [("House sparrow", 5), ("Turdus iliacus", 1)],
)
def test_strong_fixture_without_safe_cells_cannot_recommend_sites(
    bird: str, month: int
) -> None:
    result = backend().run(request(bird, month))
    assert result.bundle.occurrence.outcome.value == "strong_map_evidence"
    assert result.bundle.occurrence.safe_map_cells == []
    assert result.bundle.site_search.status == PublicSiteSearchStatus.safe_map_unavailable
    assert result.bundle.site_search.candidates == []
    assert result.plan.status == ExpeditionPlanStatus.cannot_recommend_sites
    assert ToolErrorCode.safe_map_unavailable in {
        error.code for error in result.bundle.tool_errors
    }
    assert "safe_map_unavailable" in {
        constraint.code for constraint in result.bundle.constraints
    }


def test_tiny_radius_is_no_suitable_sites_not_plan_ready() -> None:
    result = backend().run(
        request("Common woodpigeon", 6, search_radius_km=0.001)
    )
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


def test_site_source_failure_cannot_be_plan_ready() -> None:
    result = backend(FailingGreenSpaceRepository()).run(
        request("Common woodpigeon", 6)
    )
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
    safe_map_missing = backend().run(request("House sparrow", 5))
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
    assert safe_map_missing.bundle.site_search.error.code != radius_empty.bundle.site_search.error.code
