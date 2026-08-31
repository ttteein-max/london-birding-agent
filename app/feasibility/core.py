"""Deterministic evaluation logic shared by offline and live Phase 0 checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = PROJECT_ROOT / "data" / "fixtures"

STRONG_EVIDENCE_RULE = {
    "minimum_ranking_eligible_records": 50,
    "minimum_spatial_cells_1km": 5,
    "minimum_datasets": 2,
}
MAP_VIABILITY_RULE = STRONG_EVIDENCE_RULE

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


def validate_provenance(
    provenance: dict[str, Any],
    *,
    schema_version: int | None = None,
) -> list[str]:
    """Return missing or malformed provenance fields; an empty list is valid."""

    errors = [
        f"missing provenance field: {name}"
        for name in sorted(REQUIRED_PROVENANCE_FIELDS.difference(provenance))
    ]
    counts = provenance.get("record_counts")
    if not isinstance(counts, dict):
        errors.append("record_counts must be an object")
    elif schema_version == 2 and "server_match_count" in counts:
        from app.feasibility.occurrence import validate_occurrence_counts

        errors.extend(validate_occurrence_counts(counts))
    elif not {"before", "after"}.issubset(counts):
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
    """Summarise a schema-v2 occurrence scenario without reclassifying live data."""

    records = taxon.get("records", [])
    counts = taxon.get("counts")
    taxonomy = taxon.get("taxonomy", {})
    return {
        "scenario": taxon.get("scenario"),
        "input": taxon["input"],
        "scientific_name": taxonomy.get("scientific_name"),
        "taxon_key": taxonomy.get("accepted_taxon_key"),
        "server_match_count": counts.get("server_match_count") if counts else None,
        "sampled_count": counts.get("sampled_count") if counts else 0,
        "ranking_eligible_count": counts.get("ranking_eligible_count") if counts else 0,
        "retained_total_count": counts.get("retained_total_count") if counts else 0,
        "spatial_cells_1km": len(
            {
                record["spatial_cell_1km_ref"]
                for record in records
                if record.get("ranking_eligible")
            }
        ),
        "dataset_count": (taxon.get("dataset_diversity") or {}).get("ranking_dataset_count", 0),
        "status": taxon["evidence_outcome"],
    }


def load_fixture_bundle() -> dict[str, Any]:
    return {
        "taxonomy": load_json(FIXTURE_DIR / "gbif-species.json"),
        "occurrences": load_json(FIXTURE_DIR / "gbif-occurrences-london.json"),
        "postcode": load_json(FIXTURE_DIR / "postcodes-sw11-4nj.json"),
        "weather": load_json(FIXTURE_DIR / "open-meteo-london.json"),
    }
