"""Offline scripted models; manual tool calls are allowed only in this fixture."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain.messages import AIMessage
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.tools import BaseTool
from pydantic import Field

from app.schemas import FinalInvestigation, ProposedAction


class ScriptedToolCallingModel(FakeMessagesListChatModel):
    """Cycle through predefined real message objects without network access."""

    bound_tool_names: list[str] = Field(default_factory=list)

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> "ScriptedToolCallingModel":
        del tool_choice, kwargs
        self.bound_tool_names = [
            tool.name if isinstance(tool, BaseTool) else str(tool) for tool in tools
        ]
        return self


@dataclass
class ScriptedFinalizerModel:
    """Return one predefined structured finalization without an API call."""

    response: FinalInvestigation
    invocations: int = 0

    def with_structured_output(
        self,
        schema: type[FinalInvestigation],
        **kwargs: Any,
    ) -> "ScriptedFinalizerModel":
        del kwargs
        if schema is not FinalInvestigation:
            raise TypeError("Scripted finalizer only supports FinalInvestigation")
        return self

    def invoke(self, messages: Any) -> FinalInvestigation:
        del messages
        self.invocations += 1
        return self.response.model_copy(deep=True)


def scripted_tool_call(
    name: str,
    arguments: dict[str, Any],
    call_id: str,
) -> AIMessage:
    """Construct a test-only AI tool call selected by the scripted fixture."""

    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": arguments,
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def scripted_stop(content: str = "Evidence collection is complete.") -> AIMessage:
    """Construct a test-only model response with no more tool calls."""

    return AIMessage(content=content)


def make_scripted_demo_models() -> tuple[
    ScriptedToolCallingModel,
    ScriptedFinalizerModel,
]:
    """Create the repeat-loop scripted CLI demonstration."""

    investigator = ScriptedToolCallingModel(
        responses=[
            scripted_tool_call(
                "query_service_metrics",
                {"service": "checkout", "time_window": "15m"},
                "scripted-metrics-1",
            ),
            scripted_tool_call(
                "list_recent_deployments",
                {"service": "checkout", "since": "2026-08-23T07:00:00Z"},
                "scripted-deployments-2",
            ),
            scripted_tool_call(
                "search_service_logs",
                {
                    "service": "checkout",
                    "query": "connection refused after deployment",
                    "since": "2026-08-24T06:45:00Z",
                },
                "scripted-logs-3",
            ),
            scripted_stop(),
        ]
    )
    finalizer = ScriptedFinalizerModel(
        FinalInvestigation(
            diagnosis=(
                "checkout-v42 likely introduced dependency connection failures "
                "that align with the metrics regression."
            ),
            diagnosis_confidence=0.91,
            investigation_status="complete",
            proposed_action=ProposedAction(
                tool_name="rollback_deployment",
                arguments={
                    "service": "checkout",
                    "target_version": "checkout-v41",
                },
                risk_level="high",
                rationale=(
                    "Metrics, logs, and deployment evidence support proposing a "
                    "rollback to the previous recorded version."
                ),
            ),
        )
    )
    return investigator, finalizer
