"""Nodes for deterministic triage and the model-driven investigation loop."""

import json
import logging
from collections.abc import Callable
from typing import Any, Literal

from langchain.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agents import classify_incident
from app.graph.state import IncidentState
from app.schemas import EvidenceItem, FinalInvestigation, ProposedAction
from app.tools import INVESTIGATION_TOOLS

logger = logging.getLogger(__name__)

INVESTIGATOR_SYSTEM_PROMPT = """You are the read-only incident investigator.

You control the investigation by deciding which of the bound read-only tools to
call, the arguments, the order, whether another tool is needed, and when the raw
evidence is sufficient. Never request or claim to execute rollback, restart,
delete, deploy, or any other mutation. Use only the bound tools. Prefer precise
service names and ISO-8601 timestamps. Tool errors are observations: after an
error, decide whether another read-only query can still help. When you have
enough evidence, return a concise investigation summary with no tool calls.
"""

FINALIZER_SYSTEM_PROMPT = """You are the incident finalizer. Produce only the
requested structured result from the raw evidence. You propose actions but never
execute them. Use rollback_deployment only when successful deployment evidence
contains the service, deployed version, and previous target version. For missing
or insufficient evidence, propose escalate_incident or monitor_service.
"""


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def analyze_incident(state: IncidentState) -> dict:
    """Deterministically classify the outer workflow route."""

    description = state["incident_description"]
    classification = classify_incident(description)
    analysis = (
        f"Incident affects {classification.service}; classified as "
        f"{classification.severity} severity with "
        f"{classification.confidence:.0%} confidence."
    )
    logger.info(
        "NODE analyze_incident service=%s severity=%s confidence=%.2f route=%s",
        classification.service,
        classification.severity,
        classification.confidence,
        classification.investigation_route,
    )
    return {
        "classification": classification,
        "analysis": analysis,
        "messages": [HumanMessage(content=f"Investigate incident: {description}")],
        "agent_steps": 0,
        "step_limit_reached": False,
        "visited_nodes": ["analyze_incident"],
    }


def summarize_low_risk(state: IncidentState) -> dict:
    """Produce a useful lightweight result without invoking any model or tool."""

    classification = state["classification"]
    summary = (
        f"{classification.service} shows a low-severity symptom with no "
        "current signal requiring a full incident investigation."
    )
    recommendation = (
        "Monitor error rate and p95 latency for 30 minutes; escalate to the full "
        "investigation route if either crosses the service alert threshold."
    )
    logger.info("NODE summarize_low_risk completed service=%s", classification.service)
    return {
        "low_risk_summary": summary,
        "monitoring_recommendation": recommendation,
        "investigation_status": "lightweight_complete",
        "visited_nodes": ["summarize_low_risk"],
    }


def build_investigator_node(
    investigator_model: Any,
    *,
    max_agent_steps: int,
) -> Callable[[IncidentState], dict]:
    """Bind read-only tools and return the genuine model-call graph node."""

    model_with_tools = investigator_model.bind_tools(INVESTIGATION_TOOLS)

    def investigator_agent(state: IncidentState) -> dict:
        context = {
            "incident_description": state["incident_description"],
            "classification": _jsonable(state["classification"]),
            "existing_evidence": _jsonable(state.get("evidence", [])),
            "tool_errors": state.get("tool_errors", []),
            "agent_steps_used": state.get("agent_steps", 0),
            "maximum_agent_steps": max_agent_steps,
            "tool_use_constraints": {
                "read_only_only": True,
                "allowed_tools": [tool.name for tool in INVESTIGATION_TOOLS],
                "mutating_actions_forbidden": True,
            },
        }
        system_message = SystemMessage(
            content=(
                f"{INVESTIGATOR_SYSTEM_PROMPT}\n\nCurrent investigation context:\n"
                f"{json.dumps(context, indent=2, ensure_ascii=False)}"
            )
        )
        response = model_with_tools.invoke(
            [system_message, *state.get("messages", [])]
        )
        if not isinstance(response, AIMessage):
            raise TypeError("Investigator model must return an AIMessage")

        next_step = state.get("agent_steps", 0) + 1
        logger.info(
            "NODE investigator_agent step=%s tool_calls=%s",
            next_step,
            [call["name"] for call in response.tool_calls],
        )
        return {
            "messages": [response],
            "agent_steps": next_step,
            "visited_nodes": ["investigator_agent"],
        }

    return investigator_agent


def route_after_investigator(
    state: IncidentState,
    *,
    max_agent_steps: int,
) -> Literal["tools", "step_limit_guard", "finalize_investigation"]:
    """Route real model tool calls while enforcing a finite agent loop."""

    latest = state["messages"][-1]
    if not isinstance(latest, AIMessage):
        raise TypeError("Latest investigator message must be an AIMessage")

    if latest.tool_calls:
        if state.get("agent_steps", 0) >= max_agent_steps:
            logger.warning("ROUTE agent step limit -> step_limit_guard")
            return "step_limit_guard"
        logger.info("ROUTE investigator tool calls -> tools")
        return "tools"

    logger.info("ROUTE investigator complete -> finalize_investigation")
    return "finalize_investigation"


def step_limit_guard(state: IncidentState) -> dict:
    """Close unexecuted tool calls and safely continue to finalization."""

    latest = state["messages"][-1]
    if not isinstance(latest, AIMessage) or not latest.tool_calls:
        raise ValueError("Step-limit guard requires pending AIMessage tool calls")

    messages = []
    errors = []
    for call in latest.tool_calls:
        error = f"Agent step limit reached before executing {call['name']}"
        errors.append(error)
        messages.append(
            ToolMessage(
                content=json.dumps(
                    {"error": error, "error_type": "AgentStepLimitReached"}
                ),
                tool_call_id=call["id"],
                name=call["name"],
                status="error",
            )
        )
    return {
        "messages": messages,
        "tool_errors": errors,
        "step_limit_reached": True,
        "investigation_status": "step_limit_reached",
        "visited_nodes": ["step_limit_guard"],
    }


def _successful_evidence(state: IncidentState) -> list[EvidenceItem]:
    return [
        item for item in state.get("evidence", []) if "error" not in item.raw
    ]


def _deployment_records(state: IncidentState) -> list[dict[str, Any]]:
    for item in _successful_evidence(state):
        if item.source == "deployments":
            return item.raw.get("deployments", [])
    return []


def _safe_fallback_action(state: IncidentState, reason: str) -> ProposedAction:
    return ProposedAction(
        tool_name="escalate_incident",
        arguments={
            "service": state["classification"].service,
            "reason": reason,
        },
        risk_level="low",
        rationale=(
            "The workflow proposes escalation because the evidence does not safely "
            "support a mutating recovery action."
        ),
    )


def enforce_finalization_safety(
    state: IncidentState,
    candidate: FinalInvestigation,
) -> FinalInvestigation:
    """Apply deterministic safety invariants after structured model output."""

    status = candidate.investigation_status
    action = candidate.proposed_action
    successful_evidence = _successful_evidence(state)

    if state.get("step_limit_reached"):
        status = "step_limit_reached"
        action = _safe_fallback_action(state, "agent_step_limit_reached")
    elif not successful_evidence:
        status = "insufficient_evidence"
        action = _safe_fallback_action(state, "no_successful_evidence")

    if action.tool_name == "rollback_deployment":
        deployments = _deployment_records(state)
        service = state["classification"].service
        supported_targets = {
            record["previous_version"]
            for record in deployments
            if record.get("previous_version")
        }
        rollback_supported = (
            status == "complete"
            and action.arguments.get("service") == service
            and action.arguments.get("target_version") in supported_targets
        )
        if not rollback_supported:
            status = "insufficient_evidence"
            action = _safe_fallback_action(
                state, "rollback_not_supported_by_deployment_evidence"
            )

    return candidate.model_copy(
        update={
            "investigation_status": status,
            "proposed_action": action,
        }
    )


def build_finalize_node(finalizer_model: Any) -> Callable[[IncidentState], dict]:
    """Return a node that performs one structured-output finalizer model call."""

    structured_finalizer = finalizer_model.with_structured_output(
        FinalInvestigation,
        method="function_calling",
    )

    def finalize_investigation(state: IncidentState) -> dict:
        payload = {
            "incident_description": state["incident_description"],
            "classification": _jsonable(state["classification"]),
            "raw_evidence": _jsonable(state.get("evidence", [])),
            "tool_errors": state.get("tool_errors", []),
            "step_limit_reached": state.get("step_limit_reached", False),
            "last_investigator_message": _jsonable(
                state["messages"][-1].content if state.get("messages") else ""
            ),
        }
        candidate = structured_finalizer.invoke(
            [
                SystemMessage(content=FINALIZER_SYSTEM_PROMPT),
                HumanMessage(
                    content=json.dumps(payload, indent=2, ensure_ascii=False)
                ),
            ]
        )
        if not isinstance(candidate, FinalInvestigation):
            candidate = FinalInvestigation.model_validate(candidate)
        result = enforce_finalization_safety(state, candidate)
        logger.info(
            "NODE finalize_investigation status=%s action=%s",
            result.investigation_status,
            result.proposed_action.tool_name,
        )
        return {
            "diagnosis": result.diagnosis,
            "diagnosis_confidence": result.diagnosis_confidence,
            "investigation_status": result.investigation_status,
            "proposed_action": result.proposed_action,
            "visited_nodes": ["finalize_investigation"],
        }

    return finalize_investigation
