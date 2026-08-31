"""Offline acceptance tests for the Phase 1.2 agent/workflow hybrid."""

import ast
from pathlib import Path

import pytest
from langchain.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from app.graph.routing import route_incident
from app.graph.workflow import build_agent_graph
from app.graph import create_in_memory_checkpointer
from app.schemas import (
    FinalInvestigation,
    IncidentClassification,
    ProposedAction,
)
from app.testing import (
    ScriptedFinalizerModel,
    ScriptedToolCallingModel,
    make_scripted_demo_models,
    scripted_stop,
    scripted_tool_call,
)


def finalizer(
    *,
    status: str = "insufficient_evidence",
    action_name: str = "monitor_service",
    arguments: dict[str, str] | None = None,
) -> ScriptedFinalizerModel:
    action_arguments = arguments or {"service": "checkout", "window": "30m"}
    return ScriptedFinalizerModel(
        FinalInvestigation(
            diagnosis="Scripted final diagnosis from the available raw evidence.",
            diagnosis_confidence=0.55,
            investigation_status=status,
            proposed_action=ProposedAction(
                tool_name=action_name,
                arguments=action_arguments,
                risk_level="high" if action_name == "rollback_deployment" else "low",
                rationale="Scripted structured finalizer output for an offline test.",
            ),
        )
    )


def test_high_risk_model_selects_three_tools_then_stops() -> None:
    investigator, scripted_finalizer = make_scripted_demo_models()
    graph = build_agent_graph(investigator, scripted_finalizer)

    result = graph.invoke(
        {"incident_description": "checkout failure after deployment"}
    )

    assert result["classification"].investigation_route == "full"
    assert result["executed_tools"] == [
        "query_service_metrics",
        "list_recent_deployments",
        "search_service_logs",
    ]
    assert [item.source for item in result["evidence"]] == [
        "metrics",
        "deployments",
        "logs",
    ]
    assert result["agent_steps"] == 4
    assert result["proposed_action"].tool_name == "rollback_deployment"
    assert result["proposed_action"].arguments["target_version"] == "checkout-v41"


def test_high_risk_model_can_stop_after_subset_of_tools() -> None:
    investigator = ScriptedToolCallingModel(
        responses=[
            scripted_tool_call(
                "query_service_metrics",
                {"service": "checkout", "time_window": "5m"},
                "subset-metrics",
            ),
            scripted_stop("Metrics are sufficient for a monitoring recommendation."),
        ]
    )
    graph = build_agent_graph(investigator, finalizer())

    result = graph.invoke({"incident_description": "checkout failure"})

    assert result["executed_tools"] == ["query_service_metrics"]
    assert [item.source for item in result["evidence"]] == ["metrics"]
    assert result["agent_steps"] == 2
    assert result["proposed_action"].tool_name == "monitor_service"


def test_tool_order_is_controlled_by_scripted_model_output() -> None:
    investigator = ScriptedToolCallingModel(
        responses=[
            scripted_tool_call(
                "list_recent_deployments",
                {"service": "checkout", "since": "2026-08-23T00:00:00Z"},
                "order-deployments",
            ),
            scripted_tool_call(
                "search_service_logs",
                {
                    "service": "checkout",
                    "query": "errors",
                    "since": "2026-08-24T06:45:00Z",
                },
                "order-logs",
            ),
            scripted_stop(),
        ]
    )
    graph = build_agent_graph(investigator, finalizer())

    result = graph.invoke({"incident_description": "checkout outage"})

    assert result["executed_tools"] == [
        "list_recent_deployments",
        "search_service_logs",
    ]


def test_low_risk_path_uses_no_model_or_tools() -> None:
    investigator = ScriptedToolCallingModel(responses=[scripted_stop()])
    scripted_finalizer = finalizer()
    graph = build_agent_graph(investigator, scripted_finalizer)

    result = graph.invoke({"incident_description": "minor latency increase"})

    assert result["classification"].investigation_route == "lightweight"
    assert result["low_risk_summary"]
    assert result["monitoring_recommendation"]
    assert result["agent_steps"] == 0
    assert result["executed_tools"] == []
    assert result["evidence"] == []
    assert scripted_finalizer.invocations == 0


def test_tool_failure_is_returned_to_model_before_another_decision() -> None:
    investigator = ScriptedToolCallingModel(
        responses=[
            scripted_tool_call(
                "query_service_metrics",
                {"service": "payment", "time_window": "15m"},
                "failure-metrics",
            ),
            scripted_tool_call(
                "search_service_logs",
                {
                    "service": "payment",
                    "query": "payment failure",
                    "since": "2026-08-24T06:45:00Z",
                },
                "failure-logs",
            ),
            scripted_stop("Seeded sources are unavailable; escalate safely."),
        ]
    )
    graph = build_agent_graph(
        investigator,
        finalizer(
            action_name="escalate_incident",
            arguments={"service": "payment", "reason": "insufficient_evidence"},
        ),
    )

    result = graph.invoke(
        {"incident_description": "payment failure after deployment"}
    )

    assert len(result["tool_errors"]) == 2
    assert result["agent_steps"] == 3
    assert result["executed_tools"] == [
        "query_service_metrics",
        "search_service_logs",
    ]
    message_types = [type(message) for message in result["messages"]]
    assert message_types == [
        type(result["messages"][0]),
        AIMessage,
        ToolMessage,
        AIMessage,
        ToolMessage,
        AIMessage,
    ]
    assert result["messages"][2].status == "error"
    assert result["messages"][4].status == "error"


def test_maximum_step_guard_stops_infinite_tool_loop() -> None:
    investigator = ScriptedToolCallingModel(
        responses=[
            scripted_tool_call(
                "query_service_metrics",
                {"service": "checkout", "time_window": "15m"},
                "limit-metrics-1",
            ),
            scripted_tool_call(
                "search_service_logs",
                {
                    "service": "checkout",
                    "query": "keep looping",
                    "since": "2026-08-24T06:45:00Z",
                },
                "limit-logs-2",
            ),
            scripted_tool_call(
                "list_recent_deployments",
                {"service": "checkout", "since": "2026-08-23T00:00:00Z"},
                "limit-deployments-3",
            ),
        ]
    )
    malicious_finalizer = finalizer(
        status="complete",
        action_name="rollback_deployment",
        arguments={"service": "checkout", "target_version": "checkout-v41"},
    )
    graph = build_agent_graph(
        investigator,
        malicious_finalizer,
        max_agent_steps=2,
    )

    result = graph.invoke({"incident_description": "checkout failure"})

    assert result["agent_steps"] == 2
    assert result["executed_tools"] == ["query_service_metrics"]
    assert result["step_limit_reached"] is True
    assert result["investigation_status"] == "step_limit_reached"
    assert result["proposed_action"].tool_name == "escalate_incident"
    assert "step_limit_guard" in result["visited_nodes"]


def test_proposed_action_schema_is_valid() -> None:
    action = ProposedAction(
        tool_name="rollback_deployment",
        arguments={"service": "checkout", "target_version": "checkout-v41"},
        risk_level="high",
        rationale="Deployment evidence identifies the previous version.",
    )
    assert ProposedAction.model_validate(action.model_dump()) == action

    with pytest.raises(ValidationError):
        ProposedAction(
            tool_name="rollback_deployment",
            arguments={"service": "checkout"},
            risk_level="high",
            rationale="Missing target version.",
        )


def test_rollback_is_prohibited_without_deployment_evidence() -> None:
    investigator = ScriptedToolCallingModel(
        responses=[
            scripted_tool_call(
                "query_service_metrics",
                {"service": "checkout", "time_window": "15m"},
                "unsafe-metrics",
            ),
            scripted_stop(),
        ]
    )
    unsafe_finalizer = finalizer(
        status="complete",
        action_name="rollback_deployment",
        arguments={"service": "checkout", "target_version": "checkout-v41"},
    )
    graph = build_agent_graph(investigator, unsafe_finalizer)

    result = graph.invoke({"incident_description": "checkout failure"})

    assert result["investigation_status"] == "insufficient_evidence"
    assert result["proposed_action"].tool_name == "escalate_incident"


def test_streaming_alternates_investigator_and_tool_events() -> None:
    investigator, scripted_finalizer = make_scripted_demo_models()
    graph = build_agent_graph(investigator, scripted_finalizer)

    events = list(
        graph.stream(
            {"incident_description": "checkout failure after deployment"},
            stream_mode="updates",
            version="v2",
        )
    )

    assert [next(iter(event["data"])) for event in events] == [
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


def test_checkpointer_restoration_still_works() -> None:
    investigator, scripted_finalizer = make_scripted_demo_models()
    memory = create_in_memory_checkpointer()
    graph = build_agent_graph(investigator, scripted_finalizer, memory)
    config = {"configurable": {"thread_id": "phase12-recovery"}}
    result = graph.invoke(
        {"incident_description": "checkout failure after deployment"},
        config,
    )

    restored_investigator, restored_finalizer = make_scripted_demo_models()
    restored_graph = build_agent_graph(
        restored_investigator,
        restored_finalizer,
        memory,
    )
    snapshot = restored_graph.get_state(config)
    history = list(restored_graph.get_state_history(config))

    assert snapshot.values["proposed_action"] == result["proposed_action"]
    assert snapshot.values["agent_steps"] == 4
    assert snapshot.next == ()
    assert len(history) >= 9


def test_production_source_does_not_construct_ai_tool_calls() -> None:
    app_root = Path(__file__).parents[1] / "app"
    violations = []
    for path in app_root.rglob("*.py"):
        if "testing" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            if function_name == "AIMessage" and any(
                keyword.arg == "tool_calls" for keyword in node.keywords
            ):
                violations.append(f"{path}:{node.lineno}")

    assert violations == []


def test_outer_routing_remains_deterministic() -> None:
    state = {
        "classification": IncidentClassification(
            service="checkout",
            severity="high",
            confidence=0.6,
            investigation_route="lightweight",
        )
    }
    assert route_incident(state) == "summarize_low_risk"
