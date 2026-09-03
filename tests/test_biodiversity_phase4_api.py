"""Offline Phase 4 API, SSE, durability, concurrency, and privacy coverage."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.biodiversity.api.dependencies import APISettings
from app.biodiversity.api.main import create_app
from app.biodiversity.api.sse import operation_event_stream
from app.biodiversity.observability import AgentRunEvent


STRONG_REQUEST = (
    "Plan a two-hour expedition from SW11 4NJ on 15 June 2026 "
    "to look for Common woodpigeon."
)
SWIFT_REQUEST = (
    "Plan a two-hour expedition from SW11 4NJ on 15 July 2026 "
    "to look for Common swift."
)
NAMED_PLACE_REQUEST = (
    "Plan a two-hour expedition from Kensal Road on 15 June 2026 "
    "to look for Common woodpigeon."
)


def _settings(tmp_path: Path, *, delay: float = 0) -> APISettings:
    return APISettings(
        checkpoint_db=tmp_path / "checkpoints.sqlite",
        catalog_db=tmp_path / "catalog.sqlite",
        report_root=tmp_path / "reports",
        sse_heartbeat_seconds=0.02,
        operation_start_delay_seconds=delay,
    )


def _public_settings(tmp_path: Path, **updates: object) -> APISettings:
    settings = APISettings(
        checkpoint_db=tmp_path / "public-checkpoints.sqlite",
        catalog_db=tmp_path / "public-catalog.sqlite",
        report_root=tmp_path / "public-reports",
        allowed_run_modes=(("fixture", "scripted"),),
        public_demo=True,
        expose_api_docs=False,
        max_concurrent_operations=2,
        mutations_per_minute=20,
        max_demo_threads=100,
        max_storage_bytes=192 * 1024 * 1024,
        run_retention_seconds=24 * 3600,
        cleanup_interval_seconds=300,
    )
    return replace(settings, **updates)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(_settings(tmp_path))) as value:
        yield value


def _wait(client: TestClient, operation_id: str, *, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/operations/{operation_id}")
        assert response.status_code == 200
        operation = response.json()
        if operation["status"] not in {"queued", "running"}:
            return operation
        time.sleep(0.02)
    raise AssertionError(f"Operation did not finish: {operation_id}")


def _create(client: TestClient, thread_id: str, request: str) -> dict:
    response = client.post(
        "/api/v1/runs",
        json={"thread_id": thread_id, "request": request},
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    return response.json()


def _start_waiting(client: TestClient, thread_id: str = "swift") -> tuple[dict, dict]:
    accepted = _create(client, thread_id, SWIFT_REQUEST)
    operation = _wait(client, accepted["operation_id"])
    assert operation["status"] == "waiting_for_input"
    detail = client.get(f"/api/v1/runs/{thread_id}").json()
    assert detail["pending_decisions"][0]["kind"] == "actionable_tradeoff"
    return operation, detail


def test_app_lifespan_health_and_create_run_return_202(client: TestClient) -> None:
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["product"] == "London Biodiversity Expedition Planner"
    assert health.json()["allowed_run_modes"] == [
        {"data_mode": "fixture", "model_mode": "scripted"},
        {"data_mode": "live", "model_mode": "scripted"},
        {"data_mode": "fixture", "model_mode": "live"},
        {"data_mode": "live", "model_mode": "live"},
    ]
    assert health.json()["public_demo"] is False
    accepted = _create(client, "health-start", STRONG_REQUEST)
    assert accepted["events_url"].endswith(f"/{accepted['operation_id']}/events")
    operation = _wait(client, accepted["operation_id"])
    assert operation["status"] == "completed"
    assert operation["current_checkpoint_id"]


def test_public_demo_enforces_mode_policy_and_hides_api_docs(tmp_path: Path) -> None:
    with TestClient(create_app(_public_settings(tmp_path))) as client:
        health = client.get("/api/v1/health")
        assert health.json()["allowed_run_modes"] == [
            {"data_mode": "fixture", "model_mode": "scripted"}
        ]
        assert health.json()["public_demo"] is True
        denied = client.post(
            "/api/v1/runs",
            json={
                "request": STRONG_REQUEST,
                "data_mode": "live",
                "model_mode": "live",
            },
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "mode_not_allowed"
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_public_demo_rate_concurrency_run_and_storage_limits(tmp_path: Path) -> None:
    rate_settings = _public_settings(
        tmp_path / "rate",
        mutations_per_minute=1,
        operation_start_delay_seconds=0.2,
    )
    with TestClient(create_app(rate_settings)) as client:
        assert _create(client, "rate-one", STRONG_REQUEST)["status"] == "queued"
        limited = client.post(
            "/api/v1/runs",
            json={"thread_id": "rate-two", "request": STRONG_REQUEST},
        )
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "rate_limited"

    concurrent_settings = _public_settings(
        tmp_path / "concurrency",
        max_concurrent_operations=1,
        operation_start_delay_seconds=0.2,
    )
    with TestClient(create_app(concurrent_settings)) as client:
        _create(client, "active-one", STRONG_REQUEST)
        busy = client.post(
            "/api/v1/runs",
            json={"thread_id": "active-two", "request": STRONG_REQUEST},
        )
        assert busy.status_code == 503
        assert busy.json()["error"]["code"] == "service_busy"

    run_settings = _public_settings(tmp_path / "runs", max_demo_threads=1)
    with TestClient(create_app(run_settings)) as client:
        accepted = _create(client, "only-thread", STRONG_REQUEST)
        assert _wait(client, accepted["operation_id"])["status"] == "completed"
        full = client.post(
            "/api/v1/runs",
            json={"thread_id": "extra-thread", "request": STRONG_REQUEST},
        )
        assert full.status_code == 503

    storage_settings = _public_settings(tmp_path / "storage", max_storage_bytes=1)
    with TestClient(create_app(storage_settings)) as client:
        full = client.post(
            "/api/v1/runs",
            json={"thread_id": "storage-thread", "request": STRONG_REQUEST},
        )
        assert full.status_code == 503


def test_public_demo_retention_removes_catalog_checkpoints_and_reports(
    tmp_path: Path,
) -> None:
    settings = _public_settings(tmp_path, run_retention_seconds=0.01)
    with TestClient(create_app(settings)) as client:
        accepted = _create(client, "expired-demo", STRONG_REQUEST)
        operation = _wait(client, accepted["operation_id"])
        report = settings.report_root / operation["operation_id"]
        assert report.is_dir()
        time.sleep(0.02)
        service = client.app.state.phase4
        assert service.retention.cleanup() == 1
        assert client.get("/api/v1/runs/expired-demo").status_code == 404
        assert not report.exists()
        with pytest.raises(ValueError, match="No biodiversity run exists"):
            service.runtime.reader().history(thread_id="expired-demo")


def test_production_frontend_is_served_from_the_same_origin(tmp_path: Path) -> None:
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "index.html").write_text(
        '<!doctype html><title>Planner</title><div id="root">ready</div>',
        encoding="utf-8",
    )
    settings = replace(
        _settings(tmp_path),
        serve_frontend=True,
        frontend_dist=frontend,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/").text.endswith('<div id="root">ready</div>')
        assert client.get("/api/v1/health").status_code == 200


def test_fixture_scripted_hitl_resume_and_low_evidence_safety(client: TestClient) -> None:
    _operation, detail = _start_waiting(client, "low-evidence")
    decision = detail["pending_decisions"][0]
    response = client.post(
        "/api/v1/runs/low-evidence/resume",
        json={
            "checkpoint_id": decision["checkpoint_id"],
            "decision": {
                "kind": "actionable_tradeoff",
                "option": "keep_constraints_accept_low_confidence",
            },
        },
    )
    assert response.status_code == 202, response.text
    operation = _wait(client, response.json()["operation_id"])
    assert operation["status"] == "completed"
    refreshed = client.get("/api/v1/runs/low-evidence").json()
    assert refreshed["final_plan"]["status"] == "context_only"
    assert refreshed["final_plan"]["evidence_gate_passed"] is False
    assert refreshed["final_plan"]["recommended_sites"] == []
    checkpoint_id = refreshed["run"]["current_checkpoint_id"]
    mapped = client.get(
        f"/api/v1/runs/low-evidence/checkpoints/{checkpoint_id}/map"
    ).json()
    assert mapped["aggregate_grid"] == []
    assert mapped["candidate_sites"] == []


def test_invalid_resume_choice_is_rejected_before_operation_is_queued(
    client: TestClient,
) -> None:
    _operation, detail = _start_waiting(client, "invalid-resume")
    decision = detail["pending_decisions"][0]
    before = len(detail["operations"])
    response = client.post(
        "/api/v1/runs/invalid-resume/resume",
        json={
            "checkpoint_id": decision["checkpoint_id"],
            "decision": {
                "kind": "actionable_tradeoff",
                "option": "accept_uncertain_access",
            },
        },
    )
    assert response.status_code == 422
    refreshed = client.get("/api/v1/runs/invalid-resume").json()
    assert len(refreshed["operations"]) == before


def test_taxonomy_hitl_resumes_across_http_requests(client: TestClient) -> None:
    accepted = _create(
        client,
        "robin-hitl",
        "Plan a two-hour expedition from SW11 4NJ on 15 January 2026 to look for robin.",
    )
    assert _wait(client, accepted["operation_id"])["interrupt_kind"] == (
        "taxon_selection"
    )
    detail = client.get("/api/v1/runs/robin-hitl").json()
    interrupt = detail["pending_decisions"][0]
    candidate = interrupt["candidates"][0]
    response = client.post(
        "/api/v1/runs/robin-hitl/resume",
        json={
            "checkpoint_id": interrupt["checkpoint_id"],
            "decision": {
                "kind": "taxon_selection",
                "accepted_taxon_key": candidate["accepted_taxon_key"],
            },
        },
    )
    assert response.status_code == 202
    resumed = _wait(client, response.json()["operation_id"])
    assert resumed["interrupt_kind"] == "actionable_tradeoff"


def test_named_place_hitl_is_coordinate_free_and_resumes_same_thread(
    client: TestClient,
) -> None:
    accepted = _create(client, "named-place-api", NAMED_PLACE_REQUEST)
    operation = _wait(client, accepted["operation_id"])
    assert operation["status"] == "waiting_for_input"
    detail = client.get("/api/v1/runs/named-place-api").json()
    decision = detail["pending_decisions"][0]
    assert decision["kind"] == "location_correction"
    assert decision["status"] == "human_selection_required"
    assert len(decision["location_candidates"]) == 3
    public_candidates = json.dumps(decision["location_candidates"]).casefold()
    for forbidden in (
        "latitude",
        "longitude",
        "place_id",
        "osm_id",
        "boundingbox",
    ):
        assert forbidden not in public_candidates
    selected = decision["location_candidates"][0]
    response = client.post(
        "/api/v1/runs/named-place-api/resume",
        json={
            "checkpoint_id": decision["checkpoint_id"],
            "decision": {
                "kind": "location_correction",
                "candidate_id": selected["candidate_id"],
            },
        },
    )
    assert response.status_code == 202, response.text
    resumed = _wait(client, response.json()["operation_id"])
    assert resumed["thread_id"] == "named-place-api"
    assert resumed["status"] == "completed"
    refreshed = client.get("/api/v1/runs/named-place-api").json()
    assert refreshed["run"]["thread_id"] == "named-place-api"


def test_restart_reads_durable_catalog_history_and_pending_hitl(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as first:
        accepted = _create(first, "durable", SWIFT_REQUEST)
        operation = _wait(first, accepted["operation_id"])
        checkpoint_id = operation["current_checkpoint_id"]
    with TestClient(create_app(settings)) as second:
        detail = second.get("/api/v1/runs/durable")
        history = second.get("/api/v1/runs/durable/history")
        assert detail.status_code == history.status_code == 200
        assert detail.json()["run"]["current_checkpoint_id"] == checkpoint_id
        assert detail.json()["pending_decisions"][0]["kind"] == (
            "actionable_tradeoff"
        )
        assert len(history.json()["checkpoints"]) > 5


def test_sse_heartbeat_order_terminal_and_reconnect_replay(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        service = client.app.state.phase4
        service.catalog.create_operation(
            operation_id="sse-order",
            thread_id="sse-thread",
            kind="start",
            data_mode="fixture",
            model_mode="scripted",
        )
        service.catalog.update_operation("sse-order", status="running")

        async def scenario() -> None:
            stream = operation_event_stream(
                service,
                operation_id="sse-order",
                after_sequence=0,
            )
            assert await stream.__anext__() == ": heartbeat\n\n"
            first = AgentRunEvent(
                run_id="sse-order",
                sequence=1,
                event_type="node_started",
                node_id="resolve_taxon",
                timestamp=datetime.now(UTC),
            )
            service.broker.publish(first)
            service.catalog.update_operation("sse-order", last_event_sequence=1)
            encoded = await stream.__anext__()
            assert encoded.startswith("id: 1\nevent: node_started\n")
            terminal = AgentRunEvent(
                run_id="sse-order",
                sequence=2,
                event_type="run_completed",
                timestamp=datetime.now(UTC),
            )
            service.broker.publish(terminal)
            service.catalog.update_operation(
                "sse-order",
                status="completed",
                last_event_sequence=2,
            )
            encoded_terminal = await stream.__anext__()
            assert encoded_terminal.startswith("id: 2\nevent: run_completed\n")
            with pytest.raises(StopAsyncIteration):
                await stream.__anext__()
            reconnect = operation_event_stream(
                service,
                operation_id="sse-order",
                after_sequence=1,
            )
            replayed = await reconnect.__anext__()
            assert replayed.startswith("id: 2\nevent: run_completed\n")
            with pytest.raises(StopAsyncIteration):
                await reconnect.__anext__()

        asyncio.run(scenario())


def test_sse_http_headers_and_completed_replay(client: TestClient) -> None:
    accepted = _create(client, "sse-http", SWIFT_REQUEST)
    operation = _wait(client, accepted["operation_id"])
    response = client.get(
        accepted["events_url"],
        params={"after_sequence": operation["last_event_sequence"] - 1},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    ids = [line for line in response.text.splitlines() if line.startswith("id: ")]
    assert ids == [f"id: {operation['last_event_sequence']}"]
    via_header = client.get(
        accepted["events_url"],
        headers={"Last-Event-ID": str(operation["last_event_sequence"] - 1)},
    )
    assert [
        line for line in via_header.text.splitlines() if line.startswith("id: ")
    ] == ids


def test_ambiguous_execution_resume_is_409(client: TestClient) -> None:
    _operation, detail = _start_waiting(client, "ambiguous")
    source = detail["pending_decisions"][0]["checkpoint_id"]
    for duration in (3, 4):
        fork = client.post(
            "/api/v1/runs/ambiguous/fork",
            json={
                "checkpoint_id": source,
                "updates": {"duration_hours": duration},
            },
        )
        assert fork.status_code == 202, fork.text
        assert _wait(client, fork.json()["operation_id"])["status"] == (
            "waiting_for_input"
        )
    response = client.post(
        "/api/v1/runs/ambiguous/resume",
        json={
            "decision": {
                "kind": "actionable_tradeoff",
                "option": "keep_constraints_accept_low_confidence",
            }
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mutation_conflict"


def test_replay_fork_compare_terminal_and_noop_guards(client: TestClient) -> None:
    accepted = _create(client, "travel-api", STRONG_REQUEST)
    operation = _wait(client, accepted["operation_id"])
    original_final = operation["current_checkpoint_id"]
    history = client.get("/api/v1/runs/travel-api/history").json()["checkpoints"]
    before_compose = next(
        item for item in history if item["next_nodes"] == ["compose_expedition_plan"]
    )
    replay = client.post(
        "/api/v1/runs/travel-api/replay",
        json={"checkpoint_id": before_compose["checkpoint_id"]},
    )
    assert replay.status_code == 202, replay.text
    replayed = _wait(client, replay.json()["operation_id"])
    assert replayed["execution_id"] != before_compose["execution_id"]
    fork = client.post(
        "/api/v1/runs/travel-api/fork",
        json={
            "checkpoint_id": original_final,
            "updates": {"search_radius_km": 8},
            "branch_label": "Wider context",
        },
    )
    assert fork.status_code == 202, fork.text
    forked = _wait(client, fork.json()["operation_id"])
    assert forked["branch_id"] != operation["branch_id"]
    comparison = client.get(
        "/api/v1/runs/travel-api/compare",
        params={
            "checkpoint_a": original_final,
            "checkpoint_b": forked["current_checkpoint_id"],
        },
    )
    assert comparison.status_code == 200
    assert "request_constraints" in comparison.json()["comparison"][
        "changed_fields"
    ]
    original = client.get(
        f"/api/v1/runs/travel-api/checkpoints/{original_final}/evidence"
    )
    assert original.status_code == 200
    terminal_replay = client.post(
        "/api/v1/runs/travel-api/replay",
        json={"checkpoint_id": original_final},
    )
    assert terminal_replay.status_code == 422
    noop = client.post(
        "/api/v1/runs/travel-api/fork",
        json={
            "checkpoint_id": original_final,
            "updates": {"search_radius_km": 5},
        },
    )
    assert noop.status_code == 422


def test_same_thread_conflict_and_different_thread_isolation(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, delay=0.2))) as client:
        _operation, detail = _start_waiting(client, "serialised")
        checkpoint = detail["pending_decisions"][0]["checkpoint_id"]
        body = {
            "checkpoint_id": checkpoint,
            "decision": {
                "kind": "actionable_tradeoff",
                "option": "keep_constraints_accept_low_confidence",
            },
        }
        first = client.post("/api/v1/runs/serialised/resume", json=body)
        second = client.post("/api/v1/runs/serialised/resume", json=body)
        assert first.status_code == 202
        assert second.status_code == 409
        left = _create(client, "parallel-left", SWIFT_REQUEST)
        right = _create(client, "parallel-right", SWIFT_REQUEST)
        assert _wait(client, left["operation_id"])["status"] == "waiting_for_input"
        assert _wait(client, right["operation_id"])["status"] == "waiting_for_input"


def test_background_failure_status_and_error_are_sanitised(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_manager(*_args, **_kwargs):
        raise RuntimeError("secret upstream exception OPENAI_API_KEY=do-not-leak")

    monkeypatch.setattr(client.app.state.phase4.runtime, "manager", unavailable_manager)
    accepted = _create(client, "failed-worker", STRONG_REQUEST)
    operation = _wait(client, accepted["operation_id"])
    assert operation["status"] == "failed"
    assert operation["error_code"] == "upstream_unavailable"
    serialised = json.dumps(operation)
    assert "do-not-leak" not in serialised
    assert "OPENAI_API_KEY" not in serialised


def test_exact_state_evidence_and_map_views_are_private(client: TestClient) -> None:
    accepted = _create(client, "privacy", STRONG_REQUEST)
    operation = _wait(client, accepted["operation_id"])
    history = client.get("/api/v1/runs/privacy/history").json()["checkpoints"]
    checkpoint = next(
        item
        for item in history
        if item["checkpoint_id"] == operation["current_checkpoint_id"]
    )
    state = client.get(
        f"/api/v1/runs/privacy/checkpoints/{checkpoint['checkpoint_id']}/state",
        params={
            "node_id": checkpoint["node_id"],
            "graph_step": checkpoint["graph_step"],
        },
    )
    assert state.status_code == 200
    stale = client.get(
        f"/api/v1/runs/privacy/checkpoints/{checkpoint['checkpoint_id']}/state",
        params={"node_id": "resolve_taxon", "graph_step": checkpoint["graph_step"]},
    )
    assert stale.status_code == 422
    evidence = client.get(
        f"/api/v1/runs/privacy/checkpoints/{checkpoint['checkpoint_id']}/evidence"
    )
    map_response = client.get(
        f"/api/v1/runs/privacy/checkpoints/{checkpoint['checkpoint_id']}/map"
    )
    assert evidence.json()["status"] == "strong"
    mapped = map_response.json()
    assert mapped["aggregate_grid"]
    assert mapped["candidate_sites"]
    assert all(
        feature["geometry"]["type"] in {"Polygon", "MultiPolygon"}
        for feature in mapped["candidate_sites"]
    )
    assert all(
        feature["properties"]["display_id"].startswith("grid-")
        for feature in mapped["aggregate_grid"]
    )
    public_payload = json.dumps(
        {
            "state": state.json(),
            "evidence": evidence.json(),
            "map": mapped,
            "detail": client.get("/api/v1/runs/privacy").json(),
            "runs": client.get("/api/v1/runs").json(),
            "history": client.get("/api/v1/runs/privacy/history").json(),
            "operation": client.get(
                f"/api/v1/operations/{accepted['operation_id']}"
            ).json(),
            "events": client.get(accepted["events_url"]).text,
        }
    ).casefold()
    for forbidden in (
        "original_request_text",
        "sw11 4nj",
        "decimallatitude",
        "decimallongitude",
        "occurrenceid",
        "occurrence_id",
        "record_ref",
        "associated_safe_cell_ids",
        "bng-1km",
        "hmac",
        "api_key",
        "raw_tool",
    ):
        assert forbidden not in public_payload


def test_uniform_404_422_and_no_raw_validation_input(client: TestClient) -> None:
    missing = client.get("/api/v1/runs/not-there")
    assert missing.status_code == 404
    invalid = client.post(
        "/api/v1/runs",
        json={"thread_id": "bad id", "request": "SECRET RAW PROMPT"},
    )
    assert invalid.status_code == 422
    assert invalid.json() == {
        "error": {
            "code": "invalid_request",
            "message": "One or more request fields are invalid.",
            "fields": ["thread_id"],
        }
    }
    assert "SECRET RAW PROMPT" not in invalid.text


def test_cors_configuration_rejects_wildcard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BIODIVERSITY_CORS_ORIGINS", "*")
    with pytest.raises(ValueError, match="explicit origins"):
        APISettings.from_environment()
