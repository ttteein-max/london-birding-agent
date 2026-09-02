"""Manage durable biodiversity HITL runs, checkpoints, replay, and forks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.agents.model_factory import create_live_chat_model
from app.biodiversity.graph import (
    DEFAULT_BIODIVERSITY_CHECKPOINT_PATH,
    build_biodiversity_graph,
    open_biodiversity_sqlite_checkpointer,
)
from app.biodiversity.observability import AgentRunRecorder
from app.biodiversity.orchestration import BackendDependencies
from app.biodiversity.reporting import (
    default_report_directory,
    save_biodiversity_run_report,
)
from app.biodiversity.run_models import ForkRequest
from app.biodiversity.runs import BiodiversityRunManager
from app.biodiversity.testing import make_scripted_biodiversity_models


PROJECT_ROOT = Path(__file__).parents[1]


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--thread-id", required=True)
    parser.add_argument(
        "--checkpoint-db",
        type=Path,
        default=DEFAULT_BIODIVERSITY_CHECKPOINT_PATH,
    )
    parser.add_argument("--data-mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument(
        "--model-mode", choices=("scripted", "live"), default="scripted"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    _add_common(start)
    start.add_argument("--request", required=True)
    start.add_argument("--report-dir", type=Path)

    resume = subparsers.add_parser("resume")
    _add_common(resume)
    resume.add_argument("--resume-json", required=True)
    resume.add_argument("--report-dir", type=Path)

    history = subparsers.add_parser("history")
    _add_common(history)

    replay = subparsers.add_parser("replay")
    _add_common(replay)
    replay.add_argument("--checkpoint-id", required=True)
    replay.add_argument("--report-dir", type=Path)

    fork = subparsers.add_parser("fork")
    _add_common(fork)
    fork.add_argument("--checkpoint-id", required=True)
    fork.add_argument("--updates-json", required=True)
    fork.add_argument("--branch-label")
    fork.add_argument("--report-dir", type=Path)

    compare = subparsers.add_parser("compare")
    _add_common(compare)
    compare.add_argument("--checkpoint-a", required=True)
    compare.add_argument("--checkpoint-b", required=True)
    return parser


def _build_graph(args: argparse.Namespace, checkpointer: Any) -> Any:
    dependencies = (
        BackendDependencies.fixture()
        if args.data_mode == "fixture"
        else BackendDependencies.live()
    )
    if args.model_mode == "scripted":
        parser_model, evidence_model, composer_model = (
            make_scripted_biodiversity_models()
        )
        return build_biodiversity_graph(
            parser_model=parser_model,
            evidence_model=evidence_model,
            composer_model=composer_model,
            dependencies=dependencies,
            checkpointer=checkpointer,
        )
    return build_biodiversity_graph(
        create_live_chat_model(),
        dependencies=dependencies,
        checkpointer=checkpointer,
    )


def _checkpoint_id(snapshot: Any) -> str:
    return str(snapshot.config["configurable"]["checkpoint_id"])


def _safe_state(snapshot: Any) -> dict[str, Any]:
    values = snapshot.values
    interrupt_payload = None
    for task in snapshot.tasks:
        if task.interrupts:
            interrupt_payload = task.interrupts[0].value
            break
    return {
        "thread_id": snapshot.config["configurable"]["thread_id"],
        "checkpoint_id": _checkpoint_id(snapshot),
        "branch_id": values.get("branch_id"),
        "parent_branch_id": values.get("parent_branch_id"),
        "forked_from_checkpoint_id": values.get("forked_from_checkpoint_id"),
        "terminal_status": values.get("terminal_status"),
        "interrupt": interrupt_payload,
        "final_plan": values.get("final_validated_plan"),
    }


def _save_report(
    args: argparse.Namespace,
    recorder: AgentRunRecorder,
    snapshot: Any,
) -> Path:
    report_dir = args.report_dir or default_report_directory(
        PROJECT_ROOT / "reports" / "runs",
        thread_id=args.thread_id,
        run_id=recorder.run_id,
    )
    values = dict(snapshot.values)
    save_biodiversity_run_report(
        report_dir,
        recorder=recorder,
        result=values,
        request=values.get("original_request_text"),
        data_mode=args.data_mode,
        model_mode=args.model_mode,
        checkpoint_id=_checkpoint_id(snapshot),
    )
    return report_dir


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    recorder = AgentRunRecorder(thread_id=args.thread_id)
    try:
        with open_biodiversity_sqlite_checkpointer(args.checkpoint_db) as checkpointer:
            graph = _build_graph(args, checkpointer)
            manager = BiodiversityRunManager(graph, recorder=recorder)
            if args.command == "history":
                output: Any = [
                    item.model_dump(mode="json")
                    for item in manager.history(thread_id=args.thread_id)
                ]
                recorder.finish("completed")
                print(json.dumps(output, ensure_ascii=False, indent=2))
                return
            if args.command == "compare":
                output = manager.compare(
                    thread_id=args.thread_id,
                    checkpoint_a=args.checkpoint_a,
                    checkpoint_b=args.checkpoint_b,
                ).model_dump(mode="json")
                recorder.finish("completed")
                print(json.dumps(output, ensure_ascii=False, indent=2))
                return
            if args.command == "start":
                manager.start(args.request, thread_id=args.thread_id)
                extra: dict[str, Any] = {}
            elif args.command == "resume":
                manager.resume(
                    thread_id=args.thread_id,
                    resume=json.loads(args.resume_json),
                )
                extra = {}
            elif args.command == "replay":
                manager.replay(
                    thread_id=args.thread_id,
                    checkpoint_id=args.checkpoint_id,
                )
                extra = {"replayed_from_checkpoint_id": args.checkpoint_id}
            else:
                fork_request = ForkRequest(
                    thread_id=args.thread_id,
                    checkpoint_id=args.checkpoint_id,
                    updates=json.loads(args.updates_json),
                    branch_label=args.branch_label,
                )
                fork_result = manager.fork(fork_request)
                extra = {"fork": fork_result.model_dump(mode="json")}
            snapshot = graph.get_state({"configurable": {"thread_id": args.thread_id}})
            recorder.finish(
                "completed",
                payload={"terminal_status": snapshot.values.get("terminal_status")},
            )
            report_dir = _save_report(args, recorder, snapshot)
            output = {**_safe_state(snapshot), **extra, "report_dir": str(report_dir)}
            print(json.dumps(output, ensure_ascii=False, indent=2))
    except (ValueError, json.JSONDecodeError) as exc:
        recorder.finish("failed", payload={"error_type": type(exc).__name__})
        parser.error(str(exc))


if __name__ == "__main__":
    main()
