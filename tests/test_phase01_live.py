"""Opt-in public API checks. Run explicitly with: pytest -m live -q."""

from __future__ import annotations

import json

import pytest

from app.feasibility.core import PROJECT_ROOT
from scripts.validate_evidence_fixtures import build_live_candidate, live_arbitrary_check

pytestmark = pytest.mark.live


def test_live_expanded_matrix_writes_candidate_not_canonical(tmp_path) -> None:
    canonical_before = (PROJECT_ROOT / "data" / "fixtures" / "gbif-species.json").read_bytes()
    output = build_live_candidate(tmp_path / "candidate")
    taxonomy = json.loads((output / "gbif-species.json").read_text())
    assert len(taxonomy["payload"]["results"]) >= 12
    assert (PROJECT_ROOT / "data" / "fixtures" / "gbif-species.json").read_bytes() == canonical_before


def test_live_arbitrary_bird_outside_matrix() -> None:
    result = live_arbitrary_check("Blue tit", 4)
    assert result["taxonomy"]["outcome"] == "resolved"
    assert result["taxonomy"]["canonical_name"] == "Cyanistes caeruleus"
    assert result["evidence_outcome"] in {
        "strong_map_evidence",
        "limited_contextual_evidence",
        "insufficient_evidence",
    }
