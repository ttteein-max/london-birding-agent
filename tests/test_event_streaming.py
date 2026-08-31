"""Offline tests for the experimental v3 application event adapter."""

import asyncio
from fnmatch import fnmatchcase
from pathlib import Path

import pytest
from langchain_core._api import LangChainBetaWarning

from app.graph.workflow import build_agent_graph
from app.streaming import AgentRunEvent, astream_agent_run_events
from app.testing import make_scripted_demo_models


def test_scripted_v3_adapter_emits_ordered_application_events() -> None:
    async def collect() -> list[AgentRunEvent]:
        investigator, finalizer = make_scripted_demo_models()
        graph = build_agent_graph(investigator, finalizer)
        return [
            event
            async for event in astream_agent_run_events(
                graph,
                {"incident_description": "checkout failure after deployment"},
                run_id="offline-v3-run",
            )
        ]

    with pytest.warns(LangChainBetaWarning):
        events = asyncio.run(collect())

    assert all(isinstance(event, AgentRunEvent) for event in events)
    assert {event.run_id for event in events} == {"offline-v3-run"}
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert events[0].event_type == "run_started"
    assert events[-1].event_type == "run_completed"

    tool_events = [
        event
        for event in events
        if event.event_type
        in {"tool_requested", "tool_started", "tool_completed", "tool_failed"}
    ]
    assert [event.event_type for event in tool_events] == [
        "tool_requested",
        "tool_started",
        "tool_completed",
        "tool_requested",
        "tool_started",
        "tool_completed",
        "tool_requested",
        "tool_started",
        "tool_completed",
    ]
    assert [
        event.tool_name
        for event in tool_events
        if event.event_type == "tool_requested"
    ] == [
        "query_service_metrics",
        "list_recent_deployments",
        "search_service_logs",
    ]

    by_tool_call: dict[str, list[str]] = {}
    for event in tool_events:
        assert event.tool_call_id is not None
        by_tool_call.setdefault(event.tool_call_id, []).append(event.event_type)
    assert all(
        lifecycle == ["tool_requested", "tool_started", "tool_completed"]
        for lifecycle in by_tool_call.values()
    )

    model_started = [
        event for event in events if event.event_type == "model_started"
    ]
    assert len(model_started) == 4
    assert all(event.node_id == "investigator_agent" for event in model_started)

    completed_nodes = [
        event.node_id for event in events if event.event_type == "node_completed"
    ]
    assert completed_nodes == [
        "analyze_incident",
        "investigator_agent",
        "tools",
        "investigator_agent",
        "tools",
        "investigator_agent",
        "tools",
        "investigator_agent",
        "finalize_investigation",
    ]
    assert events[-1].payload["investigation_status"] == "complete"
    assert events[-1].payload["proposed_action"]["tool_name"] == (
        "rollback_deployment"
    )


def test_environment_files_ignore_policy() -> None:
    gitignore = Path(__file__).parents[1] / ".gitignore"
    patterns = {
        line.strip()
        for line in gitignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert {".env", ".env.local", ".env.*.local"}.issubset(patterns)
    assert not any(fnmatchcase(".env.example", pattern) for pattern in patterns)
