"""Opt-in live Phase 2 end-to-end test; exact counts and prose remain unpinned."""

import json
import os
import re
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agents.model_factory import create_live_chat_model
from app.biodiversity.graph import build_biodiversity_graph
from app.biodiversity.observability import AgentRunRecorder
from app.biodiversity.orchestration import BackendDependencies


pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_BIODIVERSITY_AGENT") != "1"
    or not os.getenv("OPENAI_API_KEY")
    or not os.getenv("OPENAI_MODEL"),
    reason="Set RUN_LIVE_BIODIVERSITY_AGENT=1 and OpenAI-compatible credentials.",
)
def test_live_langgraph_completes_with_model_validated_grounded_plan() -> None:
    target_date = datetime.now(ZoneInfo("Europe/London")).date()
    recorder = AgentRunRecorder(thread_id="phase2-live-pytest")
    graph = build_biodiversity_graph(
        create_live_chat_model(),
        dependencies=BackendDependencies.live(),
    )
    result = graph.invoke(
        {
            "original_request_text": (
                f"Plan a two-hour expedition from SW11 4NJ on {target_date.isoformat()} "
                "to look for Common woodpigeon."
            )
        },
        config={"callbacks": [recorder]},
    )
    recorder.finish(
        "completed",
        payload={"terminal_status": result.get("terminal_status")},
    )
    diagnostic = {
        "terminal_status": result.get("terminal_status"),
        "visited_nodes": result.get("visited_nodes"),
        "grounding_errors": result.get("grounding_errors"),
        "location_status": (result.get("resolved_location") or {}).get("status"),
        "taxon_status": (result.get("resolved_taxon") or {}).get("status"),
        "occurrence_status": (result.get("occurrence_evidence") or {}).get("outcome"),
        "weather_status": (result.get("weather_evidence") or {}).get("status"),
        "site_status": (result.get("public_site_search") or {}).get("status"),
        "generated_by": (result.get("final_validated_plan") or {}).get("generated_by"),
    }
    failure_context = json.dumps(diagnostic, ensure_ascii=False, indent=2)

    assert diagnostic["location_status"] == "resolved", failure_context
    assert diagnostic["taxon_status"] == "resolved", failure_context
    assert diagnostic["occurrence_status"] == "strong_map_evidence", failure_context
    assert diagnostic["weather_status"] == "available", failure_context
    assert diagnostic["site_status"] == "success", failure_context
    assert diagnostic["terminal_status"] == "completed", failure_context
    assert diagnostic["grounding_errors"] == [], failure_context
    assert diagnostic["generated_by"] in {
        "llm_phase_2_composer",
        "llm_phase_2_revision",
    }, failure_context

    node_spans = [span for span in recorder.spans if span.kind == "node"]
    model_spans = [span for span in recorder.spans if span.kind == "model"]
    tool_spans = [span for span in recorder.spans if span.kind == "tool"]
    recorded_nodes = Counter(span.node_id for span in node_spans)
    recorded_nodes.pop("evidence_tools", None)
    assert recorded_nodes == Counter(diagnostic["visited_nodes"]), failure_context
    assert any(span.node_id == "evidence_tools" for span in node_spans), failure_context
    assert len(model_spans) >= 5, failure_context
    assert {span.tool_name for span in tool_spans} == {
        "search_occurrences",
        "get_weather_context",
        "find_public_green_spaces",
    }, failure_context
    assert all(span.duration_ms >= 0 for span in recorder.spans), failure_context

    visible = json.dumps(result["final_validated_plan"], ensure_ascii=False).casefold()
    assert not re.search(
        r'"(?:decimallatitude|decimallongitude|occurrenceid|occurrence_id|gbifid|record_ref|associated_safe_cell_ids?)"\s*:',
        visible,
    )
    assert not re.search(
        r"\bhmac(?:\s+(?:reference|ref))?\s*(?::|=|\s)\s*(?:secret|[a-f0-9]{8,})\b",
        visible,
    )
    assert not re.search(r"\bbng-1km-\d", visible)
    assert not re.search(r"\b(?:latitude|longitude)\s*[:=]\s*-?\d+\.\d+", visible)
