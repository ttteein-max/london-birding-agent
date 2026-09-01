"""Offline timing and report-persistence tests for Phase 4 observability."""

import json
from collections import Counter

from app.biodiversity.graph import build_biodiversity_graph
from app.biodiversity.observability import AgentRunRecorder
from app.biodiversity.orchestration import BackendDependencies
from app.biodiversity.reporting import save_biodiversity_run_report
from app.biodiversity.testing import make_scripted_biodiversity_models


REQUEST = (
    "Plan a two-hour expedition from SW11 4NJ on 15 June 2026 "
    "to look for Common woodpigeon."
)


def _timed_fixture_run():
    parser, evidence, composer = make_scripted_biodiversity_models()
    graph = build_biodiversity_graph(
        parser_model=parser,
        evidence_model=evidence,
        composer_model=composer,
        dependencies=BackendDependencies.fixture(),
    )
    recorder = AgentRunRecorder(run_id="timed-fixture-run", thread_id="timed-thread")
    result = graph.invoke(
        {"original_request_text": REQUEST},
        config={"callbacks": [recorder]},
    )
    recorder.finish(
        "completed",
        payload={"terminal_status": result.get("terminal_status")},
    )
    return result, recorder


def test_recorder_times_every_completed_biodiversity_node_and_tool() -> None:
    result, recorder = _timed_fixture_run()
    report = recorder.report()

    node_spans = [span for span in report.spans if span.kind == "node"]
    recorded_nodes = Counter(span.node_id for span in node_spans)
    recorded_nodes.pop("evidence_tools", None)
    assert recorded_nodes == Counter(result["visited_nodes"])
    assert any(span.node_id == "evidence_tools" for span in node_spans)
    assert all(span.status == "completed" for span in node_spans)
    assert all(span.duration_ms >= 0 for span in node_spans)
    assert all(span.finished_at >= span.started_at for span in node_spans)

    tool_spans = [span for span in report.spans if span.kind == "tool"]
    assert {span.tool_name for span in tool_spans} == {
        "search_occurrences",
        "get_weather_context",
        "find_public_green_spaces",
    }
    assert {span.tool_call_id for span in tool_spans} == {
        item["call_id"] for item in result["executed_tool_call_audit"]
    }
    assert all(span.node_id == "evidence_tools" for span in tool_spans)

    event_types = [event.event_type for event in recorder.events]
    assert event_types[0] == "run_started"
    assert event_types[-1] == "run_completed"
    assert event_types.count("node_started") == event_types.count("node_completed")
    assert "model_started" in event_types
    assert "model_completed" in event_types


def test_saved_report_contains_phase4_events_and_timing_spans(tmp_path) -> None:
    result, recorder = _timed_fixture_run()
    paths = save_biodiversity_run_report(
        tmp_path,
        recorder=recorder,
        result=result,
        request=REQUEST,
        data_mode="fixture",
        model_mode="scripted",
    )

    events = json.loads(paths["events"].read_text(encoding="utf-8"))
    timings = json.loads(paths["timings"].read_text(encoding="utf-8"))
    metadata = json.loads(paths["metadata"].read_text(encoding="utf-8"))

    assert events["schema_version"] == 1
    assert all("timestamp" in event for event in events["events"])
    completed = [
        event
        for event in events["events"]
        if event["event_type"] in {"node_completed", "model_completed", "tool_completed"}
    ]
    assert completed
    assert all(event["started_at"] for event in completed)
    assert all(event["finished_at"] for event in completed)
    assert all(event["duration_ms"] >= 0 for event in completed)

    assert timings["status"] == "completed"
    assert timings["span_count"] == len(timings["spans"])
    assert {span["kind"] for span in timings["spans"]} == {
        "node",
        "model",
        "tool",
    }
    assert metadata["duration_ms"] == timings["duration_ms"]
    assert paths["final_plan"].exists()


def test_recorder_captures_failed_model_without_saving_error_text() -> None:
    recorder = AgentRunRecorder(run_id="failed-run")
    recorder.on_chat_model_start(
        {"name": "ExampleModel"},
        [],
        run_id="model-span",
        parent_run_id="node-span",
        metadata={"langgraph_node": "example_node", "ls_provider": "example"},
    )
    recorder.on_llm_error(RuntimeError("secret upstream message"), run_id="model-span")
    recorder.finish("failed", payload={"error_type": "RuntimeError"})

    model_span = next(span for span in recorder.spans if span.kind == "model")
    assert model_span.status == "failed"
    assert model_span.error_type == "RuntimeError"
    assert "secret upstream message" not in recorder.report().model_dump_json()
