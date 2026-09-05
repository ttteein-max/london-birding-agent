"""Offline Phase 5 entrance, provider, cache, constraint, and privacy tests."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.biodiversity.entrances import (
    SnapshotEntranceRepository,
    entrance_access_certainty,
    entrance_is_eligible,
)
from app.biodiversity.graph.nodes import build_request_walking_routes_node
from app.biodiversity.models import (
    AccessCertainty,
    ExpeditionRequest,
    LocationStatus,
    ResolvedLocation,
    WGS84Point,
)
from app.biodiversity.observability import AgentRunRecorder
from app.biodiversity.orchestration import BackendDependencies, ExpeditionBackend
from app.biodiversity.reporting import save_biodiversity_run_report
from app.biodiversity.routing import (
    FailoverWalkingRouteProvider,
    FixtureWalkingRouteProvider,
    MemoryRouteCache,
    MemoryRouteGeometryStore,
    OpenRouteServiceWalkingProvider,
    ProviderRoute,
    RoutingServices,
    TfLJourneyProvider,
    _geometry_collection,
    parse_ors_response,
    parse_tfl_journey_response,
    plan_walking_routes,
    route_cache_key,
)
from app.biodiversity.routing_models import (
    PublicEntranceCandidate,
    RouteErrorCategory,
    RouteProviderError,
    RouteStatus,
    WalkingRouteRequest,
)
from scripts.generate_entrance_snapshot import process_elements


def _request(**updates: Any) -> ExpeditionRequest:
    values: dict[str, Any] = {
        "bird_input": "Common woodpigeon",
        "postcode": "SW11 4NJ",
        "target_local_date": date(2026, 6, 15),
        "duration_hours": 3,
        "search_radius_km": 5,
    }
    values.update(updates)
    return ExpeditionRequest(**values)


@pytest.fixture(scope="module")
def strong_result():
    return ExpeditionBackend(BackendDependencies.fixture()).run(_request())


class CountingProvider:
    name = "counting"
    version = "test-v1"

    def __init__(self, *, distance_m: float = 1000, duration_seconds: float = 600):
        self.calls: list[WalkingRouteRequest] = []
        self.distance_m = distance_m
        self.duration_seconds = duration_seconds

    def route(self, request: WalkingRouteRequest) -> ProviderRoute:
        self.calls.append(request)
        return ProviderRoute(
            distance_m=self.distance_m,
            duration_seconds=self.duration_seconds,
            ascent_m=8,
            descent_m=7,
            elevation_profile=[],
            manoeuvres=[],
            geometry={
                "type": "LineString",
                "coordinates": [
                    [request.origin.longitude, request.origin.latitude],
                    [request.destination.longitude, request.destination.latitude],
                ],
            },
            retrieved_at=datetime(2026, 9, 4, tzinfo=UTC),
            provider=self.name,
            provider_version=self.version,
            licence="ODbL 1.0",
            attribution="Test provider; © OpenStreetMap contributors",
        )


class FixedEntranceRepository:
    def __init__(self, entrances: list[PublicEntranceCandidate]):
        self.entrances = entrances

    def for_site(self, site):  # type: ignore[no-untyped-def]
        return [item for item in self.entrances if item.site_id == site.site_id]


class FixedSiteGeometryRepository:
    def __init__(self, geometry: dict[str, Any]):
        self.geometry = geometry

    def get(self, site_id: str) -> dict[str, Any] | None:
        del site_id
        return self.geometry


def _services(provider: Any, entrances: Any) -> RoutingServices:
    return RoutingServices(
        entrances=entrances,
        provider=provider,
        cache=MemoryRouteCache(),
        geometry_store=MemoryRouteGeometryStore(),
    )


def _site_entrance(strong_result):  # type: ignore[no-untyped-def]
    repository = SnapshotEntranceRepository()
    return next(
        (site, entrances[0])
        for site in strong_result.bundle.site_search.candidates
        if (entrances := repository.for_site(site))
    )


def test_snapshot_entrances_are_exactly_associated_and_never_centroids(
    strong_result,
) -> None:
    repository = SnapshotEntranceRepository()
    associated = [
        (site, entrance)
        for site in strong_result.bundle.site_search.candidates
        for entrance in repository.for_site(site)
    ]
    assert associated
    assert all(
        entrance.site_id == site.site_id
        and entrance.association_method == "osm_boundary_member"
        and entrance.point != site.centre_point
        for site, entrance in associated
    )


@pytest.mark.parametrize(
    "tags",
    [
        {"entrance": "yes", "access": "private"},
        {"barrier": "gate", "access": "no"},
        {"entrance": "yes", "foot": "no"},
        {"entrance": "exit"},
        {"routing:entrance": "emergency"},
        {"barrier": "gate", "service": "delivery"},
    ],
)
def test_private_no_foot_and_exit_only_entrances_are_excluded(
    tags: dict[str, str],
) -> None:
    assert entrance_is_eligible(tags) is False


def test_missing_access_is_uncertain_and_main_routing_entrance_is_retained() -> None:
    tags = {"routing:entrance": "main", "barrier": "gate"}
    assert entrance_is_eligible(tags)
    assert entrance_access_certainty(tags) == AccessCertainty.unspecified


def test_snapshot_generator_keeps_only_boundary_members_and_audits_exclusions() -> None:
    elements = [
        {"type": "relation", "id": 20, "tags": {"leisure": "park"}, "members": [{"type": "way", "ref": 10, "role": "outer"}]},
        {"type": "way", "id": 10, "nodes": [1, 2, 3, 4, 5, 6]},
        {"type": "node", "id": 1, "lon": -0.1, "lat": 51.5, "tags": {"entrance": "main", "access": "yes"}},
        {"type": "node", "id": 2, "lon": -0.1, "lat": 51.5, "tags": {"barrier": "gate"}},
        {"type": "node", "id": 3, "lon": -0.1, "lat": 51.5, "tags": {"entrance": "yes", "access": "private"}},
        {"type": "node", "id": 4, "lon": -0.1, "lat": 51.5, "tags": {"entrance": "exit"}},
        {"type": "node", "id": 5, "lon": -0.1, "lat": 51.5, "tags": {"entrance": "yes", "foot": "no"}},
        {"type": "node", "id": 6, "lon": -0.1, "lat": 51.5},
        {"type": "node", "id": 99, "lon": -0.2, "lat": 51.6, "tags": {"entrance": "yes", "access": "yes"}},
    ]
    features, counts = process_elements(elements)
    assert {item["id"] for item in features} == {"osm-node-1", "osm-node-2"}
    assert counts == {
        "raw_tagged_nodes": 6,
        "retained": 2,
        "excluded_access_or_role": 3,
        "excluded_unassociated": 1,
    }


def test_ors_parser_converts_units_manoeuvres_and_partial_elevation() -> None:
    route = parse_ors_response(
        {
            "features": [{
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[-0.1, 51.5, 8], [-0.11, 51.51], [-0.12, 51.52, 15]]},
                "properties": {
                    "summary": {"distance": 1234.5, "duration": 678.9},
                    "segments": [{"ascent": 12.5, "descent": 5.5, "steps": [{"instruction": "Continue north", "distance": 40, "duration": 25, "type": 6}]}],
                },
            }]
        },
        provider="openrouteservice",
        provider_version="v2",
    )
    assert route.distance_m == 1234.5
    assert route.duration_seconds == 678.9
    assert route.ascent_m == 12.5
    assert route.descent_m == 5.5
    assert len(route.elevation_profile) == 2
    assert route.manoeuvres[0].instruction == "Continue north"


def _tfl_response() -> dict[str, Any]:
    return {
        "journeys": [
            {
                "duration": 25,
                "legs": [
                    {
                        "duration": 4,
                        "distance": 300,
                        "mode": {"id": "walking"},
                        "instruction": {
                            "summary": "Walk to Example Station",
                            "steps": [
                                {
                                    "description": "Continue along Example Road",
                                    "distance": 300,
                                    "travelTime": 240,
                                    "turnDirection": "STRAIGHT",
                                    "streetName": "Example Road",
                                }
                            ],
                        },
                        "departurePoint": {"commonName": "Public demo origin"},
                        "arrivalPoint": {"commonName": "Example Station"},
                        "departureTime": "2026-09-15T09:00:00",
                        "arrivalTime": "2026-09-15T09:04:00",
                        "path": {
                            "lineString": "[[51.4704,-0.1663],[51.4800,-0.1700]]"
                        },
                    },
                    {
                        "duration": 12,
                        "distance": 5000,
                        "mode": {"id": "bus"},
                        "instruction": {"summary": "70 bus to Palace Gate"},
                        "routeOptions": [{"name": "70"}],
                        "departurePoint": {"commonName": "Example Station"},
                        "arrivalPoint": {"commonName": "Palace Gate"},
                        "path": {
                            "lineString": "[[51.4800,-0.1700],[51.5010,-0.1830]]"
                        },
                    },
                    {
                        "duration": 4,
                        "distance": 200,
                        "mode": {"id": "walking"},
                        "instruction": {"summary": "Walk to Palace Gate"},
                        "departurePoint": {"commonName": "Palace Gate stop"},
                        "arrivalPoint": {"commonName": "Palace Gate"},
                        "path": {
                            "lineString": "[[51.5010,-0.1830],[51.501883,-0.184302]]"
                        },
                    },
                ],
            }
        ]
    }


def _tfl_request() -> WalkingRouteRequest:
    return WalkingRouteRequest(
        request_id="public-demo-outbound",
        site_id="osm-way-3986346",
        entrance_id="osm-node-1109765916",
        origin=WGS84Point(longitude=-0.1663, latitude=51.4704),
        destination=WGS84Point(longitude=-0.184302, latitude=51.501883),
        profile="public-transport-and-walking",
        options={
            "direction": "outbound",
            "local_date": "20260915",
            "local_time": "0900",
            "time_is": "Departing",
        },
    )


def test_tfl_parser_separates_total_travel_from_walking_and_keeps_provider_steps() -> None:
    route = parse_tfl_journey_response(
        _tfl_response(),
        request=_tfl_request(),
        provider="tfl-journey-planner",
        provider_version="v1",
    )
    assert route.distance_m == 500
    assert route.duration_seconds == 25 * 60
    assert route.walking_duration_seconds == 8 * 60
    assert route.public_transport_duration_seconds == 17 * 60
    assert [item.mode for item in route.journey_segments] == [
        "walking",
        "bus",
        "walking",
    ]
    assert route.journey_segments[1].line_name == "70"
    assert route.geometry["coordinates"][0] == [-0.1663, 51.4704]
    assert route.geometry["coordinates"][-1] == [-0.184302, 51.501883]
    assert route.manoeuvres[0].instruction == "Continue along Example Road"


def test_tfl_provider_uses_least_time_and_keeps_optional_key_backend_only() -> None:
    captured: dict[str, str] = {}

    def handler(incoming: httpx.Request) -> httpx.Response:
        captured.update(dict(incoming.url.params))
        return httpx.Response(200, json=_tfl_response(), request=incoming)

    provider = TfLJourneyProvider(
        "backend-only-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_attempts=1,
    )
    route = provider.route(_tfl_request())
    assert route.provider == "tfl-journey-planner"
    assert captured["journeyPreference"] == "LeastTime"
    assert captured["app_key"] == "backend-only-key"
    assert "backend-only-key" not in route.model_dump_json()


def test_multimodal_round_trip_subtracts_total_travel_but_limits_walking(
    strong_result,
) -> None:
    site, entrance = _site_entrance(strong_result)
    provider = TfLJourneyProvider(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda incoming: httpx.Response(
                    200, json=_tfl_response(), request=incoming
                )
            )
        ),
        max_attempts=1,
    )
    services = RoutingServices(
        entrances=FixedEntranceRepository([entrance]),
        provider=provider,
        cache=MemoryRouteCache(),
        geometry_store=MemoryRouteGeometryStore(),
        routing_profile="public-transport-and-walking",
    )
    plan, _options, _uncertain = plan_walking_routes(
        _request(duration_hours=3, maximum_walking_distance_km=2),
        strong_result.bundle.location,
        [site],
        services=services,
    )
    assert plan.status == RouteStatus.ready
    assert plan.journey_type == "public_transport_and_walking"
    assert plan.total_distance_km == 1
    assert plan.walking_duration_minutes == 16
    assert plan.total_travel_duration_minutes == 50
    assert plan.remaining_field_time_minutes == 130
    assert len(plan.journey_segments) == 6


def test_identical_reverse_geometry_is_stored_once() -> None:
    geometry = {
        "type": "LineString",
        "coordinates": [[-0.16, 51.48], [-0.15, 51.49]],
    }
    result = _geometry_collection(
        geometry,
        {
            "type": "LineString",
            "coordinates": list(reversed(geometry["coordinates"])),
        },
    )
    assert len(result["features"]) == 1
    assert result["features"][0]["properties"]["direction"] == (
        "outbound_return"
    )


def test_fixture_round_trip_sums_independent_legs_and_has_elevation(strong_result) -> None:
    plan, options, _uncertain = plan_walking_routes(
        strong_result.bundle.request,
        strong_result.bundle.location,
        strong_result.bundle.site_search.candidates,
        services=RoutingServices.fixture(),
    )
    assert plan.status == RouteStatus.ready
    selected = next(
        item
        for item in options
        if item.route is not None
        and item.route.route_geometry_reference == plan.route_geometry_reference
    )
    assert selected.route is not None
    assert selected.route.total_distance_m == pytest.approx(
        selected.route.outbound.distance_m + selected.route.return_leg.distance_m
    )
    assert selected.route.total_duration_seconds == pytest.approx(
        selected.route.outbound.duration_seconds
        + selected.route.return_leg.duration_seconds
    )
    assert plan.elevation_profile


def test_fixture_provider_rejects_a_non_planned_origin() -> None:
    provider = FixtureWalkingRouteProvider()
    request = WalkingRouteRequest(
        request_id="wrong-origin",
        site_id="osm-way-372975520",
        entrance_id="osm-node-452413249",
        origin=WGS84Point(longitude=-0.12, latitude=51.5),
        destination=WGS84Point(longitude=-0.155069, latitude=51.508356),
        options={"direction": "outbound"},
    )
    with pytest.raises(RouteProviderError) as caught:
        provider.route(request)
    assert caught.value.category == RouteErrorCategory.no_route
    assert "planned public fixture endpoints" in caught.value.message


def test_ranking_and_tie_break_are_stable_and_candidate_work_is_bounded(
    strong_result,
) -> None:
    repository = SnapshotEntranceRepository()
    pairs = [
        (site, entrances[0])
        for site in strong_result.bundle.site_search.candidates
        if (entrances := repository.for_site(site))
    ]
    assert len(pairs) >= 2
    provider = CountingProvider(distance_m=1000, duration_seconds=600)
    services = _services(
        provider,
        FixedEntranceRepository([entrance for _site, entrance in pairs]),
    )
    candidate_sites = [site for site, _entrance in pairs]
    first, first_options, _uncertain = plan_walking_routes(
        strong_result.bundle.request,
        strong_result.bundle.location,
        candidate_sites,
        services=services,
    )
    second, second_options, _uncertain = plan_walking_routes(
        strong_result.bundle.request,
        strong_result.bundle.location,
        candidate_sites,
        services=services,
    )
    assert first.selected_site_id == candidate_sites[0].site_id
    assert second.selected_site_id == first.selected_site_id
    assert [item.option_id for item in second_options] == [
        item.option_id for item in first_options
    ]
    assert len(first_options) <= 3
    assert len(provider.calls) <= 6


def test_route_uses_internal_origin_and_public_entrance_not_site_centroid(
    strong_result,
) -> None:
    site, entrance = _site_entrance(strong_result)
    provider = CountingProvider()
    plan, _options, _uncertain = plan_walking_routes(
        strong_result.bundle.request,
        strong_result.bundle.location,
        [site],
        services=_services(provider, FixedEntranceRepository([entrance])),
    )
    assert plan.status == RouteStatus.ready
    assert len(provider.calls) == 2
    assert provider.calls[0].origin == strong_result.bundle.location.rounded_start_point
    assert provider.calls[0].destination == entrance.point
    assert provider.calls[0].destination != site.centre_point
    assert provider.calls[1].origin == entrance.point


def test_route_that_crosses_target_before_claimed_entrance_is_rejected(
    strong_result,
) -> None:
    site, entrance = _site_entrance(strong_result)

    class CrossingProvider(CountingProvider):
        def route(self, request: WalkingRouteRequest) -> ProviderRoute:
            result = super().route(request)
            destination = request.destination
            if request.options["direction"] == "outbound":
                coordinates = [
                    [request.origin.longitude, request.origin.latitude],
                    [destination.longitude + 0.019, destination.latitude],
                    [destination.longitude + 0.005, destination.latitude],
                    [destination.longitude, destination.latitude],
                ]
            else:
                coordinates = [
                    [request.origin.longitude, request.origin.latitude],
                    [request.origin.longitude + 0.005, request.origin.latitude],
                    [request.destination.longitude, request.destination.latitude],
                ]
            return result.model_copy(
                update={
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coordinates,
                    }
                }
            )

    longitude = entrance.point.longitude
    latitude = entrance.point.latitude
    services = RoutingServices(
        entrances=FixedEntranceRepository([entrance]),
        provider=CrossingProvider(),
        cache=MemoryRouteCache(),
        geometry_store=MemoryRouteGeometryStore(),
        site_geometries=FixedSiteGeometryRepository(
            {
                "type": "Polygon",
                "coordinates": [
                    [
                        [longitude, latitude - 0.01],
                        [longitude + 0.02, latitude - 0.01],
                        [longitude + 0.02, latitude + 0.01],
                        [longitude, latitude + 0.01],
                        [longitude, latitude - 0.01],
                    ]
                ],
            }
        ),
    )
    plan, options, _uncertain = plan_walking_routes(
        strong_result.bundle.request,
        strong_result.bundle.location,
        [site],
        services=services,
    )
    approach = next(
        item
        for item in options[0].constraint_results
        if item.code == "verified_entrance_approach"
    )
    assert plan.status == RouteStatus.no_feasible_route
    assert not approach.passed
    assert approach.actual_value and approach.actual_value > 750


def test_search_radius_is_separate_from_actual_round_trip_walking_limit(
    strong_result,
) -> None:
    site, entrance = _site_entrance(strong_result)
    provider = CountingProvider(distance_m=2200, duration_seconds=1200)
    request = _request(search_radius_km=25, maximum_walking_distance_km=4)
    plan, options, _uncertain = plan_walking_routes(
        request,
        strong_result.bundle.location,
        [site],
        services=_services(provider, FixedEntranceRepository([entrance])),
    )
    assert plan.status == RouteStatus.no_feasible_route
    assert options[0].constraint_results[0].actual_value == 4.4
    assert options[0].constraint_results[0].passed is False


def test_duration_must_leave_positive_field_time(strong_result) -> None:
    site, entrance = _site_entrance(strong_result)
    provider = CountingProvider(distance_m=500, duration_seconds=3600)
    plan, options, _uncertain = plan_walking_routes(
        _request(duration_hours=2),
        strong_result.bundle.location,
        [site],
        services=_services(provider, FixedEntranceRepository([entrance])),
    )
    assert plan.status == RouteStatus.no_feasible_route
    assert options[0].constraint_results[1].passed is False


def test_no_walking_limit_is_explicitly_reported(strong_result) -> None:
    plan, _options, _uncertain = plan_walking_routes(
        _request(maximum_walking_distance_km=None),
        strong_result.bundle.location,
        strong_result.bundle.site_search.candidates,
        services=RoutingServices.fixture(),
    )
    distance_constraint = next(
        item for item in plan.constraint_results if item.code == "maximum_walking_distance"
    )
    assert distance_constraint.passed
    assert distance_constraint.limit_value is None
    assert "No explicit walking limit supplied" in distance_constraint.message


def test_cache_hit_avoids_provider_calls_and_key_excludes_credentials(strong_result) -> None:
    site, entrance = _site_entrance(strong_result)
    provider = CountingProvider()
    services = _services(provider, FixedEntranceRepository([entrance]))
    first, _options, _uncertain = plan_walking_routes(
        strong_result.bundle.request,
        strong_result.bundle.location,
        [site],
        services=services,
    )
    assert first.cache_status.value == "miss"
    assert len(provider.calls) == 2
    second, _options, _uncertain = plan_walking_routes(
        strong_result.bundle.request,
        strong_result.bundle.location,
        [site],
        services=services,
    )
    assert second.cache_status.value == "hit"
    assert len(provider.calls) == 2
    key = route_cache_key(provider, provider.calls[0])
    assert "secret" not in key
    assert len(key) == 64


class FailingProvider:
    name = "failing-live"
    version = "v1"

    def route(self, request: WalkingRouteRequest) -> ProviderRoute:
        del request
        raise RouteProviderError(
            RouteErrorCategory.provider_unavailable,
            "live provider unavailable",
            provider=self.name,
            retryable=True,
        )


def test_live_failure_never_falls_back_to_fixture() -> None:
    request = WalkingRouteRequest(
        request_id="route-local-1",
        site_id="site-1",
        entrance_id="entrance-1",
        origin=WGS84Point(longitude=-0.16, latitude=51.48),
        destination=WGS84Point(longitude=-0.15, latitude=51.49),
    )
    with pytest.raises(RouteProviderError, match="failover_not_configured"):
        FailoverWalkingRouteProvider(FailingProvider()).route(request)


def test_configured_live_failover_uses_only_the_second_live_adapter() -> None:
    request = WalkingRouteRequest(
        request_id="route-local-2",
        site_id="site-1",
        entrance_id="entrance-1",
        origin=WGS84Point(longitude=-0.16, latitude=51.48),
        destination=WGS84Point(longitude=-0.15, latitude=51.49),
    )
    secondary = CountingProvider()
    provider = FailoverWalkingRouteProvider(FailingProvider(), secondary)
    assert provider.route(request).provider == "counting"
    assert len(secondary.calls) == 1


@pytest.mark.parametrize(
    ("status_code", "category"),
    [
        (401, RouteErrorCategory.authentication),
        (429, RouteErrorCategory.quota),
        (422, RouteErrorCategory.no_route),
        (500, RouteErrorCategory.provider_unavailable),
    ],
)
def test_ors_http_failures_have_typed_categories_without_key_leakage(
    status_code: int,
    category: RouteErrorCategory,
) -> None:
    key = "secret-routing-key"
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status_code, request=request)
        )
    )
    provider = OpenRouteServiceWalkingProvider(
        key,
        client=client,
        max_attempts=1,
    )
    request = WalkingRouteRequest(
        request_id="route-local-error",
        site_id="site-1",
        entrance_id="entrance-1",
        origin=WGS84Point(longitude=-0.16, latitude=51.48),
        destination=WGS84Point(longitude=-0.15, latitude=51.49),
    )
    with pytest.raises(RouteProviderError) as raised:
        provider.route(request)
    assert raised.value.category == category
    assert key not in str(raised.value)


def test_ors_timeout_and_malformed_response_are_distinct() -> None:
    request = WalkingRouteRequest(
        request_id="route-local-errors",
        site_id="site-1",
        entrance_id="entrance-1",
        origin=WGS84Point(longitude=-0.16, latitude=51.48),
        destination=WGS84Point(longitude=-0.15, latitude=51.49),
    )

    def timeout_handler(incoming: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=incoming)

    timeout_provider = OpenRouteServiceWalkingProvider(
        "backend-key",
        client=httpx.Client(transport=httpx.MockTransport(timeout_handler)),
        max_attempts=1,
    )
    with pytest.raises(RouteProviderError) as timeout:
        timeout_provider.route(request)
    assert timeout.value.category == RouteErrorCategory.timeout

    malformed_provider = OpenRouteServiceWalkingProvider(
        "backend-key",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda incoming: httpx.Response(
                    200,
                    json={"unexpected": True},
                    request=incoming,
                )
            )
        ),
        max_attempts=1,
    )
    with pytest.raises(RouteProviderError) as malformed:
        malformed_provider.route(request)
    assert malformed.value.category == RouteErrorCategory.malformed_response


def test_no_entrance_fails_safely_without_calling_provider(strong_result) -> None:
    provider = CountingProvider()
    plan, options, _uncertain = plan_walking_routes(
        strong_result.bundle.request,
        strong_result.bundle.location,
        strong_result.bundle.site_search.candidates,
        services=_services(provider, FixedEntranceRepository([])),
    )
    assert plan.status == RouteStatus.no_valid_entrance
    assert options == []
    assert provider.calls == []
    assert "no centroid" in plan.limitations[0]


def test_missing_routing_origin_fails_without_provider(strong_result) -> None:
    provider = CountingProvider()
    location = ResolvedLocation(
        status=LocationStatus.source_unavailable,
        input_kind="postcode",
        message="No internal routing origin.",
    )
    plan, options, _uncertain = plan_walking_routes(
        strong_result.bundle.request,
        location,
        strong_result.bundle.site_search.candidates,
        services=_services(provider, SnapshotEntranceRepository()),
    )
    assert plan.status == RouteStatus.source_unavailable
    assert options == []
    assert provider.calls == []


def test_low_historical_evidence_node_does_not_call_routing_provider() -> None:
    low = ExpeditionBackend(BackendDependencies.fixture()).run(
        _request(bird_input="Common swift", target_local_date=date(2026, 7, 15))
    )
    assert low.bundle.evidence_outcome.value != "strong_map_evidence"
    provider = CountingProvider()
    node = build_request_walking_routes_node(
        _services(provider, SnapshotEntranceRepository())
    )
    update = node({"evidence_bundle": low.bundle.model_dump(mode="json")})
    assert update["validated_walking_plan"]["status"] == (
        "not_requested_without_strong_evidence"
    )
    assert provider.calls == []


def test_route_reports_exclude_geometry_origin_and_credentials(
    tmp_path: Path,
    strong_result,
) -> None:
    plan, options, _uncertain = plan_walking_routes(
        strong_result.bundle.request,
        strong_result.bundle.location,
        strong_result.bundle.site_search.candidates,
        services=RoutingServices.fixture(),
    )
    recorder = AgentRunRecorder(thread_id="phase5-report")
    recorder.record_event(
        "routing_provider_completed",
        payload={
            "provider": plan.provider,
            "route_id": "route-option-01-outbound",
            "cache_status": "miss",
            "duration_ms": 12.5,
        },
    )
    provider_event = recorder.events[-1]
    assert provider_event.payload["operation_id"] == recorder.run_id
    assert provider_event.payload["thread_id"] == "phase5-report"
    assert {
        "branch_id",
        "execution_id",
        "checkpoint_id",
    } <= provider_event.payload.keys()
    recorder.finish("completed")
    paths = save_biodiversity_run_report(
        tmp_path,
        recorder=recorder,
        result={
            "validated_walking_plan": plan.model_dump(mode="json"),
            "route_options": [item.model_dump(mode="json") for item in options],
            "final_validated_plan": {"walking_plan": plan.model_dump(mode="json")},
        },
        request=None,
        data_mode="fixture",
        model_mode="scripted",
    )
    visible = "\n".join(path.read_text(encoding="utf-8") for path in paths.values())
    lowered = visible.casefold()
    assert "linestring" not in lowered
    assert '"coordinates"' not in lowered
    assert '"point"' not in lowered
    assert "api_key" not in lowered
    assert "occurrence_id" not in lowered
    assert "safe_cell" not in lowered
    timing = json.loads(paths["provider_cache_timings"].read_text(encoding="utf-8"))
    assert timing["provider_duration_ms"] == 12.5
