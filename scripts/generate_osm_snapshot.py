"""Explicitly generate the one-off London OSM green-space candidate snapshot."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import time

from app.feasibility.core import PROJECT_ROOT, file_sha256
from app.feasibility.live import USER_AGENT
from app.feasibility.spatial import point_in_geometry

OSM_DIR = PROJECT_ROOT / "data" / "osm"
QUERY_PATH = OSM_DIR / "london-green-spaces.overpassql"
ENDPOINT = "https://overpass-api.de/api/interpreter"
PRIVATE_ACCESS = {"private", "no"}
EXPLICIT_PUBLIC_ACCESS = {"yes", "permissive", "designated", "public"}
RELEVANT_TAGS = (
    "name",
    "name:en",
    "leisure",
    "landuse",
    "boundary",
    "protect_class",
    "access",
    "operator",
    "opening_hours",
    "website",
    "wikidata",
)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _request_overpass(query: str) -> dict[str, Any]:
    body = urlencode({"data": query}).encode("utf-8")
    request = Request(
        ENDPOINT,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urlopen(request, timeout=150) as response:  # noqa: S310 - fixed endpoint
                if response.status != 200:
                    raise RuntimeError(f"Overpass returned HTTP {response.status}")
                return json.load(response)
        except HTTPError as exc:
            last_error = exc
            if exc.code != 429 and not 500 <= exc.code < 600:
                raise RuntimeError(f"Overpass returned HTTP {exc.code}") from exc
        except (TimeoutError, URLError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt < 3:
            time.sleep(2 ** (attempt - 1) * 3)
    raise RuntimeError(
        "Overpass snapshot failed after three bounded attempts. Keep the query and "
        "generator; an OSM-derived GeoJSON or London OSM extract with provenance is "
        f"the acceptable manual fallback. Last error: {last_error}"
    )


def _position(element: dict[str, Any]) -> tuple[float, float] | None:
    if "lat" in element and "lon" in element:
        return element["lon"], element["lat"]
    centre = element.get("center") or {}
    if "lat" in centre and "lon" in centre:
        return centre["lon"], centre["lat"]
    bounds = element.get("bounds") or {}
    if all(
        bounds.get(field) is not None
        for field in ("minlon", "maxlon", "minlat", "maxlat")
    ):
        return (
            (float(bounds["minlon"]) + float(bounds["maxlon"])) / 2,
            (float(bounds["minlat"]) + float(bounds["maxlat"])) / 2,
        )
    return None


def _geometry_centre(geometry: dict[str, Any]) -> tuple[float, float] | None:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "Point" and len(coordinates) == 2:
        return float(coordinates[0]), float(coordinates[1])
    if geometry_type == "Polygon":
        points = coordinates[0] if coordinates else []
    elif geometry_type == "MultiPolygon":
        points = [point for polygon in coordinates for point in polygon[0]]
    else:
        return None
    if not points:
        return None
    return (
        (min(point[0] for point in points) + max(point[0] for point in points)) / 2,
        (min(point[1] for point in points) + max(point[1] for point in points)) / 2,
    )


def _geometry_points(raw: list[dict[str, Any]]) -> list[list[float]]:
    return [
        [round(float(point["lon"]), 6), round(float(point["lat"]), 6)]
        for point in raw
        if point.get("lon") is not None and point.get("lat") is not None
    ]


def _stitch_rings(segments: list[list[list[float]]]) -> list[list[list[float]]]:
    remaining = [segment for segment in segments if len(segment) >= 2]
    rings: list[list[list[float]]] = []
    while remaining:
        ring = remaining.pop(0)
        while ring[0] != ring[-1]:
            match_index = next(
                (
                    index
                    for index, segment in enumerate(remaining)
                    if segment[0] == ring[-1] or segment[-1] == ring[-1]
                ),
                None,
            )
            if match_index is None:
                break
            segment = remaining.pop(match_index)
            if segment[-1] == ring[-1]:
                segment = list(reversed(segment))
            ring.extend(segment[1:])
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


def _relation_geometry(element: dict[str, Any]) -> dict[str, Any] | None:
    outer_segments: list[list[list[float]]] = []
    inner_segments: list[list[list[float]]] = []
    for member in element.get("members") or []:
        points = _geometry_points(member.get("geometry") or [])
        if len(points) < 2:
            continue
        if member.get("role") == "inner":
            inner_segments.append(points)
        else:
            outer_segments.append(points)
    outers = _stitch_rings(outer_segments)
    if not outers:
        return None
    polygons: list[list[list[list[float]]]] = [[outer] for outer in outers]
    for inner in _stitch_rings(inner_segments):
        containing = next(
            (
                polygon
                for polygon in polygons
                if point_in_geometry(
                    inner[0][0],
                    inner[0][1],
                    {"type": "Polygon", "coordinates": polygon},
                )
            ),
            None,
        )
        if containing is not None:
            containing.append(inner)
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def _feature_geometry(element: dict[str, Any]) -> dict[str, Any] | None:
    element_type = element.get("type")
    if element_type == "node":
        position = _position(element)
        return (
            {
                "type": "Point",
                "coordinates": [round(position[0], 6), round(position[1], 6)],
            }
            if position
            else None
        )
    if element_type == "way":
        ring = _geometry_points(element.get("geometry") or [])
        if len(ring) >= 4 and ring[0] == ring[-1]:
            return {"type": "Polygon", "coordinates": [ring]}
        return None
    if element_type == "relation":
        return _relation_geometry(element)
    return None


def process_elements(
    elements: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    features: list[dict[str, Any]] = []
    counts = {
        "raw": len(elements),
        "private_or_no_access": 0,
        "missing_centre": 0,
        "polygon_footprint": 0,
        "point_fallback": 0,
        "retained": 0,
    }
    seen: set[tuple[str, int]] = set()
    for element in elements:
        tags = element.get("tags") or {}
        access = (tags.get("access") or "").lower()
        if access in PRIVATE_ACCESS:
            counts["private_or_no_access"] += 1
            continue
        geometry = _feature_geometry(element)
        position = _position(element) or (
            _geometry_centre(geometry) if geometry is not None else None
        )
        if position is None:
            counts["missing_centre"] += 1
            continue
        if geometry is None:
            geometry = {
                "type": "Point",
                "coordinates": [round(position[0], 6), round(position[1], 6)],
            }
            counts["point_fallback"] += 1
        elif geometry["type"] in {"Polygon", "MultiPolygon"}:
            counts["polygon_footprint"] += 1
        else:
            counts["point_fallback"] += 1
        identity = (str(element.get("type")), int(element.get("id")))
        if identity in seen:
            continue
        seen.add(identity)
        access_certainty = (
            "explicit_public" if access in EXPLICIT_PUBLIC_ACCESS else "unspecified"
        )
        properties = {
            "osm_type": identity[0],
            "osm_id": identity[1],
            "access_certainty": access_certainty,
            "centre_point": [round(position[0], 6), round(position[1], 6)],
            "geometry_fidelity": (
                "polygon_footprint"
                if geometry["type"] in {"Polygon", "MultiPolygon"}
                else "point_fallback"
            ),
            "source_tags": {key: tags[key] for key in RELEVANT_TAGS if key in tags},
        }
        features.append(
            {
                "type": "Feature",
                "id": f"osm-{identity[0]}-{identity[1]}",
                "geometry": geometry,
                "properties": properties,
            }
        )
    features.sort(key=lambda feature: feature["id"])
    counts["retained"] = len(features)
    return features, counts


def generate_snapshot(output_dir: Path = OSM_DIR) -> tuple[Path, Path]:
    query = QUERY_PATH.read_text(encoding="utf-8")
    raw = _request_overpass(query)
    features, counts = process_elements(raw.get("elements", []))
    if not features:
        raise RuntimeError("Overpass returned no processable green-space candidates")
    retrieved_at = _utc_now()
    version = retrieved_at[:10]
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_dir / f"london-green-space-candidates-{version}.geojson"
    provenance_path = (
        output_dir / f"london-green-space-candidates-{version}.provenance.json"
    )
    collection = {
        "type": "FeatureCollection",
        "name": "London OSM public green-space candidates",
        "snapshot_version": version,
        "notice": (
            "Candidate dataset only. A missing access tag is not proof of public access; "
            "check current opening and access information before a visit."
        ),
        "attribution": "© OpenStreetMap contributors",
        "features": features,
    }
    snapshot_path.write_text(
        json.dumps(collection, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    provenance = {
        "source_name": "OpenStreetMap via Overpass API",
        "source_url": "https://www.openstreetmap.org/",
        "endpoint": ENDPOINT,
        "request_parameters": {"query_file": str(QUERY_PATH.relative_to(PROJECT_ROOT))},
        "retrieved_at_utc": retrieved_at,
        "snapshot_version": version,
        "licence": "Open Database Licence (ODbL) 1.0",
        "attribution": "© OpenStreetMap contributors",
        "checksum_sha256": file_sha256(snapshot_path),
        "record_counts": {"before": counts["raw"], "after": counts["retained"]},
        "filtering_rules": [
            "Query named park, garden, nature reserve, recreation ground, forest, meadow and protected-area features in Greater London",
            "Exclude access=private and access=no",
            "Retain polygon or multipolygon footprints when Overpass geometry can be assembled",
            "Retain a labelled centre-point fallback when no polygon footprint can be assembled",
            "Mark access as explicit_public only for access=yes, permissive, designated or public; otherwise mark unspecified",
        ],
        "known_limitations": [
            "This is a candidate dataset, not a guarantee of legal access, opening, safety or accessibility",
            "Some OSM nodes or incomplete relation geometries remain labelled point fallbacks",
            "OSM is community-maintained and can be incomplete or outdated",
            "Missing access tags are explicitly classified as unspecified",
        ],
        "feature_count": counts["retained"],
        "excluded_private_or_no_access": counts["private_or_no_access"],
        "excluded_missing_centre": counts["missing_centre"],
        "polygon_footprint_count": counts["polygon_footprint"],
        "point_fallback_count": counts["point_fallback"],
        "relevant_source_tags": list(RELEVANT_TAGS),
        "query_sha256": __import__("hashlib").sha256(query.encode("utf-8")).hexdigest(),
        "regeneration_command": "python -m scripts.generate_osm_snapshot --live-refresh",
    }
    provenance_path.write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return snapshot_path, provenance_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-refresh",
        action="store_true",
        help="Required opt-in: perform one sequential Overpass snapshot request",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OSM_DIR,
        help="Candidate output directory; defaults to the canonical OSM data directory",
    )
    args = parser.parse_args()
    if not args.live_refresh:
        parser.error("snapshot generation requires explicit --live-refresh")
    snapshot, provenance = generate_snapshot(args.output_dir)
    print(f"WROTE {snapshot}")
    print(f"WROTE {provenance}")


if __name__ == "__main__":
    main()
