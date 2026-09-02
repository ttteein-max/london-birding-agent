"""Application-owned checkpoint, branch, fork, and comparison contracts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from app.biodiversity.models import RainPreference, StrictModel


class CheckpointSummary(StrictModel):
    thread_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    parent_branch_id: str | None = None
    checkpoint_id: str = Field(min_length=1)
    parent_checkpoint_id: str | None = None
    forked_from_checkpoint_id: str | None = None
    created_at: datetime
    graph_step: int | None = None
    source: str | None = None
    next_nodes: list[str] = Field(default_factory=list)
    interrupt_kind: str | None = None
    terminal_status: str | None = None
    applied_constraint_changes: list[dict[str, Any]] = Field(default_factory=list)
    final_checkpoint_id: str | None = None


class RunBranch(StrictModel):
    thread_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    parent_branch_id: str | None = None
    head_checkpoint_id: str = Field(min_length=1)
    final_checkpoint_id: str | None = None
    forked_from_checkpoint_id: str | None = None
    fork_updates: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    terminal_status: str | None = None


class ForkUpdates(StrictModel):
    search_radius_km: float | None = Field(default=None, gt=0, le=25)
    seasonal_window_radius_months: int | None = Field(default=None, ge=1, le=3)
    target_month_override: int | None = Field(default=None, ge=1, le=12)
    target_local_date: date | None = None
    rain_preference: RainPreference | None = None
    duration_hours: float | None = Field(default=None, gt=0, le=24)
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
    parent_branch_id: str | None = None
    forked_from_checkpoint_id: str
    fork_checkpoint_id: str
    final_checkpoint_id: str | None = None
    terminal_status: str | None = None
    interrupt_kind: str | None = None
    applied_updates: dict[str, Any]


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
        ]
    ] = Field(default_factory=list)
