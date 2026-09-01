"""Checkpoint factory for JSON-safe biodiversity graph state."""

from langgraph.checkpoint.memory import InMemorySaver


def create_biodiversity_checkpointer() -> InMemorySaver:
    """Return an isolated in-memory saver for CLI and offline tests."""

    return InMemorySaver()
