"""London boundary checks and British National Grid spatial references."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from pathlib import Path
from typing import Any

from pyproj import Transformer

from app.feasibility.core import PROJECT_ROOT

BOUNDARY_DIR = PROJECT_ROOT / "data" / "boundaries"
_WGS84_TO_BNG = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)
_BNG_TO_WGS84 = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)


def location_quality_tier(uncertainty_metres: float | int | None) -> str:
    """Return the declared quality tier, including exact boundary semantics."""

    if uncertainty_metres is None:
        return "unknown"
    if uncertainty_metres < 0:
        raise ValueError("coordinate uncertainty cannot be negative")
    if uncertainty_metres <= 1_000:
        return "strong"
    if uncertainty_metres <= 5_000:
        return "weak"
    return "context_only"


def _point_on_segment(
    x: float,
    y: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
) -> bool:
    cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
    if not math.isclose(cross, 0.0, abs_tol=1e-10):
        return False
    return min(ax, bx) - 1e-12 <= x <= max(ax, bx) + 1e-12 and min(ay, by) - 1e-12 <= y <= max(ay, by) + 1e-12


def _inside_ring(longitude: float, latitude: float, ring: list[list[float]]) -> bool:
    inside = False
    for index, point in enumerate(ring):
        previous = ring[index - 1]
        x1, y1 = previous[0], previous[1]
        x2, y2 = point[0], point[1]
        if _point_on_segment(longitude, latitude, x1, y1, x2, y2):
            return True
        crosses = (y1 > latitude) != (y2 > latitude)
        if crosses:
            intersection_x = (x2 - x1) * (latitude - y1) / (y2 - y1) + x1
            if longitude < intersection_x:
                inside = not inside
    return inside


def point_in_geometry(longitude: float, latitude: float, geometry: dict[str, Any]) -> bool:
    """Deterministic point-in-polygon for GeoJSON Polygon/MultiPolygon."""

    geometry_type = geometry.get("type")
    if geometry_type == "Polygon":
        polygons = [geometry["coordinates"]]
    elif geometry_type == "MultiPolygon":
        polygons = geometry["coordinates"]
    else:
        raise ValueError(f"unsupported London boundary geometry: {geometry_type}")
    for polygon in polygons:
        if not polygon or not _inside_ring(longitude, latitude, polygon[0]):
            continue
        if any(_inside_ring(longitude, latitude, hole) for hole in polygon[1:]):
            continue
        return True
    return False


def load_london_boundary(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        matches = sorted(BOUNDARY_DIR.glob("greater-london-*.geojson"))
        if len(matches) != 1:
            raise RuntimeError(f"expected one versioned Greater London boundary, found {len(matches)}")
        path = matches[0]
    artifact = json.loads(path.read_text(encoding="utf-8"))
    feature = artifact["features"][0]
    return feature["geometry"]


def british_national_grid(longitude: float, latitude: float) -> tuple[float, float]:
    """Project WGS84 coordinates into metres in EPSG:27700."""

    easting, northing = _WGS84_TO_BNG.transform(longitude, latitude)
    if not math.isfinite(easting) or not math.isfinite(northing):
        raise ValueError("coordinate could not be projected to EPSG:27700")
    return easting, northing


def wgs84_from_british_national_grid(easting: float, northing: float) -> tuple[float, float]:
    """Transform a British National Grid position to WGS84 longitude/latitude."""

    longitude, latitude = _BNG_TO_WGS84.transform(easting, northing)
    if not math.isfinite(longitude) or not math.isfinite(latitude):
        raise ValueError("EPSG:27700 coordinate could not be projected to WGS84")
    return longitude, latitude


def metric_cell_polygon_wgs84(
    cell_easting: int,
    cell_northing: int,
    *,
    size_metres: int = 1_000,
    decimal_places: int = 5,
) -> list[list[float]]:
    """Return a closed, rounded WGS84 ring for an already aggregated BNG cell."""

    if size_metres <= 0:
        raise ValueError("cell size must be positive")
    minimum_easting = cell_easting * size_metres
    minimum_northing = cell_northing * size_metres
    corners = (
        (minimum_easting, minimum_northing),
        (minimum_easting + size_metres, minimum_northing),
        (minimum_easting + size_metres, minimum_northing + size_metres),
        (minimum_easting, minimum_northing + size_metres),
        (minimum_easting, minimum_northing),
    )
    return [
        [round(longitude, decimal_places), round(latitude, decimal_places)]
        for longitude, latitude in (
            wgs84_from_british_national_grid(easting, northing)
            for easting, northing in corners
        )
    ]


def metric_cell(longitude: float, latitude: float, *, size_metres: int) -> tuple[int, int]:
    if size_metres <= 0:
        raise ValueError("cell size must be positive")
    easting, northing = british_national_grid(longitude, latitude)
    return math.floor(easting / size_metres), math.floor(northing / size_metres)


def opaque_cell_reference(
    longitude: float,
    latitude: float,
    *,
    size_metres: int,
    secret: bytes,
) -> str:
    """Return a run-specific HMAC cell reference; the secret is never persisted."""

    cell = metric_cell(longitude, latitude, size_metres=size_metres)
    message = f"EPSG:27700:{size_metres}:{cell[0]}:{cell[1]}".encode("utf-8")
    digest = hmac.new(secret, message, hashlib.sha256).hexdigest()[:20]
    return f"bng-{size_metres}m-ref:{digest}"
