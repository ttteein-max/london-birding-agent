"""Construction of the Phase 2 London biodiversity LangGraph."""

from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.biodiversity.graph.nodes import (
    actionable_tradeoff_interrupt,
    apply_validated_user_choice,
    bird_input_correction_interrupt,
    build_compose_plan_node,
    build_evidence_agent_node,
    build_parse_request_node,
    build_resolve_location_node,
    build_resolve_taxon_node,
    build_revise_plan_node,
    deterministic_plan_fallback,
    deterministic_validation,
    evidence_loop_limit,
    grounding_and_safety_checks,
    location_correction_interrupt,
    record_structured_evidence,
    request_clarification_interrupt,
    source_resolution_failure,
    taxon_selection_interrupt,
)
from app.biodiversity.graph.routing import (
    route_after_evidence_agent,
    route_after_grounding,
    route_after_location,
    route_after_request_parse,
    route_after_taxon,
    route_after_user_choice,
    route_after_validation,
)
from app.biodiversity.graph.state import BiodiversityAgentState
from app.biodiversity.graph.tool_adapters import build_evidence_tools
from app.biodiversity.orchestration import BackendDependencies


DEFAULT_MAXIMUM_EVIDENCE_ROUNDS = 6


def build_biodiversity_graph(
    chat_model: Any | None = None,
    *,
    parser_model: Any | None = None,
    evidence_model: Any | None = None,
    composer_model: Any | None = None,
    dependencies: BackendDependencies | None = None,
    checkpointer: Any | None = None,
    maximum_evidence_rounds: int = DEFAULT_MAXIMUM_EVIDENCE_ROUNDS,
):
    """Compile an injected-model graph without reading environment variables."""

    parser_model = parser_model or chat_model
    evidence_model = evidence_model or chat_model
    composer_model = composer_model or chat_model
    if parser_model is None or evidence_model is None or composer_model is None:
        raise ValueError("Provide chat_model or all parser, evidence, and composer models")
    if maximum_evidence_rounds < 1:
        raise ValueError("maximum_evidence_rounds must be at least 1")
    dependencies = dependencies or BackendDependencies.fixture()
    evidence_tools = build_evidence_tools(dependencies)

    builder = StateGraph(BiodiversityAgentState)
    builder.add_node("parse_expedition_request", build_parse_request_node(parser_model))
    builder.add_node("request_clarification_interrupt", request_clarification_interrupt)
    builder.add_node("resolve_location", build_resolve_location_node(dependencies))
    builder.add_node("location_correction_interrupt", location_correction_interrupt)
    builder.add_node("resolve_taxon", build_resolve_taxon_node(dependencies))
    builder.add_node("taxon_selection_interrupt", taxon_selection_interrupt)
    builder.add_node("bird_input_correction_interrupt", bird_input_correction_interrupt)
    builder.add_node(
        "evidence_agent",
        build_evidence_agent_node(
            evidence_model,
            evidence_tools,
            maximum_rounds=maximum_evidence_rounds,
        ),
    )
    builder.add_node("evidence_tools", ToolNode(evidence_tools, handle_tool_errors=False))
    builder.add_node("record_structured_evidence", record_structured_evidence)
    builder.add_node("evidence_loop_limit", evidence_loop_limit)
    builder.add_node("source_resolution_failure", source_resolution_failure)
    builder.add_node("deterministic_validation", deterministic_validation)
    builder.add_node("actionable_tradeoff_interrupt", actionable_tradeoff_interrupt)
    builder.add_node("apply_validated_user_choice", apply_validated_user_choice)
    builder.add_node("compose_expedition_plan", build_compose_plan_node(composer_model))
    builder.add_node("revise_expedition_plan", build_revise_plan_node(composer_model))
    builder.add_node("grounding_and_safety_checks", grounding_and_safety_checks)
    builder.add_node("deterministic_plan_fallback", deterministic_plan_fallback)

    builder.add_edge(START, "parse_expedition_request")
    builder.add_conditional_edges(
        "parse_expedition_request",
        route_after_request_parse,
        ["request_clarification_interrupt", "resolve_location"],
    )
    builder.add_edge("request_clarification_interrupt", "resolve_location")
    builder.add_conditional_edges(
        "resolve_location",
        route_after_location,
        ["location_correction_interrupt", "resolve_taxon", "source_resolution_failure"],
    )
    builder.add_edge("location_correction_interrupt", "resolve_location")
    builder.add_conditional_edges(
        "resolve_taxon",
        route_after_taxon,
        ["taxon_selection_interrupt", "bird_input_correction_interrupt", "evidence_agent", "source_resolution_failure"],
    )
    builder.add_edge("taxon_selection_interrupt", "evidence_agent")
    builder.add_edge("bird_input_correction_interrupt", "resolve_taxon")
    builder.add_conditional_edges(
        "evidence_agent",
        partial(route_after_evidence_agent, maximum_rounds=maximum_evidence_rounds),
        ["evidence_tools", "evidence_loop_limit", "deterministic_validation"],
    )
    builder.add_edge("evidence_tools", "record_structured_evidence")
    builder.add_edge("record_structured_evidence", "evidence_agent")
    builder.add_conditional_edges(
        "deterministic_validation",
        route_after_validation,
        {
            "actionable_tradeoff_interrupt": "actionable_tradeoff_interrupt",
            "compose_expedition_plan": "compose_expedition_plan",
            "terminal": END,
        },
    )
    builder.add_edge("actionable_tradeoff_interrupt", "apply_validated_user_choice")
    builder.add_conditional_edges(
        "apply_validated_user_choice",
        route_after_user_choice,
        ["evidence_agent", "deterministic_validation"],
    )
    builder.add_edge("compose_expedition_plan", "grounding_and_safety_checks")
    builder.add_conditional_edges(
        "grounding_and_safety_checks",
        route_after_grounding,
        {
            "revise_expedition_plan": "revise_expedition_plan",
            "deterministic_plan_fallback": "deterministic_plan_fallback",
            "complete": END,
        },
    )
    builder.add_edge("revise_expedition_plan", "grounding_and_safety_checks")
    builder.add_edge("deterministic_plan_fallback", END)
    builder.add_edge("evidence_loop_limit", END)
    builder.add_edge("source_resolution_failure", END)
    return builder.compile(checkpointer=checkpointer)
