"""Strict Phase 5 contracts for public entrances and validated walking routes."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from app.biodiversity.models import AccessCertainty, StrictModel, WGS84Point

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ROUTE_SCHEMA_VERSION = 1


class RouteStatus(str, Enum):
    ready = "ready"
    not_requested_without_strong_evidence = "not_requested_without_strong_evidence"
    no_candidate_site = "no_candidate_site"
    no_valid_entrance = "no_valid_entrance"
    source_unavailable = "source_unavailable"
    no_route = "no_route"
    no_feasible_route = "no_feasible_route"
    awaiting_user_choice = "awaiting_user_choice"


class RouteErrorCategory(str, Enum):
    authentication = "authentication"
    quota = "quota"
    timeout = "timeout"
    malformed_response = "malformed_response"
    no_route = "no_route"
    provider_unavailable = "provider_unavailable"


class RouteCacheStatus(str, Enum):
    hit = "hit"
    miss = "miss"
    bypassed = "bypassed"


class PublicEntranceCandidate(StrictModel):
    """A tagged OSM point with an auditable association to one site identity."""

    entrance_id: NonEmptyText
    site_id: NonEmptyText
    site_osm_type: str = Field(pattern=r"^(node|way|relation)$")
    site_osm_id: int = Field(gt=0)
    label: NonEmptyText
    point: WGS84Point
    access_certainty: AccessCertainty
    routing_entrance: str | None = None
    entrance: str | None = None
    barrier: str | None = None
    access: str | None = None
    foot: str | None = None
    wheelchair: str | None = None
    opening_hours: str | None = None
    association_method: str = Field(pattern=r"^osm_boundary_member$")
    source_tags: dict[str, str] = Field(default_factory=dict)
    limitations: list[NonEmptyText] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_routing_destination(self) -> PublicEntranceCandidate:
        blocked = {"no", "private"}
        service_only = {"emergency", "emergency_access", "service", "exit", "exit_only"}
        if (self.access or "").casefold() in blocked:
            raise ValueError("a blocked access point cannot be a public entrance candidate")
        if (self.foot or "").casefold() in blocked:
            raise ValueError("a foot-prohibited point cannot be a walking entrance")
        values = {
            (self.entrance or "").casefold(),
            (self.routing_entrance or "").casefold(),
            (self.source_tags.get("service") or "").casefold(),
        }
        if values.intersection(service_only):
            raise ValueError("service, emergency, or exit-only points are not entrances")
        return self


class WalkingRouteRequest(StrictModel):
    request_id: NonEmptyText
    site_id: NonEmptyText
    entrance_id: NonEmptyText
    origin: WGS84Point
    destination: WGS84Point
    profile: str = Field(
        default="foot-walking",
        pattern=r"^(foot-walking|public-transport-and-walking)$",
    )
    options: dict[str, Any] = Field(default_factory=dict)


class RouteManoeuvre(StrictModel):
    instruction: NonEmptyText
    distance_m: float = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    type: int | str | None = None
    way_name: str | None = None


class ElevationSample(StrictModel):
    distance_m: float = Field(ge=0)
    elevation_m: float


class RouteLeg(StrictModel):
    leg_id: NonEmptyText
    direction: str = Field(pattern=r"^(outbound|return)$")
    distance_m: float = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    ascent_m: float | None = Field(default=None, ge=0)
    descent_m: float | None = Field(default=None, ge=0)
    manoeuvres: list[RouteManoeuvre] = Field(default_factory=list)
    geometry_reference: NonEmptyText


class JourneySegment(StrictModel):
    """Coordinate-free provider segment safe for plans, reports, and the UI."""

    segment_id: NonEmptyText
    direction: Literal["outbound", "return"]
    sequence: int = Field(ge=0)
    mode: NonEmptyText
    line_name: str | None = None
    instruction: NonEmptyText
    origin_label: str | None = None
    destination_label: str | None = None
    duration_minutes: float = Field(ge=0)
    distance_m: float | None = Field(default=None, ge=0)
    departure_time: str | None = None
    arrival_time: str | None = None


class WalkingRouteEvidence(StrictModel):
    schema_version: int = Field(default=ROUTE_SCHEMA_VERSION, ge=1)
    route_id: NonEmptyText
    site_id: NonEmptyText
    entrance_id: NonEmptyText
    provider: NonEmptyText
    provider_version: NonEmptyText
    routing_profile: str = Field(
        pattern=r"^(foot-walking|public-transport-and-walking)$"
    )
    outbound: RouteLeg
    return_leg: RouteLeg
    total_distance_m: float = Field(ge=0)
    total_duration_seconds: float = Field(ge=0)
    total_walking_duration_seconds: float | None = Field(default=None, ge=0)
    total_public_transport_duration_seconds: float = Field(default=0, ge=0)
    journey_segments: list[JourneySegment] = Field(default_factory=list)
    return_route_same_as_outbound: bool = False
    ascent_m: float | None = Field(default=None, ge=0)
    descent_m: float | None = Field(default=None, ge=0)
    elevation_profile: list[ElevationSample] = Field(default_factory=list)
    route_geometry_reference: NonEmptyText
    retrieved_at: datetime
    licence: NonEmptyText
    attribution: NonEmptyText
    cache_status: RouteCacheStatus
    limitations: list[NonEmptyText] = Field(default_factory=list)

    @model_validator(mode="after")
    def totals_match_legs(self) -> WalkingRouteEvidence:
        expected_distance = self.outbound.distance_m + self.return_leg.distance_m
        expected_duration = (
            self.outbound.duration_seconds + self.return_leg.duration_seconds
        )
        if abs(self.total_distance_m - expected_distance) > 0.1:
            raise ValueError("route distance total must equal outbound plus return")
        if abs(self.total_duration_seconds - expected_duration) > 0.1:
            raise ValueError("route duration total must equal outbound plus return")
        return self


class RouteConstraintResult(StrictModel):
    code: NonEmptyText
    passed: bool
    actual_value: float | None = None
    limit_value: float | None = None
    unit: str | None = None
    message: NonEmptyText


class RouteOption(StrictModel):
    option_id: NonEmptyText
    status: RouteStatus
    site_id: NonEmptyText
    site_name: NonEmptyText
    evidence_site_order: int = Field(ge=0)
    entrance: PublicEntranceCandidate
    route: WalkingRouteEvidence | None = None
    constraint_results: list[RouteConstraintResult] = Field(default_factory=list)
    feasible: bool = False
    ranking_key: list[int | float | str] = Field(default_factory=list)
    warnings: list[NonEmptyText] = Field(default_factory=list)


class ValidatedWalkingPlan(StrictModel):
    status: RouteStatus
    selected_site_id: NonEmptyText | None = None
    selected_site_name: NonEmptyText | None = None
    entrance: PublicEntranceCandidate | None = None
    routing_profile: str = Field(
        default="foot-walking",
        pattern=r"^(foot-walking|public-transport-and-walking)$",
    )
    journey_type: Literal[
        "walking_only", "public_transport_and_walking"
    ] = "walking_only"
    planning_departure_time_local: str | None = None
    outbound_distance_km: float | None = Field(default=None, ge=0)
    return_distance_km: float | None = Field(default=None, ge=0)
    total_distance_km: float | None = Field(default=None, ge=0)
    walking_duration_minutes: float | None = Field(default=None, ge=0)
    outbound_travel_duration_minutes: float | None = Field(default=None, ge=0)
    return_travel_duration_minutes: float | None = Field(default=None, ge=0)
    total_travel_duration_minutes: float | None = Field(default=None, ge=0)
    public_transport_duration_minutes: float | None = Field(default=None, ge=0)
    journey_segments: list[JourneySegment] = Field(default_factory=list)
    return_route_same_as_outbound: bool = False
    selection_rationale: str | None = None
    expedition_duration_minutes: float = Field(gt=0)
    remaining_field_time_minutes: float | None = Field(default=None, ge=0)
    ascent_m: float | None = Field(default=None, ge=0)
    descent_m: float | None = Field(default=None, ge=0)
    elevation_profile: list[ElevationSample] = Field(default_factory=list)
    route_geometry_reference: str | None = None
    provider: str | None = None
    provider_version: str | None = None
    retrieved_at: datetime | None = None
    licence: str | None = None
    attribution: str | None = None
    cache_status: RouteCacheStatus | None = None
    constraint_results: list[RouteConstraintResult] = Field(default_factory=list)
    alternative_feasible_routes: list[RouteOption] = Field(default_factory=list)
    warnings: list[NonEmptyText] = Field(default_factory=list)
    limitations: list[NonEmptyText] = Field(default_factory=list)

    @model_validator(mode="after")
    def ready_fields(self) -> ValidatedWalkingPlan:
        required = (
            self.selected_site_id,
            self.selected_site_name,
            self.entrance,
            self.outbound_distance_km,
            self.return_distance_km,
            self.total_distance_km,
            self.walking_duration_minutes,
            self.remaining_field_time_minutes,
            self.route_geometry_reference,
            self.provider,
            self.provider_version,
            self.retrieved_at,
            self.licence,
            self.attribution,
            self.cache_status,
        )
        if self.status == RouteStatus.ready and any(value is None for value in required):
            raise ValueError("a ready walking plan requires complete validated route facts")
        if self.status == RouteStatus.ready and not all(
            item.passed for item in self.constraint_results
        ):
            raise ValueError("a ready walking plan cannot contain failed constraints")
        return self


class RouteProviderError(RuntimeError):
    """Typed expected provider failure; messages never contain request bodies or keys."""

    def __init__(
        self,
        category: RouteErrorCategory,
        message: str,
        *,
        provider: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.provider = provider
        self.retryable = retryable
