"""Lifecycle-safe checkpointers for the biodiversity graph."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver


DEFAULT_BIODIVERSITY_CHECKPOINT_PATH = Path(
    "data/runtime/biodiversity-checkpoints.sqlite"
)


def _strict_serializer() -> JsonPlusSerializer:
    """Allow only built-in safe LangGraph/LangChain values, never pickle."""

    return JsonPlusSerializer(
        pickle_fallback=False,
        allowed_json_modules=None,
        allowed_msgpack_modules=None,
    )


def create_biodiversity_checkpointer() -> InMemorySaver:
    """Return an isolated in-memory saver for fast offline unit tests."""

    return InMemorySaver(serde=_strict_serializer())


@contextmanager
def open_biodiversity_sqlite_checkpointer(
    path: str | Path = DEFAULT_BIODIVERSITY_CHECKPOINT_PATH,
) -> Iterator[SqliteSaver]:
    """Open a durable local saver whose connection stays live in this context.

    The synchronous saver is intentionally scoped to local CLI/demo use. Callers
    must compile and use the graph inside the context; on exit the SQLite
    connection is committed and closed.
    """

    checkpoint_path = Path(path).expanduser()
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(checkpoint_path), check_same_thread=False)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        yield SqliteSaver(connection, serde=_strict_serializer())
        connection.commit()
    finally:
        connection.close()
