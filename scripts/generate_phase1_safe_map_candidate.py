"""Build a reviewed fixture candidate containing only live aggregated safe-map cells."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from app.feasibility.core import (
    FIXTURE_DIR,
    canonical_sha256,
    file_sha256,
    load_fixture_bundle,
)
from app.feasibility.live import get_json, utc_now
from app.feasibility.occurrence import RetrievalPolicy, retrieve_occurrences
from app.feasibility.spatial import load_london_boundary
from app.feasibility.taxonomy import TaxonomyOutcome
from scripts.phase0_feasibility import CANONICAL_FILENAMES, _write_json


def build_candidate(output_dir: Path) -> Path:
    """Refresh aggregates only; fail if the saved bounded sample counts drifted."""

    fixtures = load_fixture_bundle()
    occurrence = fixtures["occurrences"]
    boundary = load_london_boundary()
    coverage_gaps: list[dict[str, object]] = []
    for stored in occurrence["payload"]["results"]:
        if stored.get("evidence_outcome") != "strong_map_evidence":
            stored["safe_map_cells"] = []
            continue
        taxonomy_fields = TaxonomyOutcome.model_fields.keys()
        resolution = TaxonomyOutcome.model_validate(
            {
                key: value
                for key, value in stored["taxonomy"].items()
                if key in taxonomy_fields
            }
        )
        live = retrieve_occurrences(
            resolution,
            london_boundary=boundary,
            policy=RetrievalPolicy(target_month=stored["target_month"]),
            fetch_json=get_json,
            current_year=2026,
        )
        stable_count_fields = (
            "server_match_count",
            "sampled_count",
            "deduplicated_count",
            "ranking_eligible_count",
            "retained_total_count",
        )
        changed_fields = {
            field: {
                "fixture": stored["counts"][field],
                "live": live["counts"][field],
            }
            for field in stable_count_fields
            if live["counts"][field] != stored["counts"][field]
        }
        if changed_fields:
            stored["safe_map_cells"] = []
            coverage_gaps.append(
                {
                    "input": stored["input"],
                    "reason": "bounded_sample_count_drift",
                    "changed_fields": changed_fields,
                }
            )
            continue
        stored["safe_map_cells"] = live["safe_map_cells"]
        stored["quality_summary"] = live["quality_summary"]
        stored["dataset_diversity"] = live["dataset_diversity"]
        stored["evidence_reason"] = live["evidence_reason"]
        stored["retrieval"]["deduplication"] = live["retrieval"]["deduplication"]
        stored["retrieval"]["deduplication_rule"] = live["retrieval"][
            "deduplication_rule"
        ]

    generated_at = utc_now()
    occurrence["payload"]["metric_grid"]["application_safe_map"] = {
        "geometry_crs": "EPSG:4326",
        "aggregation_crs": "EPSG:27700",
        "cell_size_metres": 1000,
        "minimum_records_per_exposed_cell": 3,
        "only_for_outcome": "strong_map_evidence",
        "individual_coordinates_persisted": False,
    }
    occurrence["provenance"]["safe_map_generated_at_utc"] = generated_at
    occurrence["provenance"]["safe_map_coverage_gaps"] = coverage_gaps
    occurrence["provenance"]["checksum_sha256"] = canonical_sha256(
        occurrence["payload"]
    )
    limitations = occurrence["provenance"]["known_limitations"]
    safe_limitation = (
        "Safe-map polygons are aggregated 1 km cells with at least three eligible records; "
        "they are not occurrence locations or sighting predictions"
    )
    if safe_limitation not in limitations:
        limitations.append(safe_limitation)

    output_dir.mkdir(parents=True, exist_ok=False)
    for filename in CANONICAL_FILENAMES:
        if filename == "gbif-occurrences-london.json":
            _write_json(output_dir / filename, occurrence)
        else:
            shutil.copy2(FIXTURE_DIR / filename, output_dir / filename)
    manifest = {
        "candidate_created_at_utc": generated_at,
        "canonical_replaced": False,
        "files": list(CANONICAL_FILENAMES),
        "checksums_sha256": {
            filename: file_sha256(output_dir / filename)
            for filename in CANONICAL_FILENAMES
        },
        "promotion_command": (
            f"python -m scripts.phase0_feasibility --promote-candidate {output_dir}"
        ),
    }
    _write_json(output_dir / "candidate-manifest.json", manifest)
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = build_candidate(args.output_dir)
    occurrence = json.loads(
        (output / "gbif-occurrences-london.json").read_text(encoding="utf-8")
    )
    print(
        json.dumps(
            {
                "candidate": str(output),
                "safe_cell_counts": {
                    item["input"]: len(item.get("safe_map_cells", []))
                    for item in occurrence["payload"]["results"]
                },
                "raw_occurrence_coordinates_persisted": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
