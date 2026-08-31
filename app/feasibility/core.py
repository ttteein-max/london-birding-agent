"""Deterministic evaluation logic shared by offline and live Phase 0 checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = PROJECT_ROOT / "data" / "fixtures"

MAP_VIABILITY_RULE = {
    "minimum_retained_records": 50,
    "minimum_spatial_cells_1km": 5,
    "minimum_datasets": 2,
}

REQUIRED_PROVENANCE_FIELDS = {
    "source_name",
    "source_url",
    "endpoint",
    "request_parameters",
    "retrieved_at_utc",
    "snapshot_version",
    "licence",
    "attribution",
    "checksum_sha256",
    "record_counts",
    "filtering_rules",
    "known_limitations",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_provenance(provenance: dict[str, Any]) -> list[str]:
    """Return missing or malformed provenance fields; an empty list is valid."""

    errors = [
        f"missing provenance field: {name}"
        for name in sorted(REQUIRED_PROVENANCE_FIELDS.difference(provenance))
    ]
    counts = provenance.get("record_counts")
    if not isinstance(counts, dict) or not {"before", "after"}.issubset(counts):
        errors.append("record_counts must contain before and after")
    checksum = provenance.get("checksum_sha256", "")
    if not isinstance(checksum, str) or len(checksum) != 64:
        errors.append("checksum_sha256 must be a 64-character SHA-256 digest")
    if not isinstance(provenance.get("filtering_rules"), list):
        errors.append("filtering_rules must be a list")
    if not isinstance(provenance.get("known_limitations"), list):
        errors.append("known_limitations must be a list")
    return errors


def evaluate_occurrence_fixture(taxon: dict[str, Any]) -> dict[str, Any]:
    """Classify an already sanitised taxon sample using the predeclared gate."""

    records = taxon["records"]
    retained = [record for record in records if record["retained"]]
    cells = {record["spatial_cell_1km"] for record in retained}
    datasets = {record["dataset_key"] for record in retained}
    passed = (
        len(retained) >= MAP_VIABILITY_RULE["minimum_retained_records"]
        and len(cells) >= MAP_VIABILITY_RULE["minimum_spatial_cells_1km"]
        and len(datasets) >= MAP_VIABILITY_RULE["minimum_datasets"]
    )
    return {
        "scenario": taxon["scenario"],
        "input": taxon["input"],
        "scientific_name": taxon["scientific_name"],
        "taxon_key": taxon["taxon_key"],
        "raw_server_count": taxon["raw_server_count"],
        "sampled_count": len(records),
        "retained_count": len(retained),
        "spatial_cells_1km": len(cells),
        "dataset_count": len(datasets),
        "status": "map_viable" if passed else "insufficient_evidence",
    }


def load_fixture_bundle() -> dict[str, Any]:
    return {
        "taxonomy": load_json(FIXTURE_DIR / "gbif-species.json"),
        "occurrences": load_json(FIXTURE_DIR / "gbif-occurrences-london.json"),
        "postcode": load_json(FIXTURE_DIR / "postcodes-sw11-4nj.json"),
        "weather": load_json(FIXTURE_DIR / "open-meteo-london.json"),
    }
