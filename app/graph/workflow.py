"""Construction of the controlled workflow and dynamic investigator loop."""

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.graph.nodes import (
    analyze_incident,
    build_finalize_node,
    build_investigator_node,
    route_after_investigator,
    step_limit_guard,
    summarize_low_risk,
)
from app.graph.routing import route_incident
from app.graph.state import IncidentState
from app.tools import INVESTIGATION_TOOLS

DEFAULT_MAX_AGENT_STEPS = 6


def build_agent_graph(
    investigator_model: Any,
    finalizer_model: Any,
    checkpointer=None,
    *,
    max_agent_steps: int = DEFAULT_MAX_AGENT_STEPS,
):
    """Compile an injected-model graph; construction never requires an API key."""

    if max_agent_steps < 1:
        raise ValueError("max_agent_steps must be at least 1")

    builder = StateGraph(IncidentState)
    builder.add_node("analyze_incident", analyze_incident)
    builder.add_node("summarize_low_risk", summarize_low_risk)
    builder.add_node(
        "investigator_agent",
        build_investigator_node(
            investigator_model,
            max_agent_steps=max_agent_steps,
        ),
    )
    builder.add_node(
        "tools",
        ToolNode(INVESTIGATION_TOOLS, handle_tool_errors=False),
    )
    builder.add_node("step_limit_guard", step_limit_guard)
    builder.add_node(
        "finalize_investigation",
        build_finalize_node(finalizer_model),
    )

    builder.add_edge(START, "analyze_incident")
    builder.add_conditional_edges(
        "analyze_incident",
        route_incident,
        ["summarize_low_risk", "investigator_agent"],
    )
    builder.add_edge("summarize_low_risk", END)
    builder.add_conditional_edges(
        "investigator_agent",
        partial(
            route_after_investigator,
            max_agent_steps=max_agent_steps,
        ),
        ["tools", "step_limit_guard", "finalize_investigation"],
    )
    builder.add_edge("tools", "investigator_agent")
    builder.add_edge("step_limit_guard", "finalize_investigation")
    builder.add_edge("finalize_investigation", END)
    return builder.compile(checkpointer=checkpointer)
