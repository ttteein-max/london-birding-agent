"""Deterministic, model-driven, HITL, and safety nodes for Phase 2."""

from __future__ import annotations

import json
from collections.abc import Callable
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
    AccessCertainty,
    ConstraintStatus,
    EvidenceOutcome,
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
    SiteSearchAction,
    TaxonStatus,
    WeatherEvidence,
)
from app.biodiversity.orchestration import (
    BackendDependencies,
    build_deterministic_expedition_plan,
)
from app.biodiversity.tools import (
    build_expedition_evidence_bundle,
    lookup_uk_postcode,
    resolve_bird_taxon,
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
            kind = "taxon_selection"
            payload = {
                "kind": kind,
                "question": "Which GBIF candidate is the intended species?",
                "candidates": [
                    {
                        "accepted_taxon_key": item.accepted_taxon_key,
                        "common_name": item.common_name,
                        "scientific_name": item.scientific_name,
                        "canonical_name": item.canonical_name,
                        "rank": item.rank,
                        "taxonomic_status": item.taxonomic_status,
                        "resolution_method": item.resolution_method,
                        "confidence": item.confidence,
                    }
                    for item in taxon.candidates
                ],
                "resume_schema": {"accepted_taxon_key": "one listed integer key"},
            }
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
        resolution_method=selected.resolution_method,
        confidence=selected.confidence,
        candidates=taxon.candidates,
        provenance=taxon.provenance,
        rationale="The user selected this accepted taxon from the exposed GBIF candidates.",
    )
    return {
        "resolved_taxon": resolved.model_dump(mode="json"),
        "pending_hitl_kind": None,
        "pending_hitl_payload": None,
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


def _decision_names(state: BiodiversityAgentState) -> set[str]:
    return {str(item.get("option")) for item in state.get("applied_user_decisions", []) if item.get("option")}


def _actionable_tradeoff(
    state: BiodiversityAgentState,
    request: ExpeditionRequest,
    bundle: ExpeditionEvidenceBundle,
) -> dict[str, Any] | None:
    decisions = _decision_names(state)
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
    if bundle.evidence_outcome in {EvidenceOutcome.limited_contextual_evidence, EvidenceOutcome.insufficient_evidence} and "accept_context_only" not in decisions and "change_target_month" not in decisions:
        options: list[dict[str, Any]] = [{"option": "accept_context_only"}]
        if SiteSearchAction.change_target_month in actions:
            options.insert(0, {"option": "change_target_month", "allowed_months": list(range(1, 13))})
        return {
            "kind": "actionable_tradeoff",
            "question": "Would you like to change the target month or accept a context-only result?",
            "reason": "The deterministic evidence gate does not support site recommendations for the current month.",
            "options": options,
            "resume_schema": {"option": "listed option", "target_month": "required only for change_target_month"},
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


def deterministic_validation(state: BiodiversityAgentState) -> dict[str, Any]:
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
    tradeoff = _actionable_tradeoff(state, request, bundle)
    return {
        "deterministic_constraints": [item.model_dump(mode="json") for item in constraints],
        "evidence_bundle": bundle.model_dump(mode="json"),
        "deterministic_plan_status": phase1_plan.status.value,
        "deterministic_phase1_plan": phase1_plan.model_dump(mode="json"),
        "pending_hitl_kind": "actionable_tradeoff" if tradeoff else None,
        "pending_hitl_payload": tradeoff,
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
        "visited_nodes": ["apply_validated_user_choice"],
    }
    if option == "expand_search_radius":
        radius = choice.get("search_radius_km")
        if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not request.search_radius_km < float(radius) <= 25:
            raise ValueError("Expanded radius must be greater than the current radius and at most 25 km")
        request = _validated_request_update(request, {"search_radius_km": float(radius)})
        update.update({"expedition_request": request.model_dump(mode="json"), "public_site_search": None, "decision_route": "evidence_agent"})
    elif option == "change_target_month":
        month = choice.get("target_month")
        if isinstance(month, bool) or not isinstance(month, int) or month not in range(1, 13) or month == request.seasonal_target_month:
            raise ValueError("target_month must be a different integer from 1 to 12")
        request = _validated_request_update(request, {"target_month_override": month})
        update.update({"expedition_request": request.model_dump(mode="json"), "occurrence_evidence": None, "public_site_search": None, "decision_route": "evidence_agent"})
    elif option == "revise_rain_preference":
        request = _validated_request_update(request, {"rain_preference": RainPreference.no_preference})
        update["expedition_request"] = request.model_dump(mode="json")
    elif option not in {"accept_context_only", "continue_with_weather_acknowledgement", "accept_uncertain_access"}:
        raise ValueError("Unsupported deterministic trade-off option")
    return update


def build_compose_plan_node(composer_model: Any) -> Callable[[BiodiversityAgentState], dict[str, Any]]:
    structured_composer = composer_model.with_structured_output(
        BiodiversityExpeditionPlan,
        method="function_calling",
    )

    def compose_expedition_plan(state: BiodiversityAgentState) -> dict[str, Any]:
        bundle = ExpeditionEvidenceBundle.model_validate(state["evidence_bundle"])
        phase1_plan = ExpeditionPlan.model_validate(state["deterministic_phase1_plan"])
        payload = compact_plan_payload(bundle, phase1_plan)
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
            "validated_evidence": compact_plan_payload(bundle, phase1_plan),
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
        errors = validate_grounded_plan(bundle, phase1_plan, draft)
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
    fallback = deterministic_safe_plan(bundle, phase1_plan)
    return {
        "final_validated_plan": fallback.model_dump(mode="json"),
        "terminal_status": "completed_with_deterministic_fallback",
        "visited_nodes": ["deterministic_plan_fallback"],
    }
