"""Run the Phase 2 London biodiversity LangGraph in the terminal."""

from __future__ import annotations

import argparse
import json
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain.messages import AIMessage
from langgraph.types import Command

from app.agents.model_factory import create_live_chat_model
from app.biodiversity.graph import (
    DEFAULT_BIODIVERSITY_CHECKPOINT_PATH,
    build_biodiversity_graph,
    open_biodiversity_sqlite_checkpointer,
)
from app.biodiversity.orchestration import BackendDependencies
from app.biodiversity.observability import AgentRunRecorder
from app.biodiversity.reporting import (
    default_report_directory,
    save_biodiversity_run_report,
)
from app.biodiversity.testing import make_scripted_biodiversity_models


PROJECT_ROOT = Path(__file__).parents[1]


def _update_fragments(update: Any) -> list[dict[str, Any]]:
    """Normalise single and parallel ToolNode stream updates for display."""

    if isinstance(update, dict):
        return [update]
    if isinstance(update, list):
        return [fragment for fragment in update if isinstance(fragment, dict)]
    return []


def _print_updates(graph: Any, graph_input: Any, config: dict[str, Any]) -> None:
    for event in graph.stream(
        graph_input,
        config,
        stream_mode="updates",
        version="v2",
    ):
        data = event.get("data", {})
        for node_name, update in data.items():
            if node_name == "__interrupt__":
                continue
            print(f"NODE {node_name}")
            for fragment in _update_fragments(update):
                for message in fragment.get("messages", []):
                    if isinstance(message, AIMessage):
                        for call in message.tool_calls:
                            print(f"TOOL CALL {call['name']} args={json.dumps(call['args'], sort_keys=True)}")
                for field in ("occurrence_evidence", "weather_evidence", "public_site_search"):
                    value = fragment.get(field)
                    if value:
                        print(f"EVIDENCE {field} status={value.get('outcome') or value.get('status')}")
                for error in fragment.get("grounding_errors", []):
                    print(f"GROUNDING ERROR {error}")


def _automatic_resume(payload: dict[str, Any]) -> dict[str, Any]:
    kind = payload["kind"]
    if kind == "taxon_selection":
        return {"accepted_taxon_key": payload["candidates"][0]["accepted_taxon_key"]}
    if kind == "actionable_tradeoff":
        options = {item["option"]: item for item in payload["options"]}
        if "expand_search_radius" in options:
            option = options["expand_search_radius"]
            radius = min(5.0, option["maximum_radius_km"])
            if radius <= option["current_radius_km"]:
                radius = min(option["maximum_radius_km"], option["current_radius_km"] + 1.0)
            return {"option": "expand_search_radius", "search_radius_km": radius}
        for name in (
            "keep_constraints_accept_low_confidence",
            "accept_context_only",
            "accept_uncertain_access",
            "continue_with_weather_acknowledgement",
        ):
            if name in options:
                return {"option": name}
    if kind == "related_taxon_selection":
        return {"accepted_taxon_key": payload["candidates"][0]["accepted_taxon_key"]}
    raise RuntimeError(f"No safe automatic response is available for {kind}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="Natural English expedition request.")
    parser.add_argument("--data-mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--model-mode", choices=("scripted", "live"), default="scripted")
    parser.add_argument(
        "--thread-id",
        help="Stable checkpoint thread; defaults to a unique CLI thread.",
    )
    parser.add_argument(
        "--checkpoint-db",
        type=Path,
        default=DEFAULT_BIODIVERSITY_CHECKPOINT_PATH,
        help="Durable local SQLite checkpoint database.",
    )
    parser.add_argument("--resume-json", help="JSON resume object for the first interrupt.")
    parser.add_argument("--auto-resume", action="store_true", help="Use the safe scripted demonstration choice at each interrupt.")
    parser.add_argument("--compact", action="store_true", help="Print a compact final plan summary after node progress.")
    parser.add_argument(
        "--report-dir",
        type=Path,
        help="Exact directory for saved metadata, events, timings, tool audit, and final plan.",
    )
    parser.add_argument(
        "--no-save-report",
        action="store_true",
        help="Do not save the default local run report under reports/runs.",
    )
    args = parser.parse_args()
    args.thread_id = args.thread_id or f"biodiversity-cli-{uuid4().hex[:8]}"

    stack = ExitStack()
    checkpointer = stack.enter_context(
        open_biodiversity_sqlite_checkpointer(args.checkpoint_db)
    )
    dependencies = BackendDependencies.fixture() if args.data_mode == "fixture" else BackendDependencies.live()
    if args.model_mode == "scripted":
        parser_model, evidence_model, composer_model = make_scripted_biodiversity_models()
        graph = build_biodiversity_graph(
            parser_model=parser_model,
            evidence_model=evidence_model,
            composer_model=composer_model,
            dependencies=dependencies,
            checkpointer=checkpointer,
        )
    else:
        graph = build_biodiversity_graph(
            create_live_chat_model(),
            dependencies=dependencies,
            checkpointer=checkpointer,
        )
    recorder = AgentRunRecorder(thread_id=args.thread_id)
    config = {
        "configurable": {"thread_id": args.thread_id},
        "callbacks": [recorder],
    }
    if list(graph.get_state_history(config)):
        stack.close()
        raise ValueError(
            "thread_id already exists in the checkpoint database; use "
            "scripts.manage_biodiversity_runs resume or choose a new thread_id"
        )
    graph_input: Any = {
        "original_request_text": args.request,
        "branch_id": uuid4().hex,
        "parent_branch_id": None,
        "forked_from_checkpoint_id": None,
        "fork_updates": {},
        "fork_created_at": None,
    }
    supplied_resume = json.loads(args.resume_json) if args.resume_json else None
    result: dict[str, Any] = {}
    try:
        while True:
            _print_updates(graph, graph_input, config)
            snapshot = graph.get_state(config)
            interrupts = [interrupt for task in snapshot.tasks for interrupt in task.interrupts]
            if not interrupts:
                result = dict(snapshot.values)
                break
            payload = interrupts[0].value
            print("INTERRUPT")
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            if supplied_resume is not None:
                resume = supplied_resume
                supplied_resume = None
            elif args.auto_resume:
                resume = _automatic_resume(payload)
            else:
                resume = json.loads(input("Resume JSON: "))
            print(f"RESUME {json.dumps(resume, ensure_ascii=False)}")
            graph_input = Command(resume=resume)
    except Exception as error:
        recorder.finish(
            "failed",
            payload={"error_type": type(error).__name__},
        )
        if not args.no_save_report:
            report_dir = args.report_dir or default_report_directory(
                PROJECT_ROOT / "reports" / "runs",
                thread_id=args.thread_id,
                run_id=recorder.run_id,
            )
            save_biodiversity_run_report(
                report_dir,
                recorder=recorder,
                result=result,
                request=args.request,
                data_mode=args.data_mode,
                model_mode=args.model_mode,
            )
            print(f"RUN REPORT {report_dir.resolve()}")
        raise
    finally:
        stack.close()

    recorder.finish(
        "completed",
        payload={"terminal_status": result.get("terminal_status")},
    )

    final_plan = result.get("final_validated_plan")
    if args.compact and final_plan:
        final_plan = {
            "status": final_plan["status"],
            "generated_by": final_plan["generated_by"],
            "recommended_site_ids": [item["site_id"] for item in final_plan["recommended_sites"]],
            "contextual_site_ids": [item["site_id"] for item in final_plan["contextual_sites"]],
        }
    summary = {
        "terminal_status": result.get("terminal_status"),
        "location_status": (result.get("resolved_location") or {}).get("status"),
        "taxon_status": (result.get("resolved_taxon") or {}).get("status"),
        "occurrence_status": (result.get("occurrence_evidence") or {}).get("outcome"),
        "weather_status": (result.get("weather_evidence") or {}).get("status"),
        "site_search_status": (result.get("public_site_search") or {}).get("status"),
        "tool_audit": result.get("executed_tool_call_audit", []),
        "final_plan": final_plan,
        "terminal_result": result.get("terminal_result"),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if not args.no_save_report:
        report_dir = args.report_dir or default_report_directory(
            PROJECT_ROOT / "reports" / "runs",
            thread_id=args.thread_id,
            run_id=recorder.run_id,
        )
        save_biodiversity_run_report(
            report_dir,
            recorder=recorder,
            result=result,
            request=args.request,
            data_mode=args.data_mode,
            model_mode=args.model_mode,
            checkpoint_id=str(
                snapshot.config.get("configurable", {}).get("checkpoint_id") or ""
            )
            or None,
        )
        print(f"RUN REPORT {report_dir.resolve()}")


if __name__ == "__main__":
    main()
