"""Application-owned event schemas independent of LangGraph's wire format."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AgentRunEventType = Literal[
    "run_started",
    "model_started",
    "tool_requested",
    "tool_started",
    "tool_completed",
    "tool_failed",
    "node_completed",
    "run_completed",
    "run_failed",
]


class AgentRunEvent(BaseModel):
    """Stable event contract that Phase 2 can serialize as SSE data."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    event_type: AgentRunEventType
    node_id: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    timestamp: datetime
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Keep timestamps unambiguous across future browser clients."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value
