"""Privacy-bounded report persistence for biodiversity agent runs."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.biodiversity.observability import AgentRunRecorder


def default_report_directory(
    root: Path,
    *,
    thread_id: str,
    run_id: str,
    timestamp: datetime | None = None,
) -> Path:
    """Return a unique, readable directory without trusting a thread as a path."""

    safe_thread = re.sub(r"[^a-zA-Z0-9._-]+", "-", thread_id).strip("-.")
    safe_thread = safe_thread[:64] or "biodiversity-run"
    instant = timestamp or datetime.now(UTC)
    dated = instant.strftime("%Y%m%dT%H%M%SZ")
    return root / f"{dated}-{safe_thread}-{run_id[:8]}"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def save_biodiversity_run_report(
    directory: Path,
    *,
    recorder: AgentRunRecorder,
    result: dict[str, Any],
    request: str | None,
    data_mode: str,
    model_mode: str,
    checkpoint_id: str | None = None,
) -> dict[str, Path]:
    """Save safe Phase 4 inputs, excluding raw occurrences and model prompts."""

    directory.mkdir(parents=True, exist_ok=True)
    events_path, timings_path = recorder.save(directory)
    timing = recorder.report()
    metadata_path = directory / "metadata.json"
    tool_audit_path = directory / "tool-audit.json"
    final_plan_path = directory / "final-plan.json"

    _write_json(
        metadata_path,
        {
            "schema_version": 1,
            "run_id": recorder.run_id,
            "thread_id": recorder.thread_id,
            "checkpoint_id": checkpoint_id,
            "branch_id": result.get("branch_id"),
            "parent_branch_id": result.get("parent_branch_id"),
            "forked_from_checkpoint_id": result.get(
                "forked_from_checkpoint_id"
            ),
            "request": request,
            "data_mode": data_mode,
            "model_mode": model_mode,
            "status": timing.status,
            "terminal_status": result.get("terminal_status"),
            "started_at": timing.started_at.isoformat(),
            "finished_at": timing.finished_at.isoformat() if timing.finished_at else None,
            "duration_ms": timing.duration_ms,
            "event_count": timing.event_count,
            "span_count": timing.span_count,
            "privacy_note": (
                "Timing artifacts exclude prompts, model outputs, tool inputs, tool outputs, "
                "raw occurrence coordinates, occurrence identifiers and HMAC references."
            ),
        },
    )
    _write_json(tool_audit_path, result.get("executed_tool_call_audit", []))
    final_plan = result.get("final_validated_plan")
    if final_plan is not None:
        _write_json(final_plan_path, final_plan)

    paths = {
        "metadata": metadata_path,
        "events": events_path,
        "timings": timings_path,
        "tool_audit": tool_audit_path,
    }
    if final_plan is not None:
        paths["final_plan"] = final_plan_path
    return paths
