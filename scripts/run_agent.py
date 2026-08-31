"""Run the Phase 1.2 agent in explicit scripted or live mode."""

import argparse
import logging
import os

from app.agents import create_live_models
from app.graph import create_in_memory_checkpointer
from app.graph.workflow import build_agent_graph
from app.testing import make_scripted_demo_models
from scripts.cli_utils import print_json, state_history_as_tree


def _models(mode: str):
    if mode == "scripted":
        print("MODE: scripted fixture (no API call; tool choices are predefined)")
        return make_scripted_demo_models()

    model = os.getenv("OPENAI_MODEL", "<missing>")
    base_url = os.getenv("OPENAI_BASE_URL", "OpenAI default endpoint")
    print(f"MODE: live model={model} base_url={base_url}")
    return create_live_models()


def main(*, default_mode: str = "scripted") -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "incident",
        nargs="?",
        default="checkout failures increased after a deployment",
    )
    parser.add_argument(
        "--mode",
        choices=("scripted", "live"),
        default=default_mode,
    )
    parser.add_argument("--thread-id", default="phase12-demo")
    parser.add_argument("--max-agent-steps", type=int, default=6)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    investigator_model, finalizer_model = _models(args.mode)
    memory = create_in_memory_checkpointer()
    graph = build_agent_graph(
        investigator_model,
        finalizer_model,
        memory,
        max_agent_steps=args.max_agent_steps,
    )
    config = {"configurable": {"thread_id": args.thread_id}}

    print("\n=== STREAMING V2 UPDATES ===")
    for event in graph.stream(
        {"incident_description": args.incident},
        config,
        stream_mode="updates",
        version="v2",
    ):
        print_json(event)

    final_snapshot = graph.get_state(config)
    print("\n=== FINAL RESULT ===")
    print_json(final_snapshot.values)

    print("\n=== CHECKPOINTER STATE TREE ===")
    print_json(state_history_as_tree(graph, config))

    restored_graph = build_agent_graph(
        investigator_model,
        finalizer_model,
        memory,
        max_agent_steps=args.max_agent_steps,
    )
    restored_snapshot = restored_graph.get_state(config)
    print("\n=== RESTORED STATE (NEW GRAPH, SAME CHECKPOINTER) ===")
    print_json(restored_snapshot.values)


if __name__ == "__main__":
    main()
