"""Manage durable biodiversity HITL runs, checkpoints, replay, and forks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.biodiversity.model_factory import create_live_chat_model
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
from app.biodiversity.run_models import ForkRequest, RunManifest, RunProfile
from app.biodiversity.runs import BiodiversityRunManager
from app.biodiversity.testing import make_scripted_biodiversity_models


PROJECT_ROOT = Path(__file__).parents[1]


def _add_common(
    parser: argparse.ArgumentParser, *, runtime_defaults: bool = False
) -> None:
    parser.add_argument("--thread-id", required=True)
    parser.add_argument(
        "--checkpoint-db",
        type=Path,
        default=DEFAULT_BIODIVERSITY_CHECKPOINT_PATH,
    )
    parser.add_argument(
        "--data-mode",
        choices=("fixture", "live"),
        default="fixture" if runtime_defaults else None,
    )
    parser.add_argument(
        "--model-mode",
        choices=("scripted", "live"),
        default="scripted" if runtime_defaults else None,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    _add_common(start, runtime_defaults=True)
    start.add_argument("--request", required=True)
    start.add_argument("--report-dir", type=Path)

    resume = subparsers.add_parser("resume")
    _add_common(resume)
    resume.add_argument("--resume-json", required=True)
    resume_target = resume.add_mutually_exclusive_group()
    resume_target.add_argument("--checkpoint-id")
    resume_target.add_argument("--branch-id")
    resume.add_argument("--report-dir", type=Path)

    history = subparsers.add_parser("history")
    _add_common(history)

    state_view = subparsers.add_parser("state-view")
    _add_common(state_view)
    state_view.add_argument("--node-id", required=True)
    state_view.add_argument("--graph-step", required=True, type=int)
    state_view.add_argument("--checkpoint-id", required=True)

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


def _build_graph(
    *, data_mode: str, model_mode: str, checkpointer: Any
) -> Any:
    dependencies = (
        BackendDependencies.fixture()
        if data_mode == "fixture"
        else BackendDependencies.live()
    )
    if model_mode == "scripted":
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


def _run_profile(
    data_mode: str,
    model_mode: str,
    *,
    expected_model_identifier: str | None = None,
) -> RunProfile:
    if model_mode == "scripted":
        model_identifier = "scripted-biodiversity-v1"
        endpoint_fingerprint = "local-scripted"
    else:
        model_identifier = os.getenv("OPENAI_MODEL")
        if not model_identifier:
            raise RuntimeError("OPENAI_MODEL is required for live mode")
        endpoint = os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        endpoint_fingerprint = hashlib.sha256(endpoint.encode()).hexdigest()[:16]
    if (
        expected_model_identifier
        and model_identifier != expected_model_identifier
    ):
        raise ValueError(
            "OPENAI_MODEL does not match the model saved in the run manifest"
        )
    return RunProfile(
        data_mode=data_mode,
        model_mode=model_mode,
        model_identifier=model_identifier,
        endpoint_fingerprint=endpoint_fingerprint,
    )


def _resolve_execution_profile(
    args: argparse.Namespace, manifest: RunManifest | None
) -> RunProfile:
    if manifest is None:
        if args.data_mode is None or args.model_mode is None:
            raise ValueError(
                "This legacy thread has no run manifest; supply both --data-mode "
                "and --model-mode explicitly"
            )
        return _run_profile(args.data_mode, args.model_mode)
    if args.data_mode is not None and args.data_mode != manifest.data_mode:
        raise ValueError("--data-mode does not match the saved run manifest")
    if args.model_mode is not None and args.model_mode != manifest.model_mode:
        raise ValueError("--model-mode does not match the saved run manifest")
    return _run_profile(
        manifest.data_mode,
        manifest.model_mode,
        expected_model_identifier=manifest.model_identifier,
    )


def _checkpoint_id(snapshot: Any) -> str:
    return str(snapshot.config["configurable"]["checkpoint_id"])


def _safe_state(snapshot: Any) -> dict[str, Any]:
    values = snapshot.values
    metadata = dict(snapshot.metadata or {})
    interrupt_payload = None
    for task in snapshot.tasks:
        if task.interrupts:
            interrupt_payload = task.interrupts[0].value
            break
    return {
        "thread_id": snapshot.config["configurable"]["thread_id"],
        "checkpoint_id": _checkpoint_id(snapshot),
        "branch_id": values.get("branch_id"),
        "execution_id": metadata.get("execution_id")
        or values.get("execution_id"),
        "parent_execution_id": metadata.get("parent_execution_id")
        or values.get("parent_execution_id"),
        "replayed_from_checkpoint_id": metadata.get(
            "replayed_from_checkpoint_id"
        )
        or values.get("replayed_from_checkpoint_id"),
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
    profile: RunProfile,
) -> Path:
    report_dir = args.report_dir or default_report_directory(
        PROJECT_ROOT / "reports" / "runs",
        thread_id=args.thread_id,
        run_id=recorder.run_id,
    )
    values = dict(snapshot.values)
    metadata = dict(snapshot.metadata or {})
    save_biodiversity_run_report(
        report_dir,
        recorder=recorder,
        result=values,
        request=values.get("original_request_text"),
        data_mode=profile.data_mode,
        model_mode=profile.model_mode,
        checkpoint_id=_checkpoint_id(snapshot),
        execution_id=metadata.get("execution_id")
        or values.get("execution_id"),
        parent_execution_id=metadata.get("parent_execution_id")
        or values.get("parent_execution_id"),
        replayed_from_checkpoint_id=metadata.get(
            "replayed_from_checkpoint_id"
        )
        or values.get("replayed_from_checkpoint_id"),
        run_manifest=values.get("run_manifest"),
    )
    return report_dir


def _save_failure_report(
    args: argparse.Namespace,
    recorder: AgentRunRecorder,
    profile: RunProfile,
    snapshot: Any | None,
) -> Path:
    if snapshot is not None:
        return _save_report(args, recorder, snapshot, profile)
    report_dir = args.report_dir or default_report_directory(
        PROJECT_ROOT / "reports" / "runs",
        thread_id=args.thread_id,
        run_id=recorder.run_id,
    )
    save_biodiversity_run_report(
        report_dir,
        recorder=recorder,
        result={},
        request=getattr(args, "request", None),
        data_mode=profile.data_mode,
        model_mode=profile.model_mode,
        run_manifest=None,
    )
    return report_dir


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    recorder = AgentRunRecorder(thread_id=args.thread_id)
    profile: RunProfile | None = None
    snapshot: Any | None = None
    try:
        with open_biodiversity_sqlite_checkpointer(args.checkpoint_db) as checkpointer:
            reader_graph = _build_graph(
                data_mode="fixture",
                model_mode="scripted",
                checkpointer=checkpointer,
            )
            reader = BiodiversityRunManager(reader_graph, recorder=recorder)
            if args.command == "history":
                output: Any = [
                    item.model_dump(mode="json")
                    for item in reader.history(thread_id=args.thread_id)
                ]
                recorder.finish("completed")
                print(json.dumps(output, ensure_ascii=False, indent=2))
                return
            if args.command == "state-view":
                output = reader.state_view(
                    thread_id=args.thread_id,
                    node_id=args.node_id,
                    graph_step=args.graph_step,
                    checkpoint_id=args.checkpoint_id,
                ).model_dump(mode="json")
                recorder.finish("completed")
                print(json.dumps(output, ensure_ascii=False, indent=2))
                return
            if args.command == "compare":
                output = reader.compare(
                    thread_id=args.thread_id,
                    checkpoint_a=args.checkpoint_a,
                    checkpoint_b=args.checkpoint_b,
                ).model_dump(mode="json")
                recorder.finish("completed")
                print(json.dumps(output, ensure_ascii=False, indent=2))
                return
            if args.command == "start":
                profile = _run_profile(args.data_mode, args.model_mode)
                graph = (
                    reader_graph
                    if profile.data_mode == "fixture"
                    and profile.model_mode == "scripted"
                    else _build_graph(
                        data_mode=profile.data_mode,
                        model_mode=profile.model_mode,
                        checkpointer=checkpointer,
                    )
                )
            else:
                manifest = reader.manifest(thread_id=args.thread_id)
                profile = _resolve_execution_profile(args, manifest)
                graph = (
                    reader_graph
                    if profile.data_mode == "fixture"
                    and profile.model_mode == "scripted"
                    else _build_graph(
                        data_mode=profile.data_mode,
                        model_mode=profile.model_mode,
                        checkpointer=checkpointer,
                    )
                )
            manager = BiodiversityRunManager(
                graph,
                recorder=recorder,
                run_profile=profile,
            )
            if args.command == "start":
                result = manager.start(args.request, thread_id=args.thread_id)
                snapshot = manager.execution_head(
                    thread_id=args.thread_id,
                    execution_id=result["execution_id"],
                )
                extra: dict[str, Any] = {}
            elif args.command == "resume":
                result = manager.resume(
                    thread_id=args.thread_id,
                    resume=json.loads(args.resume_json),
                    checkpoint_id=args.checkpoint_id,
                    branch_id=args.branch_id,
                )
                snapshot = manager.execution_head(
                    thread_id=args.thread_id,
                    execution_id=result["execution_id"],
                )
                extra = {}
            elif args.command == "replay":
                replay_result = manager.replay(
                    thread_id=args.thread_id,
                    checkpoint_id=args.checkpoint_id,
                )
                snapshot = manager.snapshot(
                    thread_id=args.thread_id,
                    checkpoint_id=replay_result.head_checkpoint_id,
                )
                extra = {"replay": replay_result.model_dump(mode="json")}
            else:
                fork_request = ForkRequest(
                    thread_id=args.thread_id,
                    checkpoint_id=args.checkpoint_id,
                    updates=json.loads(args.updates_json),
                    branch_label=args.branch_label,
                )
                fork_result = manager.fork(fork_request)
                snapshot = manager.snapshot(
                    thread_id=args.thread_id,
                    checkpoint_id=(
                        fork_result.final_checkpoint_id
                        or fork_result.head_checkpoint_id
                    ),
                )
                extra = {"fork": fork_result.model_dump(mode="json")}
            if snapshot is None:
                raise RuntimeError("Run operation did not produce a checkpoint")
            recorder.finish(
                "completed",
                payload={"terminal_status": snapshot.values.get("terminal_status")},
            )
            report_dir = _save_report(args, recorder, snapshot, profile)
            output = {**_safe_state(snapshot), **extra, "report_dir": str(report_dir)}
            print(json.dumps(output, ensure_ascii=False, indent=2))
    except Exception as exc:
        recorder.finish("failed", payload={"error_type": type(exc).__name__})
        report_suffix = ""
        if args.command in {"start", "resume", "replay", "fork"} and profile:
            report_dir = _save_failure_report(
                args, recorder, profile, snapshot
            )
            report_suffix = f"; failure report: {report_dir}"
        parser.exit(
            2,
            f"{parser.prog}: error: {exc}{report_suffix}\n",
        )


if __name__ == "__main__":
    main()
