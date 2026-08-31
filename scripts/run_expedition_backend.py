"""Run the deterministic Phase 1 biodiversity backend."""

from __future__ import annotations

import argparse
import json
from datetime import date

from pydantic import ValidationError

from app.biodiversity.models import ExpeditionRequest, RainPreference, WGS84Point
from app.biodiversity.orchestration import run_expedition_backend


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    location = parser.add_mutually_exclusive_group(required=True)
    location.add_argument("--postcode")
    location.add_argument(
        "--start-point",
        nargs=2,
        type=float,
        metavar=("LONGITUDE", "LATITUDE"),
    )
    parser.add_argument("--bird", required=True)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--duration-hours", required=True, type=float)
    parser.add_argument("--max-walking-km", type=float)
    parser.add_argument("--avoid-heavy-rain", action="store_true")
    parser.add_argument("--target-month", type=int)
    parser.add_argument("--search-radius-km", type=float, default=5.0)
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument(
        "--compact", action="store_true", help="Print a compact safe summary."
    )
    args = parser.parse_args()
    try:
        request = ExpeditionRequest(
            bird_input=args.bird,
            postcode=args.postcode,
            start_point=(
                WGS84Point(longitude=args.start_point[0], latitude=args.start_point[1])
                if args.start_point
                else None
            ),
            target_local_date=args.date,
            duration_hours=args.duration_hours,
            maximum_walking_distance_km=args.max_walking_km,
            rain_preference=(
                RainPreference.avoid_heavy_rain
                if args.avoid_heavy_rain
                else RainPreference.no_preference
            ),
            target_month_override=args.target_month,
            search_radius_km=args.search_radius_km,
        )
    except ValidationError as exc:
        parser.error(str(exc))
    result = run_expedition_backend(request, mode=args.mode)
    if args.compact:
        occurrence = result.bundle.occurrence
        output = {
            "plan_status": result.plan.status,
            "location_status": result.bundle.location.status,
            "taxon_status": result.bundle.taxon.status,
            "evidence_outcome": result.bundle.evidence_outcome,
            "retained_records": occurrence.counts.retained_total_count if occurrence else 0,
            "ranking_records": occurrence.counts.ranking_eligible_count if occurrence else 0,
            "safe_map_cells": len(occurrence.safe_map_cells) if occurrence else 0,
            "candidate_sites": len(result.bundle.candidate_sites),
            "weather_status": result.bundle.weather.status if result.bundle.weather else None,
            "unresolved_constraints": [
                item.code for item in result.plan.unresolved_constraints
            ],
            "tool_errors": [item.code for item in result.bundle.tool_errors],
        }
    else:
        output = result.model_dump(mode="json")
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
