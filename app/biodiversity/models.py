"""Strict, frontend-independent contracts for the biodiversity backend."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RainPreference(str, Enum):
    no_preference = "no_preference"
    avoid_heavy_rain = "avoid_heavy_rain"


class LocationStatus(str, Enum):
    resolved = "resolved"
    invalid_input = "invalid_input"
    postcode_not_found = "postcode_not_found"
    outside_supported_area = "outside_supported_area"
    source_unavailable = "source_unavailable"
    malformed_upstream_response = "malformed_upstream_response"
    fixture_unavailable = "fixture_unavailable"


class TaxonStatus(str, Enum):
    resolved = "resolved"
    human_selection_required = "human_selection_required"
    taxon_not_found = "taxon_not_found"
    source_unavailable = "source_unavailable"
    malformed_upstream_response = "malformed_upstream_response"
    fixture_unavailable = "fixture_unavailable"


class EvidenceOutcome(str, Enum):
    strong_map_evidence = "strong_map_evidence"
    limited_contextual_evidence = "limited_contextual_evidence"
    insufficient_evidence = "insufficient_evidence"
    human_selection_required = "human_selection_required"
    taxon_not_found = "taxon_not_found"
    source_unavailable = "source_unavailable"


class EvidenceUse(str, Enum):
    spatial_ranking = "spatial_ranking"
    broad_zone_only = "broad_zone_only"
    historical_context_only = "historical_context_only"
    audit_only = "audit_only"
    validation = "validation"
    contextual = "contextual"


class WeatherStatus(str, Enum):
    available = "available"
    not_requested_location_unresolved = "not_requested_location_unresolved"
    weather_unavailable_for_requested_date = "weather_unavailable_for_requested_date"
    historical_context = "historical_context"
    source_unavailable = "source_unavailable"
    malformed_upstream_response = "malformed_upstream_response"
    fixture_unavailable = "fixture_unavailable"


class AccessCertainty(str, Enum):
    explicit_public = "explicit_public"
    unspecified = "unspecified"


class PublicSiteSearchStatus(str, Enum):
    success = "success"
    not_applicable_location_unresolved = "not_applicable_location_unresolved"
    not_applicable_without_strong_evidence = "not_applicable_without_strong_evidence"
    safe_map_unavailable = "safe_map_unavailable"
    no_suitable_public_sites = "no_suitable_public_sites"
    source_unavailable = "source_unavailable"


class ExpeditionPlanStatus(str, Enum):
    candidate_plan_ready = "candidate_plan_ready"
    context_only = "context_only"
    cannot_recommend_sites = "cannot_recommend_sites"


class ConstraintSeverity(str, Enum):
    error = "error"
    warning = "warning"
    information = "information"


class ConstraintStatus(str, Enum):
    satisfied = "satisfied"
    violated = "violated"
    unresolved = "unresolved"
    not_applicable = "not_applicable"


class ToolErrorCode(str, Enum):
    invalid_input = "invalid_input"
    postcode_not_found = "postcode_not_found"
    outside_supported_area = "outside_supported_area"
    human_selection_required = "human_selection_required"
    taxon_not_found = "taxon_not_found"
    source_timeout = "source_timeout"
    source_rate_limit = "source_rate_limit"
    source_unavailable = "source_unavailable"
    malformed_upstream_response = "malformed_upstream_response"
    weather_unavailable_for_requested_date = "weather_unavailable_for_requested_date"
    missing_or_corrupt_fixture = "missing_or_corrupt_fixture"
    no_occurrence_evidence = "no_occurrence_evidence"
    insufficient_quality_evidence = "insufficient_quality_evidence"
    safe_map_unavailable = "safe_map_unavailable"
    no_suitable_public_sites = "no_suitable_public_sites"
    routing_not_available = "routing_not_available"


class WGS84Point(StrictModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)


class BritishNationalGridPoint(StrictModel):
    easting: float
    northing: float
    crs: str = Field(default="EPSG:27700", pattern=r"^EPSG:27700$")


class ExpeditionRequest(StrictModel):
    bird_input: NonEmptyText
    postcode: str | None = None
    start_point: WGS84Point | None = None
    target_local_date: date
    duration_hours: float = Field(gt=0, le=24)
    maximum_walking_distance_km: float | None = Field(default=None, gt=0, le=50)
    rain_preference: RainPreference = RainPreference.no_preference
    target_month_override: int | None = Field(default=None, ge=1, le=12)
    search_radius_km: float = Field(default=5.0, gt=0, le=25)
    timezone: str = Field(default="Europe/London", pattern=r"^Europe/London$")

    @model_validator(mode="after")
    def exactly_one_location(self) -> "ExpeditionRequest":
        if (self.postcode is None) == (self.start_point is None):
            raise ValueError("provide exactly one of postcode or start_point")
        if self.postcode is not None and not self.postcode.strip():
            raise ValueError("postcode cannot be blank")
        return self

    @property
    def seasonal_target_month(self) -> int:
        return self.target_month_override or self.target_local_date.month


class EvidenceItem(StrictModel):
    source: NonEmptyText
    source_record_type: NonEmptyText
    retrieved_at: datetime
    licence: NonEmptyText
    attribution: NonEmptyText
    use_classification: EvidenceUse
    limitations: list[NonEmptyText] = Field(default_factory=list)
    source_reference: str | None = None
    dataset_references: list[str] = Field(default_factory=list)


class ResolvedLocation(StrictModel):
    status: LocationStatus
    input_kind: str
    normalised_postcode: str | None = None
    rounded_start_point: WGS84Point | None = None
    british_national_grid: BritishNationalGridPoint | None = None
    within_greater_london: bool | None = None
    administrative_district: str | None = None
    provenance: EvidenceItem | None = None
    tool_error: "ToolError | None" = None
    message: NonEmptyText

    @model_validator(mode="after")
    def resolved_fields(self) -> "ResolvedLocation":
        if self.status == LocationStatus.resolved:
            if not self.within_greater_london:
                raise ValueError("resolved location must be within Greater London")
            if self.rounded_start_point is None or self.british_national_grid is None:
                raise ValueError("resolved location requires WGS84 and EPSG:27700 positions")
        return self


class TaxonCandidate(StrictModel):
    accepted_taxon_key: int = Field(gt=0)
    common_name: str | None = None
    scientific_name: NonEmptyText
    canonical_name: NonEmptyText
    rank: NonEmptyText
    taxonomic_status: NonEmptyText
    resolution_method: NonEmptyText
    confidence: int | None = Field(default=None, ge=0, le=100)


class ResolvedTaxon(StrictModel):
    status: TaxonStatus
    original_input: str
    normalised_input: str
    accepted_taxon_key: int | None = Field(default=None, gt=0)
    common_name: str | None = None
    scientific_name: str | None = None
    canonical_name: str | None = None
    rank: str | None = None
    taxonomic_status: str | None = None
    resolution_method: str | None = None
    confidence: int | None = Field(default=None, ge=0, le=100)
    candidates: list[TaxonCandidate] = Field(default_factory=list)
    provenance: EvidenceItem | None = None
    tool_error: "ToolError | None" = None
    rationale: NonEmptyText

    @model_validator(mode="after")
    def status_fields(self) -> "ResolvedTaxon":
        accepted = (
            self.accepted_taxon_key,
            self.scientific_name,
            self.canonical_name,
            self.rank,
            self.taxonomic_status,
            self.resolution_method,
        )
        if self.status == TaxonStatus.resolved and any(value is None for value in accepted):
            raise ValueError("resolved taxon requires accepted taxonomy fields")
        if self.status == TaxonStatus.human_selection_required and len(self.candidates) < 2:
            raise ValueError("human selection requires at least two candidates")
        return self


class OccurrenceCounts(StrictModel):
    server_match_count: int = Field(ge=0)
    sampled_count: int = Field(ge=0)
    deduplicated_count: int = Field(ge=0)
    exact_duplicates_removed: int = Field(ge=0)
    possible_duplicates_retained: int = Field(ge=0)
    retained_total_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    ranking_eligible_count: int = Field(ge=0)

    @model_validator(mode="after")
    def count_semantics(self) -> "OccurrenceCounts":
        if self.sampled_count - self.exact_duplicates_removed != self.deduplicated_count:
            raise ValueError("sampled minus duplicates must equal deduplicated")
        if self.retained_total_count + self.rejected_count != self.deduplicated_count:
            raise ValueError("retained plus rejected must equal deduplicated")
        return self


class EvidenceQualitySummary(StrictModel):
    ranking_eligible_count: int = Field(ge=0)
    broad_zone_count: int = Field(ge=0)
    historical_context_count: int = Field(ge=0)
    unknown_uncertainty_count: int = Field(ge=0)
    quality_percentages: dict[str, float] = Field(default_factory=dict)
    spatial_cell_count: int = Field(ge=0)
    retained_dataset_count: int = Field(ge=0)
    ranking_dataset_count: int = Field(ge=0)
    record_count_by_dataset: dict[str, int] = Field(default_factory=dict)
    ranking_eligible_count_by_dataset: dict[str, int] = Field(default_factory=dict)
    dominant_ranking_dataset_share: float = Field(ge=0, le=1)
    dominant_basis_of_record_share: float = Field(ge=0, le=1)
    year_distribution: dict[str, int] = Field(default_factory=dict)
    month_distribution: dict[str, int] = Field(default_factory=dict)
    warnings: list[NonEmptyText] = Field(default_factory=list)


class SafeMapGeometry(StrictModel):
    type: str = Field(default="Polygon", pattern=r"^Polygon$")
    coordinates: list[list[list[float]]]

    @model_validator(mode="after")
    def closed_polygon(self) -> "SafeMapGeometry":
        if not self.coordinates or len(self.coordinates[0]) < 4:
            raise ValueError("safe cell requires a polygon ring")
        if self.coordinates[0][0] != self.coordinates[0][-1]:
            raise ValueError("safe cell polygon ring must be closed")
        return self


class SafeSpatialCell(StrictModel):
    cell_id: NonEmptyText
    geometry: SafeMapGeometry
    geometry_crs: str = Field(default="EPSG:4326", pattern=r"^EPSG:4326$")
    aggregation_crs: str = Field(default="EPSG:27700", pattern=r"^EPSG:27700$")
    cell_size_metres: int = Field(default=1000, ge=1000)
    aggregated_record_count: int = Field(ge=3)
    dataset_count: int = Field(ge=1)
    date_start: datetime | date | None = None
    date_end: datetime | date | None = None
    evidence_quality: NonEmptyText
    limitations: list[NonEmptyText] = Field(default_factory=list)


class OccurrenceEvidence(StrictModel):
    outcome: EvidenceOutcome
    reason: NonEmptyText
    seasonal_target_month: int = Field(ge=1, le=12)
    seasonal_months: list[int]
    year_window: tuple[int, int]
    counts: OccurrenceCounts
    quality: EvidenceQualitySummary
    safe_map_cells: list[SafeSpatialCell] = Field(default_factory=list)
    deduplication_method: NonEmptyText
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    limitations: list[NonEmptyText] = Field(default_factory=list)
    tool_error: "ToolError | None" = None

    @model_validator(mode="after")
    def map_cells_only_for_strong_evidence(self) -> "OccurrenceEvidence":
        if self.outcome != EvidenceOutcome.strong_map_evidence and self.safe_map_cells:
            raise ValueError("only strong evidence may expose ranked safe map cells")
        return self


class WeatherEvidence(StrictModel):
    status: WeatherStatus
    requested_date: date
    timezone: str = Field(default="Europe/London", pattern=r"^Europe/London$")
    maximum_temperature_c: float | None = None
    minimum_temperature_c: float | None = None
    precipitation_probability_percent: int | None = Field(default=None, ge=0, le=100)
    precipitation_amount_mm: float | None = Field(default=None, ge=0)
    weather_code: int | None = None
    provenance: EvidenceItem | None = None
    limitations: list[NonEmptyText] = Field(default_factory=list)
    tool_error: "ToolError | None" = None


class PublicSiteCandidate(StrictModel):
    site_id: NonEmptyText
    name: NonEmptyText
    site_type: NonEmptyText
    access_certainty: AccessCertainty
    centre_point: WGS84Point
    approximate_straight_line_distance_km: float = Field(ge=0)
    operator: str | None = None
    opening_hours: str | None = None
    website: str | None = None
    associated_safe_cell_ids: list[str] = Field(default_factory=list)
    provenance: EvidenceItem
    limitations: list[NonEmptyText] = Field(default_factory=list)


class PublicSiteSearchResult(StrictModel):
    candidates: list[PublicSiteCandidate] = Field(default_factory=list)
    searched_radius_km: float = Field(gt=0)
    status: PublicSiteSearchStatus
    error: "ToolError | None" = None

    @model_validator(mode="after")
    def grounded_success_only(self) -> "PublicSiteSearchResult":
        if self.status == PublicSiteSearchStatus.success:
            if not self.candidates:
                raise ValueError("successful site search requires at least one candidate")
            if any(not candidate.associated_safe_cell_ids for candidate in self.candidates):
                raise ValueError("successful site candidates must be associated with safe cells")
        elif self.candidates:
            raise ValueError("non-successful site search cannot expose candidates")
        return self


class ConstraintViolation(StrictModel):
    code: NonEmptyText
    status: ConstraintStatus
    severity: ConstraintSeverity
    message: NonEmptyText
    field: str | None = None


class ToolError(StrictModel):
    tool: NonEmptyText
    code: ToolErrorCode
    message: NonEmptyText
    retryable: bool = False
    source: str | None = None


class ExpeditionEvidenceBundle(StrictModel):
    request: ExpeditionRequest
    location: ResolvedLocation
    taxon: ResolvedTaxon
    occurrence: OccurrenceEvidence | None = None
    weather: WeatherEvidence | None = None
    site_search: PublicSiteSearchResult
    candidate_sites: list[PublicSiteCandidate] = Field(default_factory=list)
    constraints: list[ConstraintViolation] = Field(default_factory=list)
    evidence_outcome: EvidenceOutcome
    safety_and_scientific_limitations: list[NonEmptyText] = Field(default_factory=list)
    provenance: list[EvidenceItem] = Field(default_factory=list)
    tool_errors: list[ToolError] = Field(default_factory=list)

    @model_validator(mode="after")
    def candidate_compatibility_view(self) -> "ExpeditionEvidenceBundle":
        if self.candidate_sites != self.site_search.candidates:
            raise ValueError("candidate_sites must mirror site_search.candidates")
        return self


class ExpeditionPlan(StrictModel):
    status: ExpeditionPlanStatus
    target_bird: str
    target_date: date
    candidate_sites: list[PublicSiteCandidate] = Field(default_factory=list)
    evidence_explanation: NonEmptyText
    unresolved_constraints: list[ConstraintViolation] = Field(default_factory=list)
    limitations: list[NonEmptyText] = Field(default_factory=list)
    generated_by: str = Field(default="deterministic_phase_1_backend")


class BackendResult(StrictModel):
    bundle: ExpeditionEvidenceBundle
    plan: ExpeditionPlan


class SourceFailure(Exception):
    """Typed expected repository failure; programming errors intentionally bypass it."""

    def __init__(
        self,
        code: ToolErrorCode,
        message: str,
        *,
        source: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.source = source
        self.retryable = retryable


JSONValue = dict[str, Any]
