"""Generate a safe, offline HITL and time-travel demonstration."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from app.biodiversity.agent_models import ExpeditionRequestDraft
from app.biodiversity.graph import (
    build_biodiversity_graph,
    create_biodiversity_checkpointer,
)
from app.biodiversity.observability import AgentRunRecorder
from app.biodiversity.orchestration import BackendDependencies
from app.biodiversity.run_models import ForkRequest
from app.biodiversity.runs import BiodiversityRunManager
from app.biodiversity.testing import (
    ScriptedRequestParserModel,
    make_scripted_biodiversity_models,
)


PROJECT_ROOT = Path(__file__).parents[1]
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "reports/runs/fixture-scripted-hitl-time-travel"
)
THREAD_ID = "phase3-fixture-scripted-demo"
REQUEST = (
    "Plan a two-hour expedition from SW11 4NJ on 15 January 2026 "
    "to look for robin."
)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _interrupt(result: dict[str, Any]) -> dict[str, Any]:
    return dict(result["__interrupt__"][0].value)


def generate(output: Path) -> dict[str, Any]:
    parser, evidence, composer = make_scripted_biodiversity_models()
    parser = ScriptedRequestParserModel(
        ExpeditionRequestDraft(
            bird_input="robin",
            postcode="SW11 4NJ",
            target_local_date=date(2026, 1, 15),
            duration_hours=2,
        )
    )
    graph = build_biodiversity_graph(
        parser_model=parser,
        evidence_model=evidence,
        composer_model=composer,
        dependencies=BackendDependencies.fixture(),
        checkpointer=create_biodiversity_checkpointer(),
    )
    recorder = AgentRunRecorder(
        run_id="phase3-fixture-scripted-demo",
        thread_id=THREAD_ID,
    )
    manager = BiodiversityRunManager(graph, recorder=recorder)

    started = manager.start(REQUEST, thread_id=THREAD_ID)
    taxon_interrupt = _interrupt(started)
    taxon_checkpoint = manager.execution_head(
        thread_id=THREAD_ID,
        execution_id=started["execution_id"],
    )
    selected_taxon_key = int(
        taxon_interrupt["candidates"][0]["accepted_taxon_key"]
    )
    after_taxon = manager.resume(
        thread_id=THREAD_ID,
        checkpoint_id=taxon_checkpoint.config["configurable"]["checkpoint_id"],
        resume={"accepted_taxon_key": selected_taxon_key},
    )
    low_evidence_interrupt = _interrupt(after_taxon)
    original_completed = manager.resume(
        thread_id=THREAD_ID,
        resume={"option": "keep_constraints_accept_low_confidence"},
    )
    original_execution_id = str(original_completed["execution_id"])
    original_head = manager.execution_head(
        thread_id=THREAD_ID,
        execution_id=original_execution_id,
    )
    original_final_checkpoint_id = str(
        original_head.config["configurable"]["checkpoint_id"]
    )

    replay_source = next(
        item
        for item in manager.history(thread_id=THREAD_ID)
        if item.execution_id == original_execution_id
        and item.next_nodes == ["compose_expedition_plan"]
    )
    replay = manager.replay(
        thread_id=THREAD_ID,
        checkpoint_id=replay_source.checkpoint_id,
    )

    fork = manager.fork(
        ForkRequest(
            thread_id=THREAD_ID,
            checkpoint_id=original_final_checkpoint_id,
            updates={"search_radius_km": 8},
            branch_label="wider public-site context",
        )
    )
    fork_resumed = False
    if fork.interrupt_kind:
        manager.resume(
            thread_id=THREAD_ID,
            checkpoint_id=fork.head_checkpoint_id,
            resume={"option": "keep_constraints_accept_low_confidence"},
        )
        fork_resumed = True
    fork_head = manager.execution_head(
        thread_id=THREAD_ID,
        execution_id=fork.execution_id,
    )
    fork_final_checkpoint_id = str(
        fork_head.config["configurable"]["checkpoint_id"]
    )
    if not fork_head.values.get("final_validated_plan"):
        raise RuntimeError("Demo fork did not produce a final plan")
    comparison = manager.compare(
        thread_id=THREAD_ID,
        checkpoint_a=original_final_checkpoint_id,
        checkpoint_b=fork_final_checkpoint_id,
    )

    history = manager.history(thread_id=THREAD_ID)
    state_views = manager.state_views(thread_id=THREAD_ID)
    executions = manager.executions(thread_id=THREAD_ID)
    branches = manager.branches(thread_id=THREAD_ID)
    recorder.finish("completed")

    output.mkdir(parents=True, exist_ok=True)
    recorder.save(output)
    _write_json(
        output / "history.json",
        [item.model_dump(mode="json") for item in history],
    )
    _write_json(
        output / "state-views.json",
        [item.model_dump(mode="json") for item in state_views],
    )
    _write_json(
        output / "executions.json",
        [item.model_dump(mode="json") for item in executions],
    )
    _write_json(
        output / "branches.json",
        [item.model_dump(mode="json") for item in branches],
    )
    _write_json(
        output / "comparison.json",
        comparison.model_dump(mode="json"),
    )
    _write_json(output / "original-final-plan.json", original_completed["final_validated_plan"])
    _write_json(output / "fork-final-plan.json", dict(fork_head.values)["final_validated_plan"])

    checkpoint_events = [
        event
        for event in recorder.events
        if event.event_type == "checkpoint_created"
    ]
    summary = {
        "schema_version": 1,
        "mode": {"data": "fixture", "model": "scripted"},
        "thread_id": THREAD_ID,
        "run_id": recorder.run_id,
        "stages": [
            {
                "operation": "start",
                "interrupt_kind": taxon_interrupt["kind"],
                "selected_taxon_key": selected_taxon_key,
            },
            {
                "operation": "resume_taxon",
                "interrupt_kind": low_evidence_interrupt["kind"],
            },
            {
                "operation": "resume_low_confidence",
                "checkpoint_id": original_final_checkpoint_id,
            },
            {
                "operation": "replay",
                **replay.model_dump(mode="json"),
            },
            {
                "operation": "fork",
                **fork.model_dump(mode="json"),
                "resumed_after_interrupt": fork_resumed,
                "result_checkpoint_id": fork_final_checkpoint_id,
            },
            {
                "operation": "compare",
                "changed_fields": comparison.changed_fields,
            },
        ],
        "checkpoint_event_count": len(checkpoint_events),
        "checkpoint_history_count": len(history),
        "checkpoint_events_cover_history": {
            event.payload["checkpoint_id"] for event in checkpoint_events
        }
        == {item.checkpoint_id for item in history},
        "state_view_count": len(state_views),
        "branch_count": len(branches),
        "execution_count": len(executions),
        "privacy": {
            "raw_langgraph_state_saved": False,
            "occurrence_coordinates_saved": False,
            "occurrence_identifiers_saved": False,
            "safe_cell_associations_saved": False,
        },
    }
    _write_json(output / "demo-summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    summary = generate(args.output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"PHASE 3 DEMO {args.output.resolve()}")


if __name__ == "__main__":
    main()
