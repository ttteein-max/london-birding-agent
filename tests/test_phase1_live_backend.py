"""Opt-in live smoke test for the typed Phase 1 backend."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.biodiversity.models import (
    BackendResult,
    EvidenceOutcome,
    ExpeditionPlanStatus,
    ExpeditionRequest,
    LocationStatus,
    PublicSiteSearchStatus,
    TaxonStatus,
)
from app.biodiversity.orchestration import run_expedition_backend

pytestmark = pytest.mark.live


def test_live_phase1_backend_smoke_without_permanent_count_assertions() -> None:
    requested_date = datetime.now(ZoneInfo("Europe/London")).date()
    request = ExpeditionRequest(
        bird_input="Common woodpigeon",
        postcode="SW11 4NJ",
        target_local_date=requested_date,
        duration_hours=2,
        search_radius_km=5,
    )

    result = run_expedition_backend(request, mode="live")

    assert isinstance(result, BackendResult)
    assert result.bundle.request == request
    assert result.bundle.location.status == LocationStatus.resolved
    assert result.bundle.taxon.status == TaxonStatus.resolved
    assert result.bundle.occurrence.outcome in {
        EvidenceOutcome.strong_map_evidence,
        EvidenceOutcome.limited_contextual_evidence,
        EvidenceOutcome.insufficient_evidence,
    }
    assert isinstance(result.bundle.site_search.status, PublicSiteSearchStatus)
    public_json = result.model_dump_json()
    assert '"decimalLatitude":' not in public_json
    assert '"decimalLongitude":' not in public_json
    if result.plan.status == ExpeditionPlanStatus.candidate_plan_ready:
        assert result.bundle.site_search.status == PublicSiteSearchStatus.success
        assert result.bundle.occurrence.safe_map_cells
        assert all(
            candidate.associated_safe_cell_ids
            for candidate in result.bundle.site_search.candidates
        )
