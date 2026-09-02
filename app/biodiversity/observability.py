"""Biodiversity-owned timing recorder for LangGraph nodes, models, and tools."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler
from pydantic import BaseModel, ConfigDict, Field, field_validator

SpanKind = Literal["node", "model", "tool"]
SpanStatus = Literal["completed", "failed"]
AgentRunEventType = Literal[
    "run_started",
    "node_started",
    "node_completed",
    "node_failed",
    "model_started",
    "model_completed",
    "model_failed",
    "tool_started",
    "tool_completed",
    "tool_failed",
    "run_completed",
    "run_failed",
]


class AgentRunEvent(BaseModel):
    """Stable Phase 4 event contract independent of LangGraph internals."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    event_type: AgentRunEventType
    span_id: str | None = None
    parent_span_id: str | None = None
    node_id: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    timestamp: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp", "started_at", "finished_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("event timestamps must include a timezone")
        return value


class AgentRunSpan(BaseModel):
    """One completed timing span ready for Phase 4 timeline rendering."""

    model_config = ConfigDict(extra="forbid")

    start_sequence: int = Field(ge=1)
    completion_sequence: int = Field(ge=1)
    span_id: str = Field(min_length=1)
    parent_span_id: str | None = None
    kind: SpanKind
    name: str = Field(min_length=1)
    node_id: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    started_at: datetime
    finished_at: datetime
    duration_ms: float = Field(ge=0)
    status: SpanStatus
    error_type: str | None = None


class AgentRunTimingReport(BaseModel):
    """Persisted timing data independent of LangGraph's callback wire format."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    thread_id: str | None = None
    status: Literal["running", "completed", "failed"]
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    event_count: int = Field(ge=1)
    span_count: int = Field(ge=0)
    spans: list[AgentRunSpan]


@dataclass(frozen=True)
class _ActiveSpan:
    start_sequence: int
    span_id: str
    parent_span_id: str | None
    kind: SpanKind
    name: str
    node_id: str | None
    tool_name: str | None
    tool_call_id: str | None
    started_at: datetime
    started_ns: int
    payload: dict[str, Any]


def _serialised_name(serialized: dict[str, Any] | None) -> str | None:
    if not isinstance(serialized, dict):
        return None
    name = serialized.get("name")
    return str(name) if name else None


def _metadata(kwargs: dict[str, Any]) -> dict[str, Any]:
    value = kwargs.get("metadata")
    return dict(value) if isinstance(value, dict) else {}


def _node_id(kwargs: dict[str, Any]) -> str | None:
    value = _metadata(kwargs).get("langgraph_node")
    return str(value) if value else None


class AgentRunRecorder(BaseCallbackHandler):
    """Record safe lifecycle events and monotonic durations through callbacks.

    Inputs, prompts, model outputs, and tool results are deliberately omitted.
    The resulting files can therefore drive a Phase 4 execution timeline without
    becoming a second copy of sensitive biodiversity state.
    """

    run_inline = True

    def __init__(self, *, run_id: str | None = None, thread_id: str | None = None) -> None:
        self.run_id = run_id or uuid4().hex
        self.thread_id = thread_id
        self._lock = threading.RLock()
        self._started_at = datetime.now(UTC)
        self._started_ns = time.perf_counter_ns()
        self._finished_at: datetime | None = None
        self._duration_ms: float | None = None
        self._status: Literal["running", "completed", "failed"] = "running"
        self._sequence = 0
        self._active: dict[str, _ActiveSpan] = {}
        self._events: list[AgentRunEvent] = []
        self._spans: list[AgentRunSpan] = []
        self._append_event(
            "run_started",
            span_id=self.run_id,
            timestamp=self._started_at,
            started_at=self._started_at,
            payload={"thread_id": thread_id} if thread_id else {},
        )

    @property
    def events(self) -> list[AgentRunEvent]:
        with self._lock:
            return list(self._events)

    @property
    def spans(self) -> list[AgentRunSpan]:
        with self._lock:
            return list(self._spans)

    def _append_event(
        self,
        event_type: AgentRunEventType,
        *,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        node_id: str | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        timestamp: datetime | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        duration_ms: float | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AgentRunEvent:
        self._sequence += 1
        event = AgentRunEvent(
            run_id=self.run_id,
            sequence=self._sequence,
            event_type=event_type,
            span_id=span_id,
            parent_span_id=parent_span_id,
            node_id=node_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            timestamp=timestamp or datetime.now(UTC),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            payload=payload or {},
        )
        self._events.append(event)
        return event

    def _begin(
        self,
        *,
        callback_run_id: Any,
        parent_run_id: Any,
        kind: SpanKind,
        name: str,
        node_id: str | None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        span_id = str(callback_run_id)
        parent_span_id = str(parent_run_id) if parent_run_id else None
        started_at = datetime.now(UTC)
        event_type: AgentRunEventType = {
            "node": "node_started",
            "model": "model_started",
            "tool": "tool_started",
        }[kind]
        with self._lock:
            if span_id in self._active:
                return
            event = self._append_event(
                event_type,
                span_id=span_id,
                parent_span_id=parent_span_id,
                node_id=node_id,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                timestamp=started_at,
                started_at=started_at,
                payload=payload,
            )
            self._active[span_id] = _ActiveSpan(
                start_sequence=event.sequence,
                span_id=span_id,
                parent_span_id=parent_span_id,
                kind=kind,
                name=name,
                node_id=node_id,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                started_at=started_at,
                started_ns=time.perf_counter_ns(),
                payload=payload or {},
            )

    def _end(self, callback_run_id: Any, *, failed: bool, error: BaseException | None = None) -> None:
        span_id = str(callback_run_id)
        with self._lock:
            active = self._active.pop(span_id, None)
            if active is None:
                return
            finished_at = datetime.now(UTC)
            duration_ms = max(0.0, (time.perf_counter_ns() - active.started_ns) / 1_000_000)
            event_type: AgentRunEventType
            if active.kind == "node":
                event_type = "node_failed" if failed else "node_completed"
            elif active.kind == "model":
                event_type = "model_failed" if failed else "model_completed"
            else:
                event_type = "tool_failed" if failed else "tool_completed"
            payload = dict(active.payload)
            error_type = type(error).__name__ if error else None
            if error_type:
                payload["error_type"] = error_type
            completion = self._append_event(
                event_type,
                span_id=active.span_id,
                parent_span_id=active.parent_span_id,
                node_id=active.node_id,
                tool_name=active.tool_name,
                tool_call_id=active.tool_call_id,
                timestamp=finished_at,
                started_at=active.started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                payload=payload,
            )
            self._spans.append(
                AgentRunSpan(
                    start_sequence=active.start_sequence,
                    completion_sequence=completion.sequence,
                    span_id=active.span_id,
                    parent_span_id=active.parent_span_id,
                    kind=active.kind,
                    name=active.name,
                    node_id=active.node_id,
                    tool_name=active.tool_name,
                    tool_call_id=active.tool_call_id,
                    started_at=active.started_at,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                    status="failed" if failed else "completed",
                    error_type=error_type,
                )
            )

    def on_chain_start(self, serialized: dict[str, Any], inputs: Any, **kwargs: Any) -> None:
        del inputs
        tags = kwargs.get("tags") or []
        node_id = _node_id(kwargs)
        if not node_id or not any(str(tag).startswith("graph:step:") for tag in tags):
            return
        step = _metadata(kwargs).get("langgraph_step")
        self._begin(
            callback_run_id=kwargs["run_id"],
            parent_run_id=kwargs.get("parent_run_id"),
            kind="node",
            name=node_id,
            node_id=node_id,
            payload={"langgraph_step": step} if step is not None else {},
        )

    def on_chain_end(self, outputs: Any, **kwargs: Any) -> None:
        del outputs
        self._end(kwargs["run_id"], failed=False)

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        self._end(kwargs["run_id"], failed=True, error=error)

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: Any,
        **kwargs: Any,
    ) -> None:
        del messages
        metadata = _metadata(kwargs)
        provider = metadata.get("ls_provider")
        invocation = kwargs.get("invocation_params") or {}
        model_name = str(
            invocation.get("model_name")
            or invocation.get("model")
            or _serialised_name(serialized)
            or "chat_model"
        )
        payload = {"model": model_name}
        if provider:
            payload["provider"] = str(provider)
        self._begin(
            callback_run_id=kwargs["run_id"],
            parent_run_id=kwargs.get("parent_run_id"),
            kind="model",
            name=model_name,
            node_id=_node_id(kwargs),
            payload=payload,
        )

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        del response
        self._end(kwargs["run_id"], failed=False)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        self._end(kwargs["run_id"], failed=True, error=error)

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        del input_str
        tool_name = _serialised_name(serialized) or str(kwargs.get("name") or "tool")
        tool_call_id = kwargs.get("tool_call_id")
        self._begin(
            callback_run_id=kwargs["run_id"],
            parent_run_id=kwargs.get("parent_run_id"),
            kind="tool",
            name=tool_name,
            node_id=_node_id(kwargs),
            tool_name=tool_name,
            tool_call_id=str(tool_call_id) if tool_call_id else None,
        )

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        del output
        self._end(kwargs["run_id"], failed=False)

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        self._end(kwargs["run_id"], failed=True, error=error)

    def finish(
        self,
        status: Literal["completed", "failed"],
        *,
        payload: dict[str, Any] | None = None,
    ) -> AgentRunTimingReport:
        with self._lock:
            if self._finished_at is None:
                self._finished_at = datetime.now(UTC)
                self._duration_ms = max(
                    0.0, (time.perf_counter_ns() - self._started_ns) / 1_000_000
                )
                self._status = status
                self._append_event(
                    "run_completed" if status == "completed" else "run_failed",
                    span_id=self.run_id,
                    timestamp=self._finished_at,
                    started_at=self._started_at,
                    finished_at=self._finished_at,
                    duration_ms=self._duration_ms,
                    payload=payload,
                )
            return self.report()

    def report(self) -> AgentRunTimingReport:
        with self._lock:
            return AgentRunTimingReport(
                run_id=self.run_id,
                thread_id=self.thread_id,
                status=self._status,
                started_at=self._started_at,
                finished_at=self._finished_at,
                duration_ms=self._duration_ms,
                event_count=len(self._events),
                span_count=len(self._spans),
                spans=sorted(self._spans, key=lambda item: item.start_sequence),
            )

    def save(self, directory: Path) -> tuple[Path, Path]:
        """Persist ordered events plus a compact span report."""

        directory.mkdir(parents=True, exist_ok=True)
        events_path = directory / "events.json"
        timings_path = directory / "timings.json"
        events_payload = {
            "schema_version": 1,
            "run_id": self.run_id,
            "events": [event.model_dump(mode="json") for event in self.events],
        }
        events_path.write_text(
            json.dumps(events_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        timings_path.write_text(
            self.report().model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        return events_path, timings_path
