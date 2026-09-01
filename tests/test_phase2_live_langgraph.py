"""Opt-in live Phase 2 smoke test; exact counts and prose are intentionally unpinned."""

import os

import pytest

from app.agents.model_factory import create_live_chat_model
from app.biodiversity.graph import build_biodiversity_graph
from app.biodiversity.orchestration import BackendDependencies


pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_BIODIVERSITY_AGENT") != "1"
    or not os.getenv("OPENAI_API_KEY")
    or not os.getenv("OPENAI_MODEL"),
    reason="Set RUN_LIVE_BIODIVERSITY_AGENT=1 and OpenAI-compatible credentials.",
)
def test_live_langgraph_smoke_preserves_typed_privacy_invariants() -> None:
    graph = build_biodiversity_graph(
        create_live_chat_model(),
        dependencies=BackendDependencies.live(),
    )
    result = graph.invoke(
        {
            "original_request_text": (
                "Plan a two-hour expedition from SW11 4NJ on 1 September 2026 "
                "to look for Common woodpigeon."
            )
        }
    )
    assert result["resolved_location"]["status"] in {
        "resolved",
        "source_unavailable",
        "malformed_upstream_response",
    }
    visible = str(result.get("final_validated_plan") or result.get("terminal_result")).casefold()
    assert "decimallatitude" not in visible
    assert "occurrenceid" not in visible
    assert "record_ref" not in visible
    assert "hmac" not in visible
