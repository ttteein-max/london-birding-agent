"""Explicitly retrieve a versioned Greater London administrative boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.feasibility.core import PROJECT_ROOT, file_sha256
from app.feasibility.live import get_json, utc_now

BOUNDARY_DIR = PROJECT_ROOT / "data" / "boundaries"
ENDPOINT = "https://nominatim.openstreetmap.org/search"


def generate_boundary(output_dir: Path = BOUNDARY_DIR) -> tuple[Path, Path]:
    response, request_url = get_json(
        ENDPOINT,
        {
            "q": "Greater London, England, United Kingdom",
            "format": "geojson",
            "polygon_geojson": 1,
            "addressdetails": 1,
            "limit": 1,
            "countrycodes": "gb",
        },
    )
    features = response.get("features") or []
    if len(features) != 1:
        raise RuntimeError(f"expected one Greater London boundary result, got {len(features)}")
    source = features[0]
    properties = source.get("properties") or {}
    if properties.get("osm_type") != "relation" or properties.get("osm_id") != 175342:
        raise RuntimeError(
            "Nominatim result was not the expected OSM Greater London relation 175342"
        )
    geometry = source.get("geometry") or {}
    if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        raise RuntimeError("Greater London result did not contain polygon geometry")

    retrieved_at = utc_now()
    version = retrieved_at[:10]
    output_dir.mkdir(parents=True, exist_ok=True)
    boundary_path = output_dir / f"greater-london-{version}.geojson"
    provenance_path = output_dir / f"greater-london-{version}.provenance.json"
    artifact = {
        "type": "FeatureCollection",
        "name": "Greater London administrative boundary",
        "snapshot_version": version,
        "attribution": "© OpenStreetMap contributors",
        "features": [
            {
                "type": "Feature",
                "id": "osm-relation-175342",
                "properties": {
                    "name": "Greater London",
                    "osm_type": "relation",
                    "osm_id": 175342,
                    "boundary": "administrative",
                    "admin_level": "5",
                },
                "geometry": geometry,
            }
        ],
    }
    boundary_path.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    provenance = {
        "source_name": "OpenStreetMap Greater London boundary via Nominatim",
        "source_url": "https://www.openstreetmap.org/relation/175342",
        "endpoint": ENDPOINT,
        "request_parameters": {"request_url": request_url, "osm_relation": 175342},
        "retrieved_at_utc": retrieved_at,
        "snapshot_version": version,
        "licence": "Open Database Licence (ODbL) 1.0",
        "attribution": "© OpenStreetMap contributors",
        "checksum_sha256": file_sha256(boundary_path),
        "record_counts": {"before": len(features), "after": 1},
        "filtering_rules": [
            "Require OSM relation 175342",
            "Require Polygon or MultiPolygon geometry",
            "Retain boundary geometry and compact administrative identifiers only",
        ],
        "known_limitations": [
            "The boundary is a dated OpenStreetMap snapshot and may change",
            "Boundary inclusion does not establish site access",
        ],
        "regeneration_command": "python -m scripts.generate_london_boundary --live-refresh",
    }
    provenance_path.write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return boundary_path, provenance_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-refresh", action="store_true", help="Required explicit network opt-in")
    parser.add_argument("--output-dir", type=Path, default=BOUNDARY_DIR)
    args = parser.parse_args()
    if not args.live_refresh:
        parser.error("boundary generation requires explicit --live-refresh")
    boundary, provenance = generate_boundary(args.output_dir)
    print(f"WROTE {boundary}")
    print(f"WROTE {provenance}")


if __name__ == "__main__":
    main()
