"""Safe presentation metadata for the actual compiled LangGraph topology."""

from __future__ import annotations

from typing import Any

from app.biodiversity.api.schemas import (
    WorkflowEdgeView,
    WorkflowNodeView,
    WorkflowTopologyView,
)
from app.biodiversity.run_models import WORKFLOW_VERSION


# Presentation metadata lives server-side beside the workflow and is validated
# against the compiled graph. The browser never maintains a second topology.
_NODE_PRESENTATION: dict[str, tuple[str, str, str, str, int]] = {
    "__start__": (
        "Start",
        "entry",
        "start",
        "Accept a new execution or resume command.",
        0,
    ),
    "parse_expedition_request": (
        "Parse expedition request",
        "intake",
        "model",
        "Turn the natural-language request into typed constraints.",
        0,
    ),
    "request_clarification_interrupt": (
        "Request clarification",
        "intake",
        "hitl",
        "Pause for missing or contradictory request fields.",
        1,
    ),
    "geocode_location_query": (
        "Geocode location query",
        "location",
        "deterministic",
        "Resolve a place description without exposing raw coordinates.",
        0,
    ),
    "resolve_location": (
        "Resolve location",
        "location",
        "deterministic",
        "Validate the generalised start inside Greater London.",
        1,
    ),
    "location_correction_interrupt": (
        "Location correction",
        "location",
        "hitl",
        "Pause when the intended London location is ambiguous.",
        2,
    ),
    "resolve_taxon": (
        "Resolve taxon",
        "taxonomy",
        "deterministic",
        "Match the requested bird to an accepted taxonomy.",
        0,
    ),
    "prepare_taxon_selection": (
        "Prepare taxon selection",
        "taxonomy",
        "deterministic",
        "Build a bounded list of safe taxonomy candidates.",
        1,
    ),
    "taxon_selection_interrupt": (
        "Taxon selection",
        "taxonomy",
        "hitl",
        "Pause for a human choice between plausible taxa.",
        2,
    ),
    "bird_input_correction_interrupt": (
        "Bird input correction",
        "taxonomy",
        "hitl",
        "Pause for a corrected bird name when resolution fails.",
        3,
    ),
    "evidence_agent": (
        "Evidence agent",
        "evidence",
        "model",
        "Choose the bounded evidence tools required by the request.",
        0,
    ),
    "evidence_tools": (
        "Evidence tools",
        "evidence",
        "tool",
        "Call taxonomy, occurrence, weather, and public-site sources.",
        1,
    ),
    "record_structured_evidence": (
        "Record structured evidence",
        "evidence",
        "deterministic",
        "Validate and merge tool outputs into the durable state.",
        2,
    ),
    "evidence_loop_limit": (
        "Evidence loop limit",
        "evidence",
        "terminal",
        "Stop safely if evidence collection exceeds its bounded loop.",
        3,
    ),
    "source_resolution_failure": (
        "Source resolution failure",
        "evidence",
        "terminal",
        "Stop with a safe source-failure outcome.",
        4,
    ),
    "deterministic_validation": (
        "Deterministic validation",
        "validation",
        "deterministic",
        "Apply evidence gates and expedition constraints.",
        0,
    ),
    "actionable_tradeoff_interrupt": (
        "Actionable trade-off",
        "validation",
        "hitl",
        "Pause for a human choice when constraints and evidence conflict.",
        1,
    ),
    "apply_validated_user_choice": (
        "Apply validated choice",
        "validation",
        "deterministic",
        "Apply only allow-listed human decision fields.",
        2,
    ),
    "related_taxon_selection_interrupt": (
        "Related taxon selection",
        "validation",
        "hitl",
        "Pause before substituting a related taxon.",
        3,
    ),
    "refresh_invalidated_evidence": (
        "Refresh invalidated evidence",
        "validation",
        "deterministic",
        "Recompute evidence invalidated by a changed constraint.",
        4,
    ),
    "compose_expedition_plan": (
        "Compose expedition plan",
        "planning",
        "model",
        "Compose a plan from the validated evidence bundle.",
        0,
    ),
    "revise_expedition_plan": (
        "Revise expedition plan",
        "planning",
        "model",
        "Repair a draft that did not pass deterministic grounding.",
        1,
    ),
    "grounding_and_safety_checks": (
        "Grounding and safety checks",
        "planning",
        "deterministic",
        "Verify claims, provenance, constraints, and safe wording.",
        2,
    ),
    "deterministic_plan_fallback": (
        "Deterministic plan fallback",
        "planning",
        "terminal",
        "Produce a safe fallback after the bounded revision loop.",
        3,
    ),
    "__end__": (
        "End",
        "outcome",
        "end",
        "Persist the execution outcome and final checkpoint.",
        0,
    ),
}

_STAGE_ORDER = {
    "entry": 0,
    "intake": 1,
    "location": 2,
    "taxonomy": 3,
    "evidence": 4,
    "validation": 5,
    "planning": 6,
    "outcome": 7,
}


def build_workflow_topology(compiled_graph: Any) -> WorkflowTopologyView:
    """Return a safe DTO derived from, and checked against, the compiled graph."""

    graph = compiled_graph.get_graph()
    compiled_ids = set(graph.nodes)
    described_ids = set(_NODE_PRESENTATION)
    if compiled_ids != described_ids:
        missing = sorted(compiled_ids - described_ids)
        stale = sorted(described_ids - compiled_ids)
        raise RuntimeError(
            f"Workflow presentation metadata mismatch; missing={missing}, stale={stale}"
        )

    nodes = [
        WorkflowNodeView(
            node_id=node_id,
            label=metadata[0],
            stage=metadata[1],
            kind=metadata[2],
            summary=metadata[3],
            order=metadata[4],
        )
        for node_id, metadata in _NODE_PRESENTATION.items()
    ]
    nodes.sort(key=lambda item: (_STAGE_ORDER[item.stage], item.order, item.node_id))

    edges = [
        WorkflowEdgeView(
            source=edge.source,
            target=edge.target,
            conditional=bool(edge.conditional),
            route_label=(
                str(edge.data)
                if isinstance(edge.data, (str, int, float, bool))
                else None
            ),
        )
        for edge in graph.edges
    ]
    edges.sort(key=lambda item: (item.source, item.target, item.route_label or ""))
    return WorkflowTopologyView(
        workflow_version=WORKFLOW_VERSION,
        nodes=nodes,
        edges=edges,
    )
