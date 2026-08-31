"""Typed state shared by the incident-response LangGraph workflow."""

import operator
from typing import Annotated, Literal

from langchain.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import NotRequired, Required, TypedDict

from app.schemas import EvidenceItem, IncidentClassification, ProposedAction


class IncidentState(TypedDict, total=False):
    """State carried through every stage of an incident-response run.

    ``incident_description`` is the graph input. Each node returns only the raw
    fields it updates. Reducers append evidence, executed-tool audit entries,
    expected tool errors, and visited node names across graph steps.
    """

    incident_description: Required[str]
    messages: NotRequired[Annotated[list[AnyMessage], add_messages]]
    classification: NotRequired[IncidentClassification]
    analysis: NotRequired[str]
    low_risk_summary: NotRequired[str]
    monitoring_recommendation: NotRequired[str]
    evidence: NotRequired[Annotated[list[EvidenceItem], operator.add]]
    diagnosis: NotRequired[str]
    diagnosis_confidence: NotRequired[float]
    investigation_status: NotRequired[
        Literal[
            "lightweight_complete",
            "complete",
            "insufficient_evidence",
            "step_limit_reached",
        ]
    ]
    proposed_action: NotRequired[ProposedAction]
    agent_steps: NotRequired[int]
    step_limit_reached: NotRequired[bool]
    executed_tools: NotRequired[Annotated[list[str], operator.add]]
    tool_errors: NotRequired[Annotated[list[str], operator.add]]
    visited_nodes: NotRequired[Annotated[list[str], operator.add]]
