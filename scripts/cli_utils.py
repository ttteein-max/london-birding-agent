"""Shared JSON and checkpoint formatting helpers for local CLI demos."""

import json
from typing import Any


def json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return list(value)
    return str(value)


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=json_default))


def state_history_as_tree(graph, config: dict) -> list[dict]:
    """Return checkpoint history in chronological, JSON-friendly form."""

    history = []
    for snapshot in reversed(list(graph.get_state_history(config))):
        configurable = snapshot.config.get("configurable", {})
        parent = (snapshot.parent_config or {}).get("configurable", {})
        history.append(
            {
                "checkpoint_id": configurable.get("checkpoint_id"),
                "parent_checkpoint_id": parent.get("checkpoint_id"),
                "step": snapshot.metadata.get("step"),
                "source": snapshot.metadata.get("source"),
                "next": list(snapshot.next),
                "values": snapshot.values,
            }
        )
    return history
