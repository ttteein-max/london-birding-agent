"""LangGraph state and workflow construction."""

from app.graph.checkpoint import create_in_memory_checkpointer
from app.graph.state import IncidentState

__all__ = ["IncidentState", "build_agent_graph", "create_in_memory_checkpointer"]


def build_agent_graph(
    investigator_model,
    finalizer_model,
    checkpointer=None,
    *,
    max_agent_steps=6,
):
    """Import lazily to keep the state module independently importable."""

    from app.graph.workflow import build_agent_graph as _build_agent_graph

    return _build_agent_graph(
        investigator_model,
        finalizer_model,
        checkpointer,
        max_agent_steps=max_agent_steps,
    )
