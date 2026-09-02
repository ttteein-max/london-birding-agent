"""Credential-free scripted chat-model paths for Phase 3."""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from typing import Any, Sequence

from langchain.messages import AIMessage, HumanMessage
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.tools import BaseTool
from pydantic import Field

from app.biodiversity.agent_models import (
    BiodiversityExpeditionPlan,
    ExpeditionRequestDraft,
    PlanConstraint,
    PlanSiteOption,
    PlanWeatherContext,
)


NUMBER_WORDS = {
    "one": 1.0,
    "two": 2.0,
    "three": 3.0,
    "four": 4.0,
    "five": 5.0,
    "six": 6.0,
    "seven": 7.0,
    "eight": 8.0,
}


def _request_text(messages: Any) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content).split("Untrusted expedition request data:\n", 1)[-1]
    return ""


def _parse_date(text: str):
    iso = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if iso:
        return datetime.strptime(iso.group(1), "%Y-%m-%d").date()
    long_date = re.search(
        r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b",
        text,
        re.IGNORECASE,
    )
    if long_date:
        return datetime.strptime(" ".join(long_date.groups()), "%d %B %Y").date()
    return None


def parse_scripted_request(text: str) -> ExpeditionRequestDraft:
    """Parse demonstration grammar without filling absent required values."""

    postcode_match = re.search(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", text, re.IGNORECASE)
    bird_match = re.search(
        r"(?:look(?:ing)? for|find|observe|see)\s+(.+?)(?:[.;]|\s+(?:within|with a|and avoid|for a)\b|$)",
        text,
        re.IGNORECASE,
    )
    duration_match = re.search(r"\b(\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight)[ -]hours?\b", text, re.IGNORECASE)
    duration = None
    if duration_match:
        token = duration_match.group(1).casefold()
        duration = NUMBER_WORDS.get(token, float(token) if token.replace(".", "", 1).isdigit() else None)
    radius_match = re.search(r"(?:search radius(?: of)?|within)\s+(\d+(?:\.\d+)?)\s*km\b", text, re.IGNORECASE)
    walk_match = re.search(r"(?:maximum|max(?:imum)? walking distance(?: of)?|walk no more than)\s+(\d+(?:\.\d+)?)\s*km\b", text, re.IGNORECASE)
    month_match = re.search(r"target month(?: override)?(?: of| is)?\s+(\d{1,2})\b", text, re.IGNORECASE)
    point_match = re.search(r"(?:point|coordinates?)\s*\(?\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*\)?", text, re.IGNORECASE)
    return ExpeditionRequestDraft(
        bird_input=bird_match.group(1).strip() if bird_match else None,
        postcode=postcode_match.group(1).upper() if postcode_match else None,
        start_point={"longitude": float(point_match.group(1)), "latitude": float(point_match.group(2))} if point_match else None,
        target_local_date=_parse_date(text),
        duration_hours=duration,
        maximum_walking_distance_km=float(walk_match.group(1)) if walk_match else None,
        rain_preference="avoid_heavy_rain" if re.search(r"avoid (?:heavy )?rain", text, re.IGNORECASE) else None,
        target_month_override=int(month_match.group(1)) if month_match else None,
        search_radius_km=float(radius_match.group(1)) if radius_match else None,
    )


class ScriptedRequestParserModel:
    def __init__(self, response: ExpeditionRequestDraft | dict[str, Any] | None = None) -> None:
        self.response = response
        self.invocations = 0
        self.last_messages: Any = None

    def with_structured_output(self, schema: type[Any], **kwargs: Any) -> "ScriptedRequestParserModel":
        del kwargs
        if schema is not ExpeditionRequestDraft:
            raise TypeError("Scripted parser only supports ExpeditionRequestDraft")
        return self

    def invoke(self, messages: Any) -> ExpeditionRequestDraft:
        self.invocations += 1
        self.last_messages = messages
        if self.response is None:
            return parse_scripted_request(_request_text(messages))
        return ExpeditionRequestDraft.model_validate(copy.deepcopy(self.response))


class ScriptedEvidenceModel(FakeMessagesListChatModel):
    bound_tool_names: list[str] = Field(default_factory=list)

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> "ScriptedEvidenceModel":
        del tool_choice, kwargs
        self.bound_tool_names = [tool.name if isinstance(tool, BaseTool) else str(tool) for tool in tools]
        return self


def scripted_tool_call(name: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {}, "id": call_id, "type": "tool_call"}],
    )


def _payload_from_messages(messages: Any) -> tuple[dict[str, Any], bool]:
    content = next(str(message.content) for message in reversed(messages) if isinstance(message, HumanMessage))
    payload = json.loads(content)
    is_revision = "validated_evidence" in payload
    return (payload["validated_evidence"] if is_revision else payload), is_revision


def plan_from_compact_payload(payload: dict[str, Any], *, revision: bool = False) -> BiodiversityExpeditionPlan:
    constraints = [
        PlanConstraint(
            code=item["code"],
            status=item["status"],
            severity=item["severity"],
            message=item["message"],
        )
        for item in payload["constraints"]
    ]
    weather = PlanWeatherContext.model_validate(payload["weather_context"]) if payload.get("weather_context") else None
    return BiodiversityExpeditionPlan(
        status=payload["status"],
        target_species=payload["target_species"],
        target_date=payload["target_date"],
        duration_hours=payload["duration_hours"],
        resolved_london_start_context=payload["resolved_london_start_context"],
        recommended_sites=[PlanSiteOption.model_validate(item) for item in payload["directly_grounded_candidates"]],
        contextual_sites=[PlanSiteOption.model_validate(item) for item in payload["contextual_sites_non_recommended"]],
        weather_context=weather,
        constraints=constraints,
        unresolved_limitations=payload["unresolved_limitations"],
        suggested_next_actions=payload["suggested_next_actions"],
        provenance_references=payload["provenance_references"],
        evidence_attributions=payload["evidence_attributions"],
        evidence_citations=payload["required_evidence_citations"],
        evidence_gate_passed=payload["evidence_gate_passed"],
        low_confidence_accepted=payload["low_confidence_accepted"],
        low_confidence_notice=payload["low_confidence_notice"],
        explanation=(
            f"{payload['phase1_evidence_explanation']} Candidate distances are approximate straight-line projected distances, not walking distances. "
            "Contextual sites are not recommendations, and access and opening must be checked independently."
        ),
        generated_by="llm_phase_2_revision" if revision else "llm_phase_2_composer",
    )


class ScriptedPlanComposerModel:
    """Build a valid plan from compact input or return queued adversarial drafts."""

    def __init__(self, responses: list[Any] | None = None) -> None:
        self.responses = list(responses or [])
        self.invocations = 0
        self.last_messages: Any = None

    def with_structured_output(self, schema: type[Any], **kwargs: Any) -> "ScriptedPlanComposerModel":
        del kwargs
        if schema is not BiodiversityExpeditionPlan:
            raise TypeError("Scripted composer only supports BiodiversityExpeditionPlan")
        return self

    def invoke(self, messages: Any) -> Any:
        self.invocations += 1
        self.last_messages = messages
        payload, revision = _payload_from_messages(messages)
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            if callable(response):
                return response(payload)
            return copy.deepcopy(response)
        return plan_from_compact_payload(payload, revision=revision)


def make_scripted_biodiversity_models() -> tuple[
    ScriptedRequestParserModel,
    ScriptedEvidenceModel,
    ScriptedPlanComposerModel,
]:
    parser = ScriptedRequestParserModel()
    evidence = ScriptedEvidenceModel(
        responses=[
            scripted_tool_call("search_occurrences", "scripted-occurrence-1"),
            scripted_tool_call("get_weather_context", "scripted-weather-2"),
            scripted_tool_call("find_public_green_spaces", "scripted-sites-3"),
            AIMessage(content="The useful evidence is complete."),
            scripted_tool_call("find_public_green_spaces", "scripted-sites-rerun-4"),
            AIMessage(content="The updated site evidence is complete."),
        ]
    )
    return parser, evidence, ScriptedPlanComposerModel()
