"""Deterministic Phase 5 walking/multimodal providers, cache, geometry, and ranking."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
    JourneySegment,
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
from app.biodiversity.repositories import SnapshotGreenSpaceRepository
from app.feasibility.core import FIXTURE_DIR, PROJECT_ROOT, canonical_sha256
from app.feasibility.spatial import point_in_geometry

ORS_ENDPOINT = "https://api.heigit.org/openrouteservice/v2/directions/foot-walking/geojson"
GRAPHHOPPER_ENDPOINT = "https://graphhopper.com/api/1/route"
TFL_JOURNEY_ENDPOINT = "https://api.tfl.gov.uk/Journey/JourneyResults"
ROUTE_PROVIDER_VERSION = "phase-5-v1"
ROUTE_CACHE_SCHEMA_VERSION = 1
MAX_ROUTE_CANDIDATES = 3
MAX_ROUTE_ENTRANCES_PER_SITE = 2
MAX_INSIDE_APPROACH_METRES = 250.0
RouteEventCallback = Callable[[str, dict[str, Any]], None]


class ProviderRoute(StrictModel):
    """One provider journey.

    ``distance_m`` is walking distance. ``duration_seconds`` is complete travel
    time, which is identical to walking time for walking-only providers.
    """

    distance_m: float = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    walking_duration_seconds: float | None = Field(default=None, ge=0)
    public_transport_duration_seconds: float = Field(default=0, ge=0)
    ascent_m: float | None = Field(default=None, ge=0)
    descent_m: float | None = Field(default=None, ge=0)
    elevation_profile: list[ElevationSample] = Field(default_factory=list)
    manoeuvres: list[RouteManoeuvre] = Field(default_factory=list)
    journey_segments: list[JourneySegment] = Field(default_factory=list)
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


class SiteGeometryRepository(Protocol):
    def get(self, site_id: str) -> dict[str, Any] | None: ...


class SnapshotSiteGeometryRepository:
    """Read candidate footprints only for deterministic entrance-arrival checks."""

    def __init__(self, repository: SnapshotGreenSpaceRepository | None = None) -> None:
        self.repository = repository or SnapshotGreenSpaceRepository()
        self._values: dict[str, dict[str, Any]] | None = None

    def get(self, site_id: str) -> dict[str, Any] | None:
        if self._values is None:
            try:
                features, _provenance = self.repository.snapshot()
            except Exception:
                self._values = {}
            else:
                self._values = {
                    str(item.get("id")): dict(item.get("geometry") or {})
                    for item in features
                    if isinstance(item, dict) and item.get("id")
                }
        value = self._values.get(site_id)
        return json.loads(json.dumps(value)) if value else None


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
    outbound_coordinates = outbound.get("coordinates")
    return_coordinates = return_geometry.get("coordinates")
    same_path = (
        outbound.get("type") == "LineString"
        and return_geometry.get("type") == "LineString"
        and isinstance(outbound_coordinates, list)
        and isinstance(return_coordinates, list)
        and _paths_share_corridor(
            outbound_coordinates,
            list(reversed(return_coordinates)),
        )
    )
    if same_path:
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "direction": "outbound_return",
                        "same_path_both_directions": True,
                    },
                    "geometry": outbound,
                }
            ],
        }
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


def _point_to_segment_distance_m(
    point: list[float], start: list[float], end: list[float]
) -> float:
    latitude = math.radians((point[1] + start[1] + end[1]) / 3)
    longitude_scale = 111_320 * math.cos(latitude)
    latitude_scale = 110_540
    px, py = point[0] * longitude_scale, point[1] * latitude_scale
    sx, sy = start[0] * longitude_scale, start[1] * latitude_scale
    ex, ey = end[0] * longitude_scale, end[1] * latitude_scale
    dx, dy = ex - sx, ey - sy
    if dx == dy == 0:
        return math.hypot(px - sx, py - sy)
    ratio = max(0.0, min(1.0, ((px - sx) * dx + (py - sy) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (sx + ratio * dx), py - (sy + ratio * dy))


def _paths_share_corridor(
    first: list[Any], second: list[Any], *, tolerance_m: float = 35
) -> bool:
    """Return true only when reverse journeys materially share one corridor."""

    valid_first = [point for point in first if isinstance(point, list) and len(point) >= 2]
    valid_second = [point for point in second if isinstance(point, list) and len(point) >= 2]
    if len(valid_first) < 2 or len(valid_second) < 2:
        return False
    if (
        _distance_m(valid_first[0], valid_second[0]) > 50
        or _distance_m(valid_first[-1], valid_second[-1]) > 50
    ):
        return False

    def close_ratio(points: list[list[float]], path: list[list[float]]) -> float:
        close = sum(
            min(
                _point_to_segment_distance_m(point, path[index - 1], path[index])
                for index in range(1, len(path))
            )
            <= tolerance_m
            for point in points
        )
        return close / len(points)

    return close_ratio(valid_first, valid_second) >= 0.9 and close_ratio(
        valid_second, valid_first
    ) >= 0.9


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


def _parse_tfl_line_string(value: Any) -> list[list[float]]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        return []
    coordinates: list[list[float]] = []
    for point in value:
        if not isinstance(point, list) or len(point) < 2:
            continue
        # The TfL Journey API serialises path points as [latitude, longitude].
        coordinates.append([float(point[1]), float(point[0])])
    return coordinates


def parse_tfl_journey_response(
    raw: Any,
    *,
    request: WalkingRouteRequest,
    provider: str,
    provider_version: str,
    retrieved_at: datetime | None = None,
) -> ProviderRoute:
    """Parse the fastest returned TfL journey into coordinate-free and map facts."""

    try:
        journey = next(
            (
                item
                for item in raw["journeys"]
                if any(
                    str((leg.get("mode") or {}).get("id") or "") != "walking"
                    for leg in (item.get("legs") or [])
                )
            ),
            None,
        )
        if journey is None:
            raise RouteProviderError(
                RouteErrorCategory.no_route,
                "TfL Journey Planner returned no public-transport journey.",
                provider=provider,
            )
        legs = journey["legs"]
        if not isinstance(legs, list) or not legs:
            raise ValueError("journey has no legs")
        direction = str(request.options.get("direction") or "outbound")
        if direction not in {"outbound", "return"}:
            raise ValueError("invalid journey direction")
        coordinates: list[list[float]] = []
        segments: list[JourneySegment] = []
        manoeuvres: list[RouteManoeuvre] = []
        walking_distance = 0.0
        walking_duration = 0.0
        public_transport_duration = 0.0
        for index, leg in enumerate(legs):
            mode = str((leg.get("mode") or {}).get("id") or "unknown")
            duration_minutes = float(leg.get("duration") or 0)
            distance = float(leg.get("distance") or 0)
            instruction = leg.get("instruction") or {}
            instruction_text = str(
                instruction.get("summary")
                or instruction.get("detailed")
                or f"Continue by {mode}"
            )
            route_options = leg.get("routeOptions") or []
            route_option = route_options[0] if route_options else {}
            line_name = str(route_option.get("name") or "").strip() or None
            if mode == "walking":
                walking_distance += distance
                walking_duration += duration_minutes * 60
                manoeuvres.extend(
                    RouteManoeuvre(
                        instruction=str(step.get("description") or instruction_text),
                        distance_m=float(step.get("distance") or 0),
                        duration_seconds=float(step.get("travelTime") or 0),
                        type=step.get("turnDirection"),
                        way_name=(
                            str(step["streetName"])
                            if step.get("streetName")
                            else None
                        ),
                    )
                    for step in (instruction.get("steps") or [])
                    if isinstance(step, dict)
                )
            else:
                public_transport_duration += duration_minutes * 60
            departure = leg.get("departurePoint") or {}
            arrival = leg.get("arrivalPoint") or {}
            origin_label = (
                str(departure["commonName"])
                if departure.get("commonName")
                else None
            )
            destination_label = (
                str(arrival["commonName"])
                if arrival.get("commonName")
                else None
            )
            if direction == "outbound" and index == 0:
                origin_label = "Route origin"
            if direction == "return" and index == len(legs) - 1:
                destination_label = "Route origin"
            segments.append(
                JourneySegment(
                    segment_id=f"{request.request_id}-segment-{index + 1:02d}",
                    direction=direction,
                    sequence=index,
                    mode=mode,
                    line_name=line_name,
                    instruction=instruction_text,
                    origin_label=origin_label,
                    destination_label=destination_label,
                    duration_minutes=duration_minutes,
                    distance_m=distance if mode == "walking" else None,
                    departure_time=leg.get("departureTime"),
                    arrival_time=leg.get("arrivalTime"),
                )
            )
            leg_coordinates = _parse_tfl_line_string(
                (leg.get("path") or {}).get("lineString")
            )
            if coordinates and leg_coordinates and coordinates[-1] == leg_coordinates[0]:
                coordinates.extend(leg_coordinates[1:])
            else:
                coordinates.extend(leg_coordinates)
        if len(coordinates) < 2:
            raise ValueError("journey geometry is unavailable")
        coordinates[0] = [request.origin.longitude, request.origin.latitude]
        coordinates[-1] = [request.destination.longitude, request.destination.latitude]
        journey_duration_seconds = float(journey["duration"]) * 60
        public_transport_duration = max(
            public_transport_duration,
            journey_duration_seconds - walking_duration,
        )
        return ProviderRoute(
            distance_m=walking_distance,
            duration_seconds=journey_duration_seconds,
            walking_duration_seconds=walking_duration,
            public_transport_duration_seconds=public_transport_duration,
            manoeuvres=manoeuvres,
            journey_segments=segments,
            geometry={"type": "LineString", "coordinates": coordinates},
            retrieved_at=retrieved_at or datetime.now(UTC),
            provider=provider,
            provider_version=provider_version,
            licence="Transport for London open data terms; map data © OpenStreetMap contributors",
            attribution="Powered by Transport for London Journey Planner API; © OpenStreetMap contributors",
            limitations=[
                "Public-transport times are a planning result and must be checked for live disruption before travel.",
                "A 09:00 Europe/London departure is used when the request supplies no time of day.",
                "Elevation is unavailable for public-transport legs; only provider-supplied walking information is shown.",
            ],
        )
    except (
        AttributeError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise RouteProviderError(
            RouteErrorCategory.malformed_response,
            "TfL Journey Planner response did not match the expected contract.",
            provider=provider,
        ) from exc


class TfLJourneyProvider:
    """Fastest public-transport-and-walking journeys from the official TfL API."""

    name = "tfl-journey-planner"
    version = "unified-api-v1-least-time"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = 20,
        max_attempts: int = 2,
    ) -> None:
        self.api_key = api_key or os.getenv("TFL_API_KEY")
        self.client = client or httpx.Client(timeout=timeout_seconds)
        self.max_attempts = max_attempts
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be 1..3")

    def route(self, request: WalkingRouteRequest) -> ProviderRoute:
        if request.profile != "public-transport-and-walking":
            raise RouteProviderError(
                RouteErrorCategory.malformed_response,
                "TfL Journey Planner requires the public-transport-and-walking profile.",
                provider=self.name,
            )
        local_date = str(request.options.get("local_date") or "")
        local_time = str(request.options.get("local_time") or "")
        time_is = str(request.options.get("time_is") or "Departing")
        if (
            len(local_date) != 8
            or len(local_time) != 4
            or time_is not in {"Departing", "Arriving"}
        ):
            raise RouteProviderError(
                RouteErrorCategory.malformed_response,
                "TfL routing requires a validated local date, time and timeIs value.",
                provider=self.name,
            )
        url = (
            f"{TFL_JOURNEY_ENDPOINT}/"
            f"{request.origin.latitude:.6f},{request.origin.longitude:.6f}/to/"
            f"{request.destination.latitude:.6f},{request.destination.longitude:.6f}"
        )
        params: dict[str, str] = {
            "date": local_date,
            "time": local_time,
            "timeIs": time_is,
            "journeyPreference": "LeastTime",
            "mode": (
                "bus,tube,overground,elizabeth-line,national-rail,dlr,tram,walking"
            ),
            "walkingSpeed": "Average",
            "walkingOptimization": "True",
        }
        if self.api_key:
            params["app_key"] = self.api_key
        error: RouteProviderError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.client.get(
                    url,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "London-Biodiversity-Expedition-Planner/5.1",
                    },
                )
            except httpx.TimeoutException:
                error = RouteProviderError(
                    RouteErrorCategory.timeout,
                    "TfL Journey Planner timed out.",
                    provider=self.name,
                    retryable=True,
                )
            except httpx.HTTPError:
                error = RouteProviderError(
                    RouteErrorCategory.provider_unavailable,
                    "TfL Journey Planner could not be reached.",
                    provider=self.name,
                    retryable=True,
                )
            else:
                if response.status_code in {401, 403}:
                    raise RouteProviderError(
                        RouteErrorCategory.authentication,
                        "TfL Journey Planner rejected the backend credential.",
                        provider=self.name,
                    )
                if response.status_code == 429:
                    error = RouteProviderError(
                        RouteErrorCategory.quota,
                        "TfL Journey Planner quota is unavailable.",
                        provider=self.name,
                        retryable=True,
                    )
                elif response.status_code in {300, 404, 422}:
                    raise RouteProviderError(
                        RouteErrorCategory.no_route,
                        "TfL Journey Planner found no usable public-transport journey.",
                        provider=self.name,
                    )
                elif response.status_code >= 500:
                    error = RouteProviderError(
                        RouteErrorCategory.provider_unavailable,
                        "TfL Journey Planner is temporarily unavailable.",
                        provider=self.name,
                        retryable=True,
                    )
                elif response.status_code >= 400:
                    raise RouteProviderError(
                        RouteErrorCategory.malformed_response,
                        "TfL Journey Planner rejected the bounded route request.",
                        provider=self.name,
                    )
                else:
                    try:
                        raw = response.json()
                    except ValueError as exc:
                        raise RouteProviderError(
                            RouteErrorCategory.malformed_response,
                            "TfL Journey Planner returned invalid JSON.",
                            provider=self.name,
                        ) from exc
                    return parse_tfl_journey_response(
                        raw,
                        request=request,
                        provider=self.name,
                        provider_version=self.version,
                    )
            if attempt < self.max_attempts:
                time.sleep(0.25 * attempt)
        assert error is not None
        raise error


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
            **{
                key: value
                for key, value in request.options.items()
                if key
                not in {"direction", "local_date", "local_time", "time_is"}
            },
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
    site_geometries: SiteGeometryRepository | None = None
    routing_profile: str = "foot-walking"
    event_callback: RouteEventCallback | None = None
    maximum_candidates: int = MAX_ROUTE_CANDIDATES
    maximum_entrances_per_site: int = MAX_ROUTE_ENTRANCES_PER_SITE
    maximum_inside_approach_metres: float = MAX_INSIDE_APPROACH_METRES

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
            site_geometries=SnapshotSiteGeometryRepository(),
            event_callback=event_callback,
            maximum_entrances_per_site=3,
            # The compact fixture geometry is intentionally sparse; live routes
            # use the stricter default threshold below.
            maximum_inside_approach_metres=750,
        )

    @classmethod
    def live(
        cls,
        *,
        runtime_directory: Path,
        event_callback: RouteEventCallback | None = None,
    ) -> RoutingServices:
        provider = FailoverWalkingRouteProvider(
            TfLJourneyProvider(),
            event_callback=event_callback,
        )
        return cls(
            entrances=SnapshotEntranceRepository(),
            provider=provider,
            cache=JsonFileRouteCache(runtime_directory / "cache"),
            geometry_store=FileRouteGeometryStore(runtime_directory / "geometries"),
            site_geometries=SnapshotSiteGeometryRepository(),
            routing_profile="public-transport-and-walking",
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


def _walking_seconds(route: ProviderRoute) -> float:
    return (
        route.walking_duration_seconds
        if route.walking_duration_seconds is not None
        else route.duration_seconds
    )


def _same_path_in_reverse(
    outbound: dict[str, Any], return_geometry: dict[str, Any]
) -> bool:
    collection = _geometry_collection(outbound, return_geometry)
    return bool(
        collection.get("features")
        and collection["features"][0].get("properties", {}).get(
            "same_path_both_directions"
        )
    )


def _inside_approach_distance_m(
    route_geometry: dict[str, Any], site_geometry: dict[str, Any]
) -> float | None:
    """Distance travelled inside the target polygon before reaching its entrance."""

    if site_geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        return None
    coordinates = route_geometry.get("coordinates")
    if route_geometry.get("type") != "LineString" or not isinstance(coordinates, list):
        return None
    first_inside = next(
        (
            index
            for index, point in enumerate(coordinates)
            if isinstance(point, list)
            and len(point) >= 2
            and point_in_geometry(float(point[0]), float(point[1]), site_geometry)
        ),
        None,
    )
    if first_inside is None:
        return 0.0
    return sum(
        _distance_m(coordinates[index - 1], coordinates[index])
        for index in range(first_inside + 1, len(coordinates))
    )


def _route_option(
    *,
    services: RoutingServices,
    request: ExpeditionRequest,
    origin: WGS84Point,
    site: PublicSiteCandidate,
    site_order: int,
    entrance_order: int,
    entrance: PublicEntranceCandidate,
) -> RouteOption:
    option_id = f"route-option-{site_order + 1:02d}"
    if entrance_order:
        option_id += f"-entrance-{entrance_order + 1:02d}"
    base_options = {"preference": "recommended"}
    planning_start = datetime.combine(
        request.target_local_date, datetime.min.time()
    ).replace(hour=9)
    planning_end = planning_start + timedelta(hours=request.duration_hours)
    outbound_request = WalkingRouteRequest(
        request_id=f"{option_id}-outbound",
        site_id=site.site_id,
        entrance_id=entrance.entrance_id,
        origin=origin,
        destination=entrance.point,
        profile=services.routing_profile,
        options={
            **base_options,
            "direction": "outbound",
            "local_date": planning_start.strftime("%Y%m%d"),
            "local_time": planning_start.strftime("%H%M"),
            "time_is": "Departing",
        },
    )
    return_request = WalkingRouteRequest(
        request_id=f"{option_id}-return",
        site_id=site.site_id,
        entrance_id=entrance.entrance_id,
        origin=entrance.point,
        destination=origin,
        profile=services.routing_profile,
        options={
            **base_options,
            "direction": "return",
            "local_date": planning_end.strftime("%Y%m%d"),
            "local_time": planning_end.strftime("%H%M"),
            "time_is": "Arriving",
        },
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
    return_route_same_as_outbound = _same_path_in_reverse(
        outbound.geometry, return_route.geometry
    )
    services.geometry_store.put(
        route_reference,
        outbound=outbound.geometry,
        return_geometry=return_route.geometry,
    )
    total_distance = outbound.distance_m + return_route.distance_m
    total_duration = outbound.duration_seconds + return_route.duration_seconds
    total_walking_duration = _walking_seconds(outbound) + _walking_seconds(return_route)
    total_public_transport_duration = (
        outbound.public_transport_duration_seconds
        + return_route.public_transport_duration_seconds
    )
    walking_limit = request.maximum_walking_distance_km
    walking_passed = walking_limit is None or total_distance <= walking_limit * 1000
    expedition_seconds = request.duration_hours * 3600
    duration_passed = total_duration < expedition_seconds
    site_geometry = (
        services.site_geometries.get(site.site_id)
        if services.site_geometries is not None
        else None
    )
    inside_approach = (
        _inside_approach_distance_m(outbound.geometry, site_geometry)
        if site_geometry
        else None
    )
    entrance_approach_passed = (
        inside_approach is None
        or inside_approach <= services.maximum_inside_approach_metres
    )
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
                "Travel leaves positive field time within the complete outing duration."
                if duration_passed
                else "Travel consumes or exceeds the complete outing duration."
            ),
        ),
        RouteConstraintResult(
            code="verified_entrance_approach",
            passed=entrance_approach_passed,
            actual_value=(
                round(inside_approach, 1) if inside_approach is not None else None
            ),
            limit_value=(
                services.maximum_inside_approach_metres
                if inside_approach is not None
                else None
            ),
            unit="metres inside target site before endpoint",
            message=(
                "The route approaches the verified entrance from outside the selected site."
                if entrance_approach_passed and inside_approach is not None
                else (
                    "The saved footprint cannot support a polygon entrance-approach audit."
                    if inside_approach is None
                    else "The route enters and crosses too much of the selected site before reaching the claimed entrance."
                )
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
        routing_profile=services.routing_profile,
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
        total_walking_duration_seconds=total_walking_duration,
        total_public_transport_duration_seconds=total_public_transport_duration,
        journey_segments=[
            *outbound.journey_segments,
            *return_route.journey_segments,
        ],
        return_route_same_as_outbound=return_route_same_as_outbound,
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
            round(total_duration, 3),
            round(total_distance, 3),
            site_order,
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
    routable: list[
        tuple[int, int, PublicSiteCandidate, PublicEntranceCandidate]
    ] = []
    routable_site_count = 0
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
        explicit = [
            entrance
            for entrance in entrances
            if entrance.access_certainty == AccessCertainty.explicit_public
        ]
        uncertain = [
            entrance
            for entrance in entrances
            if entrance.access_certainty == AccessCertainty.unspecified
        ]
        def distance_key(
            entrance: PublicEntranceCandidate,
        ) -> tuple[float, str]:
            return (
                _distance_m(
                    [
                        location.rounded_start_point.longitude,
                        location.rounded_start_point.latitude,
                    ],
                    [entrance.point.longitude, entrance.point.latitude],
                ),
                entrance.entrance_id,
            )
        selected_entrances = sorted(explicit, key=distance_key)[
            : services.maximum_entrances_per_site
        ]
        if allow_uncertain_entrance and uncertain:
            selected_entrances = [
                *selected_entrances[:1],
                sorted(uncertain, key=distance_key)[0],
            ]
        if selected_entrances:
            routable_site_count += 1
        for entrance_order, entrance in enumerate(selected_entrances):
            routable.append((site_order, entrance_order, site, entrance))
        if routable_site_count >= services.maximum_candidates:
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
            entrance_order=entrance_order,
            entrance=entrance,
        )
        for site_order, entrance_order, site, entrance in routable
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
    outbound_distance_km = round(route.outbound.distance_m / 1000, 3)
    return_distance_km = round(route.return_leg.distance_m / 1000, 3)
    return (
        ValidatedWalkingPlan(
            status=RouteStatus.ready,
            selected_site_id=selected.site_id,
            selected_site_name=selected.site_name,
            entrance=selected.entrance,
            routing_profile=route.routing_profile,
            journey_type=(
                "public_transport_and_walking"
                if route.routing_profile == "public-transport-and-walking"
                else "walking_only"
            ),
            planning_departure_time_local=(
                f"{request.target_local_date.isoformat()} 09:00 Europe/London"
                if route.routing_profile == "public-transport-and-walking"
                else None
            ),
            outbound_distance_km=outbound_distance_km,
            return_distance_km=return_distance_km,
            total_distance_km=round(
                outbound_distance_km + return_distance_km, 3
            ),
            walking_duration_minutes=round(
                (
                    route.total_walking_duration_seconds
                    if route.total_walking_duration_seconds is not None
                    else route.total_duration_seconds
                )
                / 60,
                1,
            ),
            outbound_travel_duration_minutes=round(
                route.outbound.duration_seconds / 60, 1
            ),
            return_travel_duration_minutes=round(
                route.return_leg.duration_seconds / 60, 1
            ),
            total_travel_duration_minutes=round(
                route.total_duration_seconds / 60, 1
            ),
            public_transport_duration_minutes=round(
                route.total_public_transport_duration_seconds / 60, 1
            ),
            journey_segments=route.journey_segments,
            return_route_same_as_outbound=route.return_route_same_as_outbound,
            selection_rationale=(
                "Selected by deterministic routing code: the historical-evidence gate passed; "
                "the site is directly grounded; the entrance is acceptable; all route constraints passed. "
                f"{len(options)} bounded route option{'s' if len(options) != 1 else ''} were evaluated across up to "
                f"{services.maximum_candidates} directly grounded sites with mapped entrances, encountered in evidence order. "
                "Explicit-public entrance access was preferred, followed by the least complete travel time; "
                f"walking distance, evidence order and entrance ID provide stable tie-breaks across {len(feasible)} feasible option"
                f"{'s' if len(feasible) != 1 else ''}. "
                "The model did not choose or recalculate the destination."
            ),
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
