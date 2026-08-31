"""All-or-nothing candidate fixture promotion tests."""

import json
import shutil

import pytest

from app.feasibility.core import FIXTURE_DIR, canonical_sha256, file_sha256
from scripts.phase0_feasibility import (
    CANONICAL_FILENAMES,
    promote_candidate,
    promote_occurrence_candidate,
)


def write_manifest(candidate) -> None:
    manifest = {
        "candidate_created_at_utc": "2026-08-31T00:00:00Z",
        "canonical_replaced": False,
        "files": list(CANONICAL_FILENAMES),
        "checksums_sha256": {
            filename: file_sha256(candidate / filename)
            for filename in CANONICAL_FILENAMES
        },
        "promotion_command": "test only",
    }
    (candidate / "candidate-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def test_invalid_candidate_cannot_partially_overwrite_canonical_set(tmp_path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    before = {
        filename: (FIXTURE_DIR / filename).read_bytes()
        for filename in CANONICAL_FILENAMES
    }
    for filename in CANONICAL_FILENAMES:
        shutil.copy2(FIXTURE_DIR / filename, candidate / filename)
    invalid_path = candidate / "open-meteo-london.json"
    invalid = json.loads(invalid_path.read_text(encoding="utf-8"))
    invalid["payload"]["daily"].pop("time")
    invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
    write_manifest(candidate)

    with pytest.raises(RuntimeError):
        promote_candidate(candidate)

    assert {
        filename: (FIXTURE_DIR / filename).read_bytes()
        for filename in CANONICAL_FILENAMES
    } == before


def test_occurrence_only_promotion_rejects_canonical_taxonomy_key_drift(
    tmp_path,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    for filename in CANONICAL_FILENAMES:
        shutil.copy2(FIXTURE_DIR / filename, candidate / filename)

    occurrence_path = candidate / "gbif-occurrences-london.json"
    occurrence = json.loads(occurrence_path.read_text(encoding="utf-8"))
    woodpigeon = next(
        item
        for item in occurrence["payload"]["results"]
        if item["input"] == "Common woodpigeon"
    )
    woodpigeon["taxonomy"]["accepted_taxon_key"] += 1
    occurrence["provenance"]["checksum_sha256"] = canonical_sha256(
        occurrence["payload"]
    )
    occurrence_path.write_text(json.dumps(occurrence), encoding="utf-8")
    write_manifest(candidate)

    canonical_before = (FIXTURE_DIR / "gbif-occurrences-london.json").read_bytes()
    with pytest.raises(RuntimeError, match="occurrence-only promotion rejected"):
        promote_occurrence_candidate(candidate)
    assert (
        FIXTURE_DIR / "gbif-occurrences-london.json"
    ).read_bytes() == canonical_before
