"""Application-owned checkpoint, branch, fork, and comparison contracts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from app.biodiversity.models import RainPreference, StrictModel


WORKFLOW_VERSION = "phase-5.0"
STATE_SCHEMA_VERSION = 3


class RunProfile(StrictModel):
    """Immutable runtime choices that must survive a process restart."""

    workflow_version: Literal["phase-5.0"] = WORKFLOW_VERSION
    state_schema_version: Literal[3] = STATE_SCHEMA_VERSION
    data_mode: Literal["fixture", "live"] = "fixture"
    model_mode: Literal["scripted", "live"] = "scripted"
    model_identifier: str = Field(default="scripted-biodiversity-v1", min_length=1)
    endpoint_fingerprint: str = Field(default="local-scripted", min_length=1)
    routing_provider: str = Field(default="fixture-openrouteservice", min_length=1)
    routing_provider_version: str = Field(
        default="ors-api-shaped-2026-09-04", min_length=1
    )
    routing_profile: Literal["foot-walking", "public-transport-and-walking"] = (
        "foot-walking"
    )
    route_schema_version: Literal[2] = 2
    routing_endpoint_fingerprint: str = Field(default="local-fixture", min_length=1)


class RunManifest(StrictModel):
    """Readable Phase 4/5 checkpoint identity, excluding all credentials."""

    workflow_version: Literal["phase-3.1", "phase-5.0"]
    state_schema_version: Literal[2, 3]
    data_mode: Literal["fixture", "live"]
    model_mode: Literal["scripted", "live"]
    model_identifier: str = Field(min_length=1)
    endpoint_fingerprint: str = Field(min_length=1)
    routing_provider: str | None = None
    routing_provider_version: str | None = None
    routing_profile: Literal["foot-walking", "public-transport-and-walking"] | None = (
        None
    )
    route_schema_version: Literal[1, 2] | None = None
    routing_endpoint_fingerprint: str | None = None
    created_at: datetime


class CheckpointSummary(StrictModel):
    thread_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    parent_execution_id: str | None = None
    replayed_from_checkpoint_id: str | None = None
    parent_branch_id: str | None = None
    checkpoint_id: str = Field(min_length=1)
    parent_checkpoint_id: str | None = None
    forked_from_checkpoint_id: str | None = None
    created_at: datetime
    node_id: str = Field(min_length=1)
    graph_step: int | None = None
    source: str | None = None
    next_nodes: list[str] = Field(default_factory=list)
    interrupt_kind: str | None = None
    terminal_status: str | None = None
    applied_constraint_changes: list[dict[str, Any]] = Field(default_factory=list)
    final_checkpoint_id: str | None = None


class StateRequestView(StrictModel):
    """Location-redacted request constraints safe for a frontend timeline."""

    target_local_date: date | None = None
    duration_hours: float | None = None
    maximum_walking_distance_km: float | None = None
    rain_preference: str | None = None
    target_month_override: int | None = None
    seasonal_window_radius_months: int | None = None
    search_radius_km: float | None = None


class StateLocationView(StrictModel):
    status: str | None = None
    input_kind: str | None = None
    within_greater_london: bool | None = None
    administrative_district: str | None = None


class StateTaxonView(StrictModel):
    status: str | None = None
    accepted_taxon_key: int | None = None
    common_name: str | None = None
    scientific_name: str | None = None
    canonical_name: str | None = None
    rank: str | None = None
    taxonomic_status: str | None = None


class StateEvidenceView(StrictModel):
    occurrence_outcome: str | None = None
    sampled_count: int | None = None
    retained_count: int | None = None
    ranking_eligible_count: int | None = None
    safe_map_cell_count: int = Field(default=0, ge=0)
    weather_status: str | None = None
    public_site_status: str | None = None
    candidate_site_count: int = Field(default=0, ge=0)
    contextual_site_count: int = Field(default=0, ge=0)
    public_entrance_count: int = Field(default=0, ge=0)
    route_option_count: int = Field(default=0, ge=0)
    tool_error_count: int = Field(default=0, ge=0)


class StatePlanView(StrictModel):
    deterministic_status: str | None = None
    final_status: str | None = None
    generated_by: str | None = None
    recommended_site_count: int = Field(default=0, ge=0)
    contextual_site_count: int = Field(default=0, ge=0)
    evidence_gate_passed: bool | None = None
    low_confidence_accepted: bool = False
    grounding_error_count: int = Field(default=0, ge=0)
    route_status: str | None = None
    selected_route_site_id: str | None = None
    total_walking_distance_km: float | None = Field(default=None, ge=0)
    total_travel_duration_minutes: float | None = Field(default=None, ge=0)
    remaining_field_time_minutes: float | None = Field(default=None, ge=0)
    routing_provider: str | None = None
    route_cache_status: str | None = None


class StateDecisionView(StrictModel):
    kind: str = Field(min_length=1)
    option: str | None = None
    accepted_taxon_key: int | None = Field(default=None, gt=0)
    selected_taxon_name: str | None = None
    bird_input: str | None = None
    relation_level: str | None = None
    rationale: str | None = None
    search_radius_km: float | None = Field(default=None, gt=0, le=25)
    seasonal_window_radius_months: int | None = Field(default=None, ge=1, le=3)
    maximum_walking_distance_km: float | None = Field(default=None, gt=0, le=50)
    changed_fields: list[str] = Field(default_factory=list)


class StateHitlView(StrictModel):
    waiting: bool = False
    interrupt_kind: str | None = None
    applied_decisions: list[StateDecisionView] = Field(default_factory=list)


class StateCountersView(StrictModel):
    evidence_loop_count: int = Field(default=0, ge=0)
    plan_revision_count: int = Field(default=0, ge=0)
    recorded_tool_call_count: int = Field(default=0, ge=0)


class StateView(StrictModel):
    """Strictly allow-listed, coordinate-free view of one saved checkpoint."""

    schema_version: Literal[1] = 1
    thread_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    checkpoint_id: str = Field(min_length=1)
    parent_checkpoint_id: str | None = None
    node_id: str = Field(min_length=1)
    graph_step: int
    source: str | None = None
    created_at: datetime
    next_nodes: list[str] = Field(default_factory=list)
    terminal_status: str | None = None
    request: StateRequestView | None = None
    location: StateLocationView | None = None
    taxon: StateTaxonView | None = None
    evidence: StateEvidenceView
    plan: StatePlanView
    hitl: StateHitlView
    counters: StateCountersView
    invalidated_evidence: list[str] = Field(default_factory=list)


class RunBranch(StrictModel):
    thread_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    branch_label: str | None = Field(default=None, max_length=80)
    parent_branch_id: str | None = None
    head_checkpoint_id: str = Field(min_length=1)
    final_checkpoint_id: str | None = None
    forked_from_checkpoint_id: str | None = None
    fork_updates: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    terminal_status: str | None = None


class RunExecution(StrictModel):
    thread_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    parent_execution_id: str | None = None
    replayed_from_checkpoint_id: str | None = None
    head_checkpoint_id: str = Field(min_length=1)
    final_checkpoint_id: str | None = None
    created_at: datetime
    terminal_status: str | None = None
    interrupt_kind: str | None = None


class ForkUpdates(StrictModel):
    search_radius_km: float | None = Field(default=None, gt=0, le=25)
    seasonal_window_radius_months: int | None = Field(default=None, ge=1, le=3)
    target_month_override: int | None = Field(default=None, ge=1, le=12)
    target_local_date: date | None = None
    rain_preference: RainPreference | None = None
    duration_hours: float | None = Field(default=None, gt=0, le=24)
    maximum_walking_distance_km: float | None = Field(default=None, gt=0, le=50)
    selected_related_taxon_key: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_change(self) -> "ForkUpdates":
        if not self.model_dump(exclude_none=True):
            raise ValueError("fork updates must contain at least one supported change")
        return self


class ForkRequest(StrictModel):
    thread_id: str = Field(min_length=1)
    checkpoint_id: str = Field(min_length=1)
    updates: ForkUpdates
    branch_label: str | None = Field(default=None, max_length=80)


class ForkResult(StrictModel):
    thread_id: str
    branch_id: str
    execution_id: str
    parent_branch_id: str | None = None
    forked_from_checkpoint_id: str
    fork_checkpoint_id: str
    head_checkpoint_id: str
    final_checkpoint_id: str | None = None
    terminal_status: str | None = None
    interrupt_kind: str | None = None
    applied_updates: dict[str, Any]


class ReplayResult(StrictModel):
    thread_id: str
    branch_id: str
    execution_id: str
    parent_execution_id: str | None = None
    replayed_from_checkpoint_id: str
    head_checkpoint_id: str
    final_checkpoint_id: str | None = None
    terminal_status: str | None = None
    interrupt_kind: str | None = None


class ComparedValue(StrictModel):
    checkpoint_a: Any = None
    checkpoint_b: Any = None
    changed: bool


class PlanComparison(StrictModel):
    thread_id: str
    checkpoint_a: str
    checkpoint_b: str
    request_constraints: ComparedValue
    selected_taxon: ComparedValue
    evidence_outcome: ComparedValue
    weather_status: ComparedValue
    plan_status: ComparedValue
    recommended_site_ids: ComparedValue
    contextual_site_ids: ComparedValue
    limitations: ComparedValue
    provenance_sources: ComparedValue
    applied_user_decisions: ComparedValue
    route_status: ComparedValue
    selected_route_site_id: ComparedValue
    total_walking_distance_km: ComparedValue
    remaining_field_time_minutes: ComparedValue
    route_constraints: ComparedValue
    changed_fields: list[
        Literal[
            "request_constraints",
            "selected_taxon",
            "evidence_outcome",
            "weather_status",
            "plan_status",
            "recommended_site_ids",
            "contextual_site_ids",
            "limitations",
            "provenance_sources",
            "applied_user_decisions",
            "route_status",
            "selected_route_site_id",
            "total_walking_distance_km",
            "remaining_field_time_minutes",
            "route_constraints",
        ]
    ] = Field(default_factory=list)
