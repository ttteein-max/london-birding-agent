"""Run deterministic Phase 0 checks or explicitly refresh public-API fixtures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.feasibility.core import (
    FIXTURE_DIR,
    MAP_VIABILITY_RULE,
    canonical_sha256,
    evaluate_occurrence_fixture,
    load_fixture_bundle,
    validate_provenance,
)
from app.feasibility.live import (
    fetch_occurrence_fixture,
    fetch_postcode_fixture,
    fetch_taxonomy_fixture,
    fetch_weather_fixture,
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def refresh_live_fixtures() -> None:
    taxonomy = fetch_taxonomy_fixture()
    occurrences = fetch_occurrence_fixture(taxonomy)
    postcode = fetch_postcode_fixture()
    weather = fetch_weather_fixture(
        postcode["payload"]["latitude"],
        postcode["payload"]["longitude"],
    )
    for filename, value in (
        ("gbif-species.json", taxonomy),
        ("gbif-occurrences-london.json", occurrences),
        ("postcodes-sw11-4nj.json", postcode),
        ("open-meteo-london.json", weather),
    ):
        _write_json(FIXTURE_DIR / filename, value)


def validate_and_report() -> int:
    fixtures = load_fixture_bundle()
    failures: list[str] = []
    for name, fixture in fixtures.items():
        failures.extend(f"{name}: {error}" for error in validate_provenance(fixture["provenance"]))
        actual = canonical_sha256(fixture["payload"])
        if actual != fixture["provenance"]["checksum_sha256"]:
            failures.append(f"{name}: payload checksum mismatch")

    ambiguity = fixtures["taxonomy"]["payload"]["ambiguous"]
    if ambiguity["resolution_status"] != "human_selection_required" or len(ambiguity["candidates"]) < 2:
        failures.append("taxonomy: ambiguous robin candidate list is not preserved")

    results = [
        evaluate_occurrence_fixture(taxon)
        for taxon in fixtures["occurrences"]["payload"]["taxa"]
    ]
    viable = sum(result["status"] == "map_viable" for result in results)
    safe_failures = sum(result["status"] == "insufficient_evidence" for result in results)
    if viable < 3:
        failures.append(f"occurrences: expected at least 3 viable taxa, got {viable}")
    if safe_failures < 1:
        failures.append("occurrences: expected at least one safe insufficient-evidence result")

    print("MODE: deterministic offline fixtures (no network or API keys)")
    print(f"MAP VIABILITY RULE: {json.dumps(MAP_VIABILITY_RULE, sort_keys=True)}")
    print(json.dumps({"taxa": results, "ambiguity": ambiguity}, indent=2, ensure_ascii=False))
    if failures:
        print(json.dumps({"status": "failed", "errors": failures}, indent=2))
        return 1
    print(json.dumps({"status": "passed", "viable_taxa": viable, "safe_failures": safe_failures}))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-refresh",
        action="store_true",
        help="Explicitly call GBIF, Postcodes.io and Open-Meteo and replace fixtures",
    )
    args = parser.parse_args()
    if args.live_refresh:
        print("MODE: explicit live refresh; public APIs will be called sequentially")
        refresh_live_fixtures()
    raise SystemExit(validate_and_report())


if __name__ == "__main__":
    main()
