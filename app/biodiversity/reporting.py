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


def _redacted_entrance(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    return {
        key: value.get(key)
        for key in (
            "entrance_id",
            "site_id",
            "label",
            "access_certainty",
            "routing_entrance",
            "entrance",
            "barrier",
            "access",
            "foot",
            "wheelchair",
            "opening_hours",
            "association_method",
            "limitations",
        )
    }


def _redacted_route_option(value: dict[str, Any]) -> dict[str, Any]:
    route = dict(value.get("route") or {})
    return {
        "option_id": value.get("option_id"),
        "status": value.get("status"),
        "site_id": value.get("site_id"),
        "site_name": value.get("site_name"),
        "evidence_site_order": value.get("evidence_site_order"),
        "entrance": _redacted_entrance(value.get("entrance")),
        "route": (
            {
                "route_id": route.get("route_id"),
                "provider": route.get("provider"),
                "provider_version": route.get("provider_version"),
                "routing_profile": route.get("routing_profile"),
                "total_distance_m": route.get("total_distance_m"),
                "total_duration_seconds": route.get("total_duration_seconds"),
                "total_walking_duration_seconds": route.get(
                    "total_walking_duration_seconds"
                ),
                "total_public_transport_duration_seconds": route.get(
                    "total_public_transport_duration_seconds"
                ),
                "ascent_m": route.get("ascent_m"),
                "descent_m": route.get("descent_m"),
                "retrieved_at": route.get("retrieved_at"),
                "licence": route.get("licence"),
                "attribution": route.get("attribution"),
                "cache_status": route.get("cache_status"),
                "limitations": route.get("limitations") or [],
            }
            if route
            else None
        ),
        "constraint_results": value.get("constraint_results") or [],
        "feasible": bool(value.get("feasible")),
        "warnings": value.get("warnings") or [],
    }


def _redacted_walking_plan(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    output = {
        key: item
        for key, item in value.items()
        if key not in {"entrance", "elevation_profile", "route_geometry_reference"}
    }
    output["entrance"] = _redacted_entrance(value.get("entrance"))
    output["elevation_summary"] = {
        "sample_count": len(value.get("elevation_profile") or []),
        "ascent_m": value.get("ascent_m"),
        "descent_m": value.get("descent_m"),
    }
    output["alternative_feasible_routes"] = [
        _redacted_route_option(dict(item))
        for item in value.get("alternative_feasible_routes") or []
    ]
    return output


def save_biodiversity_run_report(
    directory: Path,
    *,
    recorder: AgentRunRecorder,
    result: dict[str, Any],
    request: str | None,
    data_mode: str,
    model_mode: str,
    checkpoint_id: str | None = None,
    execution_id: str | None = None,
    parent_execution_id: str | None = None,
    replayed_from_checkpoint_id: str | None = None,
    run_manifest: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Save safe Phase 5 artifacts without origins, route geometry, or occurrences."""

    directory.mkdir(parents=True, exist_ok=True)
    events_path, timings_path = recorder.save(directory)
    timing = recorder.report()
    metadata_path = directory / "metadata.json"
    tool_audit_path = directory / "tool-audit.json"
    final_plan_path = directory / "final-plan.json"
    route_audit_path = directory / "route-audit.json"
    route_plan_path = directory / "route-plan.json"
    provider_timing_path = directory / "provider-cache-timings.json"

    _write_json(
        metadata_path,
        {
            "schema_version": 3,
            "run_id": recorder.run_id,
            "thread_id": recorder.thread_id,
            "checkpoint_id": checkpoint_id,
            "execution_id": execution_id or result.get("execution_id"),
            "parent_execution_id": parent_execution_id
            or result.get("parent_execution_id"),
            "replayed_from_checkpoint_id": replayed_from_checkpoint_id
            or result.get("replayed_from_checkpoint_id"),
            "branch_id": result.get("branch_id"),
            "parent_branch_id": result.get("parent_branch_id"),
            "forked_from_checkpoint_id": result.get(
                "forked_from_checkpoint_id"
            ),
            "request": request,
            "data_mode": data_mode,
            "model_mode": model_mode,
            "run_manifest": run_manifest or result.get("run_manifest"),
            "status": timing.status,
            "terminal_status": result.get("terminal_status"),
            "started_at": timing.started_at.isoformat(),
            "finished_at": timing.finished_at.isoformat() if timing.finished_at else None,
            "duration_ms": timing.duration_ms,
            "event_count": timing.event_count,
            "span_count": timing.span_count,
            "event_sink_error_types": recorder.event_sink_error_types,
            "privacy_note": (
                "Timing artifacts exclude prompts, model outputs, tool inputs, tool outputs, "
                "raw occurrence coordinates, occurrence identifiers and HMAC references."
            ),
        },
    )
    _write_json(tool_audit_path, result.get("executed_tool_call_audit", []))
    final_plan = result.get("final_validated_plan")
    if final_plan is not None:
        safe_final_plan = dict(final_plan)
        safe_final_plan["walking_plan"] = _redacted_walking_plan(
            final_plan.get("walking_plan")
        )
        _write_json(final_plan_path, safe_final_plan)
    elif final_plan_path.exists():
        final_plan_path.unlink()

    walking_plan = result.get("validated_walking_plan")
    route_options = list(result.get("route_options") or [])
    _write_json(
        route_audit_path,
        {
            "schema_version": 1,
            "route_options": [
                _redacted_route_option(dict(item)) for item in route_options
            ],
            "privacy_note": (
                "This audit excludes routing origins, route geometry, provider request "
                "bodies, occurrence data, safe-cell coordinates and credentials."
            ),
        },
    )
    _write_json(route_plan_path, _redacted_walking_plan(walking_plan))
    provider_events = [
        event
        for event in recorder.events
        if event.event_type
        in {
            "routing_provider_completed",
            "routing_provider_failed",
            "provider_attempt",
            "provider_failover",
            "route_cache_hit",
            "route_cache_miss",
        }
    ]
    _write_json(
        provider_timing_path,
        {
            "schema_version": 1,
            "provider_duration_ms": round(
                sum(event.duration_ms or 0 for event in provider_events), 3
            ),
            "cache_hits": sum(
                event.event_type == "route_cache_hit" for event in provider_events
            ),
            "cache_misses": sum(
                event.event_type == "route_cache_miss" for event in provider_events
            ),
            "attempts": [
                {
                    "event_type": event.event_type,
                    "provider": event.payload.get("provider"),
                    "route_id": event.payload.get("route_id"),
                    "cache_status": event.payload.get("cache_status"),
                    "error_category": event.payload.get("error_category"),
                    "started_at": (
                        event.started_at.isoformat() if event.started_at else None
                    ),
                    "finished_at": (
                        event.finished_at.isoformat() if event.finished_at else None
                    ),
                    "duration_ms": event.duration_ms,
                }
                for event in provider_events
            ],
        },
    )

    paths = {
        "metadata": metadata_path,
        "events": events_path,
        "timings": timings_path,
        "tool_audit": tool_audit_path,
        "route_audit": route_audit_path,
        "route_plan": route_plan_path,
        "provider_cache_timings": provider_timing_path,
    }
    if final_plan is not None:
        paths["final_plan"] = final_plan_path
    return paths
