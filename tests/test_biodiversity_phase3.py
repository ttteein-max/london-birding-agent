"""Offline acceptance coverage for Phase 3 HITL persistence and time travel."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.types import Command

from app.biodiversity.agent_models import ExpeditionRequestDraft
from app.biodiversity.graph import (
    build_biodiversity_graph,
    create_biodiversity_checkpointer,
    open_biodiversity_sqlite_checkpointer,
)
from app.biodiversity.observability import AgentRunRecorder
from app.biodiversity.reporting import save_biodiversity_run_report
from app.biodiversity.run_models import ForkRequest, RunManifest, RunProfile
from app.biodiversity.runs import BiodiversityRunManager
from app.biodiversity.testing import (
    ScriptedRequestParserModel,
    make_scripted_biodiversity_models,
)


def _draft(bird: str, month: int) -> ExpeditionRequestDraft:
    return ExpeditionRequestDraft(
        bird_input=bird,
        postcode="SW11 4NJ",
        target_local_date=date(2026, month, 15),
        duration_hours=2,
    )


def _graph(checkpointer, draft: ExpeditionRequestDraft | None = None):
    parser, evidence, composer = make_scripted_biodiversity_models()
    if draft is not None:
        parser = ScriptedRequestParserModel(draft)
    return build_biodiversity_graph(
        parser_model=parser,
        evidence_model=evidence,
        composer_model=composer,
        checkpointer=checkpointer,
    ), composer


def _interrupt(result: dict) -> dict:
    return result["__interrupt__"][0].value


def test_sqlite_interrupt_survives_graph_rebuild_and_thread_isolation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "checkpoints.sqlite"
    with open_biodiversity_sqlite_checkpointer(database) as checkpointer:
        graph, _ = _graph(checkpointer, _draft("robin", 1))
        first = BiodiversityRunManager(graph).start("Look for robin", thread_id="one")
        assert _interrupt(first)["kind"] == "taxon_selection"
        assert first["run_manifest"]["data_mode"] == "fixture"
        assert first["run_manifest"]["model_mode"] == "scripted"

    with open_biodiversity_sqlite_checkpointer(database) as checkpointer:
        graph, _ = _graph(checkpointer, _draft("robin", 1))
        manager = BiodiversityRunManager(graph)
        with pytest.raises(ValueError, match="No biodiversity run"):
            manager.resume(
                thread_id="two",
                resume={"accepted_taxon_key": 2489281},
            )
        result = manager.resume(
            thread_id="one",
            resume={"accepted_taxon_key": 2489281},
        )
        assert result["resolved_taxon"]["accepted_taxon_key"] == 2489281
        assert _interrupt(result)["kind"] == "actionable_tradeoff"
        assert result["occurrence_evidence"]["outcome"] == "insufficient_evidence"
        occurrence_calls = [
            item
            for item in result["executed_tool_call_audit"]
            if item["tool_name"] == "search_occurrences"
        ]
        assert len(occurrence_calls) == 1
        assert json.loads(occurrence_calls[0]["canonical_arguments"])[
            "accepted_taxon_key"
        ] == 2489281
        completed = manager.resume(
            thread_id="one",
            resume={"option": "keep_constraints_accept_low_confidence"},
        )
        assert completed["terminal_status"] == "completed"
        assert completed["final_validated_plan"]["recommended_sites"] == []
        assert completed["final_validated_plan"]["evidence_gate_passed"] is False
        assert completed["final_validated_plan"]["low_confidence_accepted"] is True
        assert "evidence gate did not pass" in completed["final_validated_plan"][
            "low_confidence_notice"
        ]


def test_saved_run_manifest_rejects_runtime_mode_switch_without_mutation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "manifest.sqlite"
    with open_biodiversity_sqlite_checkpointer(database) as checkpointer:
        graph, _ = _graph(checkpointer, _draft("robin", 1))
        manager = BiodiversityRunManager(graph)
        manager.start("robin", thread_id="manifest")
        before = len(manager.history(thread_id="manifest"))

    with open_biodiversity_sqlite_checkpointer(database) as checkpointer:
        graph, _ = _graph(checkpointer, _draft("robin", 1))
        mismatched = BiodiversityRunManager(
            graph,
            run_profile=RunProfile(
                data_mode="live",
                model_mode="scripted",
                model_identifier="scripted-biodiversity-v1",
            ),
        )
        with pytest.raises(ValueError, match="data_mode"):
            mismatched.resume(
                thread_id="manifest",
                resume={"accepted_taxon_key": 2489281},
            )
        assert len(mismatched.history(thread_id="manifest")) == before


def test_phase4_manifest_remains_readable_but_is_explicitly_read_only() -> None:
    stored = RunManifest.model_validate(
        {
            "workflow_version": "phase-3.1",
            "state_schema_version": 2,
            "data_mode": "fixture",
            "model_mode": "scripted",
            "model_identifier": "scripted-biodiversity-v1",
            "endpoint_fingerprint": "local-scripted",
            "created_at": datetime.now(UTC),
        }
    )
    assert stored.routing_provider is None
    graph, _ = _graph(create_biodiversity_checkpointer())
    manager = BiodiversityRunManager(graph)
    snapshot = SimpleNamespace(
        values={"run_manifest": stored.model_dump(mode="json")}
    )
    with pytest.raises(ValueError, match="read-only.*new Phase 5 run"):
        manager._validate_run_profile(snapshot)


def test_ambiguous_payload_has_taxonomy_bounded_previews_and_no_private_data() -> None:
    graph, _ = _graph(create_biodiversity_checkpointer(), _draft("robin", 1))
    result = graph.invoke(
        {"original_request_text": "robin"},
        {"configurable": {"thread_id": "preview"}},
    )
    payload = _interrupt(result)
    first = payload["candidates"][0]
    assert {
        "accepted_taxon_key",
        "common_name",
        "scientific_name",
        "canonical_name",
        "rank",
        "taxonomic_status",
        "class",
        "order",
        "family",
        "genus",
        "resolution_method",
        "confidence",
        "evidence_preview",
    } <= set(first)
    assert first["evidence_preview"]["status"] == "evaluated"
    assert first["evidence_preview"]["evidence_outcome"] == "insufficient_evidence"
    assert payload["candidates"][3]["evidence_preview"]["status"] == (
        "not_evaluated_budget"
    )
    serialised = json.dumps(payload).casefold()
    for forbidden in (
        "decimallatitude",
        "decimallongitude",
        "occurrenceid",
        "record_ref",
        "hmac",
        "safe_cell",
    ):
        assert forbidden not in serialised


def test_candidate_preview_source_failure_is_not_reported_as_no_evidence() -> None:
    graph, _ = _graph(create_biodiversity_checkpointer(), _draft("eagle", 6))
    result = graph.invoke(
        {"original_request_text": "eagle"},
        {"configurable": {"thread_id": "preview-source-failure"}},
    )
    preview = _interrupt(result)["candidates"][0]["evidence_preview"]
    assert preview["status"] == "source_failure"
    assert preview["source_status"] == "missing_or_corrupt_fixture"
    assert preview["evidence_outcome"] == "source_unavailable"


def test_invalid_taxon_resume_does_not_mutate_saved_interrupt_state() -> None:
    graph, _ = _graph(create_biodiversity_checkpointer(), _draft("robin", 1))
    manager = BiodiversityRunManager(graph)
    manager.start("robin", thread_id="invalid")
    config = {"configurable": {"thread_id": "invalid"}}
    before = graph.get_state(config)
    history_count = len(manager.history(thread_id="invalid"))
    with pytest.raises(ValueError, match="not present"):
        manager.resume(
            thread_id="invalid",
            resume={"accepted_taxon_key": 999999},
        )
    after = graph.get_state(config)
    assert after.values["resolved_taxon"] == before.values["resolved_taxon"]
    assert after.values["pending_hitl_payload"] == before.values["pending_hitl_payload"]
    assert after.next == before.next
    assert len(manager.history(thread_id="invalid")) == history_count


def test_expand_radius_reruns_only_public_site_search() -> None:
    parser, evidence, composer = make_scripted_biodiversity_models()
    parser = ScriptedRequestParserModel(
        ExpeditionRequestDraft(
            bird_input="Common woodpigeon",
            postcode="SW11 4NJ",
            target_local_date=date(2026, 6, 15),
            duration_hours=2,
            search_radius_km=0.1,
        )
    )
    graph = build_biodiversity_graph(
        parser_model=parser,
        evidence_model=evidence,
        composer_model=composer,
        checkpointer=create_biodiversity_checkpointer(),
    )
    config = {"configurable": {"thread_id": "radius"}}
    first = graph.invoke({"original_request_text": "tiny radius"}, config)
    assert [_option["option"] for _option in _interrupt(first)["options"]] == [
        "expand_search_radius"
    ]
    result = graph.invoke(
        Command(resume={"option": "expand_search_radius", "search_radius_km": 5}),
        config,
    )
    names = [item["tool_name"] for item in result["executed_tool_call_audit"]]
    assert names.count("search_occurrences") == 1
    assert names.count("get_weather_context") == 1
    assert names.count("find_public_green_spaces") == 2


def test_widen_window_reruns_occurrence_and_sites_but_not_weather() -> None:
    graph, _ = _graph(create_biodiversity_checkpointer(), _draft("Common swift", 7))
    config = {"configurable": {"thread_id": "widen"}}
    first = graph.invoke({"original_request_text": "swift"}, config)
    assert "widen_seasonal_window" in {
        item["option"] for item in _interrupt(first)["options"]
    }
    result = graph.invoke(
        Command(
            resume={
                "option": "widen_seasonal_window",
                "seasonal_window_radius_months": 2,
            }
        ),
        config,
    )
    names = [item["tool_name"] for item in result["executed_tool_call_audit"]]
    assert names.count("search_occurrences") == 2
    assert names.count("get_weather_context") == 1
    assert names.count("find_public_green_spaces") == 2
    assert result["occurrence_evidence"]["seasonal_months"] == [5, 6, 7, 8, 9]


def test_related_taxon_requires_second_interrupt_and_rejects_forged_key() -> None:
    graph, _ = _graph(create_biodiversity_checkpointer(), _draft("Common swift", 7))
    manager = BiodiversityRunManager(graph)
    manager.start("swift", thread_id="related")
    second = manager.resume(
        thread_id="related",
        resume={"option": "consider_related_taxa"},
    )
    payload = _interrupt(second)
    assert payload["kind"] == "related_taxon_selection"
    assert {item["relation_level"] for item in payload["candidates"]} == {
        "same_genus"
    }
    with pytest.raises(ValueError, match="not present"):
        manager.resume(
            thread_id="related",
            resume={"accepted_taxon_key": 999999},
        )
    chosen = payload["candidates"][0]["accepted_taxon_key"]
    result = manager.resume(
        thread_id="related",
        resume={"accepted_taxon_key": chosen},
    )
    assert result["resolved_taxon"]["accepted_taxon_key"] == chosen
    assert result["weather_evidence"] is not None
    assert result["related_taxon_candidates"] == []
    assert result["related_taxon_source_key"] is None
    assert result["resolved_taxon"]["provenance"]["source_record_type"] == (
        "accepted_related_taxon_selection"
    )
    assert [item["tool_name"] for item in result["executed_tool_call_audit"]].count(
        "get_weather_context"
    ) == 1


def test_accept_low_confidence_has_no_recommended_sites() -> None:
    graph, _ = _graph(create_biodiversity_checkpointer(), _draft("Common swift", 7))
    config = {"configurable": {"thread_id": "accept-low"}}
    graph.invoke({"original_request_text": "swift"}, config)
    result = graph.invoke(
        Command(resume={"option": "keep_constraints_accept_low_confidence"}),
        config,
    )
    assert result["final_validated_plan"]["status"] == "context_only"
    assert result["final_validated_plan"]["recommended_sites"] == []
    assert result["final_validated_plan"]["evidence_gate_passed"] is False
    assert result["final_validated_plan"]["low_confidence_accepted"] is True
    assert any(
        item.get("option") == "keep_constraints_accept_low_confidence"
        for item in result["applied_user_decisions"]
    )


def test_history_replay_fork_and_comparison_preserve_original_plan() -> None:
    checkpointer = create_biodiversity_checkpointer()
    graph, composer = _graph(checkpointer, _draft("Common swift", 7))
    manager = BiodiversityRunManager(graph)
    manager.start("swift", thread_id="travel")
    original = manager.resume(
        thread_id="travel",
        resume={"option": "keep_constraints_accept_low_confidence"},
    )
    history = manager.history(thread_id="travel")
    assert all(item.checkpoint_id for item in history)
    assert any(item.parent_checkpoint_id for item in history)
    original_final = history[0].checkpoint_id

    before_compose = next(
        item for item in history if item.next_nodes == ["compose_expedition_plan"]
    )
    invocations_before = composer.invocations
    original_branch = manager.branches(thread_id="travel")[0]
    replay = manager.replay(
        thread_id="travel",
        checkpoint_id=before_compose.checkpoint_id,
    )
    assert composer.invocations == invocations_before + 1
    assert replay.execution_id != before_compose.execution_id
    assert replay.parent_execution_id == before_compose.execution_id
    assert replay.replayed_from_checkpoint_id == before_compose.checkpoint_id
    branch_after_replay = next(
        item
        for item in manager.branches(thread_id="travel")
        if item.branch_id == original_branch.branch_id
    )
    assert branch_after_replay.final_checkpoint_id == original_final
    assert branch_after_replay.created_at == original_branch.created_at
    replay_execution = next(
        item
        for item in manager.executions(thread_id="travel")
        if item.execution_id == replay.execution_id
    )
    assert replay_execution.final_checkpoint_id == replay.final_checkpoint_id

    fork = manager.fork(
        ForkRequest(
            thread_id="travel",
            checkpoint_id=original_final,
            updates={"search_radius_km": 8},
            branch_label="wider local context",
        )
    )
    assert fork.parent_branch_id
    assert fork.final_checkpoint_id != original_final
    original_snapshot = next(
        item
        for item in graph.get_state_history(
            {"configurable": {"thread_id": "travel"}}
        )
        if item.config["configurable"]["checkpoint_id"] == original_final
    )
    assert original_snapshot.values["final_validated_plan"] == original[
        "final_validated_plan"
    ]
    comparison = manager.compare(
        thread_id="travel",
        checkpoint_a=original_final,
        checkpoint_b=fork.final_checkpoint_id,
    )
    assert comparison.request_constraints.changed is True
    assert comparison.request_constraints.checkpoint_a["search_radius_km"] == 5
    assert comparison.request_constraints.checkpoint_b["search_radius_km"] == 8

    fork_snapshot = next(
        item
        for item in graph.get_state_history(
            {"configurable": {"thread_id": "travel"}}
        )
        if item.config["configurable"]["checkpoint_id"]
        == fork.final_checkpoint_id
    )
    audit_ids = [
        item["call_id"] for item in fork_snapshot.values["executed_tool_call_audit"]
    ]
    assert len(audit_ids) == len(set(audit_ids))
    decisions = [
        json.dumps(item, sort_keys=True)
        for item in fork_snapshot.values["applied_user_decisions"]
    ]
    assert len(decisions) == len(set(decisions))
    refreshed_history = manager.history(thread_id="travel")
    original_summaries = [
        item
        for item in refreshed_history
        if item.execution_id == before_compose.execution_id
    ]
    assert {item.final_checkpoint_id for item in original_summaries} == {
        original_final
    }
    public_contract = json.dumps(
        {
            "history": [item.model_dump(mode="json") for item in history],
            "fork": fork.model_dump(mode="json"),
            "comparison": comparison.model_dump(mode="json"),
        }
    ).casefold()
    for forbidden in (
        "decimallatitude",
        "decimallongitude",
        "record_ref",
        "hmac",
        "safe_map_cells",
    ):
        assert forbidden not in public_contract


def test_terminal_checkpoint_replay_is_rejected_without_ghost_execution() -> None:
    graph, _ = _graph(create_biodiversity_checkpointer())
    manager = BiodiversityRunManager(graph)
    completed = manager.start(
        "Plan a two-hour expedition from SW11 4NJ on 15 June 2026 "
        "to look for Common woodpigeon.",
        thread_id="terminal-replay",
    )
    terminal_checkpoint = manager.history(thread_id="terminal-replay")[0]
    assert completed["terminal_status"] == "completed"
    assert terminal_checkpoint.next_nodes == []
    executions_before = manager.executions(thread_id="terminal-replay")

    with pytest.raises(ValueError, match="no downstream nodes"):
        manager.replay(
            thread_id="terminal-replay",
            checkpoint_id=terminal_checkpoint.checkpoint_id,
        )

    assert manager.executions(thread_id="terminal-replay") == executions_before


def test_replayed_interrupt_resume_keeps_replay_execution_identity() -> None:
    graph, _ = _graph(create_biodiversity_checkpointer(), _draft("Common swift", 7))
    manager = BiodiversityRunManager(graph)
    manager.start("swift", thread_id="replay-resume")
    interrupted = manager.history(thread_id="replay-resume")[0]
    assert interrupted.interrupt_kind == "actionable_tradeoff"

    replay = manager.replay(
        thread_id="replay-resume",
        checkpoint_id=interrupted.checkpoint_id,
    )
    resumed = manager.resume(
        thread_id="replay-resume",
        checkpoint_id=replay.head_checkpoint_id,
        resume={"option": "keep_constraints_accept_low_confidence"},
    )

    assert resumed["execution_id"] == replay.execution_id
    assert resumed["parent_execution_id"] == replay.parent_execution_id
    assert resumed["replayed_from_checkpoint_id"] == interrupted.checkpoint_id
    replay_head = manager.execution_head(
        thread_id="replay-resume",
        execution_id=replay.execution_id,
    )
    assert replay_head.values["terminal_status"] == "completed"


def test_phase3_events_and_report_include_checkpoint_branch_metadata(
    tmp_path: Path,
) -> None:
    recorder = AgentRunRecorder(thread_id="reported")
    graph, _ = _graph(create_biodiversity_checkpointer())
    manager = BiodiversityRunManager(graph, recorder=recorder)
    started = manager.start(
        (
            "Plan a two-hour expedition from SW11 4NJ on 15 June 2026 "
            "to look for Common woodpigeon."
        ),
        thread_id="reported",
    )
    original_checkpoint = manager.history(thread_id="reported")[0].checkpoint_id
    manager.fork(
        ForkRequest(
            thread_id="reported",
            checkpoint_id=original_checkpoint,
            updates={"search_radius_km": 8},
        )
    )
    snapshot = graph.get_state({"configurable": {"thread_id": "reported"}})
    result = dict(snapshot.values)
    recorder.finish("completed", payload={"terminal_status": "completed"})
    save_biodiversity_run_report(
        tmp_path,
        recorder=recorder,
        result=result,
        request=started["original_request_text"],
        data_mode="fixture",
        model_mode="scripted",
        checkpoint_id=snapshot.config["configurable"]["checkpoint_id"],
    )
    events = json.loads((tmp_path / "events.json").read_text())
    event_types = {item["event_type"] for item in events["events"]}
    assert "checkpoint_created" in event_types
    assert {"fork_created", "fork_started", "fork_completed"} <= event_types
    metadata = json.loads((tmp_path / "metadata.json").read_text())
    assert metadata["checkpoint_id"]
    assert metadata["branch_id"]
    assert metadata["parent_branch_id"]
    assert metadata["forked_from_checkpoint_id"] == original_checkpoint
    assert metadata["execution_id"]
    assert {span.kind for span in recorder.spans} == {"node", "model", "tool"}
    checkpoint_events = [
        item
        for item in events["events"]
        if item["event_type"] == "checkpoint_created"
    ]
    history = manager.history(thread_id="reported")
    assert len(checkpoint_events) == len(history)
    assert len({item["payload"]["checkpoint_id"] for item in checkpoint_events}) == (
        len(checkpoint_events)
    )
    assert all(item["node_id"] for item in checkpoint_events)
    assert all("graph_step" in item["payload"] for item in checkpoint_events)
    input_checkpoint = next(
        item for item in checkpoint_events if item["node_id"] == "__input__"
    )
    assert input_checkpoint["payload"]["execution_id"] == started["execution_id"]
    assert input_checkpoint["payload"]["branch_id"] == started["branch_id"]


def test_state_view_is_exact_and_excludes_raw_checkpoint_state() -> None:
    graph, _ = _graph(create_biodiversity_checkpointer())
    manager = BiodiversityRunManager(graph)
    manager.start(
        "Plan a two-hour expedition from SW11 4NJ on 15 June 2026 "
        "to look for Common woodpigeon.",
        thread_id="state-view",
    )
    final = manager.history(thread_id="state-view")[0]
    view = manager.state_view(
        thread_id="state-view",
        node_id=final.node_id,
        graph_step=final.graph_step,
        checkpoint_id=final.checkpoint_id,
    )

    assert view.node_id == "grounding_and_safety_checks"
    assert view.plan.final_status == "candidate_plan_ready"
    assert view.evidence.safe_map_cell_count > 0
    all_views = manager.state_views(thread_id="state-view")
    assert len(all_views) == len(manager.history(thread_id="state-view"))
    assert "evidence_tools" in {item.node_id for item in all_views}
    serialised = view.model_dump_json().casefold()
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
        "structured_evidence_log",
        "decimallatitude",
        "decimallongitude",
        "occurrenceid",
        "record_ref",
        "hmac",
    ):
        assert forbidden not in serialised

    with pytest.raises(ValueError, match="do not match"):
        manager.state_view(
            thread_id="state-view",
            node_id="resolve_taxon",
            graph_step=final.graph_step,
            checkpoint_id=final.checkpoint_id,
        )


def test_multiple_pending_branches_require_an_exact_resume_target() -> None:
    graph, _ = _graph(create_biodiversity_checkpointer(), _draft("Common swift", 7))
    manager = BiodiversityRunManager(graph)
    manager.start("swift", thread_id="multi-pending")
    source = manager.history(thread_id="multi-pending")[0].checkpoint_id
    first = manager.fork(
        ForkRequest(
            thread_id="multi-pending",
            checkpoint_id=source,
            updates={"duration_hours": 3},
        )
    )
    second = manager.fork(
        ForkRequest(
            thread_id="multi-pending",
            checkpoint_id=source,
            updates={"duration_hours": 4},
        )
    )
    assert first.interrupt_kind == second.interrupt_kind == "actionable_tradeoff"
    with pytest.raises(ValueError, match="multiple pending executions"):
        manager.resume(
            thread_id="multi-pending",
            resume={"option": "keep_constraints_accept_low_confidence"},
        )
    completed = manager.resume(
        thread_id="multi-pending",
        checkpoint_id=first.head_checkpoint_id,
        resume={"option": "keep_constraints_accept_low_confidence"},
    )
    assert completed["expedition_request"]["duration_hours"] == 3
    assert completed["terminal_status"] == "completed"
    still_pending = manager.execution_head(
        thread_id="multi-pending", execution_id=second.execution_id
    )
    assert _interrupt_kind(still_pending) == "actionable_tradeoff"


def _interrupt_kind(snapshot) -> str | None:
    for task in snapshot.tasks:
        if task.interrupts:
            return task.interrupts[0].value.get("kind")
    return None


def test_noop_fork_is_rejected_in_favour_of_replay() -> None:
    graph, _ = _graph(create_biodiversity_checkpointer())
    manager = BiodiversityRunManager(graph)
    manager.start(
        "Plan a two-hour expedition from SW11 4NJ on 15 June 2026 "
        "to look for Common woodpigeon.",
        thread_id="noop-fork",
    )
    source = manager.history(thread_id="noop-fork")[0].checkpoint_id
    with pytest.raises(ValueError, match="use replay"):
        manager.fork(
            ForkRequest(
                thread_id="noop-fork",
                checkpoint_id=source,
                updates={"search_radius_km": 5},
            )
        )


def test_fixture_scripted_phase3_needs_no_openai_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    graph, _ = _graph(create_biodiversity_checkpointer(), _draft("Common swift", 7))
    result = graph.invoke(
        {"original_request_text": "swift"},
        {"configurable": {"thread_id": "no-key"}},
    )
    assert _interrupt(result)["kind"] == "actionable_tradeoff"
