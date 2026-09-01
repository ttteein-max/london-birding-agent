"""JSON-safe typed state and explicit reducers for the biodiversity graph."""

from __future__ import annotations

import json
import operator
from typing import Annotated, Any, Literal

from langchain.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import NotRequired, Required, TypedDict


def replace_list(_left: list[Any], right: list[Any]) -> list[Any]:
    """Make replacement semantics explicit for list-valued state fields."""

    return list(right)


def merge_unique_dicts(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Append dictionaries while suppressing exact retry/resume duplicates."""

    merged = list(left)
    seen = {json.dumps(item, sort_keys=True, default=str) for item in merged}
    for item in right:
        key = json.dumps(item, sort_keys=True, default=str)
        if key not in seen:
            merged.append(item)
            seen.add(key)
    return merged


def merge_by_call_id(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge tool records idempotently by LangChain tool-call identifier."""

    merged = list(left)
    indexes = {str(item.get("call_id")): index for index, item in enumerate(merged)}
    for item in right:
        call_id = str(item.get("call_id"))
        if call_id in indexes:
            merged[indexes[call_id]] = item
        else:
            indexes[call_id] = len(merged)
            merged.append(item)
    return merged


def merge_unique_strings(left: list[str], right: list[str]) -> list[str]:
    return list(dict.fromkeys([*left, *right]))


class BiodiversityAgentState(TypedDict, total=False):
    """Checkpointed representation; domain models are stored as JSON dictionaries."""

    original_request_text: Required[str]
    messages: NotRequired[Annotated[list[AnyMessage], add_messages]]
    parsed_request_draft: NotRequired[dict[str, Any] | None]
    expedition_request: NotRequired[dict[str, Any] | None]
    resolved_location: NotRequired[dict[str, Any] | None]
    resolved_taxon: NotRequired[dict[str, Any] | None]
    occurrence_evidence: NotRequired[dict[str, Any] | None]
    weather_evidence: NotRequired[dict[str, Any] | None]
    public_site_search: NotRequired[dict[str, Any] | None]
    structured_evidence_log: NotRequired[
        Annotated[list[dict[str, Any]], merge_unique_dicts]
    ]
    pending_tool_results: NotRequired[
        Annotated[list[dict[str, Any]], merge_by_call_id]
    ]
    recorded_tool_call_ids: NotRequired[Annotated[list[str], merge_unique_strings]]
    executed_tool_call_audit: NotRequired[
        Annotated[list[dict[str, Any]], merge_by_call_id]
    ]
    tool_errors: NotRequired[Annotated[list[dict[str, Any]], merge_unique_dicts]]
    deterministic_constraints: NotRequired[
        Annotated[list[dict[str, Any]], replace_list]
    ]
    evidence_bundle: NotRequired[dict[str, Any] | None]
    deterministic_plan_status: NotRequired[str | None]
    deterministic_phase1_plan: NotRequired[dict[str, Any] | None]
    draft_llm_plan: NotRequired[dict[str, Any] | None]
    final_validated_plan: NotRequired[dict[str, Any] | None]
    grounding_errors: NotRequired[Annotated[list[str], replace_list]]
    pending_hitl_kind: NotRequired[str | None]
    pending_hitl_payload: NotRequired[dict[str, Any] | None]
    pending_user_choice: NotRequired[dict[str, Any] | None]
    applied_user_decisions: NotRequired[
        Annotated[list[dict[str, Any]], merge_unique_dicts]
    ]
    evidence_loop_count: NotRequired[int]
    plan_revision_count: NotRequired[int]
    decision_route: NotRequired[
        Literal["evidence_agent", "deterministic_validation"] | None
    ]
    terminal_status: NotRequired[str | None]
    terminal_result: NotRequired[dict[str, Any] | None]
    visited_nodes: NotRequired[Annotated[list[str], operator.add]]
