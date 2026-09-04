"""Offline Phase 5 API contracts for safe route views and basemap configuration."""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.biodiversity.api.dependencies import APISettings
from app.biodiversity.api.main import create_app

REQUEST = (
    "Plan a three-hour expedition from SW11 4NJ on 15 June 2026 "
    "to look for Common woodpigeon."
)


def _wait(client: TestClient, operation_id: str) -> dict:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        operation = client.get(f"/api/v1/operations/{operation_id}").json()
        if operation["status"] not in {"queued", "running"}:
            return operation
        time.sleep(0.02)
    raise AssertionError("Phase 5 API operation timed out")


def test_strong_fixture_exposes_typed_route_views_only_through_safe_endpoints(
    tmp_path: Path,
) -> None:
    settings = APISettings(
        checkpoint_db=tmp_path / "checkpoints.sqlite",
        catalog_db=tmp_path / "catalog.sqlite",
        report_root=tmp_path / "reports",
        route_runtime=tmp_path / "routes",
        sse_heartbeat_seconds=0.02,
    )
    with TestClient(create_app(settings)) as client:
        config = client.get("/api/v1/map/config")
        assert config.status_code == 200
        assert config.json()["style_url"] == (
            "https://tiles.openfreemap.org/styles/liberty"
        )
        assert "OpenMapTiles" in " ".join(config.json()["attributions"])
        assert "tiles.openfreemap.org" in config.headers["content-security-policy"]

        accepted = client.post(
            "/api/v1/runs",
            json={"thread_id": "phase5-route-api", "request": REQUEST},
        )
        assert accepted.status_code == 202
        operation_id = accepted.json()["operation_id"]
        operation = _wait(client, operation_id)
        assert operation["status"] == "completed"
        checkpoint_id = operation["current_checkpoint_id"]

        plan = client.get(
            f"/api/v1/runs/phase5-route-api/checkpoints/{checkpoint_id}/plan"
        ).json()
        walking = plan["walking_plan"]
        assert walking["status"] == "ready"
        assert walking["selected_site_id"]
        assert walking["entrance"]["association_method"] == "osm_boundary_member"
        assert walking["total_distance_km"] == round(
            walking["outbound_distance_km"] + walking["return_distance_km"], 3
        )
        assert walking["remaining_field_time_minutes"] > 0

        map_view = client.get(
            f"/api/v1/runs/phase5-route-api/checkpoints/{checkpoint_id}/map"
        ).json()
        assert map_view["selected_entrance"]
        assert map_view["selected_site_id"] == walking["selected_site_id"]
        assert map_view["route_geometry_reference"]

        routes = client.get(
            f"/api/v1/runs/phase5-route-api/checkpoints/{checkpoint_id}/routes"
        ).json()
        assert routes["status"] == "ready"
        assert routes["options"]
        selected = next(
            item
            for item in routes["options"]
            if item["site_id"] == walking["selected_site_id"]
        )
        assert selected["route_geometry_reference"] == (
            map_view["route_geometry_reference"]
        )

        geometry = client.get(
            "/api/v1/runs/phase5-route-api/checkpoints/"
            f"{checkpoint_id}/route-geometry/{selected['route_geometry_reference']}"
        )
        assert geometry.status_code == 200
        payload = geometry.json()
        assert payload["origin_visibility"] == "local_private"
        assert payload["geojson"]["type"] == "FeatureCollection"
        assert {item["properties"]["direction"] for item in payload["geojson"]["features"]} == {
            "outbound",
            "return",
        }

        invented = client.get(
            "/api/v1/runs/phase5-route-api/checkpoints/"
            f"{checkpoint_id}/route-geometry/route-aaaaaaaaaaaaaaaaaaaaaaaa"
        )
        assert invented.status_code == 422

        event_text = client.get(accepted.json()["events_url"]).text.casefold()
        assert "routing_provider_completed" in event_text
        assert "route_finalised" in event_text
        assert '"coordinates"' not in event_text
        assert "linestring" not in event_text
        assert "ors_api_key" not in event_text

    report = tmp_path / "reports" / operation_id
    assert (report / "route-audit.json").is_file()
    assert (report / "route-plan.json").is_file()
    assert (report / "provider-cache-timings.json").is_file()
    timings = json.loads((report / "timings.json").read_text(encoding="utf-8"))
    provider_spans = [
        span for span in timings["spans"] if span["kind"] == "provider"
    ]
    assert len(provider_spans) == 4
    assert all(span["name"] == "fixture-openrouteservice" for span in provider_spans)
    report_payload = "\n".join(
        path.read_text(encoding="utf-8")
        for path in report.glob("*.json")
    ).casefold()
    assert '"coordinates"' not in report_payload
    assert "linestring" not in report_payload


def test_disabled_basemap_returns_explicit_overlay_fallback(tmp_path: Path) -> None:
    settings = APISettings(
        checkpoint_db=tmp_path / "checkpoints.sqlite",
        catalog_db=tmp_path / "catalog.sqlite",
        report_root=tmp_path / "reports",
        route_runtime=tmp_path / "routes",
        basemap_style_url=None,
    )
    with TestClient(create_app(settings)) as client:
        payload = client.get("/api/v1/map/config").json()
    assert payload == {
        "style_url": None,
        "provider": "Unavailable",
        "attributions": [
            "© OpenFreeMap",
            "© OpenMapTiles",
            "© OpenStreetMap contributors",
        ],
        "fallback_message": (
            "Basemap unavailable — evidence overlays remain available"
        ),
    }


def test_public_demo_exposes_only_the_planned_fixture_route_geometry(
    tmp_path: Path,
) -> None:
    settings = APISettings(
        checkpoint_db=tmp_path / "public-checkpoints.sqlite",
        catalog_db=tmp_path / "public-catalog.sqlite",
        report_root=tmp_path / "public-reports",
        route_runtime=tmp_path / "public-routes",
        allowed_run_modes=(("fixture", "scripted"),),
        public_demo=True,
        expose_api_docs=False,
        max_concurrent_operations=2,
        mutations_per_minute=20,
        max_demo_threads=10,
        max_storage_bytes=64 * 1024 * 1024,
        run_retention_seconds=3600,
    )
    with TestClient(create_app(settings)) as client:
        accepted = client.post(
            "/api/v1/runs",
            json={"thread_id": "public-route", "request": REQUEST},
        ).json()
        operation = _wait(client, accepted["operation_id"])
        checkpoint_id = operation["current_checkpoint_id"]
        map_view = client.get(
            f"/api/v1/runs/public-route/checkpoints/{checkpoint_id}/map"
        ).json()
        geometry = client.get(
            "/api/v1/runs/public-route/checkpoints/"
            f"{checkpoint_id}/route-geometry/"
            f"{map_view['route_geometry_reference']}"
        )
    assert geometry.status_code == 200
    assert geometry.json()["origin_visibility"] == "planned_public_fixture"


def test_route_limit_resume_invalidates_old_evidence_and_uses_new_constraint(
    tmp_path: Path,
) -> None:
    settings = APISettings(
        checkpoint_db=tmp_path / "checkpoints.sqlite",
        catalog_db=tmp_path / "catalog.sqlite",
        report_root=tmp_path / "reports",
        route_runtime=tmp_path / "routes",
    )
    limited_request = (
        "Plan a three-hour expedition from SW11 4NJ on 15 June 2026 "
        "to look for Common woodpigeon with a maximum walking distance of 3 km."
    )
    with TestClient(create_app(settings)) as client:
        accepted = client.post(
            "/api/v1/runs",
            json={"thread_id": "route-limit-resume", "request": limited_request},
        ).json()
        assert _wait(client, accepted["operation_id"])["status"] == (
            "waiting_for_input"
        )
        detail = client.get("/api/v1/runs/route-limit-resume").json()
        decision = detail["pending_decisions"][0]
        assert decision["kind"] == "route_tradeoff"

        resumed = client.post(
            "/api/v1/runs/route-limit-resume/resume",
            json={
                "checkpoint_id": decision["checkpoint_id"],
                "decision": {
                    "kind": "route_tradeoff",
                    "option": "increase_maximum_walking_distance",
                    "maximum_walking_distance_km": 10,
                },
            },
        )
        assert resumed.status_code == 202
        operation = _wait(client, resumed.json()["operation_id"])
        assert operation["status"] == "completed"
        final = client.get("/api/v1/runs/route-limit-resume").json()[
            "final_plan"
        ]
        walking = final["walking_plan"]
        assert walking["status"] == "ready"
        distance_constraint = next(
            item
            for item in walking["constraint_results"]
            if item["code"] == "maximum_walking_distance"
        )
        assert distance_constraint["limit_value"] == 10
        assert distance_constraint["passed"] is True


def test_basemap_secret_query_is_rejected_before_browser_configuration(
    tmp_path: Path,
) -> None:
    try:
        APISettings(
            checkpoint_db=tmp_path / "checkpoints.sqlite",
            catalog_db=tmp_path / "catalog.sqlite",
            report_root=tmp_path / "reports",
            route_runtime=tmp_path / "routes",
            basemap_style_url="https://maps.example/style?access_token=secret",
        )
    except ValueError as exc:
        assert "browser-visible secret" in str(exc)
    else:
        raise AssertionError("Secret-bearing basemap URLs must be rejected")


def test_custom_basemap_style_and_resources_are_added_to_strict_csp(
    tmp_path: Path,
) -> None:
    settings = APISettings(
        checkpoint_db=tmp_path / "checkpoints.sqlite",
        catalog_db=tmp_path / "catalog.sqlite",
        report_root=tmp_path / "reports",
        route_runtime=tmp_path / "routes",
        basemap_style_url="https://styles.example.test/liberty.json",
        basemap_resource_origins=("https://tiles.example.test",),
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/map/config")
    policy = response.headers["content-security-policy"]
    assert "https://styles.example.test" in policy
    assert "https://tiles.example.test" in policy


def test_basemap_resource_origins_reject_csp_injection(tmp_path: Path) -> None:
    try:
        APISettings(
            checkpoint_db=tmp_path / "checkpoints.sqlite",
            catalog_db=tmp_path / "catalog.sqlite",
            report_root=tmp_path / "reports",
            route_runtime=tmp_path / "routes",
            basemap_resource_origins=("https://tiles.example; script-src *",),
        )
    except ValueError as exc:
        assert "HTTPS origins" in str(exc)
    else:
        raise AssertionError("CSP fragments must be rejected")


def test_openapi_contains_route_contracts_and_dedicated_geometry_path() -> None:
    schema = create_app().openapi()
    assert "ValidatedWalkingPlanView" in schema["components"]["schemas"]
    assert "RouteOptionsView" in schema["components"]["schemas"]
    assert "RouteGeometryView" in schema["components"]["schemas"]
    assert any("route-geometry" in path for path in schema["paths"])
    serialised = json.dumps(schema).casefold()
    assert "occurrence_coordinates" not in serialised
