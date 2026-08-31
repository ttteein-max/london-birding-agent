"""Validate Phase 0.1 fixtures, generate live candidates, or promote explicitly."""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.feasibility.core import (
    FIXTURE_DIR,
    PROJECT_ROOT,
    STRONG_EVIDENCE_RULE,
    canonical_sha256,
    evaluate_occurrence_fixture,
    load_fixture_bundle,
    validate_provenance,
)
from app.feasibility.live import fetch_postcode_fixture, fetch_weather_fixture, get_json, utc_now
from app.feasibility.occurrence import (
    RetrievalPolicy,
    retrieve_occurrences,
    validate_occurrence_counts,
)
from app.feasibility.spatial import load_london_boundary
from app.feasibility.taxonomy import GBIFBirdNameResolver

MATRIX_PATH = PROJECT_ROOT / "data" / "evaluation" / "phase-0.1-matrix.json"
CANONICAL_FILENAMES = (
    "gbif-species.json",
    "gbif-occurrences-london.json",
    "postcodes-sw11-4nj.json",
    "open-meteo-london.json",
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _aggregate_counts(results: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "server_match_count",
        "sampled_count",
        "deduplicated_count",
        "duplicates_removed",
        "ranking_eligible_count",
        "weak_count",
        "context_only_count",
        "unknown_uncertainty_count",
        "rejected_count",
        "retained_total_count",
    )
    aggregate = {key: 0 for key in keys}
    rejection_counts: Counter[str] = Counter()
    for result in results:
        counts = result.get("counts")
        if not counts:
            continue
        for key in keys:
            aggregate[key] += counts[key]
        rejection_counts.update(counts["rejection_counts_by_reason"])
    aggregate["rejection_counts_by_reason"] = dict(sorted(rejection_counts.items()))
    return aggregate


def build_live_candidate(output_dir: Path, matrix_path: Path = MATRIX_PATH) -> Path:
    """Write versioned candidates only; never replace canonical fixtures."""

    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    boundary = load_london_boundary()
    resolver = GBIFBirdNameResolver(get_json)
    taxonomy_results: list[dict[str, Any]] = []
    occurrence_results: list[dict[str, Any]] = []
    taxonomy_request_urls: list[str] = []
    occurrence_request_urls: list[str] = []
    for entry in matrix["inputs"]:
        resolution = resolver.resolve(entry["input"])
        taxonomy_request_urls.extend(resolution.request_urls)
        taxonomy_result = {
            "scenario": entry["scenario"],
            "reason": entry["reason"],
            **resolution.model_dump(mode="json", exclude={"request_urls"}),
        }
        taxonomy_results.append(taxonomy_result)
        occurrence = retrieve_occurrences(
            resolution,
            london_boundary=boundary,
            policy=RetrievalPolicy(target_month=entry["target_month"]),
            fetch_json=get_json,
        )
        occurrence["scenario"] = entry["scenario"]
        occurrence["scenario_reason"] = entry["reason"]
        occurrence["target_month"] = entry["target_month"]
        occurrence_results.append(occurrence)
        if occurrence.get("retrieval"):
            occurrence_request_urls.extend(occurrence["retrieval"]["request_urls"])

    retrieved_at = utc_now()
    outcome_counts = Counter(item["outcome"] for item in taxonomy_results)
    taxonomy_payload = {
        "matrix_description": matrix["description"],
        "matrix_schema_version": matrix["schema_version"],
        "results": taxonomy_results,
    }
    taxonomy_fixture = {
        "schema_version": 2,
        "payload": taxonomy_payload,
        "provenance": {
            "source_name": "GBIF Species API",
            "source_url": "https://www.gbif.org/developer/species",
            "endpoint": "https://api.gbif.org/v1/species/match and /species/search",
            "request_parameters": {
                "request_count": len(taxonomy_request_urls),
                "requests": taxonomy_request_urls,
                "matrix_file": str(matrix_path.relative_to(PROJECT_ROOT)),
            },
            "retrieved_at_utc": retrieved_at,
            "snapshot_version": retrieved_at[:10],
            "licence": "GBIF API terms; taxonomy source datasets retain their own terms",
            "attribution": "GBIF.org",
            "checksum_sha256": canonical_sha256(taxonomy_payload),
            "record_counts": {
                "before": len(matrix["inputs"]),
                "after": len(taxonomy_results),
                "outcomes": dict(sorted(outcome_counts.items())),
            },
            "filtering_rules": [
                "Resolve the actual normalised user text through GBIF",
                "Restrict candidates to accepted species-rank Aves taxa",
                "Require human selection for multiple reasonable candidates",
            ],
            "known_limitations": [
                "GBIF common-name coverage and result ordering change over time",
                "The evaluation matrix is a regression sample, not a species whitelist",
            ],
        },
    }

    aggregate_counts = _aggregate_counts(occurrence_results)
    occurrence_payload = {
        "matrix_description": matrix["description"],
        "boundary": {
            "artifact": "data/boundaries/greater-london-2026-08-31.geojson",
            "final_inclusion_test": "deterministic local point-in-polygon",
        },
        "metric_grid": {
            "crs": "EPSG:27700",
            "cell_size_metres": 1000,
            "stored_reference": "run-specific HMAC; secret not persisted",
        },
        "default_retrieval_policy": {
            "latest_complete_years_plus_current": 5,
            "seasonal_window": "target month ±1 month",
            "page_size": 300,
            "maximum_pages_per_taxon": 3,
            "request_budget_per_taxon": 3,
            "early_stop": "strong evidence gate reached or server results exhausted",
        },
        "results": occurrence_results,
    }
    occurrence_fixture = {
        "schema_version": 2,
        "payload": occurrence_payload,
        "provenance": {
            "source_name": "GBIF Occurrence Search API",
            "source_url": "https://www.gbif.org/developer/occurrence",
            "endpoint": "https://api.gbif.org/v1/occurrence/search",
            "request_parameters": {
                "request_count": len(occurrence_request_urls),
                "requests": occurrence_request_urls,
                "page_size": 300,
                "maximum_pages_per_taxon": 3,
                "request_budget_per_taxon": 3,
            },
            "retrieved_at_utc": retrieved_at,
            "snapshot_version": retrieved_at[:10],
            "licence": "Each occurrence record and media object retains its own licence field",
            "attribution": "GBIF.org and the contributing datasets named by datasetKey",
            "checksum_sha256": canonical_sha256(occurrence_payload),
            "record_counts": aggregate_counts,
            "filtering_rules": [
                "Use the bounding envelope only to limit remote results",
                "Require local point-in-polygon inclusion in the versioned Greater London boundary",
                "Exclude fatal geospatial issues and non-PRESENT records",
                "Only uncertainty ≤1,000 m is eligible for EPSG:27700 1 km spatial ranking",
                "Retain weak, context-only and unknown-uncertainty evidence only at their permitted use level",
                "Deduplicate deterministically before sanitisation",
                "Never persist occurrence coordinates or the HMAC cell secret",
            ],
            "known_limitations": [
                "Server match counts describe bounded year/month queries, not abundance",
                "At most 900 records per resolved taxon are sampled",
                "Live data, uncertainty fields and dataset composition change over time",
                "Sparse records cannot support hotspot or guaranteed-sighting claims",
                "Record licences do not automatically license linked media",
            ],
        },
    }
    postcode = fetch_postcode_fixture()
    weather = fetch_weather_fixture(
        postcode["payload"]["latitude"],
        postcode["payload"]["longitude"],
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, value in (
        ("gbif-species.json", taxonomy_fixture),
        ("gbif-occurrences-london.json", occurrence_fixture),
        ("postcodes-sw11-4nj.json", postcode),
        ("open-meteo-london.json", weather),
    ):
        _write_json(output_dir / filename, value)
    manifest = {
        "candidate_created_at_utc": retrieved_at,
        "canonical_replaced": False,
        "files": list(CANONICAL_FILENAMES),
        "promotion_command": f"python -m scripts.phase0_feasibility --promote-candidate {output_dir}",
    }
    _write_json(output_dir / "candidate-manifest.json", manifest)
    return output_dir


def promote_candidate(candidate_dir: Path) -> None:
    """Explicit promotion boundary; validation alone never calls this function."""

    for filename in CANONICAL_FILENAMES:
        source = candidate_dir / filename
        if not source.is_file():
            raise RuntimeError(f"candidate is missing {filename}")
    candidate_occurrence = json.loads(
        (candidate_dir / "gbif-occurrences-london.json").read_text(encoding="utf-8")
    )
    if candidate_occurrence.get("schema_version") != 2:
        raise RuntimeError("only schema-version 2 occurrence candidates can be promoted")
    if validate_occurrence_counts(candidate_occurrence["provenance"]["record_counts"]):
        raise RuntimeError("candidate occurrence counts are inconsistent")
    for filename in CANONICAL_FILENAMES:
        shutil.copy2(candidate_dir / filename, FIXTURE_DIR / filename)


def live_arbitrary_check(user_input: str, target_month: int) -> dict[str, Any]:
    resolver = GBIFBirdNameResolver(get_json)
    resolution = resolver.resolve(user_input)
    result = retrieve_occurrences(
        resolution,
        london_boundary=load_london_boundary(),
        policy=RetrievalPolicy(target_month=target_month),
        fetch_json=get_json,
    )
    # Keep the command output compact and never print occurrence-level references.
    return {
        "taxonomy": resolution.model_dump(mode="json", exclude={"request_urls", "candidates"}),
        "evidence_outcome": result["evidence_outcome"],
        "counts": result.get("counts"),
        "dataset_diversity": result.get("dataset_diversity"),
        "retrieval": {
            key: value
            for key, value in (result.get("retrieval") or {}).items()
            if key != "request_urls"
        }
        or None,
    }


def validate_and_report() -> int:
    fixtures = load_fixture_bundle()
    failures: list[str] = []
    for name, fixture in fixtures.items():
        failures.extend(
            f"{name}: {error}"
            for error in validate_provenance(
                fixture["provenance"], schema_version=fixture["schema_version"]
            )
        )
        if canonical_sha256(fixture["payload"]) != fixture["provenance"]["checksum_sha256"]:
            failures.append(f"{name}: dated snapshot payload checksum mismatch")
    if fixtures["taxonomy"]["schema_version"] != 2:
        failures.append("taxonomy: canonical fixture must use schema version 2")
    if fixtures["occurrences"]["schema_version"] != 2:
        failures.append("occurrences: canonical fixture must use schema version 2")

    taxonomy_results = fixtures["taxonomy"]["payload"].get("results", [])
    occurrence_results = fixtures["occurrences"]["payload"].get("results", [])
    if len(taxonomy_results) < 12 or len(occurrence_results) < 12:
        failures.append("evaluation matrix must contain at least 12 inputs")
    taxonomy_outcomes = Counter(item.get("outcome") for item in taxonomy_results)
    evidence_outcomes = Counter(item.get("evidence_outcome") for item in occurrence_results)
    for expected in ("resolved", "human_selection_required", "taxon_not_found"):
        if not taxonomy_outcomes[expected]:
            failures.append(f"taxonomy: missing {expected} behaviour")
    for expected in (
        "strong_map_evidence",
        "limited_contextual_evidence",
        "human_selection_required",
        "taxon_not_found",
    ):
        if not evidence_outcomes[expected]:
            failures.append(f"occurrences: missing {expected} behaviour")
    for result in occurrence_results:
        if result.get("counts"):
            failures.extend(
                f"{result['input']}: {error}"
                for error in validate_occurrence_counts(result["counts"])
            )

    summaries = [evaluate_occurrence_fixture(item) for item in occurrence_results]
    print("MODE: deterministic offline canonical snapshots (no network or API keys)")
    print(f"STRONG EVIDENCE RULE: {json.dumps(STRONG_EVIDENCE_RULE, sort_keys=True)}")
    print(json.dumps({"results": summaries}, indent=2, ensure_ascii=False))
    if failures:
        print(json.dumps({"status": "failed", "errors": failures}, indent=2))
        return 1
    print(
        json.dumps(
            {
                "status": "passed",
                "taxonomy_outcomes": taxonomy_outcomes,
                "evidence_outcomes": evidence_outcomes,
            },
            default=dict,
        )
    )
    return 0


def _default_candidate_dir() -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return PROJECT_ROOT / "data" / "candidates" / timestamp


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-refresh", action="store_true", help="Write live candidate fixtures; never overwrite canonical fixtures")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--promote-candidate", type=Path, help="Explicitly replace canonical fixtures from a validated candidate directory")
    parser.add_argument("--live-check", metavar="BIRD_NAME", help="Run one arbitrary live resolver and occurrence check without writing fixtures")
    parser.add_argument("--target-month", type=int, default=6)
    args = parser.parse_args()
    selected = sum(bool(value) for value in (args.live_refresh, args.promote_candidate, args.live_check))
    if selected > 1:
        parser.error("choose only one of --live-refresh, --promote-candidate or --live-check")
    if args.live_refresh:
        output = build_live_candidate(args.output_dir or _default_candidate_dir())
        print(f"WROTE LIVE CANDIDATE {output}")
        return
    if args.promote_candidate:
        promote_candidate(args.promote_candidate)
        print(f"PROMOTED CANDIDATE {args.promote_candidate}")
        raise SystemExit(validate_and_report())
    if args.live_check:
        print(json.dumps(live_arbitrary_check(args.live_check, args.target_month), indent=2, ensure_ascii=False))
        return
    raise SystemExit(validate_and_report())


if __name__ == "__main__":
    main()
