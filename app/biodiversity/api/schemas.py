"""Strict HTTP DTOs for the Phase 4 visual product.

These contracts intentionally do not mirror raw LangGraph state. Every state,
evidence, map, interrupt, and plan response is assembled from an explicit
allowlist.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from app.biodiversity.models import StrictModel
from app.biodiversity.run_models import (
    CheckpointSummary,
    ForkUpdates,
    PlanComparison,
    RunBranch,
    RunExecution,
)


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ThreadId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]
OperationStatus = Literal[
    "queued", "running", "waiting_for_input", "completed", "failed"
]
OperationKind = Literal["start", "resume", "replay", "fork"]
EvidenceDisplayStatus = Literal[
    "pending", "strong", "limited", "insufficient", "source_failure"
]


class RunModeView(StrictModel):
    data_mode: Literal["fixture", "live"]
    model_mode: Literal["scripted", "live"]


class ErrorDetail(StrictModel):
    code: NonEmptyText
    message: NonEmptyText
    fields: list[str] = Field(default_factory=list)


class ErrorResponse(StrictModel):
    error: ErrorDetail


class HealthView(StrictModel):
    status: Literal["ok"] = "ok"
    product: Literal["London Biodiversity Expedition Planner"] = (
        "London Biodiversity Expedition Planner"
    )
    api_version: Literal["v1"] = "v1"
    workflow_version: NonEmptyText
    default_data_mode: Literal["fixture", "live"]
    default_model_mode: Literal["scripted", "live"]
    allowed_run_modes: list[RunModeView]
    public_demo: bool


class CreateRunRequest(StrictModel):
    request: NonEmptyText = Field(max_length=4000)
    thread_id: ThreadId | None = None
    data_mode: Literal["fixture", "live"] = "fixture"
    model_mode: Literal["scripted", "live"] = "scripted"


class TaxonSelectionDecision(StrictModel):
    kind: Literal["taxon_selection", "related_taxon_selection"]
    accepted_taxon_key: int = Field(gt=0)


class BirdCorrectionDecision(StrictModel):
    kind: Literal["bird_input_correction"]
    bird_input: NonEmptyText = Field(max_length=240)


class LocationCorrectionDecision(StrictModel):
    kind: Literal["location_correction"]
    postcode: NonEmptyText = Field(max_length=16)


class RequestClarificationDecision(StrictModel):
    kind: Literal["request_clarification"]
    updates: dict[str, Any]


class ActionableTradeoffDecision(StrictModel):
    kind: Literal["actionable_tradeoff"]
    option: Literal[
        "expand_search_radius",
        "widen_seasonal_window",
        "consider_related_taxa",
        "keep_constraints_accept_low_confidence",
        "accept_context_only",
        "continue_with_weather_acknowledgement",
        "accept_uncertain_access",
        "revise_rain_preference",
    ]
    search_radius_km: float | None = Field(default=None, gt=0, le=25)
    seasonal_window_radius_months: int | None = Field(default=None, ge=1, le=3)

    @model_validator(mode="after")
    def option_fields(self) -> "ActionableTradeoffDecision":
        if self.option == "expand_search_radius":
            if self.search_radius_km is None or self.seasonal_window_radius_months is not None:
                raise ValueError("expand_search_radius requires only search_radius_km")
        elif self.option == "widen_seasonal_window":
            if self.seasonal_window_radius_months is None or self.search_radius_km is not None:
                raise ValueError(
                    "widen_seasonal_window requires only seasonal_window_radius_months"
                )
        elif self.search_radius_km is not None or self.seasonal_window_radius_months is not None:
            raise ValueError("the selected option accepts no numeric field")
        return self


ResumeDecision = Annotated[
    TaxonSelectionDecision
    | BirdCorrectionDecision
    | LocationCorrectionDecision
    | RequestClarificationDecision
    | ActionableTradeoffDecision,
    Field(discriminator="kind"),
]


class ResumeRunRequest(StrictModel):
    decision: ResumeDecision
    checkpoint_id: str | None = Field(default=None, min_length=1)
    branch_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def one_target(self) -> "ResumeRunRequest":
        if self.checkpoint_id and self.branch_id:
            raise ValueError("choose checkpoint_id or branch_id, not both")
        return self

    def manager_payload(self) -> dict[str, Any]:
        return self.decision.model_dump(mode="json", exclude={"kind"}, exclude_none=True)


class ReplayRunRequest(StrictModel):
    checkpoint_id: NonEmptyText


class ForkRunRequest(StrictModel):
    checkpoint_id: NonEmptyText
    updates: ForkUpdates
    branch_label: str | None = Field(default=None, max_length=80)


class OperationAccepted(StrictModel):
    operation_id: NonEmptyText
    thread_id: ThreadId
    status: Literal["queued"] = "queued"
    events_url: NonEmptyText


class OperationView(StrictModel):
    operation_id: NonEmptyText
    thread_id: ThreadId
    branch_id: str | None = None
    execution_id: str | None = None
    kind: OperationKind
    status: OperationStatus
    data_mode: Literal["fixture", "live"]
    model_mode: Literal["scripted", "live"]
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_event_sequence: int = Field(default=0, ge=0)
    current_checkpoint_id: str | None = None
    interrupt_kind: str | None = None
    error_code: str | None = None


class RunSummary(StrictModel):
    thread_id: ThreadId
    status: OperationStatus
    data_mode: Literal["fixture", "live"]
    model_mode: Literal["scripted", "live"]
    created_at: datetime
    updated_at: datetime
    current_checkpoint_id: str | None = None
    branch_id: str | None = None
    execution_id: str | None = None
    interrupt_kind: str | None = None


class TaxonEvidencePreviewView(StrictModel):
    status: str
    evidence_outcome: str | None = None
    sampled_count: int | None = Field(default=None, ge=0)
    retained_count: int | None = Field(default=None, ge=0)
    ranking_eligible_count: int | None = Field(default=None, ge=0)
    dataset_count: int | None = Field(default=None, ge=0)
    target_months: list[int] = Field(default_factory=list)
    source_status: str
    limitations: list[str] = Field(default_factory=list)


class TaxonCandidateView(StrictModel):
    accepted_taxon_key: int = Field(gt=0)
    common_name: str | None = None
    scientific_name: str
    canonical_name: str
    rank: str
    taxonomic_status: str
    class_name: str | None = None
    order: str | None = None
    family: str | None = None
    genus: str | None = None
    resolution_method: str
    confidence: int | None = None
    relation_level: str | None = None
    relation_basis: str | None = None
    evidence_preview: TaxonEvidencePreviewView | None = None


class HitlOptionView(StrictModel):
    option: str
    current_radius_km: float | None = None
    maximum_radius_km: float | None = None
    current_radius_months: int | None = None
    maximum_radius_months: int | None = None
    candidate_count: int | None = None
    relation_levels: list[str] = Field(default_factory=list)


class PendingDecisionView(StrictModel):
    kind: Literal[
        "request_clarification",
        "location_correction",
        "bird_input_correction",
        "taxon_selection",
        "actionable_tradeoff",
        "related_taxon_selection",
    ]
    question: str
    checkpoint_id: str
    branch_id: str
    execution_id: str
    validation_errors: list[str] = Field(default_factory=list)
    status: str | None = None
    rationale: str | None = None
    candidates: list[TaxonCandidateView] = Field(default_factory=list)
    options: list[HitlOptionView] = Field(default_factory=list)


class PlanSiteView(StrictModel):
    site_id: str
    name: str
    access_certainty: str
    approximate_straight_line_distance_km: float
    evidence_tier: str


class WeatherDayView(StrictModel):
    status: str
    requested_date: date
    maximum_temperature_c: float | None = None
    minimum_temperature_c: float | None = None
    precipitation_probability_percent: int | None = None
    precipitation_amount_mm: float | None = None
    weather_code: int | None = None
    explanation: str


class ConstraintView(StrictModel):
    code: str
    status: str
    severity: str
    message: str


class FinalPlanView(StrictModel):
    status: str
    target_species: str
    target_date: date
    duration_hours: float
    resolved_london_start_context: str
    recommended_sites: list[PlanSiteView] = Field(default_factory=list)
    contextual_sites: list[PlanSiteView] = Field(default_factory=list)
    weather_context: WeatherDayView | None = None
    constraints: list[ConstraintView] = Field(default_factory=list)
    unresolved_limitations: list[str] = Field(default_factory=list)
    suggested_next_actions: list[str] = Field(default_factory=list)
    provenance_references: list[str] = Field(default_factory=list)
    evidence_attributions: list[str] = Field(default_factory=list)
    evidence_citations: list[str] = Field(default_factory=list)
    evidence_gate_passed: bool
    low_confidence_accepted: bool = False
    low_confidence_notice: str | None = None
    explanation: str
    generated_by: str


class RunDetail(StrictModel):
    run: RunSummary
    operations: list[OperationView] = Field(default_factory=list)
    branches: list[RunBranch] = Field(default_factory=list)
    executions: list[RunExecution] = Field(default_factory=list)
    pending_decisions: list[PendingDecisionView] = Field(default_factory=list)
    final_plan: FinalPlanView | None = None


class EvidenceCountsView(StrictModel):
    server_match_count: int = Field(default=0, ge=0)
    sampled_count: int = Field(default=0, ge=0)
    deduplicated_count: int = Field(default=0, ge=0)
    exact_duplicates_removed: int = Field(default=0, ge=0)
    possible_duplicates_retained: int = Field(default=0, ge=0)
    retained_count: int = Field(default=0, ge=0)
    rejected_count: int = Field(default=0, ge=0)
    ranking_eligible_count: int = Field(default=0, ge=0)


class EvidenceQualityView(StrictModel):
    spatial_cell_count: int = Field(default=0, ge=0)
    retained_dataset_count: int = Field(default=0, ge=0)
    ranking_dataset_count: int = Field(default=0, ge=0)
    dominant_ranking_dataset_share: float = Field(default=0, ge=0, le=1)
    dominant_basis_of_record_share: float = Field(default=0, ge=0, le=1)
    quality_percentages: dict[str, float] = Field(default_factory=dict)
    year_distribution: dict[str, int] = Field(default_factory=dict)
    month_distribution: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ProvenanceView(StrictModel):
    source: str
    source_record_type: str
    retrieved_at: datetime
    licence: str
    attribution: str
    use_classification: str
    source_reference: str | None = None
    limitations: list[str] = Field(default_factory=list)


class EvidenceView(StrictModel):
    thread_id: str
    checkpoint_id: str
    branch_id: str
    execution_id: str
    status: EvidenceDisplayStatus
    occurrence_outcome: str | None = None
    reason: str | None = None
    counts: EvidenceCountsView
    quality: EvidenceQualityView
    seasonal_target_month: int | None = None
    seasonal_months: list[int] = Field(default_factory=list)
    year_window: tuple[int, int] | None = None
    candidate_sites: list[PlanSiteView] = Field(default_factory=list)
    contextual_sites: list[PlanSiteView] = Field(default_factory=list)
    constraints: list[ConstraintView] = Field(default_factory=list)
    weather: WeatherDayView | None = None
    provenance: list[ProvenanceView] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    server_match_note: Literal[
        "Server match count is not abundance or a population estimate."
    ] = "Server match count is not abundance or a population estimate."


class GeoJSONGeometry(StrictModel):
    type: Literal["Point", "Polygon", "MultiPolygon"]
    coordinates: Any


class MapCellProperties(StrictModel):
    display_id: str
    layer: Literal["aggregate_evidence"] = "aggregate_evidence"
    density_band: Literal["lower", "medium", "higher"]
    dataset_diversity_band: Literal["single", "multiple"]
    date_start: date | datetime | None = None
    date_end: date | datetime | None = None
    label: str


class MapSiteProperties(StrictModel):
    site_id: str
    layer: Literal["candidate", "contextual"]
    name: str
    site_type: str
    access_certainty: str
    evidence_tier: str
    approximate_straight_line_distance_km: float
    operator: str | None = None
    opening_hours: str | None = None
    website: str | None = None


class MapCellFeature(StrictModel):
    type: Literal["Feature"] = "Feature"
    geometry: GeoJSONGeometry
    properties: MapCellProperties


class MapSiteFeature(StrictModel):
    type: Literal["Feature"] = "Feature"
    geometry: GeoJSONGeometry
    properties: MapSiteProperties


class MapStartContext(StrictModel):
    type: Literal["Feature"] = "Feature"
    geometry: GeoJSONGeometry
    properties: dict[Literal["layer", "label", "precision"], str]


class MapEvidenceView(StrictModel):
    thread_id: str
    checkpoint_id: str
    branch_id: str
    execution_id: str
    status: EvidenceDisplayStatus
    aggregate_grid: list[MapCellFeature] = Field(default_factory=list)
    candidate_sites: list[MapSiteFeature] = Field(default_factory=list)
    contextual_sites: list[MapSiteFeature] = Field(default_factory=list)
    start_context: MapStartContext | None = None
    attributions: list[str]
    limitations: list[str]
    grid_note: str


class HistoryView(StrictModel):
    checkpoints: list[CheckpointSummary]
    branches: list[RunBranch]
    executions: list[RunExecution]


class CompareRequest(StrictModel):
    checkpoint_a: NonEmptyText
    checkpoint_b: NonEmptyText


class ComparisonView(StrictModel):
    comparison: PlanComparison
