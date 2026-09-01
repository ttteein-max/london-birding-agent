"""Conditional routing contracts for the biodiversity graph."""

from __future__ import annotations

from typing import Literal

from langchain.messages import AIMessage

from app.biodiversity.graph.state import BiodiversityAgentState


def route_after_request_parse(state: BiodiversityAgentState) -> Literal["request_clarification_interrupt", "resolve_location"]:
    return "request_clarification_interrupt" if state.get("pending_hitl_kind") == "request_clarification" else "resolve_location"


def route_after_location(state: BiodiversityAgentState) -> Literal["location_correction_interrupt", "resolve_taxon", "source_resolution_failure"]:
    status = (state.get("resolved_location") or {}).get("status")
    if state.get("pending_hitl_kind") == "location_correction":
        return "location_correction_interrupt"
    if status == "resolved":
        return "resolve_taxon"
    return "source_resolution_failure"


def route_after_taxon(state: BiodiversityAgentState) -> Literal[
    "taxon_selection_interrupt",
    "bird_input_correction_interrupt",
    "evidence_agent",
    "source_resolution_failure",
]:
    kind = state.get("pending_hitl_kind")
    if kind == "taxon_selection":
        return "taxon_selection_interrupt"
    if kind == "bird_input_correction":
        return "bird_input_correction_interrupt"
    if (state.get("resolved_taxon") or {}).get("status") == "resolved":
        return "evidence_agent"
    return "source_resolution_failure"


def route_after_evidence_agent(
    state: BiodiversityAgentState,
    *,
    maximum_rounds: int,
) -> Literal["evidence_tools", "evidence_loop_limit", "deterministic_validation"]:
    latest = state.get("messages", [])[-1]
    if not isinstance(latest, AIMessage):
        raise TypeError("Latest evidence-agent message must be an AIMessage")
    if latest.tool_calls:
        if state.get("evidence_loop_count", 0) >= maximum_rounds:
            return "evidence_loop_limit"
        return "evidence_tools"
    return "deterministic_validation"


def route_after_validation(state: BiodiversityAgentState) -> Literal["actionable_tradeoff_interrupt", "compose_expedition_plan", "terminal"]:
    if state.get("terminal_status"):
        return "terminal"
    if state.get("pending_hitl_kind") == "actionable_tradeoff":
        return "actionable_tradeoff_interrupt"
    return "compose_expedition_plan"


def route_after_user_choice(state: BiodiversityAgentState) -> Literal["evidence_agent", "deterministic_validation"]:
    route = state.get("decision_route")
    if route not in {"evidence_agent", "deterministic_validation"}:
        raise ValueError("Validated user choice did not set a valid route")
    return route


def route_after_grounding(state: BiodiversityAgentState) -> Literal["revise_expedition_plan", "deterministic_plan_fallback", "complete"]:
    if not state.get("grounding_errors"):
        return "complete"
    if state.get("plan_revision_count", 0) < 1:
        return "revise_expedition_plan"
    return "deterministic_plan_fallback"
