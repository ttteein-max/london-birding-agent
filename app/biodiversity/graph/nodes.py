"""Deterministic, model-driven, HITL, and safety nodes for Phase 3."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from langchain.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import interrupt
from pydantic import ValidationError

from app.biodiversity.agent_models import (
    BiodiversityExpeditionPlan,
    ExpeditionRequestDraft,
    TerminalAgentResult,
)
from app.biodiversity.graph.grounding import (
    compact_plan_payload,
    deterministic_safe_plan,
    validate_grounded_plan,
)
from app.biodiversity.graph.prompts import (
    EVIDENCE_AGENT_PROMPT,
    PLAN_COMPOSER_PROMPT,
    PLAN_REVISION_PROMPT,
    REQUEST_PARSER_PROMPT,
)
from app.biodiversity.graph.state import BiodiversityAgentState
from app.biodiversity.models import (
    ConstraintStatus,
    EvidenceItem,
    EvidenceOutcome,
    EvidenceUse,
    ExpeditionEvidenceBundle,
    ExpeditionPlan,
    ExpeditionRequest,
    LocationStatus,
    OccurrenceEvidence,
    PublicSiteSearchResult,
    PublicSiteSearchStatus,
    RainPreference,
    ResolvedLocation,
    ResolvedTaxon,
    RelatedTaxonCandidate,
    SiteSearchAction,
    TaxonStatus,
    TaxonEvidencePreview,
    WeatherEvidence,
)
from app.biodiversity.orchestration import (
    BackendDependencies,
    build_deterministic_expedition_plan,
)
from app.biodiversity.tools import (
    build_expedition_evidence_bundle,
    find_public_green_spaces,
    get_weather_context,
    lookup_uk_postcode,
    resolve_bird_taxon,
    search_occurrences,
    validate_expedition_constraints,
)


def _json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _request_from_draft(draft: ExpeditionRequestDraft) -> tuple[ExpeditionRequest | None, list[str]]:
    missing = [
        field
        for field in ("bird_input", "target_local_date", "duration_hours")
        if getattr(draft, field) in {None, ""}
    ]
    if draft.postcode is None and draft.start_point is None:
        missing.append("postcode_or_start_point")
    if draft.postcode is not None and draft.start_point is not None:
        missing.append("exactly_one_location_required")
    if missing:
        return None, [f"Missing or contradictory field: {field}." for field in missing]
    values = draft.model_dump(exclude_none=True)
    values["timezone"] = "Europe/London"
    try:
        return ExpeditionRequest.model_validate(values), []
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(item) for item in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        ]


def _validated_request_update(
    request: ExpeditionRequest,
    updates: dict[str, Any],
) -> ExpeditionRequest:
    values = request.model_dump(mode="python")
    values.update(updates)
    return ExpeditionRequest.model_validate(values)


def build_parse_request_node(parser_model: Any) -> Callable[[BiodiversityAgentState], dict[str, Any]]:
    structured_parser = parser_model.with_structured_output(
        ExpeditionRequestDraft,
        method="function_calling",
    )

    def parse_expedition_request(state: BiodiversityAgentState) -> dict[str, Any]:
        original = state["original_request_text"]
        response = structured_parser.invoke(
            [
                SystemMessage(content=REQUEST_PARSER_PROMPT),
                HumanMessage(content=f"Untrusted expedition request data:\n{original}"),
            ]
        )
        draft = (
            response
            if isinstance(response, ExpeditionRequestDraft)
            else ExpeditionRequestDraft.model_validate(response)
        )
        request, errors = _request_from_draft(draft)
        payload = None
        kind = None
        if errors:
            kind = "request_clarification"
            payload = {
                "kind": kind,
                "question": "Please provide corrections for the missing or contradictory expedition details.",
                "validation_errors": errors,
                "parsed_draft": draft.model_dump(mode="json"),
                "resume_schema": {"updates": "object containing corrected request fields"},
            }
        return {
            "parsed_request_draft": draft.model_dump(mode="json"),
            "expedition_request": request.model_dump(mode="json") if request else None,
            "pending_hitl_kind": kind,
            "pending_hitl_payload": payload,
            "messages": [HumanMessage(content=f"Expedition request: {original}")],
            "structured_evidence_log": [],
            "pending_tool_results": [],
            "recorded_tool_call_ids": [],
            "executed_tool_call_audit": [],
            "tool_errors": [],
            "deterministic_constraints": [],
            "applied_user_decisions": [],
            "grounding_errors": [],
            "evidence_loop_count": 0,
            "plan_revision_count": 0,
            "terminal_status": None,
            "visited_nodes": ["parse_expedition_request"],
        }

    return parse_expedition_request


def request_clarification_interrupt(state: BiodiversityAgentState) -> dict[str, Any]:
    payload = state.get("pending_hitl_payload")
    if state.get("pending_hitl_kind") != "request_clarification" or not payload:
        raise ValueError("Request clarification interrupt has no pending payload")
    resumed = interrupt(payload)
    if not isinstance(resumed, dict) or set(resumed) != {"updates"} or not isinstance(resumed["updates"], dict):
        raise ValueError("Resume must be {'updates': {...}}")
    allowed = set(ExpeditionRequestDraft.model_fields)
    unknown = set(resumed["updates"]) - allowed
    if unknown:
        raise ValueError(f"Unsupported request correction fields: {sorted(unknown)}")
    merged = dict(state.get("parsed_request_draft") or {})
    merged.update(resumed["updates"])
    try:
        draft = ExpeditionRequestDraft.model_validate(merged)
    except ValidationError as exc:
        raise ValueError(f"Invalid request correction: {exc}") from exc
    request, errors = _request_from_draft(draft)
    if request is None:
        raise ValueError(f"Request correction is still invalid: {'; '.join(errors)}")
    return {
        "parsed_request_draft": draft.model_dump(mode="json"),
        "expedition_request": request.model_dump(mode="json"),
        "pending_hitl_kind": None,
        "pending_hitl_payload": None,
        "applied_user_decisions": [
            {"kind": "request_clarification", "updates": resumed["updates"]}
        ],
        "visited_nodes": ["request_clarification_interrupt"],
    }


def build_resolve_location_node(dependencies: BackendDependencies) -> Callable[[BiodiversityAgentState], dict[str, Any]]:
    def resolve_location(state: BiodiversityAgentState) -> dict[str, Any]:
        request = ExpeditionRequest.model_validate(state["expedition_request"])
        location = lookup_uk_postcode(request, repository=dependencies.postcode)
        kind = None
        payload = None
        if location.status in {
            LocationStatus.invalid_input,
            LocationStatus.postcode_not_found,
            LocationStatus.outside_supported_area,
        }:
            kind = "location_correction"
            payload = {
                "kind": kind,
                "question": "Please provide a valid UK postcode or generalised point within Greater London.",
                "status": location.status.value,
                "message": location.message,
                "resume_schema": {"postcode": "London postcode", "or_start_point": {"longitude": "number", "latitude": "number"}},
            }
        return {
            "resolved_location": location.model_dump(mode="json"),
            "pending_hitl_kind": kind,
            "pending_hitl_payload": payload,
            "visited_nodes": ["resolve_location"],
        }

    return resolve_location


def location_correction_interrupt(state: BiodiversityAgentState) -> dict[str, Any]:
    payload = state.get("pending_hitl_payload")
    if state.get("pending_hitl_kind") != "location_correction" or not payload:
        raise ValueError("Location correction interrupt has no pending payload")
    resumed = interrupt(payload)
    if not isinstance(resumed, dict) or set(resumed) not in ({"postcode"}, {"start_point"}):
        raise ValueError("Resume must contain exactly one of postcode or start_point")
    request = ExpeditionRequest.model_validate(state["expedition_request"])
    update = {"postcode": None, "start_point": None, **resumed}
    try:
        corrected = _validated_request_update(request, update)
    except ValidationError as exc:
        raise ValueError(f"Invalid location correction: {exc}") from exc
    return {
        "expedition_request": corrected.model_dump(mode="json"),
        "resolved_location": None,
        "pending_hitl_kind": None,
        "pending_hitl_payload": None,
        "applied_user_decisions": [{"kind": "location_correction", **resumed}],
        "visited_nodes": ["location_correction_interrupt"],
    }


def build_resolve_taxon_node(dependencies: BackendDependencies) -> Callable[[BiodiversityAgentState], dict[str, Any]]:
    def resolve_taxon(state: BiodiversityAgentState) -> dict[str, Any]:
        request = ExpeditionRequest.model_validate(state["expedition_request"])
        taxon = resolve_bird_taxon(request.bird_input, repository=dependencies.taxonomy)
        kind = None
        payload = None
        if taxon.status == TaxonStatus.human_selection_required:
            # Evidence preview is deliberately a separate checkpointed node. The
            # interrupt node itself performs no I/O and is safe to re-enter.
            kind = "taxon_preview_pending"
        elif taxon.status == TaxonStatus.taxon_not_found:
            kind = "bird_input_correction"
            payload = {
                "kind": kind,
                "question": "Please correct the bird common or scientific name.",
                "status": taxon.status.value,
                "rationale": taxon.rationale,
                "resume_schema": {"bird_input": "corrected name"},
            }
        return {
            "resolved_taxon": taxon.model_dump(mode="json"),
            "pending_hitl_kind": kind,
            "pending_hitl_payload": payload,
            "visited_nodes": ["resolve_taxon"],
        }

    return resolve_taxon


def build_prepare_taxon_selection_node(
    dependencies: BackendDependencies,
    *,
    candidate_budget: int = 3,
) -> Callable[[BiodiversityAgentState], dict[str, Any]]:
    """Collect bounded, coordinate-free previews before the interrupt checkpoint."""

    if not 1 <= candidate_budget <= 3:
        raise ValueError("candidate preview budget must be between 1 and 3")

    def prepare_taxon_selection(state: BiodiversityAgentState) -> dict[str, Any]:
        taxon = ResolvedTaxon.model_validate(state["resolved_taxon"])
        request = ExpeditionRequest.model_validate(state["expedition_request"])
        if taxon.status != TaxonStatus.human_selection_required:
            raise ValueError("Taxon evidence preview requires ambiguous candidates")
        candidates: list[dict[str, Any]] = []
        previews: list[dict[str, Any]] = []
        for index, item in enumerate(taxon.candidates):
            if index >= candidate_budget:
                preview = TaxonEvidencePreview(
                    status="not_evaluated_budget",
                    target_months=[],
                    source_status="not_evaluated_budget",
                    limitations=[
                        "The strict candidate/request budget was exhausted; this is not a no-evidence result."
                    ],
                )
            else:
                candidate_taxon = ResolvedTaxon(
                    status=TaxonStatus.resolved,
                    original_input=taxon.original_input,
                    normalised_input=taxon.normalised_input,
                    accepted_taxon_key=item.accepted_taxon_key,
                    common_name=item.common_name,
                    scientific_name=item.scientific_name,
                    canonical_name=item.canonical_name,
                    rank=item.rank,
                    taxonomic_status=item.taxonomic_status,
                    class_name=item.class_name,
                    order=item.order,
                    family=item.family,
                    genus=item.genus,
                    resolution_method=item.resolution_method,
                    confidence=item.confidence,
                    rationale="Candidate-only bounded historical evidence preview.",
                )
                evidence = search_occurrences(
                    candidate_taxon,
                    target_month=request.seasonal_target_month,
                    seasonal_window_radius_months=request.seasonal_window_radius_months,
                    repository=dependencies.occurrences,
                    preview=True,
                )
                item_provenance = evidence.evidence_items[0] if evidence.evidence_items else None
                preview = TaxonEvidencePreview(
                    status="source_failure" if evidence.tool_error else "evaluated",
                    evidence_outcome=evidence.outcome,
                    sampled_count=evidence.counts.sampled_count,
                    retained_count=evidence.counts.retained_total_count,
                    ranking_eligible_count=evidence.counts.ranking_eligible_count,
                    dataset_count=evidence.quality.retained_dataset_count,
                    target_months=evidence.seasonal_months,
                    source_status=(
                        evidence.tool_error.code.value
                        if evidence.tool_error
                        else "available"
                    ),
                    limitations=evidence.limitations,
                    provenance_reference=(
                        item_provenance.source_reference if item_provenance else None
                    ),
                )
            preview_json = preview.model_dump(mode="json")
            previews.append(
                {
                    "accepted_taxon_key": item.accepted_taxon_key,
                    "preview": preview_json,
                }
            )
            candidate = item.model_dump(mode="json")
            candidate["class"] = candidate.pop("class_name")
            candidate["evidence_preview"] = preview_json
            candidates.append(candidate)
        payload = {
            "kind": "taxon_selection",
            "question": "Which GBIF candidate is the intended species?",
            "candidates": candidates,
            "candidate_budget": candidate_budget,
            "request_budget": candidate_budget,
            "resume_schema": {"accepted_taxon_key": "one listed integer key"},
        }
        return {
            "taxon_evidence_previews": previews,
            "pending_hitl_kind": "taxon_selection",
            "pending_hitl_payload": payload,
            "visited_nodes": ["prepare_taxon_selection"],
        }

    return prepare_taxon_selection


def taxon_selection_interrupt(state: BiodiversityAgentState) -> dict[str, Any]:
    payload = state.get("pending_hitl_payload")
    taxon = ResolvedTaxon.model_validate(state["resolved_taxon"])
    if state.get("pending_hitl_kind") != "taxon_selection" or not payload:
        raise ValueError("Taxon selection interrupt has no pending payload")
    resumed = interrupt(payload)
    if not isinstance(resumed, dict) or set(resumed) != {"accepted_taxon_key"}:
        raise ValueError("Resume must contain only accepted_taxon_key")
    key = resumed["accepted_taxon_key"]
    if isinstance(key, bool) or not isinstance(key, int):
        raise ValueError("accepted_taxon_key must be an integer")
    selected = next((item for item in taxon.candidates if item.accepted_taxon_key == key), None)
    if selected is None:
        raise ValueError("accepted_taxon_key is not present in the candidate list")
    resolved = ResolvedTaxon(
        status=TaxonStatus.resolved,
        original_input=taxon.original_input,
        normalised_input=taxon.normalised_input,
        accepted_taxon_key=selected.accepted_taxon_key,
        common_name=selected.common_name,
        scientific_name=selected.scientific_name,
        canonical_name=selected.canonical_name,
        rank=selected.rank,
        taxonomic_status=selected.taxonomic_status,
        class_name=selected.class_name,
        order=selected.order,
        family=selected.family,
        genus=selected.genus,
        resolution_method=selected.resolution_method,
        confidence=selected.confidence,
        candidates=taxon.candidates,
        provenance=taxon.provenance,
        rationale="The user selected this accepted taxon from the exposed GBIF candidates.",
    )
    return {
        "resolved_taxon": resolved.model_dump(mode="json"),
        "related_taxon_candidates": [],
        "related_taxon_source_key": None,
        "pending_hitl_kind": None,
        "pending_hitl_payload": None,
        "taxon_evidence_previews": [],
        "applied_user_decisions": [{"kind": "taxon_selection", "accepted_taxon_key": key}],
        "visited_nodes": ["taxon_selection_interrupt"],
    }


def bird_input_correction_interrupt(state: BiodiversityAgentState) -> dict[str, Any]:
    payload = state.get("pending_hitl_payload")
    if state.get("pending_hitl_kind") != "bird_input_correction" or not payload:
        raise ValueError("Bird input correction interrupt has no pending payload")
    resumed = interrupt(payload)
    if not isinstance(resumed, dict) or set(resumed) != {"bird_input"}:
        raise ValueError("Resume must contain only bird_input")
    bird_input = resumed["bird_input"]
    if not isinstance(bird_input, str) or not bird_input.strip():
        raise ValueError("bird_input must be non-empty text")
    request = ExpeditionRequest.model_validate(state["expedition_request"])
    corrected = _validated_request_update(request, {"bird_input": bird_input.strip()})
    return {
        "expedition_request": corrected.model_dump(mode="json"),
        "resolved_taxon": None,
        "pending_hitl_kind": None,
        "pending_hitl_payload": None,
        "applied_user_decisions": [{"kind": "bird_input_correction", "bird_input": bird_input.strip()}],
        "visited_nodes": ["bird_input_correction_interrupt"],
    }


def build_evidence_agent_node(
    evidence_model: Any,
    evidence_tools: list[Any],
    *,
    maximum_rounds: int,
) -> Callable[[BiodiversityAgentState], dict[str, Any]]:
    model_with_tools = evidence_model.bind_tools(evidence_tools)

    def evidence_agent(state: BiodiversityAgentState) -> dict[str, Any]:
        context = {
            "validated_request": state.get("expedition_request"),
            "resolved_location_status": (state.get("resolved_location") or {}).get("status"),
            "resolved_taxon": {
                key: (state.get("resolved_taxon") or {}).get(key)
                for key in ("accepted_taxon_key", "common_name", "scientific_name", "status")
            },
            "evidence_available": {
                "occurrence": state.get("occurrence_evidence") is not None,
                "weather": state.get("weather_evidence") is not None,
                "public_sites": state.get("public_site_search") is not None,
            },
            "tool_audit": [
                {key: item.get(key) for key in ("tool_name", "status", "retryable")}
                for item in state.get("executed_tool_call_audit", [])
            ],
            "round": state.get("evidence_loop_count", 0) + 1,
            "maximum_rounds": maximum_rounds,
        }
        response = model_with_tools.invoke(
            [
                SystemMessage(content=f"{EVIDENCE_AGENT_PROMPT}\n\nTrusted compact state:\n{json.dumps(context, ensure_ascii=False, indent=2)}"),
                *state.get("messages", []),
            ]
        )
        if not isinstance(response, AIMessage):
            raise TypeError("Evidence model must return an AIMessage")
        return {
            "messages": [response],
            "evidence_loop_count": state.get("evidence_loop_count", 0) + 1,
            "visited_nodes": ["evidence_agent"],
        }

    return evidence_agent


def record_structured_evidence(state: BiodiversityAgentState) -> dict[str, Any]:
    recorded = set(state.get("recorded_tool_call_ids", []))
    new_results = [item for item in state.get("pending_tool_results", []) if item.get("call_id") not in recorded]
    def compact_provenance(item: dict[str, Any]) -> list[dict[str, Any]]:
        result = item.get("result") or {}
        sources: list[dict[str, Any]] = []
        if item.get("result_kind") == "occurrence_evidence":
            sources = list(result.get("evidence_items") or [])
        elif item.get("result_kind") == "weather_evidence" and result.get("provenance"):
            sources = [result["provenance"]]
        elif item.get("result_kind") == "public_site_search":
            sites = list(result.get("candidates") or result.get("contextual_sites") or [])
            if sites and sites[0].get("provenance"):
                sources = [sites[0]["provenance"]]
        return [
            {
                key: source.get(key)
                for key in ("source", "retrieved_at", "licence", "attribution", "source_reference")
            }
            for source in sources
        ]

    update: dict[str, Any] = {
        "recorded_tool_call_ids": [str(item["call_id"]) for item in new_results],
        "executed_tool_call_audit": [
            {
                "call_id": item["call_id"],
                "tool_name": item["tool_name"],
                "canonical_arguments": item["canonical_arguments"],
                "status": item["status"],
                "retryable": item["retryable"],
            }
            for item in new_results
        ],
        "structured_evidence_log": [
            {
                "evidence_id": f"{item['tool_name']}:{item['call_id']}",
                "source_tool": item["tool_name"],
                "status": item["status"],
                "summary": item["summary"],
                "provenance": compact_provenance(item),
            }
            for item in new_results
        ],
        "tool_errors": [item["error"] for item in new_results if item.get("error")],
        "visited_nodes": ["record_structured_evidence"],
    }
    for item in new_results:
        result = item.get("result")
        if not result:
            continue
        if item.get("result_kind") == "occurrence_evidence":
            update["occurrence_evidence"] = OccurrenceEvidence.model_validate(result).model_dump(mode="json")
        elif item.get("result_kind") == "weather_evidence":
            update["weather_evidence"] = WeatherEvidence.model_validate(result).model_dump(mode="json")
        elif item.get("result_kind") == "public_site_search":
            update["public_site_search"] = PublicSiteSearchResult.model_validate(result).model_dump(mode="json")
    return update


def _terminal(reason: str, status: str = "safe_failure") -> dict[str, Any]:
    result = TerminalAgentResult(
        status=status,
        reason=reason,
        audit_explanation="The graph stopped without producing an unsupported expedition plan.",
    )
    return {
        "terminal_status": status,
        "terminal_result": result.model_dump(mode="json"),
        "final_validated_plan": None,
    }


def evidence_loop_limit(state: BiodiversityAgentState) -> dict[str, Any]:
    latest = state.get("messages", [])[-1]
    messages: list[ToolMessage] = []
    if isinstance(latest, AIMessage):
        for call in latest.tool_calls:
            messages.append(
                ToolMessage(
                    content=json.dumps({"status": "not_executed", "reason": "evidence_loop_limit_reached"}),
                    tool_call_id=call["id"],
                    name=call["name"],
                    status="error",
                )
            )
    return {
        **_terminal("The evidence agent reached its deterministic round limit.", "evidence_loop_limit_reached"),
        "messages": messages,
        "visited_nodes": ["evidence_loop_limit"],
    }


def source_resolution_failure(state: BiodiversityAgentState) -> dict[str, Any]:
    location = state.get("resolved_location") or {}
    taxon = state.get("resolved_taxon") or {}
    reason = location.get("message") or taxon.get("rationale") or "A required deterministic source was unavailable."
    return {**_terminal(reason), "visited_nodes": ["source_resolution_failure"]}


def _actionable_tradeoff(
    state: BiodiversityAgentState,
    request: ExpeditionRequest,
    bundle: ExpeditionEvidenceBundle,
    related_candidates: list[RelatedTaxonCandidate],
) -> dict[str, Any] | None:
    decisions = {
        str(item.get("option"))
        for item in state.get("applied_user_decisions", [])
        if item.get("option")
    }
    sites = bundle.site_search
    actions = set(sites.suggested_actions)
    if sites.status == PublicSiteSearchStatus.no_suitable_public_sites and SiteSearchAction.expand_search_radius in actions and "expand_search_radius" not in decisions:
        return {
            "kind": "actionable_tradeoff",
            "question": "Would you like to expand the deterministic site-search radius?",
            "reason": "No directly grounded public-site polygon was found within the current radius.",
            "options": [{"option": "expand_search_radius", "current_radius_km": request.search_radius_km, "maximum_radius_km": 25.0}],
            "resume_schema": {"option": "expand_search_radius", "search_radius_km": "number greater than current radius and at most 25"},
        }
    if (
        bundle.evidence_outcome
        in {
            EvidenceOutcome.limited_contextual_evidence,
            EvidenceOutcome.insufficient_evidence,
        }
        and not state.get("low_confidence_accepted", False)
    ):
        options: list[dict[str, Any]] = []
        if request.seasonal_window_radius_months < 3:
            options.append(
                {
                    "option": "widen_seasonal_window",
                    "current_radius_months": request.seasonal_window_radius_months,
                    "maximum_radius_months": 3,
                }
            )
        if related_candidates:
            options.append(
                {
                    "option": "consider_related_taxa",
                    "candidate_count": len(related_candidates),
                    "relation_levels": sorted(
                        {item.relation_level for item in related_candidates}
                    ),
                }
            )
        options.append({"option": "keep_constraints_accept_low_confidence"})
        return {
            "kind": "actionable_tradeoff",
            "question": "How should the low historical-evidence result continue?",
            "reason": (
                "London-wide occurrence evidence does not pass the deterministic gate; "
                "expanding the local site radius would not solve this condition."
            ),
            "options": options,
            "resume_schema": {
                "option": "one listed option",
                "seasonal_window_radius_months": (
                    "required only for widen_seasonal_window"
                ),
            },
        }
    rain = next((item for item in bundle.constraints if item.code == "rain_preference" and item.status == ConstraintStatus.unresolved), None)
    if rain and not {"revise_rain_preference", "continue_with_weather_acknowledgement"}.intersection(decisions):
        return {
            "kind": "actionable_tradeoff",
            "question": "The exact-date forecast conflicts with the rain preference. How should the plan continue?",
            "reason": rain.message,
            "options": [{"option": "revise_rain_preference"}, {"option": "continue_with_weather_acknowledgement"}],
            "resume_schema": {"option": "one listed option"},
        }
    uncertain = next((item for item in bundle.constraints if item.code == "site_access_uncertain"), None)
    if uncertain and "accept_uncertain_access" not in decisions:
        return {
            "kind": "actionable_tradeoff",
            "question": "Some candidate-site access is unspecified. Continue while acknowledging that access must be checked?",
            "reason": uncertain.message,
            "options": [{"option": "accept_uncertain_access"}],
            "resume_schema": {"option": "accept_uncertain_access"},
        }
    return None


def deterministic_validation(
    state: BiodiversityAgentState,
    *,
    dependencies: BackendDependencies | None = None,
) -> dict[str, Any]:
    request = ExpeditionRequest.model_validate(state["expedition_request"])
    location = ResolvedLocation.model_validate(state["resolved_location"])
    taxon = ResolvedTaxon.model_validate(state["resolved_taxon"])
    if not state.get("occurrence_evidence"):
        return {**_terminal("Required occurrence evidence was not collected."), "visited_nodes": ["deterministic_validation"]}
    if not state.get("public_site_search"):
        return {**_terminal("Required public-site evidence was not collected."), "visited_nodes": ["deterministic_validation"]}
    occurrence = OccurrenceEvidence.model_validate(state["occurrence_evidence"])
    weather = WeatherEvidence.model_validate(state["weather_evidence"]) if state.get("weather_evidence") else None
    sites = PublicSiteSearchResult.model_validate(state["public_site_search"])
    constraints = validate_expedition_constraints(request, location, taxon, occurrence, weather, sites)
    bundle = build_expedition_evidence_bundle(request, location, taxon, occurrence, weather, sites, constraints)
    phase1_plan = build_deterministic_expedition_plan(bundle)
    if occurrence.outcome == EvidenceOutcome.source_unavailable or sites.status == PublicSiteSearchStatus.source_unavailable:
        return {
            **_terminal("A required evidence source was unavailable; this is not treated as insufficient evidence."),
            "deterministic_constraints": [item.model_dump(mode="json") for item in constraints],
            "evidence_bundle": bundle.model_dump(mode="json"),
            "deterministic_plan_status": phase1_plan.status.value,
            "deterministic_phase1_plan": phase1_plan.model_dump(mode="json"),
            "visited_nodes": ["deterministic_validation"],
        }
    related_candidates: list[RelatedTaxonCandidate] = []
    related_taxon_source_key: int | None = None
    if bundle.evidence_outcome in {
        EvidenceOutcome.limited_contextual_evidence,
        EvidenceOutcome.insufficient_evidence,
    }:
        related_taxon_source_key = taxon.accepted_taxon_key
        saved_source_key = state.get("related_taxon_source_key")
        raw_related = (
            list(state.get("related_taxon_candidates") or [])
            if saved_source_key == taxon.accepted_taxon_key
            else []
        )
        if saved_source_key != taxon.accepted_taxon_key and dependencies is not None:
            try:
                raw_related = dependencies.taxonomy.related(
                    taxon.accepted_taxon_key or 0,
                    limit=6,
                    request_budget=3,
                )
            except Exception:
                # Related taxa are an optional deterministic trade-off. A source
                # failure removes that option; it is never reported as no evidence.
                raw_related = []
        related_candidates = [
            RelatedTaxonCandidate.model_validate(item) for item in raw_related
        ]
    tradeoff = _actionable_tradeoff(
        state,
        request,
        bundle,
        related_candidates,
    )
    return {
        "deterministic_constraints": [item.model_dump(mode="json") for item in constraints],
        "evidence_bundle": bundle.model_dump(mode="json"),
        "deterministic_plan_status": phase1_plan.status.value,
        "deterministic_phase1_plan": phase1_plan.model_dump(mode="json"),
        "pending_hitl_kind": "actionable_tradeoff" if tradeoff else None,
        "pending_hitl_payload": tradeoff,
        "related_taxon_candidates": [
            item.model_dump(mode="json") for item in related_candidates
        ],
        "related_taxon_source_key": related_taxon_source_key,
        "terminal_status": None,
        "visited_nodes": ["deterministic_validation"],
    }


def actionable_tradeoff_interrupt(state: BiodiversityAgentState) -> dict[str, Any]:
    payload = state.get("pending_hitl_payload")
    if state.get("pending_hitl_kind") != "actionable_tradeoff" or not payload:
        raise ValueError("Actionable trade-off interrupt has no pending payload")
    resumed = interrupt(payload)
    if not isinstance(resumed, dict) or not isinstance(resumed.get("option"), str):
        raise ValueError("Resume must contain a string option")
    allowed = {item["option"] for item in payload.get("options", [])}
    if resumed["option"] not in allowed:
        raise ValueError("Resume option is not present in the deterministic option list")
    return {
        "pending_user_choice": resumed,
        "visited_nodes": ["actionable_tradeoff_interrupt"],
    }


def apply_validated_user_choice(state: BiodiversityAgentState) -> dict[str, Any]:
    choice = state.get("pending_user_choice")
    if not choice:
        raise ValueError("No pending user choice to apply")
    option = choice["option"]
    request = ExpeditionRequest.model_validate(state["expedition_request"])
    update: dict[str, Any] = {
        "pending_hitl_kind": None,
        "pending_hitl_payload": None,
        "pending_user_choice": None,
        "applied_user_decisions": [{"kind": "actionable_tradeoff", **choice}],
        "decision_route": "deterministic_validation",
        "deterministic_constraints": [],
        "evidence_bundle": None,
        "deterministic_plan_status": None,
        "deterministic_phase1_plan": None,
        "draft_llm_plan": None,
        "final_validated_plan": None,
        "grounding_errors": [],
        "terminal_status": None,
        "terminal_result": None,
        "low_confidence_accepted": False,
        "visited_nodes": ["apply_validated_user_choice"],
    }
    if option == "expand_search_radius":
        radius = choice.get("search_radius_km")
        if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not request.search_radius_km < float(radius) <= 25:
            raise ValueError("Expanded radius must be greater than the current radius and at most 25 km")
        request = _validated_request_update(request, {"search_radius_km": float(radius)})
        update.update(
            {
                "expedition_request": request.model_dump(mode="json"),
                "public_site_search": None,
                "invalidated_evidence": ["public_sites"],
                "decision_route": "refresh_invalidated_evidence",
            }
        )
    elif option == "widen_seasonal_window":
        radius = choice.get("seasonal_window_radius_months")
        if (
            isinstance(radius, bool)
            or not isinstance(radius, int)
            or not request.seasonal_window_radius_months < radius <= 3
        ):
            raise ValueError(
                "seasonal_window_radius_months must increase and be at most 3"
            )
        request = _validated_request_update(
            request,
            {"seasonal_window_radius_months": radius},
        )
        update.update(
            {
                "expedition_request": request.model_dump(mode="json"),
                "occurrence_evidence": None,
                "public_site_search": None,
                "invalidated_evidence": ["occurrence", "public_sites"],
                "decision_route": "refresh_invalidated_evidence",
                "low_confidence_accepted": False,
            }
        )
    elif option == "consider_related_taxa":
        current_key = (state.get("resolved_taxon") or {}).get(
            "accepted_taxon_key"
        )
        if state.get("related_taxon_source_key") != current_key:
            raise ValueError(
                "Saved related candidates do not belong to the current taxon"
            )
        candidates = [
            RelatedTaxonCandidate.model_validate(item)
            for item in state.get("related_taxon_candidates", [])
        ]
        if not candidates:
            raise ValueError("No validated related taxa are available")
        update.update(
            {
                "pending_hitl_kind": "related_taxon_selection",
                "pending_hitl_payload": {
                    "kind": "related_taxon_selection",
                    "related_to_taxon_key": current_key,
                    "question": (
                        "Which deterministic GBIF-related taxon should replace the target? "
                        "Taxonomic relation is not ecological interchangeability."
                    ),
                    "candidates": [
                        {
                            **item.model_dump(mode="json", exclude={"class_name"}),
                            "class": item.class_name,
                        }
                        for item in candidates
                    ],
                    "resume_schema": {
                        "accepted_taxon_key": "one listed integer key"
                    },
                },
                "decision_route": "related_taxon_selection_interrupt",
                "low_confidence_accepted": False,
            }
        )
    elif option == "revise_rain_preference":
        request = _validated_request_update(request, {"rain_preference": RainPreference.no_preference})
        update["expedition_request"] = request.model_dump(mode="json")
    elif option not in {
        "keep_constraints_accept_low_confidence",
        "accept_context_only",
        "continue_with_weather_acknowledgement",
        "accept_uncertain_access",
    }:
        raise ValueError("Unsupported deterministic trade-off option")
    elif option in {
        "keep_constraints_accept_low_confidence",
        "accept_context_only",
    }:
        update["low_confidence_accepted"] = True
    return update


def related_taxon_selection_interrupt(
    state: BiodiversityAgentState,
) -> dict[str, Any]:
    """Apply only a related accepted key already saved in the payload."""

    payload = state.get("pending_hitl_payload")
    if state.get("pending_hitl_kind") != "related_taxon_selection" or not payload:
        raise ValueError("Related-taxon selection interrupt has no pending payload")
    current_key = (state.get("resolved_taxon") or {}).get("accepted_taxon_key")
    if payload.get("related_to_taxon_key") != current_key:
        raise ValueError("Related-taxon payload does not match the current taxon")
    resumed = interrupt(payload)
    if not isinstance(resumed, dict) or set(resumed) != {"accepted_taxon_key"}:
        raise ValueError("Resume must contain only accepted_taxon_key")
    key = resumed["accepted_taxon_key"]
    if isinstance(key, bool) or not isinstance(key, int):
        raise ValueError("accepted_taxon_key must be an integer")
    candidates = []
    for raw in payload.get("candidates", []):
        item = dict(raw)
        item["class_name"] = item.pop("class", None)
        candidates.append(RelatedTaxonCandidate.model_validate(item))
    selected = next(
        (item for item in candidates if item.accepted_taxon_key == key),
        None,
    )
    if selected is None:
        raise ValueError("accepted_taxon_key is not present in related candidates")
    previous = ResolvedTaxon.model_validate(state["resolved_taxon"])
    request = ExpeditionRequest.model_validate(state["expedition_request"])
    provenance = previous.provenance
    if provenance is not None:
        provenance = provenance.model_copy(
            update={
                "source_record_type": "accepted_related_taxon_selection",
                "retrieved_at": datetime.now(UTC),
                "use_classification": EvidenceUse.validation,
                "limitations": list(
                    dict.fromkeys(
                        [
                            *provenance.limitations,
                            "The selected species came from a bounded GBIF related-taxon query.",
                            "Taxonomic relation does not imply ecological interchangeability.",
                        ]
                    )
                ),
            }
        )
    else:
        provenance = EvidenceItem(
            source="GBIF Species API",
            source_record_type="accepted_related_taxon_selection",
            retrieved_at=datetime.now(UTC),
            licence="GBIF API terms; source datasets retain their own terms",
            attribution="GBIF.org",
            use_classification=EvidenceUse.validation,
            limitations=[
                "Taxonomic relation does not imply ecological interchangeability."
            ],
            source_reference="https://www.gbif.org/developer/species",
        )
    resolved = ResolvedTaxon(
        status=TaxonStatus.resolved,
        original_input=request.bird_input,
        normalised_input=selected.canonical_name.casefold(),
        accepted_taxon_key=selected.accepted_taxon_key,
        common_name=selected.common_name,
        scientific_name=selected.scientific_name,
        canonical_name=selected.canonical_name,
        rank=selected.rank,
        taxonomic_status=selected.taxonomic_status,
        class_name=selected.class_name,
        order=selected.order,
        family=selected.family,
        genus=selected.genus,
        resolution_method=selected.resolution_method,
        confidence=selected.confidence,
        candidates=[],
        provenance=provenance,
        rationale=(
            "The user selected a candidate returned by the deterministic GBIF "
            f"{selected.relation_level} query; related does not imply ecological "
            "interchangeability or easier observation."
        ),
    )
    updated_request = _validated_request_update(
        request,
        {"bird_input": selected.common_name or selected.canonical_name},
    )
    return {
        "expedition_request": updated_request.model_dump(mode="json"),
        "resolved_taxon": resolved.model_dump(mode="json"),
        "related_taxon_candidates": [],
        "related_taxon_source_key": None,
        "occurrence_evidence": None,
        "public_site_search": None,
        "deterministic_constraints": [],
        "evidence_bundle": None,
        "deterministic_plan_status": None,
        "deterministic_phase1_plan": None,
        "draft_llm_plan": None,
        "final_validated_plan": None,
        "terminal_status": None,
        "terminal_result": None,
        "low_confidence_accepted": False,
        "pending_hitl_kind": None,
        "pending_hitl_payload": None,
        "invalidated_evidence": ["occurrence", "public_sites"],
        "applied_user_decisions": [
            {
                "kind": "related_taxon_selection",
                "accepted_taxon_key": key,
                "relation_level": selected.relation_level,
                "rationale": selected.relation_basis,
            }
        ],
        "visited_nodes": ["related_taxon_selection_interrupt"],
    }


def _refresh_audit(
    tool_name: str,
    canonical_arguments: dict[str, Any],
    summary: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    canonical = json.dumps(canonical_arguments, sort_keys=True)
    digest = hashlib.sha256(f"{tool_name}:{canonical}".encode()).hexdigest()[:16]
    call_id = f"phase3-refresh-{digest}"
    audit = {
        "call_id": call_id,
        "tool_name": tool_name,
        "canonical_arguments": canonical,
        "status": "success",
        "retryable": False,
    }
    log = {
        "evidence_id": f"{tool_name}:{call_id}",
        "source_tool": tool_name,
        "status": "success",
        "summary": summary,
        "provenance": [],
    }
    return audit, log


def build_refresh_invalidated_evidence_node(
    dependencies: BackendDependencies,
) -> Callable[[BiodiversityAgentState], dict[str, Any]]:
    """Recompute only state declared invalid by a validated HITL/fork update."""

    def refresh_invalidated_evidence(
        state: BiodiversityAgentState,
    ) -> dict[str, Any]:
        invalidated = set(state.get("invalidated_evidence", []))
        if not invalidated:
            raise ValueError("No deterministic evidence was marked for refresh")
        request = ExpeditionRequest.model_validate(state["expedition_request"])
        location = ResolvedLocation.model_validate(state["resolved_location"])
        taxon = ResolvedTaxon.model_validate(state["resolved_taxon"])
        update: dict[str, Any] = {
            "invalidated_evidence": [],
            "deterministic_constraints": [],
            "evidence_bundle": None,
            "deterministic_plan_status": None,
            "deterministic_phase1_plan": None,
            "draft_llm_plan": None,
            "final_validated_plan": None,
            "grounding_errors": [],
            "terminal_status": None,
            "terminal_result": None,
            "visited_nodes": ["refresh_invalidated_evidence"],
        }
        audits: list[dict[str, Any]] = []
        logs: list[dict[str, Any]] = []
        occurrence = (
            OccurrenceEvidence.model_validate(state["occurrence_evidence"])
            if state.get("occurrence_evidence")
            else None
        )
        if "occurrence" in invalidated:
            occurrence = search_occurrences(
                taxon,
                target_month=request.seasonal_target_month,
                seasonal_window_radius_months=request.seasonal_window_radius_months,
                repository=dependencies.occurrences,
            )
            update["occurrence_evidence"] = occurrence.model_dump(mode="json")
            audit, log = _refresh_audit(
                "search_occurrences",
                {
                    "accepted_taxon_key": taxon.accepted_taxon_key,
                    "target_month": request.seasonal_target_month,
                    "seasonal_months": occurrence.seasonal_months,
                },
                {
                    "outcome": occurrence.outcome.value,
                    "sampled_count": occurrence.counts.sampled_count,
                    "retained_count": occurrence.counts.retained_total_count,
                    "ranking_eligible_count": occurrence.counts.ranking_eligible_count,
                },
            )
            audits.append(audit)
            logs.append(log)
        if "weather" in invalidated:
            weather = get_weather_context(
                location,
                request.target_local_date,
                repository=dependencies.weather,
            )
            update["weather_evidence"] = weather.model_dump(mode="json")
            audit, log = _refresh_audit(
                "get_weather_context",
                {"requested_date": request.target_local_date.isoformat()},
                {"status": weather.status.value},
            )
            audits.append(audit)
            logs.append(log)
        if "public_sites" in invalidated:
            if occurrence is None:
                raise ValueError("Occurrence evidence is required for site refresh")
            sites = find_public_green_spaces(
                location,
                search_radius_km=request.search_radius_km,
                occurrence=occurrence,
                repository=dependencies.green_spaces,
            )
            update["public_site_search"] = sites.model_dump(mode="json")
            audit, log = _refresh_audit(
                "find_public_green_spaces",
                {
                    "search_radius_km": request.search_radius_km,
                    "occurrence_outcome": occurrence.outcome.value,
                    "seasonal_months": occurrence.seasonal_months,
                },
                {
                    "status": sites.status.value,
                    "recommended_candidate_count": len(sites.candidates),
                    "contextual_site_count": len(sites.contextual_sites),
                },
            )
            audits.append(audit)
            logs.append(log)
        update["executed_tool_call_audit"] = audits
        update["structured_evidence_log"] = logs
        return update

    return refresh_invalidated_evidence


def apply_fork_updates(state: BiodiversityAgentState) -> dict[str, Any]:
    """Graph anchor used only as the validated ``update_state(..., as_node=...)`` writer."""

    del state
    return {}


def build_compose_plan_node(composer_model: Any) -> Callable[[BiodiversityAgentState], dict[str, Any]]:
    structured_composer = composer_model.with_structured_output(
        BiodiversityExpeditionPlan,
        method="function_calling",
    )

    def compose_expedition_plan(state: BiodiversityAgentState) -> dict[str, Any]:
        bundle = ExpeditionEvidenceBundle.model_validate(state["evidence_bundle"])
        phase1_plan = ExpeditionPlan.model_validate(state["deterministic_phase1_plan"])
        payload = compact_plan_payload(
            bundle,
            phase1_plan,
            low_confidence_accepted=state.get(
                "low_confidence_accepted", False
            ),
        )
        try:
            response = structured_composer.invoke(
                [SystemMessage(content=PLAN_COMPOSER_PROMPT), HumanMessage(content=json.dumps(payload, ensure_ascii=False, indent=2))]
            )
            plan = response if isinstance(response, BiodiversityExpeditionPlan) else BiodiversityExpeditionPlan.model_validate(response)
            draft = plan.model_dump(mode="json")
        except Exception as exc:  # model/schema failures become bounded grounding failures
            draft = {"__generation_error__": f"{type(exc).__name__}: {exc}"}
        return {"draft_llm_plan": draft, "grounding_errors": [], "visited_nodes": ["compose_expedition_plan"]}

    return compose_expedition_plan


def build_revise_plan_node(composer_model: Any) -> Callable[[BiodiversityAgentState], dict[str, Any]]:
    structured_composer = composer_model.with_structured_output(BiodiversityExpeditionPlan, method="function_calling")

    def revise_expedition_plan(state: BiodiversityAgentState) -> dict[str, Any]:
        bundle = ExpeditionEvidenceBundle.model_validate(state["evidence_bundle"])
        phase1_plan = ExpeditionPlan.model_validate(state["deterministic_phase1_plan"])
        payload = {
            "validated_evidence": compact_plan_payload(
                bundle,
                phase1_plan,
                low_confidence_accepted=state.get(
                    "low_confidence_accepted", False
                ),
            ),
            "rejected_draft": state.get("draft_llm_plan"),
            "validation_errors": state.get("grounding_errors", []),
        }
        try:
            response = structured_composer.invoke(
                [SystemMessage(content=f"{PLAN_COMPOSER_PROMPT}\n{PLAN_REVISION_PROMPT}"), HumanMessage(content=json.dumps(payload, ensure_ascii=False, indent=2))]
            )
            plan = response if isinstance(response, BiodiversityExpeditionPlan) else BiodiversityExpeditionPlan.model_validate(response)
            draft = plan.model_dump(mode="json")
        except Exception as exc:
            draft = {"__generation_error__": f"{type(exc).__name__}: {exc}"}
        return {
            "draft_llm_plan": draft,
            "plan_revision_count": state.get("plan_revision_count", 0) + 1,
            "grounding_errors": [],
            "visited_nodes": ["revise_expedition_plan"],
        }

    return revise_expedition_plan


def grounding_and_safety_checks(state: BiodiversityAgentState) -> dict[str, Any]:
    bundle = ExpeditionEvidenceBundle.model_validate(state["evidence_bundle"])
    phase1_plan = ExpeditionPlan.model_validate(state["deterministic_phase1_plan"])
    raw = state.get("draft_llm_plan") or {}
    try:
        draft = BiodiversityExpeditionPlan.model_validate(raw)
    except ValidationError as exc:
        errors = [f"Draft plan schema validation failed: {exc}"]
    else:
        errors = validate_grounded_plan(
            bundle,
            phase1_plan,
            draft,
            low_confidence_accepted=state.get(
                "low_confidence_accepted", False
            ),
        )
    if errors:
        return {"grounding_errors": errors, "visited_nodes": ["grounding_and_safety_checks"]}
    return {
        "grounding_errors": [],
        "final_validated_plan": draft.model_dump(mode="json"),
        "terminal_status": "completed",
        "visited_nodes": ["grounding_and_safety_checks"],
    }


def deterministic_plan_fallback(state: BiodiversityAgentState) -> dict[str, Any]:
    bundle = ExpeditionEvidenceBundle.model_validate(state["evidence_bundle"])
    phase1_plan = ExpeditionPlan.model_validate(state["deterministic_phase1_plan"])
    fallback = deterministic_safe_plan(
        bundle,
        phase1_plan,
        low_confidence_accepted=state.get("low_confidence_accepted", False),
    )
    return {
        "final_validated_plan": fallback.model_dump(mode="json"),
        "terminal_status": "completed_with_deterministic_fallback",
        "visited_nodes": ["deterministic_plan_fallback"],
    }
