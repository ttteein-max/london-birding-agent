"""Reconnect-safe Server-Sent Events over the Phase 3 event broker."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import Request

from app.biodiversity.api.services.operations import Phase4Application


def encode_event(event: object) -> str:
    sequence = getattr(event, "sequence")
    event_type = getattr(event, "event_type")
    payload = event.model_dump_json()
    return f"id: {sequence}\nevent: {event_type}\ndata: {payload}\n\n"


async def operation_event_stream(
    application: Phase4Application,
    *,
    operation_id: str,
    after_sequence: int,
    request: Request | None = None,
) -> AsyncIterator[str]:
    """Replay retained events, wait for new events, and stop at operation terminal."""

    sequence = after_sequence
    while True:
        if request is not None and await request.is_disconnected():
            return
        batch = await asyncio.to_thread(
            application.event_batch,
            operation_id=operation_id,
            after_sequence=sequence,
            wait=True,
        )
        if batch.events:
            for event in batch.events:
                if event.sequence <= sequence:
                    continue
                yield encode_event(event)
                sequence = event.sequence
        elif not batch.terminal:
            yield ": heartbeat\n\n"
        if batch.terminal and sequence >= batch.latest_sequence:
            return
