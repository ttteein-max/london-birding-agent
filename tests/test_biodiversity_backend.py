"""Deterministic fixture-mode Phase 1 end-to-end scenarios."""

from datetime import date

import pytest

from app.biodiversity.models import (
    EvidenceOutcome,
    ExpeditionPlanStatus,
    ExpeditionRequest,
    PublicSiteSearchStatus,
    TaxonStatus,
)
from app.biodiversity.orchestration import run_expedition_backend


def run(bird: str, month: int, **overrides: object):
    values = {
        "bird_input": bird,
        "postcode": "SW11 4NJ",
        "target_local_date": date(2026, month, 15),
        "duration_hours": 2,
    }
    values.update(overrides)
    return run_expedition_backend(ExpeditionRequest(**values))


def test_strong_common_bird_scenario() -> None:
    result = run("Common woodpigeon", 6)
    assert result.bundle.taxon.status == TaxonStatus.resolved
    assert result.bundle.evidence_outcome == EvidenceOutcome.strong_map_evidence
    assert result.plan.status == ExpeditionPlanStatus.candidate_plan_ready
    assert result.bundle.site_search.status == PublicSiteSearchStatus.success
    assert result.bundle.candidate_sites
    assert all(
        candidate.associated_safe_cell_ids
        for candidate in result.bundle.candidate_sites
    )
    assert all(
        "decimalLatitude" not in item.model_dump_json()
        for item in result.bundle.candidate_sites
    )


def test_limited_seasonal_bird_and_date_controls_month() -> None:
    result = run("Common swift", 7)
    assert result.bundle.evidence_outcome == EvidenceOutcome.limited_contextual_evidence
    assert result.bundle.occurrence.seasonal_target_month == 7
    assert result.bundle.occurrence.seasonal_months == [6, 7, 8]
    assert result.bundle.candidate_sites == []
    assert "coordinate_quality_limited" in result.bundle.occurrence.quality.warnings


def test_ambiguous_bird_requires_selection() -> None:
    result = run("robin", 1)
    assert result.bundle.taxon.status == TaxonStatus.human_selection_required
    assert result.bundle.evidence_outcome == EvidenceOutcome.human_selection_required
    assert len(result.bundle.taxon.candidates) >= 2
    assert result.bundle.candidate_sites == []


def test_valid_bird_with_insufficient_evidence() -> None:
    result = run("Kakapo", 6)
    assert result.bundle.taxon.status == TaxonStatus.resolved
    assert result.bundle.evidence_outcome == EvidenceOutcome.insufficient_evidence
    assert result.plan.status == ExpeditionPlanStatus.cannot_recommend_sites


def test_invalid_bird_name() -> None:
    result = run("londun sky parrott xyz", 6)
    assert result.bundle.taxon.status == TaxonStatus.taxon_not_found
    assert result.bundle.evidence_outcome == EvidenceOutcome.taxon_not_found


def test_valid_postcode_outside_london_is_rejected() -> None:
    result = run("Common woodpigeon", 6, postcode="OX1 1AA")
    assert result.bundle.location.status.value == "outside_supported_area"
    assert result.plan.status == ExpeditionPlanStatus.cannot_recommend_sites
    assert result.bundle.candidate_sites == []


def test_walking_limit_survives_in_structured_plan() -> None:
    result = run("Common woodpigeon", 6, maximum_walking_distance_km=2.5)
    routing = [
        item for item in result.plan.unresolved_constraints if item.code == "routing_not_available"
    ]
    assert len(routing) == 1
    assert "not route-validated" in routing[0].message


@pytest.mark.parametrize(
    ("bird", "month"),
    [("House sparrow", 5), ("Falco subbuteo", 7), ("Turdus iliacus", 1)],
)
def test_scientific_and_common_fixture_paths_are_deterministic(bird: str, month: int) -> None:
    first = run(bird, month)
    second = run(bird, month)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
