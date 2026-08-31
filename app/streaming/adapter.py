"""Experimental LangGraph v3 to application-event adapter."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langgraph.prebuilt import ToolCallTransformer
from langgraph.stream import StreamTransformer
from langgraph.types import Command
from pydantic import BaseModel

from app.streaming.events import AgentRunEvent, AgentRunEventType


class _ApplicationEventModes(StreamTransformer):
    """Request node deltas needed by the application projection."""

    required_stream_modes = ("updates",)

    def init(self) -> dict:
        return {}

    def process(self, event: Any) -> bool:
        return True


def _jsonable(value: Any) -> Any:
    """Convert internal values without leaking LangGraph event envelopes."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, BaseMessage):
        result: dict[str, Any] = {
            "type": value.type,
            "content": _jsonable(value.content),
        }
        if isinstance(value, ToolMessage):
            result.update(
                {
                    "name": value.name,
                    "tool_call_id": value.tool_call_id,
                    "status": value.status,
                }
            )
        return result
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _event_timestamp(raw_event: Mapping[str, Any] | None = None) -> datetime:
    if raw_event is not None:
        params = raw_event.get("params")
        if isinstance(params, Mapping):
            milliseconds = params.get("timestamp")
            if isinstance(milliseconds, (int, float)):
                return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
    return datetime.now(timezone.utc)


def _event_data(raw_event: Mapping[str, Any]) -> Any:
    params = raw_event.get("params")
    return params.get("data") if isinstance(params, Mapping) else None


def _message_and_metadata(data: Any) -> tuple[BaseMessage | None, dict[str, Any]]:
    if not isinstance(data, (tuple, list)) or len(data) < 2:
        return None, {}
    message, metadata = data[0], data[1]
    if not isinstance(message, BaseMessage) or not isinstance(metadata, Mapping):
        return None, {}
    return message, dict(metadata)


def _node_payload(delta: Mapping[str, Any]) -> dict[str, Any]:
    """Publish selected domain fields instead of a raw LangGraph state delta."""

    allowed_fields = (
        "classification",
        "analysis",
        "low_risk_summary",
        "monitoring_recommendation",
        "agent_steps",
        "step_limit_reached",
        "evidence",
        "executed_tools",
        "tool_errors",
        "diagnosis",
        "diagnosis_confidence",
        "investigation_status",
        "proposed_action",
    )
    payload = {
        field: _jsonable(delta[field]) for field in allowed_fields if field in delta
    }
    payload["changed_fields"] = sorted(
        field for field in delta if field not in {"messages", "visited_nodes"}
    )
    return payload


def _tool_result_payload(output: Any) -> tuple[bool, dict[str, Any]]:
    """Return application failure semantics plus selected structured output."""

    update = output.update if isinstance(output, Command) else None
    if not isinstance(update, Mapping):
        return False, {"result": _jsonable(output)}

    payload: dict[str, Any] = {}
    for field in ("evidence", "executed_tools", "tool_errors"):
        if field in update:
            payload[field] = _jsonable(update[field])

    messages = update.get("messages", [])
    failed_message = next(
        (
            message
            for message in messages
            if isinstance(message, ToolMessage) and message.status == "error"
        ),
        None,
    )
    failed = bool(update.get("tool_errors")) or failed_message is not None
    if failed_message is not None:
        payload["error"] = _jsonable(failed_message.content)
    return failed, payload


def _run_payload(state_projection: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "classification",
        "diagnosis",
        "diagnosis_confidence",
        "investigation_status",
        "proposed_action",
        "low_risk_summary",
        "monitoring_recommendation",
        "agent_steps",
        "step_limit_reached",
    )
    return {
        field: _jsonable(state_projection[field])
        for field in fields
        if field in state_projection
    }


async def astream_agent_run_events(
    graph: Any,
    graph_input: dict[str, Any],
    config: dict[str, Any] | None = None,
    *,
    run_id: str | None = None,
) -> AsyncIterator[AgentRunEvent]:
    """Project experimental LangGraph v3 events into the stable app schema.

    LangGraph protocol events remain an implementation detail. Phase 2 should
    serialize only the yielded ``AgentRunEvent`` objects.
    """

    application_run_id = run_id or uuid4().hex
    sequence = 0
    seen_model_runs: set[str] = set()
    seen_tool_requests: set[str] = set()
    tool_names: dict[str, str] = {}
    state_projection: dict[str, Any] = {}

    def make_event(
        event_type: AgentRunEventType,
        *,
        raw_event: Mapping[str, Any] | None = None,
        node_id: str | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AgentRunEvent:
        nonlocal sequence
        sequence += 1
        return AgentRunEvent(
            run_id=application_run_id,
            sequence=sequence,
            event_type=event_type,
            node_id=node_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            timestamp=_event_timestamp(raw_event),
            payload=payload or {},
        )

    thread_id = (config or {}).get("configurable", {}).get("thread_id")
    start_payload = {"incident_description": graph_input.get("incident_description")}
    if thread_id is not None:
        start_payload["thread_id"] = str(thread_id)
    yield make_event("run_started", payload=start_payload)

    try:
        stream = await graph.astream_events(
            graph_input,
            config,
            version="v3",
            transformers=[ToolCallTransformer, _ApplicationEventModes],
        )

        async for raw_event in stream:
            method = raw_event.get("method")
            data = _event_data(raw_event)

            if method == "messages":
                message, metadata = _message_and_metadata(data)
                if not isinstance(message, (AIMessage, AIMessageChunk)):
                    continue
                node_id = metadata.get("langgraph_node")
                model_run_key = str(
                    metadata.get("langgraph_checkpoint_ns")
                    or message.id
                    or f"{node_id}:{metadata.get('langgraph_step')}"
                )
                if model_run_key in seen_model_runs:
                    continue
                seen_model_runs.add(model_run_key)
                yield make_event(
                    "model_started",
                    raw_event=raw_event,
                    node_id=str(node_id) if node_id else None,
                    payload={
                        "provider": metadata.get("ls_provider"),
                        "model_type": metadata.get("ls_model_type"),
                    },
                )
                continue

            if method == "updates" and isinstance(data, Mapping):
                for raw_node_id, raw_delta in data.items():
                    if not isinstance(raw_delta, Mapping):
                        continue
                    node_id = str(raw_node_id)
                    delta = dict(raw_delta)
                    state_projection.update(delta)

                    if node_id == "investigator_agent":
                        messages = delta.get("messages", [])
                        latest = messages[-1] if messages else None
                        if isinstance(latest, AIMessage):
                            for tool_call in latest.tool_calls:
                                tool_call_id = str(tool_call.get("id") or "")
                                if not tool_call_id or tool_call_id in seen_tool_requests:
                                    continue
                                seen_tool_requests.add(tool_call_id)
                                tool_name = str(tool_call.get("name") or "")
                                tool_names[tool_call_id] = tool_name
                                yield make_event(
                                    "tool_requested",
                                    raw_event=raw_event,
                                    node_id=node_id,
                                    tool_name=tool_name or None,
                                    tool_call_id=tool_call_id,
                                    payload={
                                        "arguments": _jsonable(tool_call.get("args", {}))
                                    },
                                )

                    yield make_event(
                        "node_completed",
                        raw_event=raw_event,
                        node_id=node_id,
                        payload=_node_payload(delta),
                    )
                continue

            if method == "tools" and isinstance(data, Mapping):
                source_event = data.get("event")
                tool_call_id = str(data.get("tool_call_id") or "")
                tool_name = str(
                    data.get("tool_name") or tool_names.get(tool_call_id) or ""
                )
                if tool_call_id and tool_name:
                    tool_names[tool_call_id] = tool_name

                if source_event == "tool-started":
                    yield make_event(
                        "tool_started",
                        raw_event=raw_event,
                        node_id="tools",
                        tool_name=tool_name or None,
                        tool_call_id=tool_call_id or None,
                        payload={"arguments": _jsonable(data.get("input", {}))},
                    )
                elif source_event == "tool-finished":
                    failed, payload = _tool_result_payload(data.get("output"))
                    yield make_event(
                        "tool_failed" if failed else "tool_completed",
                        raw_event=raw_event,
                        node_id="tools",
                        tool_name=tool_name or None,
                        tool_call_id=tool_call_id or None,
                        payload=payload,
                    )
                elif source_event == "tool-error":
                    yield make_event(
                        "tool_failed",
                        raw_event=raw_event,
                        node_id="tools",
                        tool_name=tool_name or None,
                        tool_call_id=tool_call_id or None,
                        payload={"error": _jsonable(data.get("error"))},
                    )

        yield make_event(
            "run_completed",
            payload=_run_payload(state_projection),
        )
    except Exception as error:
        yield make_event(
            "run_failed",
            payload={
                "error_type": type(error).__name__,
                "message": str(error),
            },
        )
        raise
