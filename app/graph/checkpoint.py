"""Checkpoint factories with explicit serialization boundaries."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


def create_in_memory_checkpointer() -> InMemorySaver:
    """Create the Phase 1 in-memory saver and allow its state schema types."""

    serializer = JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("app.schemas", "IncidentClassification"),
            ("app.schemas", "EvidenceItem"),
            ("app.schemas", "ProposedAction"),
        ]
    )
    return InMemorySaver(serde=serializer)
