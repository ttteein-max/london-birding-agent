"""Construction of the Phase 3 London biodiversity LangGraph."""

from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.biodiversity.graph.nodes import (
    actionable_tradeoff_interrupt,
    apply_route_tradeoff_choice,
    apply_validated_user_choice,
    bird_input_correction_interrupt,
    build_compose_plan_node,
    build_evidence_agent_node,
    build_geocode_location_node,
    build_parse_request_node,
    build_prepare_taxon_selection_node,
    build_request_walking_routes_node,
    build_resolve_public_site_entrances_node,
    build_refresh_invalidated_evidence_node,
    build_resolve_location_node,
    build_resolve_taxon_node,
    build_revise_plan_node,
    deterministic_plan_fallback,
    deterministic_validation,
    evidence_loop_limit,
    grounding_and_safety_checks,
    location_correction_interrupt,
    record_structured_evidence,
    rank_route_options,
    related_taxon_selection_interrupt,
    request_clarification_interrupt,
    route_tradeoff_interrupt,
    source_resolution_failure,
    taxon_selection_interrupt,
    validate_route_constraints,
)
from app.biodiversity.graph.routing import (
    route_after_evidence_agent,
    route_after_grounding,
    route_after_location,
    route_after_request_clarification,
    route_after_request_parse,
    route_after_route_choice,
    route_after_route_ranking,
    route_after_taxon,
    route_after_user_choice,
    route_after_validation,
)
from app.biodiversity.graph.state import BiodiversityAgentState
from app.biodiversity.graph.tool_adapters import build_evidence_tools
from app.biodiversity.orchestration import BackendDependencies
from app.biodiversity.routing import RoutingServices


DEFAULT_MAXIMUM_EVIDENCE_ROUNDS = 6


def build_biodiversity_graph(
    chat_model: Any | None = None,
    *,
    parser_model: Any | None = None,
    evidence_model: Any | None = None,
    composer_model: Any | None = None,
    dependencies: BackendDependencies | None = None,
    routing_services: RoutingServices | None = None,
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
    routing_services = routing_services or RoutingServices.fixture()
    evidence_tools = build_evidence_tools(dependencies)

    builder = StateGraph(BiodiversityAgentState)
    builder.add_node("parse_expedition_request", build_parse_request_node(parser_model))
    builder.add_node("request_clarification_interrupt", request_clarification_interrupt)
    builder.add_node(
        "geocode_location_query",
        build_geocode_location_node(dependencies),
    )
    builder.add_node("resolve_location", build_resolve_location_node(dependencies))
    builder.add_node("location_correction_interrupt", location_correction_interrupt)
    builder.add_node("resolve_taxon", build_resolve_taxon_node(dependencies))
    builder.add_node(
        "prepare_taxon_selection",
        build_prepare_taxon_selection_node(dependencies),
    )
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
    builder.add_node(
        "deterministic_validation",
        partial(deterministic_validation, dependencies=dependencies),
    )
    builder.add_node("actionable_tradeoff_interrupt", actionable_tradeoff_interrupt)
    builder.add_node("apply_validated_user_choice", apply_validated_user_choice)
    builder.add_node(
        "related_taxon_selection_interrupt",
        related_taxon_selection_interrupt,
    )
    builder.add_node(
        "refresh_invalidated_evidence",
        build_refresh_invalidated_evidence_node(dependencies),
    )
    builder.add_node(
        "resolve_public_site_entrances",
        build_resolve_public_site_entrances_node(routing_services),
    )
    builder.add_node(
        "request_walking_routes",
        build_request_walking_routes_node(routing_services),
    )
    builder.add_node("validate_route_constraints", validate_route_constraints)
    builder.add_node("rank_route_options", rank_route_options)
    builder.add_node("route_tradeoff_interrupt", route_tradeoff_interrupt)
    builder.add_node("apply_route_tradeoff_choice", apply_route_tradeoff_choice)
    builder.add_node("compose_expedition_plan", build_compose_plan_node(composer_model))
    builder.add_node("revise_expedition_plan", build_revise_plan_node(composer_model))
    builder.add_node("grounding_and_safety_checks", grounding_and_safety_checks)
    builder.add_node("deterministic_plan_fallback", deterministic_plan_fallback)

    builder.add_edge(START, "parse_expedition_request")
    builder.add_conditional_edges(
        "parse_expedition_request",
        route_after_request_parse,
        [
            "request_clarification_interrupt",
            "geocode_location_query",
            "resolve_location",
        ],
    )
    builder.add_conditional_edges(
        "request_clarification_interrupt",
        route_after_request_clarification,
        ["geocode_location_query", "resolve_location"],
    )
    builder.add_edge("geocode_location_query", "location_correction_interrupt")
    builder.add_conditional_edges(
        "resolve_location",
        route_after_location,
        ["location_correction_interrupt", "resolve_taxon", "source_resolution_failure"],
    )
    builder.add_edge("location_correction_interrupt", "resolve_location")
    builder.add_conditional_edges(
        "resolve_taxon",
        route_after_taxon,
        [
            "prepare_taxon_selection",
            "taxon_selection_interrupt",
            "bird_input_correction_interrupt",
            "evidence_agent",
            "source_resolution_failure",
        ],
    )
    builder.add_edge("prepare_taxon_selection", "taxon_selection_interrupt")
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
            "resolve_public_site_entrances": "resolve_public_site_entrances",
            "terminal": END,
        },
    )
    builder.add_edge("actionable_tradeoff_interrupt", "apply_validated_user_choice")
    builder.add_conditional_edges(
        "apply_validated_user_choice",
        route_after_user_choice,
        [
            "evidence_agent",
            "refresh_invalidated_evidence",
            "related_taxon_selection_interrupt",
            "deterministic_validation",
        ],
    )
    builder.add_edge(
        "related_taxon_selection_interrupt",
        "refresh_invalidated_evidence",
    )
    builder.add_edge("refresh_invalidated_evidence", "deterministic_validation")
    builder.add_edge("resolve_public_site_entrances", "request_walking_routes")
    builder.add_edge("request_walking_routes", "validate_route_constraints")
    builder.add_edge("validate_route_constraints", "rank_route_options")
    builder.add_conditional_edges(
        "rank_route_options",
        route_after_route_ranking,
        ["route_tradeoff_interrupt", "compose_expedition_plan"],
    )
    builder.add_edge("route_tradeoff_interrupt", "apply_route_tradeoff_choice")
    builder.add_conditional_edges(
        "apply_route_tradeoff_choice",
        route_after_route_choice,
        ["request_walking_routes", "compose_expedition_plan"],
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
