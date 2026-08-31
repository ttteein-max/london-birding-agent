"""Coordinate-quality, London-boundary and metric-grid behaviour tests."""

from __future__ import annotations

from app.feasibility.spatial import (
    british_national_grid,
    load_london_boundary,
    location_quality_tier,
    metric_cell,
    opaque_cell_reference,
    point_in_geometry,
)


def test_coordinate_quality_boundaries() -> None:
    assert location_quality_tier(1_000) == "strong"
    assert location_quality_tier(1_001) == "weak"
    assert location_quality_tier(5_000) == "weak"
    assert location_quality_tier(5_001) == "context_only"
    assert location_quality_tier(None) == "unknown"


def test_polygon_inclusion_and_hole_exclusion() -> None:
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
            [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]],
        ],
    }
    assert point_in_geometry(2, 2, geometry)
    assert not point_in_geometry(5, 5, geometry)
    assert not point_in_geometry(12, 2, geometry)
    assert point_in_geometry(0, 5, geometry)


def test_real_london_boundary_rejects_bbox_only_point() -> None:
    boundary = load_london_boundary()
    assert point_in_geometry(-0.1276, 51.5074, boundary)  # Trafalgar Square
    assert not point_in_geometry(-0.3956, 51.6538, boundary)  # Watford, inside old bbox


def test_epsg27700_is_metric_and_uses_one_kilometre_cells() -> None:
    easting, northing = british_national_grid(-0.1276, 51.5074)
    assert 529_000 < easting < 531_000
    assert 179_000 < northing < 182_000
    cell = metric_cell(-0.1276, 51.5074, size_metres=1_000)
    assert cell == (int(easting // 1_000), int(northing // 1_000))


def test_opaque_reference_depends_on_unpersisted_secret() -> None:
    first = opaque_cell_reference(-0.1276, 51.5074, size_metres=1_000, secret=b"a" * 32)
    same = opaque_cell_reference(-0.1276, 51.5074, size_metres=1_000, secret=b"a" * 32)
    other_session = opaque_cell_reference(-0.1276, 51.5074, size_metres=1_000, secret=b"b" * 32)
    assert first == same
    assert first != other_session
    assert "530" not in first
