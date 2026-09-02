"""Strict, model-facing contracts for the Phase 3 biodiversity agent."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from app.biodiversity.models import (
    AccessCertainty,
    ConstraintSeverity,
    ConstraintStatus,
    ExpeditionPlanStatus,
    SiteEvidenceTier,
    SiteSearchAction,
    StrictModel,
)


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ExpeditionRequestPointDraft(StrictModel):
    longitude: float | None = None
    latitude: float | None = None


class ExpeditionRequestDraft(StrictModel):
    """Untrusted structured parse; deterministic validation happens afterwards."""

    bird_input: str | None = None
    postcode: str | None = None
    start_point: ExpeditionRequestPointDraft | None = None
    target_local_date: date | None = None
    duration_hours: float | None = None
    maximum_walking_distance_km: float | None = None
    rain_preference: str | None = None
    target_month_override: int | None = None
    seasonal_window_radius_months: int | None = None
    search_radius_km: float | None = None


class PlanSiteOption(StrictModel):
    """A coordinate-free site view suitable for model-generated prose."""

    site_id: NonEmptyText
    name: NonEmptyText
    access_certainty: AccessCertainty
    approximate_straight_line_distance_km: float = Field(ge=0)
    evidence_tier: SiteEvidenceTier


class PlanWeatherContext(StrictModel):
    status: NonEmptyText
    requested_date: date
    maximum_temperature_c: float | None = None
    minimum_temperature_c: float | None = None
    precipitation_probability_percent: int | None = Field(default=None, ge=0, le=100)
    precipitation_amount_mm: float | None = Field(default=None, ge=0)
    explanation: NonEmptyText


class PlanConstraint(StrictModel):
    code: NonEmptyText
    status: ConstraintStatus
    severity: ConstraintSeverity
    message: NonEmptyText


class BiodiversityExpeditionPlan(StrictModel):
    """Strict Phase 3 plan returned only after deterministic grounding checks."""

    status: ExpeditionPlanStatus
    target_species: NonEmptyText
    target_date: date
    duration_hours: float = Field(gt=0, le=24)
    resolved_london_start_context: NonEmptyText
    recommended_sites: list[PlanSiteOption] = Field(default_factory=list)
    contextual_sites: list[PlanSiteOption] = Field(default_factory=list)
    weather_context: PlanWeatherContext | None = None
    constraints: list[PlanConstraint] = Field(default_factory=list)
    unresolved_limitations: list[NonEmptyText] = Field(default_factory=list)
    suggested_next_actions: list[SiteSearchAction] = Field(default_factory=list)
    provenance_references: list[NonEmptyText] = Field(default_factory=list)
    evidence_attributions: list[NonEmptyText] = Field(default_factory=list)
    evidence_citations: list[NonEmptyText] = Field(default_factory=list)
    evidence_gate_passed: bool
    low_confidence_accepted: bool = False
    low_confidence_notice: NonEmptyText | None = None
    explanation: NonEmptyText
    generated_by: Literal[
        "llm_composer",
        "llm_revision",
        "deterministic_fallback",
        # Legacy values remain readable for already-persisted Phase 2/3 reports.
        "llm_phase_2_composer",
        "llm_phase_2_revision",
        "deterministic_phase_2_fallback",
    ] = "llm_composer"

    @model_validator(mode="after")
    def site_tiers_match_sections(self) -> "BiodiversityExpeditionPlan":
        if any(
            site.evidence_tier != SiteEvidenceTier.directly_grounded
            for site in self.recommended_sites
        ):
            raise ValueError("recommended sites must be directly grounded")
        if any(
            site.evidence_tier == SiteEvidenceTier.directly_grounded
            for site in self.contextual_sites
        ):
            raise ValueError("contextual sites must not be directly grounded")
        if self.low_confidence_accepted:
            if self.evidence_gate_passed:
                raise ValueError(
                    "accepted low confidence requires a failed historical-evidence gate"
                )
            if self.recommended_sites:
                raise ValueError(
                    "accepted low confidence cannot contain recommended sites"
                )
            if not self.low_confidence_notice:
                raise ValueError(
                    "accepted low confidence requires an explicit final-plan notice"
                )
        elif self.low_confidence_notice is not None:
            raise ValueError(
                "low-confidence notice requires an explicit user acceptance"
            )
        return self


class TerminalAgentResult(StrictModel):
    status: Literal[
        "safe_failure",
        "evidence_loop_limit_reached",
        "invalid_resume",
    ]
    reason: NonEmptyText
    audit_explanation: NonEmptyText
