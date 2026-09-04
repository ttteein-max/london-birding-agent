"""Opt-in Phase 5 live routing checks; never part of default pytest."""

from __future__ import annotations

import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.biodiversity.graph import build_biodiversity_graph
from app.biodiversity.model_factory import create_live_chat_model
from app.biodiversity.models import WGS84Point
from app.biodiversity.orchestration import BackendDependencies
from app.biodiversity.routing import (
    ORS_ENDPOINT,
    OpenRouteServiceWalkingProvider,
    RoutingServices,
)
from app.biodiversity.routing_models import RouteStatus, WalkingRouteRequest

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    not os.getenv("ORS_API_KEY"),
    reason="ORS_API_KEY is missing; current HeiGIT foot-walking live test skipped.",
)
def test_current_heigit_foot_walking_endpoint_without_openai_key() -> None:
    assert ORS_ENDPOINT == (
        "https://api.heigit.org/openrouteservice/v2/directions/foot-walking/geojson"
    )
    route = OpenRouteServiceWalkingProvider().route(
        WalkingRouteRequest(
            request_id="public-london-demo-live",
            site_id="osm-way-30747908",
            entrance_id="osm-node-452413249",
            origin=WGS84Point(longitude=-0.1663, latitude=51.4704),
            destination=WGS84Point(longitude=-0.1519896, latitude=51.5076053),
        )
    )
    assert route.distance_m > 0
    assert route.duration_seconds > 0
    assert route.provider == "openrouteservice"


@pytest.mark.skipif(
    not (
        os.getenv("ORS_API_KEY")
        and os.getenv("OPENAI_API_KEY")
        and os.getenv("OPENAI_MODEL")
    ),
    reason="ORS_API_KEY and OpenAI credentials are required for a live/live milestone.",
)
def test_live_live_milestone_uses_current_evidence_model_and_routing(
    tmp_path,
) -> None:
    target_date = datetime.now(ZoneInfo("Europe/London")).date()
    graph = build_biodiversity_graph(
        create_live_chat_model(),
        dependencies=BackendDependencies.live(),
        routing_services=RoutingServices.live(
            runtime_directory=tmp_path / "routes"
        ),
    )
    result = graph.invoke(
        {
            "original_request_text": (
                "Plan a three-hour expedition from SW11 4NJ on "
                f"{target_date.isoformat()} to look for Common woodpigeon."
            )
        }
    )
    assert result["terminal_status"] == "completed"
    walking = result["final_validated_plan"]["walking_plan"]
    assert walking["status"] == RouteStatus.ready.value
    assert walking["provider"] in {"openrouteservice", "graphhopper"}
    public_plan = json.dumps(result["final_validated_plan"]).casefold()
    assert '"latitude"' not in public_plan
    assert '"longitude"' not in public_plan
    assert '"geometry"' not in public_plan
