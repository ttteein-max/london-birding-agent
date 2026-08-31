"""All-or-nothing candidate fixture promotion tests."""

import json
import shutil

import pytest

from app.feasibility.core import FIXTURE_DIR, file_sha256
from scripts.phase0_feasibility import CANONICAL_FILENAMES, promote_candidate


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
    (candidate / "candidate-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError):
        promote_candidate(candidate)

    assert {
        filename: (FIXTURE_DIR / filename).read_bytes()
        for filename in CANONICAL_FILENAMES
    } == before
