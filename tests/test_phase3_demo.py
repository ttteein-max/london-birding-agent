"""Acceptance coverage for the reproducible Phase 3 feature demonstration."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.generate_phase3_demo import generate


def test_phase3_demo_covers_hitl_fork_replay_compare_and_safe_views(
    tmp_path: Path,
) -> None:
    summary = generate(tmp_path)
    operations = [item["operation"] for item in summary["stages"]]
    assert operations == [
        "start",
        "resume_taxon",
        "resume_low_confidence",
        "replay",
        "fork",
        "compare",
    ]
    assert summary["checkpoint_events_cover_history"] is True
    assert summary["checkpoint_event_count"] == summary["state_view_count"]
    assert summary["branch_count"] == 2
    assert summary["execution_count"] == 3
    fork_stage = next(
        item for item in summary["stages"] if item["operation"] == "fork"
    )
    assert fork_stage["terminal_status"] == "completed"

    events = json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
    sequences = [item["sequence"] for item in events["events"]]
    assert sequences == list(range(1, len(sequences) + 1))
    checkpoint_events = [
        item
        for item in events["events"]
        if item["event_type"] == "checkpoint_created"
    ]
    assert len(checkpoint_events) > 3
    assert all(item["node_id"] for item in checkpoint_events)
    assert "evidence_tools" in {item["node_id"] for item in checkpoint_events}

    safe_views = (tmp_path / "state-views.json").read_text(encoding="utf-8")
    casefolded = safe_views.casefold()
    for forbidden in (
        "sw11 4nj",
        "original_request_text",
        "messages",
        "pending_hitl_payload",
        "rounded_start_point",
        "british_national_grid",
        "safe_map_cells",
        "associated_safe_cell_ids",
        "executed_tool_call_audit",
        "decimallatitude",
        "decimallongitude",
        "occurrenceid",
        "record_ref",
        "hmac",
    ):
        assert forbidden not in casefolded
