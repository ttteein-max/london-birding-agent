"""Deterministic Phase 5 walking providers, cache, geometry store, and ranking."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import httpx
from pydantic import Field

from app.biodiversity.entrances import EntranceRepository, SnapshotEntranceRepository
from app.biodiversity.models import (
    AccessCertainty,
    ExpeditionRequest,
    PublicSiteCandidate,
    ResolvedLocation,
    StrictModel,
    WGS84Point,
)
from app.biodiversity.routing_models import (
    ElevationSample,
    PublicEntranceCandidate,
    RouteCacheStatus,
    RouteConstraintResult,
    RouteErrorCategory,
    RouteLeg,
    RouteManoeuvre,
    RouteOption,
    RouteProviderError,
    RouteStatus,
    ValidatedWalkingPlan,
    WalkingRouteEvidence,
    WalkingRouteRequest,
)
from app.feasibility.core import FIXTURE_DIR, PROJECT_ROOT, canonical_sha256

ORS_ENDPOINT = "https://api.heigit.org/openrouteservice/v2/directions/foot-walking/geojson"
GRAPHHOPPER_ENDPOINT = "https://graphhopper.com/api/1/route"
ROUTE_PROVIDER_VERSION = "phase-5-v1"
ROUTE_CACHE_SCHEMA_VERSION = 1
MAX_ROUTE_CANDIDATES = 3
RouteEventCallback = Callable[[str, dict[str, Any]], None]


class ProviderRoute(StrictModel):
    distance_m: float = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    ascent_m: float | None = Field(default=None, ge=0)
    descent_m: float | None = Field(default=None, ge=0)
    elevation_profile: list[ElevationSample] = Field(default_factory=list)
    manoeuvres: list[RouteManoeuvre] = Field(default_factory=list)
    geometry: dict[str, Any]
    retrieved_at: datetime
    provider: str
    provider_version: str
    licence: str
    attribution: str
    limitations: list[str] = Field(default_factory=list)


class WalkingRouteProvider(Protocol):
    name: str
    version: str

    def route(self, request: WalkingRouteRequest) -> ProviderRoute: ...


class RouteCache(Protocol):
    def get(self, key: str) -> ProviderRoute | None: ...

    def put(self, key: str, value: ProviderRoute) -> None: ...


class RouteGeometryStore(Protocol):
    def put(
        self,
        reference: str,
        *,
        outbound: dict[str, Any],
        return_geometry: dict[str, Any],
    ) -> None: ...

    def get(self, reference: str) -> dict[str, Any] | None: ...


class MemoryRouteCache:
    def __init__(self) -> None:
        self.values: dict[str, ProviderRoute] = {}

    def get(self, key: str) -> ProviderRoute | None:
        value = self.values.get(key)
        return value.model_copy(deep=True) if value else None

    def put(self, key: str, value: ProviderRoute) -> None:
        self.values[key] = value.model_copy(deep=True)


class JsonFileRouteCache:
    """Small local cache containing route facts only; credentials are never inputs."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> ProviderRoute | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            return ProviderRoute.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, key: str, value: ProviderRoute) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        temporary.write_text(value.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)


class MemoryRouteGeometryStore:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}

    def put(
        self,
        reference: str,
        *,
        outbound: dict[str, Any],
        return_geometry: dict[str, Any],
    ) -> None:
        self.values[reference] = _geometry_collection(outbound, return_geometry)

    def get(self, reference: str) -> dict[str, Any] | None:
        value = self.values.get(reference)
        return json.loads(json.dumps(value)) if value else None


class FileRouteGeometryStore:
    """Private runtime geometry store; references, rather than coordinates, enter state."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, reference: str) -> Path:
        if not reference.startswith("route-") or not reference.replace("-", "").isalnum():
            raise ValueError("Invalid route geometry reference")
        return self.directory / f"{reference}.geojson"

    def put(
        self,
        reference: str,
        *,
        outbound: dict[str, Any],
        return_geometry: dict[str, Any],
    ) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(reference)
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(
                _geometry_collection(outbound, return_geometry),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def get(self, reference: str) -> dict[str, Any] | None:
        path = self._path(reference)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if value.get("type") == "FeatureCollection" else None


def _geometry_collection(
    outbound: dict[str, Any], return_geometry: dict[str, Any]
) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"direction": "outbound"},
                "geometry": outbound,
            },
            {
                "type": "Feature",
                "properties": {"direction": "return"},
                "geometry": return_geometry,
            },
        ],
    }


def route_cache_key(provider: WalkingRouteProvider, request: WalkingRouteRequest) -> str:
    payload = {
        "schema_version": ROUTE_CACHE_SCHEMA_VERSION,
        "provider": provider.name,
        "provider_version": provider.version,
        "profile": request.profile,
        "origin": request.origin.model_dump(mode="json"),
        "destination": request.destination.model_dump(mode="json"),
        "options": request.options,
    }
    return canonical_sha256(payload)


def _distance_m(left: list[float], right: list[float]) -> float:
    lon1, lat1 = map(math.radians, left[:2])
    lon2, lat2 = map(math.radians, right[:2])
    delta_lon = lon2 - lon1
    delta_lat = lat2 - lat1
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 6_371_008.8 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def _elevation_samples(coordinates: list[Any]) -> list[ElevationSample]:
    samples: list[ElevationSample] = []
    distance = 0.0
    previous: list[float] | None = None
    for raw in coordinates:
        if not isinstance(raw, list) or len(raw) < 3:
            previous = raw if isinstance(raw, list) and len(raw) >= 2 else previous
            continue
        point = [float(raw[0]), float(raw[1])]
        if previous is not None:
            distance += _distance_m(previous, point)
        samples.append(
            ElevationSample(distance_m=round(distance, 1), elevation_m=float(raw[2]))
        )
        previous = point
    return samples


def parse_ors_response(
    raw: Any,
    *,
    provider: str,
    provider_version: str,
    retrieved_at: datetime | None = None,
) -> ProviderRoute:
    try:
        feature = raw["features"][0]
        properties = feature["properties"]
        summary = properties["summary"]
        geometry = feature["geometry"]
        coordinates = geometry["coordinates"]
        if geometry.get("type") != "LineString" or len(coordinates) < 2:
            raise ValueError("route geometry is not a LineString")
        segments = properties.get("segments") or []
        steps = [
            step
            for segment in segments
            for step in (segment.get("steps") or [])
        ]
        ascent_values = [segment.get("ascent") for segment in segments]
        descent_values = [segment.get("descent") for segment in segments]
        cleaned_geometry = {
            "type": "LineString",
            "coordinates": [
                [float(point[0]), float(point[1])]
                for point in coordinates
            ],
        }
        return ProviderRoute(
            distance_m=float(summary["distance"]),
            duration_seconds=float(summary["duration"]),
            ascent_m=(
                sum(float(value) for value in ascent_values if value is not None)
                if any(value is not None for value in ascent_values)
                else None
            ),
            descent_m=(
                sum(float(value) for value in descent_values if value is not None)
                if any(value is not None for value in descent_values)
                else None
            ),
            elevation_profile=_elevation_samples(coordinates),
            manoeuvres=[
                RouteManoeuvre(
                    instruction=str(step["instruction"]),
                    distance_m=float(step.get("distance") or 0),
                    duration_seconds=float(step.get("duration") or 0),
                    type=step.get("type"),
                    way_name=(str(step["name"]) if step.get("name") else None),
                )
                for step in steps
                if isinstance(step, dict) and step.get("instruction")
            ],
            geometry=cleaned_geometry,
            retrieved_at=retrieved_at or datetime.now(UTC),
            provider=provider,
            provider_version=provider_version,
            licence="OpenStreetMap data under ODbL 1.0; provider terms also apply",
            attribution="openrouteservice by HeiGIT; © OpenStreetMap contributors",
            limitations=[
                "Walking routes are provider-computed plans and must be checked against current closures and local conditions.",
                "Elevation can be absent or partial in provider responses.",
            ],
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RouteProviderError(
            RouteErrorCategory.malformed_response,
            "Routing provider response did not match the expected GeoJSON contract.",
            provider=provider,
        ) from exc


class FixtureWalkingRouteProvider:
    name = "fixture-openrouteservice"
    version = "ors-api-shaped-2026-09-04"

    def __init__(
        self,
        path: Path = FIXTURE_DIR / "ors-foot-walking-routes.json",
    ) -> None:
        self.path = path

    def route(self, request: WalkingRouteRequest) -> ProviderRoute:
        try:
            fixture = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RouteProviderError(
                RouteErrorCategory.provider_unavailable,
                "The saved walking-route fixture is missing or malformed.",
                provider=self.name,
            ) from exc
        payload = fixture.get("payload")
        provenance = fixture.get("provenance") or {}
        if canonical_sha256(payload) != provenance.get("checksum_sha256"):
            raise RouteProviderError(
                RouteErrorCategory.malformed_response,
                "The saved walking-route fixture checksum is invalid.",
                provider=self.name,
            )
        direction = str(request.options.get("direction") or "outbound")
        match = next(
            (
                item
                for item in payload.get("routes") or []
                if item.get("site_id") == request.site_id
                and item.get("entrance_id") == request.entrance_id
                and item.get("direction") == direction
            ),
            None,
        )
        if match is None:
            raise RouteProviderError(
                RouteErrorCategory.no_route,
                "No saved route exists for this fixture entrance.",
                provider=self.name,
            )
        route = parse_ors_response(
            match.get("response"),
            provider=self.name,
            provider_version=self.version,
            retrieved_at=datetime.fromisoformat(
                str(provenance["retrieved_at_utc"]).replace("Z", "+00:00")
            ),
        )
        coordinates = route.geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) < 2:
            raise RouteProviderError(
                RouteErrorCategory.malformed_response,
                "The saved route fixture has no auditable endpoints.",
                provider=self.name,
            )
        start = coordinates[0]
        end = coordinates[-1]

        def matches(point: WGS84Point, coordinate: Any) -> bool:
            return (
                isinstance(coordinate, list)
                and len(coordinate) >= 2
                and isinstance(coordinate[0], (int, float))
                and isinstance(coordinate[1], (int, float))
                and abs(point.longitude - float(coordinate[0])) <= 0.0001
                and abs(point.latitude - float(coordinate[1])) <= 0.0001
            )

        if not matches(request.origin, start) or not matches(request.destination, end):
            raise RouteProviderError(
                RouteErrorCategory.no_route,
                "The saved route is available only for its planned public fixture endpoints.",
                provider=self.name,
            )
        return route.model_copy(
            update={
                "licence": str(provenance["licence"]),
                "attribution": str(provenance["attribution"]),
                "limitations": list(provenance.get("known_limitations") or []),
            }
        )


class OpenRouteServiceWalkingProvider:
    name = "openrouteservice"
    version = "v2-foot-walking-geojson"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = 15,
        max_attempts: int = 2,
    ) -> None:
        self.api_key = api_key or os.getenv("ORS_API_KEY")
        self.client = client or httpx.Client(timeout=timeout_seconds)
        self.max_attempts = max_attempts
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be 1..3")

    def route(self, request: WalkingRouteRequest) -> ProviderRoute:
        if not self.api_key:
            raise RouteProviderError(
                RouteErrorCategory.authentication,
                "ORS_API_KEY is not configured on the backend.",
                provider=self.name,
            )
        body = {
            "coordinates": [
                [request.origin.longitude, request.origin.latitude],
                [request.destination.longitude, request.destination.latitude],
            ],
            "elevation": True,
            "instructions": True,
            **{key: value for key, value in request.options.items() if key != "direction"},
        }
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.client.post(
                    ORS_ENDPOINT,
                    headers={
                        "Authorization": self.api_key,
                        "Accept": "application/geo+json, application/json",
                        "Content-Type": "application/json",
                        "User-Agent": "London-Biodiversity-Expedition-Planner/5.0",
                    },
                    json=body,
                )
            except httpx.TimeoutException:
                error = RouteProviderError(
                    RouteErrorCategory.timeout,
                    "openrouteservice timed out.",
                    provider=self.name,
                    retryable=True,
                )
            except httpx.HTTPError:
                error = RouteProviderError(
                    RouteErrorCategory.provider_unavailable,
                    "openrouteservice could not be reached.",
                    provider=self.name,
                    retryable=True,
                )
            else:
                if response.status_code in {401, 403}:
                    raise RouteProviderError(
                        RouteErrorCategory.authentication,
                        "openrouteservice rejected the backend credential.",
                        provider=self.name,
                    )
                if response.status_code == 429:
                    error = RouteProviderError(
                        RouteErrorCategory.quota,
                        "openrouteservice quota is unavailable.",
                        provider=self.name,
                        retryable=True,
                    )
                elif response.status_code in {404, 422}:
                    raise RouteProviderError(
                        RouteErrorCategory.no_route,
                        "openrouteservice found no usable walking route.",
                        provider=self.name,
                    )
                elif response.status_code >= 500:
                    error = RouteProviderError(
                        RouteErrorCategory.provider_unavailable,
                        "openrouteservice is temporarily unavailable.",
                        provider=self.name,
                        retryable=True,
                    )
                elif response.status_code >= 400:
                    raise RouteProviderError(
                        RouteErrorCategory.malformed_response,
                        "openrouteservice rejected the bounded route request.",
                        provider=self.name,
                    )
                else:
                    try:
                        raw = response.json()
                    except ValueError as exc:
                        raise RouteProviderError(
                            RouteErrorCategory.malformed_response,
                            "openrouteservice returned invalid JSON.",
                            provider=self.name,
                        ) from exc
                    return parse_ors_response(
                        raw,
                        provider=self.name,
                        provider_version=self.version,
                    )
            if attempt < self.max_attempts:
                time.sleep(0.25 * attempt)
        raise error


class GraphHopperWalkingProvider:
    """Optional second live adapter; never used as a fixture fallback."""

    name = "graphhopper"
    version = "route-api-v1-foot"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("GRAPHHOPPER_API_KEY")
        self.client = client or httpx.Client(timeout=15)

    def route(self, request: WalkingRouteRequest) -> ProviderRoute:
        if not self.api_key:
            raise RouteProviderError(
                RouteErrorCategory.authentication,
                "GRAPHHOPPER_API_KEY is not configured on the backend.",
                provider=self.name,
            )
        try:
            response = self.client.get(
                GRAPHHOPPER_ENDPOINT,
                params=[
                    ("point", f"{request.origin.latitude},{request.origin.longitude}"),
                    (
                        "point",
                        f"{request.destination.latitude},{request.destination.longitude}",
                    ),
                    ("profile", "foot"),
                    ("points_encoded", "false"),
                    ("instructions", "true"),
                    ("elevation", "true"),
                    ("key", self.api_key),
                ],
            )
        except httpx.TimeoutException as exc:
            raise RouteProviderError(
                RouteErrorCategory.timeout,
                "GraphHopper timed out.",
                provider=self.name,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise RouteProviderError(
                RouteErrorCategory.provider_unavailable,
                "GraphHopper could not be reached.",
                provider=self.name,
                retryable=True,
            ) from exc
        if response.status_code in {401, 403}:
            raise RouteProviderError(
                RouteErrorCategory.authentication,
                "GraphHopper rejected the backend credential.",
                provider=self.name,
            )
        if response.status_code == 429:
            raise RouteProviderError(
                RouteErrorCategory.quota,
                "GraphHopper quota is unavailable.",
                provider=self.name,
                retryable=True,
            )
        if response.status_code >= 500:
            raise RouteProviderError(
                RouteErrorCategory.provider_unavailable,
                "GraphHopper is temporarily unavailable.",
                provider=self.name,
                retryable=True,
            )
        try:
            path = response.json()["paths"][0]
            coordinates = path["points"]["coordinates"]
            geometry = {
                "type": "LineString",
                "coordinates": [point[:2] for point in coordinates],
            }
            instructions = path.get("instructions") or []
            return ProviderRoute(
                distance_m=float(path["distance"]),
                duration_seconds=float(path["time"]) / 1000,
                ascent_m=path.get("ascend"),
                descent_m=path.get("descend"),
                elevation_profile=_elevation_samples(coordinates),
                manoeuvres=[
                    RouteManoeuvre(
                        instruction=str(item["text"]),
                        distance_m=float(item.get("distance") or 0),
                        duration_seconds=float(item.get("time") or 0) / 1000,
                        type=item.get("sign"),
                        way_name=(str(item["street_name"]) if item.get("street_name") else None),
                    )
                    for item in instructions
                    if item.get("text")
                ],
                geometry=geometry,
                retrieved_at=datetime.now(UTC),
                provider=self.name,
                provider_version=self.version,
                licence="OpenStreetMap data under ODbL 1.0; GraphHopper terms apply",
                attribution="GraphHopper; © OpenStreetMap contributors",
                limitations=[
                    "Walking routes must be checked against current closures and local conditions."
                ],
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RouteProviderError(
                RouteErrorCategory.malformed_response,
                "GraphHopper response did not match the expected contract.",
                provider=self.name,
            ) from exc


class FailoverWalkingRouteProvider:
    def __init__(
        self,
        primary: WalkingRouteProvider,
        secondary: WalkingRouteProvider | None = None,
        *,
        event_callback: RouteEventCallback | None = None,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.event_callback = event_callback
        self.name = primary.name
        self.version = primary.version

    def _event(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_callback:
            self.event_callback(event_type, payload)

    def route(self, request: WalkingRouteRequest) -> ProviderRoute:
        providers = [self.primary, *([self.secondary] if self.secondary else [])]
        last: RouteProviderError | None = None
        for attempt, provider in enumerate(providers, start=1):
            assert provider is not None
            self._event(
                "provider_attempt",
                {
                    "provider": provider.name,
                    "attempt": attempt,
                    "route_id": request.request_id,
                },
            )
            try:
                result = provider.route(request)
            except RouteProviderError as exc:
                last = exc
                self._event(
                    "routing_provider_failed",
                    {
                        "provider": provider.name,
                        "route_id": request.request_id,
                        "error_category": exc.category.value,
                    },
                )
                continue
            if attempt > 1:
                self._event(
                    "provider_failover",
                    {
                        "provider": provider.name,
                        "route_id": request.request_id,
                        "status": "completed",
                    },
                )
            return result
        assert last is not None
        if self.secondary is None:
            raise RouteProviderError(
                last.category,
                f"{last.message} failover_not_configured.",
                provider=last.provider,
                retryable=last.retryable,
            ) from last
        raise last


@dataclass(frozen=True)
class RoutingServices:
    entrances: EntranceRepository
    provider: WalkingRouteProvider
    cache: RouteCache
    geometry_store: RouteGeometryStore
    event_callback: RouteEventCallback | None = None
    maximum_candidates: int = MAX_ROUTE_CANDIDATES

    @classmethod
    def fixture(
        cls,
        *,
        runtime_directory: Path | None = None,
        event_callback: RouteEventCallback | None = None,
    ) -> RoutingServices:
        if runtime_directory is None:
            cache: RouteCache = MemoryRouteCache()
            geometries: RouteGeometryStore = MemoryRouteGeometryStore()
        else:
            cache = JsonFileRouteCache(runtime_directory / "cache")
            geometries = FileRouteGeometryStore(runtime_directory / "geometries")
        return cls(
            entrances=SnapshotEntranceRepository(),
            provider=FixtureWalkingRouteProvider(),
            cache=cache,
            geometry_store=geometries,
            event_callback=event_callback,
        )

    @classmethod
    def live(
        cls,
        *,
        runtime_directory: Path,
        event_callback: RouteEventCallback | None = None,
    ) -> RoutingServices:
        primary = OpenRouteServiceWalkingProvider()
        secondary = GraphHopperWalkingProvider() if os.getenv("GRAPHHOPPER_API_KEY") else None
        provider = FailoverWalkingRouteProvider(
            primary,
            secondary,
            event_callback=event_callback,
        )
        return cls(
            entrances=SnapshotEntranceRepository(),
            provider=provider,
            cache=JsonFileRouteCache(runtime_directory / "cache"),
            geometry_store=FileRouteGeometryStore(runtime_directory / "geometries"),
            event_callback=event_callback,
        )


def _event(services: RoutingServices, event_type: str, **payload: Any) -> None:
    if services.event_callback:
        services.event_callback(event_type, payload)


def _cached_route(
    services: RoutingServices, request: WalkingRouteRequest
) -> tuple[ProviderRoute, RouteCacheStatus]:
    key = route_cache_key(services.provider, request)
    cached = services.cache.get(key)
    if cached is not None:
        _event(
            services,
            "route_cache_hit",
            provider=cached.provider,
            route_id=request.request_id,
            cache_status="hit",
        )
        return cached, RouteCacheStatus.hit
    _event(
        services,
        "route_cache_miss",
        provider=services.provider.name,
        route_id=request.request_id,
        cache_status="miss",
    )
    started = time.perf_counter_ns()
    _event(
        services,
        "routing_provider_started",
        provider=services.provider.name,
        route_id=request.request_id,
    )
    try:
        value = services.provider.route(request)
    except RouteProviderError as exc:
        _event(
            services,
            "routing_provider_failed",
            provider=exc.provider,
            route_id=request.request_id,
            error_category=exc.category.value,
            duration_ms=(time.perf_counter_ns() - started) / 1_000_000,
        )
        raise
    _event(
        services,
        "routing_provider_completed",
        provider=value.provider,
        route_id=request.request_id,
        cache_status="miss",
        duration_ms=(time.perf_counter_ns() - started) / 1_000_000,
    )
    services.cache.put(key, value)
    return value, RouteCacheStatus.miss


def _combined_elevation(
    outbound: ProviderRoute, return_route: ProviderRoute
) -> list[ElevationSample]:
    samples = list(outbound.elevation_profile)
    samples.extend(
        ElevationSample(
            distance_m=round(outbound.distance_m + sample.distance_m, 1),
            elevation_m=sample.elevation_m,
        )
        for sample in return_route.elevation_profile
    )
    return samples


def _route_option(
    *,
    services: RoutingServices,
    request: ExpeditionRequest,
    origin: WGS84Point,
    site: PublicSiteCandidate,
    site_order: int,
    entrance: PublicEntranceCandidate,
) -> RouteOption:
    option_id = f"route-option-{site_order + 1:02d}"
    base_options = {"preference": "recommended"}
    outbound_request = WalkingRouteRequest(
        request_id=f"{option_id}-outbound",
        site_id=site.site_id,
        entrance_id=entrance.entrance_id,
        origin=origin,
        destination=entrance.point,
        options={**base_options, "direction": "outbound"},
    )
    return_request = WalkingRouteRequest(
        request_id=f"{option_id}-return",
        site_id=site.site_id,
        entrance_id=entrance.entrance_id,
        origin=entrance.point,
        destination=origin,
        options={**base_options, "direction": "return"},
    )
    try:
        outbound, outbound_cache = _cached_route(services, outbound_request)
        return_route, return_cache = _cached_route(services, return_request)
    except RouteProviderError as exc:
        return RouteOption(
            option_id=option_id,
            status=(
                RouteStatus.no_route
                if exc.category == RouteErrorCategory.no_route
                else RouteStatus.source_unavailable
            ),
            site_id=site.site_id,
            site_name=site.name,
            evidence_site_order=site_order,
            entrance=entrance,
            feasible=False,
            warnings=[f"{exc.category.value}: {exc.message}"],
        )
    route_reference = "route-" + hashlib.sha256(
        (
            route_cache_key(services.provider, outbound_request)
            + route_cache_key(services.provider, return_request)
        ).encode()
    ).hexdigest()[:24]
    services.geometry_store.put(
        route_reference,
        outbound=outbound.geometry,
        return_geometry=return_route.geometry,
    )
    total_distance = outbound.distance_m + return_route.distance_m
    total_duration = outbound.duration_seconds + return_route.duration_seconds
    walking_limit = request.maximum_walking_distance_km
    walking_passed = walking_limit is None or total_distance <= walking_limit * 1000
    expedition_seconds = request.duration_hours * 3600
    duration_passed = total_duration < expedition_seconds
    constraints = [
        RouteConstraintResult(
            code="maximum_walking_distance",
            passed=walking_passed,
            actual_value=round(total_distance / 1000, 3),
            limit_value=walking_limit,
            unit="km",
            message=(
                "No explicit walking limit supplied; the computed round trip is reported without inventing a limit."
                if walking_limit is None
                else (
                    "The full outbound and return walking distance is within the supplied limit."
                    if walking_passed
                    else "The full outbound and return walking distance exceeds the supplied limit."
                )
            ),
        ),
        RouteConstraintResult(
            code="expedition_duration",
            passed=duration_passed,
            actual_value=round(total_duration / 60, 1),
            limit_value=round(request.duration_hours * 60, 1),
            unit="minutes",
            message=(
                "Walking leaves positive field time within the expedition duration."
                if duration_passed
                else "Walking consumes or exceeds the complete expedition duration."
            ),
        ),
    ]
    ascent_values = [outbound.ascent_m, return_route.ascent_m]
    descent_values = [outbound.descent_m, return_route.descent_m]
    cache_status = (
        RouteCacheStatus.hit
        if outbound_cache == return_cache == RouteCacheStatus.hit
        else RouteCacheStatus.miss
    )
    evidence = WalkingRouteEvidence(
        route_id=option_id,
        site_id=site.site_id,
        entrance_id=entrance.entrance_id,
        provider=outbound.provider,
        provider_version=outbound.provider_version,
        routing_profile="foot-walking",
        outbound=RouteLeg(
            leg_id=f"{option_id}-outbound",
            direction="outbound",
            distance_m=outbound.distance_m,
            duration_seconds=outbound.duration_seconds,
            ascent_m=outbound.ascent_m,
            descent_m=outbound.descent_m,
            manoeuvres=outbound.manoeuvres,
            geometry_reference=f"{route_reference}:outbound",
        ),
        return_leg=RouteLeg(
            leg_id=f"{option_id}-return",
            direction="return",
            distance_m=return_route.distance_m,
            duration_seconds=return_route.duration_seconds,
            ascent_m=return_route.ascent_m,
            descent_m=return_route.descent_m,
            manoeuvres=return_route.manoeuvres,
            geometry_reference=f"{route_reference}:return",
        ),
        total_distance_m=total_distance,
        total_duration_seconds=total_duration,
        ascent_m=(
            sum(value for value in ascent_values if value is not None)
            if any(value is not None for value in ascent_values)
            else None
        ),
        descent_m=(
            sum(value for value in descent_values if value is not None)
            if any(value is not None for value in descent_values)
            else None
        ),
        elevation_profile=_combined_elevation(outbound, return_route),
        route_geometry_reference=route_reference,
        retrieved_at=max(outbound.retrieved_at, return_route.retrieved_at),
        licence=outbound.licence,
        attribution=outbound.attribution,
        cache_status=cache_status,
        limitations=list(
            dict.fromkeys([*outbound.limitations, *return_route.limitations])
        ),
    )
    feasible = all(item.passed for item in constraints)
    return RouteOption(
        option_id=option_id,
        status=RouteStatus.ready if feasible else RouteStatus.no_feasible_route,
        site_id=site.site_id,
        site_name=site.name,
        evidence_site_order=site_order,
        entrance=entrance,
        route=evidence,
        constraint_results=constraints,
        feasible=feasible,
        ranking_key=[
            0 if entrance.access_certainty == AccessCertainty.explicit_public else 1,
            site_order,
            round(total_distance, 3),
            entrance.entrance_id,
        ],
        warnings=(
            ["Entrance access is uncertain and requires explicit user acceptance."]
            if entrance.access_certainty == AccessCertainty.unspecified
            else []
        ),
    )


def plan_walking_routes(
    request: ExpeditionRequest,
    location: ResolvedLocation,
    candidate_sites: list[PublicSiteCandidate],
    *,
    services: RoutingServices,
    allow_uncertain_entrance: bool = False,
    resolved_entrances: list[PublicEntranceCandidate] | None = None,
) -> tuple[ValidatedWalkingPlan, list[RouteOption], bool]:
    """Resolve verified endpoints, request bounded round trips, validate and rank."""

    expedition_minutes = request.duration_hours * 60
    if not candidate_sites:
        return (
            ValidatedWalkingPlan(
                status=RouteStatus.no_candidate_site,
                expedition_duration_minutes=expedition_minutes,
                limitations=["No directly grounded candidate site was available."],
            ),
            [],
            False,
        )
    if location.status.value != "resolved" or location.rounded_start_point is None:
        return (
            ValidatedWalkingPlan(
                status=RouteStatus.source_unavailable,
                expedition_duration_minutes=expedition_minutes,
                limitations=["A validated internal routing origin was unavailable."],
            ),
            [],
            False,
        )
    _event(services, "entrance_resolution_started", candidate_count=len(candidate_sites))
    routable: list[tuple[int, PublicSiteCandidate, PublicEntranceCandidate]] = []
    uncertain_available = False
    for site_order, site in enumerate(candidate_sites):
        entrances = (
            [item for item in resolved_entrances if item.site_id == site.site_id]
            if resolved_entrances is not None
            else services.entrances.for_site(site)
        )
        uncertain_available = uncertain_available or any(
            item.access_certainty == AccessCertainty.unspecified for item in entrances
        )
        acceptable = [
            entrance
            for entrance in entrances
            if entrance.access_certainty == AccessCertainty.explicit_public
            or allow_uncertain_entrance
        ]
        if acceptable:
            routable.append((site_order, site, acceptable[0]))
        if len(routable) >= services.maximum_candidates:
            break
    _event(
        services,
        "entrance_resolution_completed",
        candidate_count=len(routable),
        status="completed",
    )
    if not routable:
        return (
            ValidatedWalkingPlan(
                status=RouteStatus.no_valid_entrance,
                expedition_duration_minutes=expedition_minutes,
                warnings=(
                    ["Uncertain mapped entrances are available only with explicit user acceptance."]
                    if uncertain_available
                    else []
                ),
                limitations=[
                    "No acceptable OSM boundary-member entrance was available; no centroid or nearest-road substitute was used."
                ],
            ),
            [],
            uncertain_available,
        )
    options = [
        _route_option(
            services=services,
            request=request,
            origin=location.rounded_start_point,
            site=site,
            site_order=site_order,
            entrance=entrance,
        )
        for site_order, site, entrance in routable
    ]
    _event(
        services,
        "route_validation_completed",
        candidate_count=len(options),
        status="completed",
    )
    feasible = sorted(
        (option for option in options if option.feasible and option.route),
        key=lambda option: tuple(option.ranking_key),
    )
    _event(
        services,
        "route_ranking_completed",
        candidate_count=len(feasible),
        status="completed",
    )
    if not feasible:
        statuses = {option.status for option in options}
        status = (
            RouteStatus.source_unavailable
            if statuses == {RouteStatus.source_unavailable}
            else RouteStatus.no_route
            if statuses <= {RouteStatus.no_route, RouteStatus.source_unavailable}
            else RouteStatus.no_feasible_route
        )
        return (
            ValidatedWalkingPlan(
                status=status,
                expedition_duration_minutes=expedition_minutes,
                constraint_results=[
                    constraint
                    for option in options
                    for constraint in option.constraint_results
                ],
                warnings=list(
                    dict.fromkeys(
                        warning for option in options for warning in option.warnings
                    )
                ),
                limitations=[
                    "No route passed both the full-excursion walking-distance and expedition-duration constraints."
                ],
            ),
            options,
            uncertain_available,
        )
    selected = feasible[0]
    route = selected.route
    assert route is not None
    return (
        ValidatedWalkingPlan(
            status=RouteStatus.ready,
            selected_site_id=selected.site_id,
            selected_site_name=selected.site_name,
            entrance=selected.entrance,
            routing_profile=route.routing_profile,
            outbound_distance_km=round(route.outbound.distance_m / 1000, 3),
            return_distance_km=round(route.return_leg.distance_m / 1000, 3),
            total_distance_km=round(route.total_distance_m / 1000, 3),
            walking_duration_minutes=round(route.total_duration_seconds / 60, 1),
            expedition_duration_minutes=round(expedition_minutes, 1),
            remaining_field_time_minutes=round(
                expedition_minutes - route.total_duration_seconds / 60, 1
            ),
            ascent_m=route.ascent_m,
            descent_m=route.descent_m,
            elevation_profile=route.elevation_profile,
            route_geometry_reference=route.route_geometry_reference,
            provider=route.provider,
            provider_version=route.provider_version,
            retrieved_at=route.retrieved_at,
            licence=route.licence,
            attribution=route.attribution,
            cache_status=route.cache_status,
            constraint_results=selected.constraint_results,
            alternative_feasible_routes=feasible[1:],
            warnings=selected.warnings,
            limitations=list(
                dict.fromkeys(
                    [
                        *route.limitations,
                        "Historical occurrence evidence does not predict or guarantee a sighting.",
                        "The route endpoint is the mapped public-site entrance, never an occurrence point, evidence-cell centre or polygon centroid.",
                    ]
                )
            ),
        ),
        options,
        uncertain_available,
    )


DEFAULT_ROUTE_RUNTIME_DIRECTORY = PROJECT_ROOT / "data" / "runtime" / "routes"
