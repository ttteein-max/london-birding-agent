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
    return (
        min(ax, bx) - 1e-12 <= x <= max(ax, bx) + 1e-12
        and min(ay, by) - 1e-12 <= y <= max(ay, by) + 1e-12
    )


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


def point_in_geometry(
    longitude: float, latitude: float, geometry: dict[str, Any]
) -> bool:
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


def _geometry_points(geometry: dict[str, Any]) -> list[list[float]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if (
        geometry_type == "Point"
        and isinstance(coordinates, list)
        and len(coordinates) == 2
    ):
        return [coordinates]
    if geometry_type == "Polygon":
        return [point for ring in coordinates or [] for point in ring]
    if geometry_type == "MultiPolygon":
        return [
            point for polygon in coordinates or [] for ring in polygon for point in ring
        ]
    raise ValueError(f"unsupported GeoJSON geometry: {geometry_type}")


def _geometry_rings(geometry: dict[str, Any]) -> list[list[list[float]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Point":
        return []
    if geometry_type == "Polygon":
        return list(coordinates or [])
    if geometry_type == "MultiPolygon":
        return [ring for polygon in coordinates or [] for ring in polygon]
    raise ValueError(f"unsupported GeoJSON geometry: {geometry_type}")


def _segments_intersect(
    first_start: list[float],
    first_end: list[float],
    second_start: list[float],
    second_end: list[float],
) -> bool:
    def orientation(a: list[float], b: list[float], c: list[float]) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    first_second = orientation(first_start, first_end, second_start)
    first_third = orientation(first_start, first_end, second_end)
    second_first = orientation(second_start, second_end, first_start)
    second_second = orientation(second_start, second_end, first_end)
    if (first_second > 0 > first_third or first_second < 0 < first_third) and (
        second_first > 0 > second_second or second_first < 0 < second_second
    ):
        return True
    return (
        (
            math.isclose(first_second, 0.0, abs_tol=1e-10)
            and _point_on_segment(
                second_start[0], second_start[1], *first_start, *first_end
            )
        )
        or (
            math.isclose(first_third, 0.0, abs_tol=1e-10)
            and _point_on_segment(
                second_end[0], second_end[1], *first_start, *first_end
            )
        )
        or (
            math.isclose(second_first, 0.0, abs_tol=1e-10)
            and _point_on_segment(
                first_start[0], first_start[1], *second_start, *second_end
            )
        )
        or (
            math.isclose(second_second, 0.0, abs_tol=1e-10)
            and _point_on_segment(
                first_end[0], first_end[1], *second_start, *second_end
            )
        )
    )


def geometries_intersect(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Return whether supported Point/Polygon/MultiPolygon geometries intersect."""

    first_type = first.get("type")
    second_type = second.get("type")
    if first_type == "Point":
        point = _geometry_points(first)[0]
        if second_type == "Point":
            return point == _geometry_points(second)[0]
        return point_in_geometry(point[0], point[1], second)
    if second_type == "Point":
        point = _geometry_points(second)[0]
        return point_in_geometry(point[0], point[1], first)

    if any(
        point_in_geometry(point[0], point[1], second)
        for point in _geometry_points(first)
    ):
        return True
    if any(
        point_in_geometry(point[0], point[1], first)
        for point in _geometry_points(second)
    ):
        return True
    first_rings = _geometry_rings(first)
    second_rings = _geometry_rings(second)
    return any(
        _segments_intersect(
            first_ring[index - 1],
            first_ring[index],
            second_ring[other - 1],
            second_ring[other],
        )
        for first_ring in first_rings
        for second_ring in second_rings
        for index in range(len(first_ring))
        for other in range(len(second_ring))
    )


def _point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    delta_x = end[0] - start[0]
    delta_y = end[1] - start[1]
    squared_length = delta_x * delta_x + delta_y * delta_y
    if squared_length == 0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    fraction = max(
        0.0,
        min(
            1.0,
            ((point[0] - start[0]) * delta_x + (point[1] - start[1]) * delta_y)
            / squared_length,
        ),
    )
    projection = (start[0] + fraction * delta_x, start[1] + fraction * delta_y)
    return math.hypot(point[0] - projection[0], point[1] - projection[1])


def geometry_distance_metres(first: dict[str, Any], second: dict[str, Any]) -> float:
    """Minimum projected boundary distance for supported GeoJSON geometries."""

    if geometries_intersect(first, second):
        return 0.0
    first_points = [
        british_national_grid(point[0], point[1]) for point in _geometry_points(first)
    ]
    second_points = [
        british_national_grid(point[0], point[1]) for point in _geometry_points(second)
    ]
    first_segments = [
        (british_national_grid(*ring[index - 1]), british_national_grid(*ring[index]))
        for ring in _geometry_rings(first)
        for index in range(len(ring))
    ]
    second_segments = [
        (british_national_grid(*ring[index - 1]), british_national_grid(*ring[index]))
        for ring in _geometry_rings(second)
        for index in range(len(ring))
    ]
    distances = [
        math.hypot(first_point[0] - second_point[0], first_point[1] - second_point[1])
        for first_point in first_points
        for second_point in second_points
    ]
    distances.extend(
        _point_segment_distance(point, start, end)
        for point in first_points
        for start, end in second_segments
    )
    distances.extend(
        _point_segment_distance(point, start, end)
        for point in second_points
        for start, end in first_segments
    )
    if not distances:
        raise ValueError("geometry distance requires at least one coordinate")
    return min(distances)


def load_london_boundary(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        matches = sorted(BOUNDARY_DIR.glob("greater-london-*.geojson"))
        if len(matches) != 1:
            raise RuntimeError(
                f"expected one versioned Greater London boundary, found {len(matches)}"
            )
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


def wgs84_from_british_national_grid(
    easting: float, northing: float
) -> tuple[float, float]:
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


def metric_cell(
    longitude: float, latitude: float, *, size_metres: int
) -> tuple[int, int]:
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
