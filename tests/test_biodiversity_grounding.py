"""Adversarial deterministic grounding and fallback tests."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.biodiversity.agent_models import ExpeditionRequestDraft, PlanSiteOption
from app.biodiversity.graph import build_biodiversity_graph
from app.biodiversity.graph.grounding import provenance_references
from app.biodiversity.models import ExpeditionPlanStatus
from app.biodiversity.testing import (
    ScriptedEvidenceModel,
    ScriptedPlanComposerModel,
    ScriptedRequestParserModel,
    scripted_tool_call,
)
from app.biodiversity.testing.scripted_models import plan_from_compact_payload
from langchain.messages import AIMessage


def evidence() -> ScriptedEvidenceModel:
    return ScriptedEvidenceModel(
        responses=[
            scripted_tool_call("search_occurrences", "ground-occ"),
            scripted_tool_call("get_weather_context", "ground-weather"),
            scripted_tool_call("find_public_green_spaces", "ground-sites"),
            AIMessage(content="Complete."),
        ]
    )


def run_with_responses(responses: list):
    graph = build_biodiversity_graph(
        parser_model=ScriptedRequestParserModel(
            ExpeditionRequestDraft(
                bird_input="Common woodpigeon",
                postcode="SW11 4NJ",
                target_local_date=date(2026, 6, 15),
                duration_hours=2,
            )
        ),
        evidence_model=evidence(),
        composer_model=ScriptedPlanComposerModel(responses),
    )
    return graph.invoke({"original_request_text": "Grounding test"})


def invented_site(payload):
    plan = plan_from_compact_payload(payload)
    fake = PlanSiteOption(
        site_id="invented-site",
        name="Invented Park",
        access_certainty="explicit_public",
        approximate_straight_line_distance_km=1,
        evidence_tier="directly_grounded",
    )
    return plan.model_copy(update={"recommended_sites": [fake]})


def promoted_contextual_site(payload):
    plan = plan_from_compact_payload(payload)
    source = payload["contextual_sites_non_recommended"][0]
    fake = PlanSiteOption(
        **{**source, "evidence_tier": "directly_grounded"}
    )
    return plan.model_copy(update={"recommended_sites": [fake]})


@pytest.mark.parametrize(
    "claim",
    [
        "This site has confirmed public access.",
        "The walking distance is 1 km.",
        "The bird is abundant here.",
        "This evidence predicts a sighting.",
        "The weather gives a sighting probability of 80%.",
        "You will see the bird here.",
    ],
)
def test_grounding_rejects_access_walking_abundance_prediction_and_guarantees(
    claim: str,
) -> None:
    def unsafe(payload):
        plan = plan_from_compact_payload(payload)
        return plan.model_copy(update={"explanation": claim})

    result = run_with_responses([unsafe])
    assert result["plan_revision_count"] == 1
    assert result["terminal_status"] == "completed"
    assert result["final_validated_plan"]["generated_by"] == "llm_revision"


@pytest.mark.parametrize(
    "leak",
    [
        "decimalLatitude=51.50001",
        "occurrenceID=secret-record",
        "record_ref=secret",
        "HMAC reference secret",
        "associated_safe_cell_ids=['invented-cell']",
    ],
)
def test_grounding_rejects_precise_coordinate_or_identifier_leakage(leak: str) -> None:
    def unsafe(payload):
        plan = plan_from_compact_payload(payload)
        return plan.model_copy(update={"explanation": leak})

    result = run_with_responses([unsafe])
    assert result["plan_revision_count"] == 1
    assert leak not in result["final_validated_plan"]["explanation"]


def test_grounding_allows_privacy_disclaimer_without_sensitive_values() -> None:
    def disclaimer(payload):
        plan = plan_from_compact_payload(payload)
        return plan.model_copy(
            update={
                "explanation": (
                    f"{plan.explanation} No HMAC references, record_ref values or "
                    "occurrenceID values are included."
                )
            }
        )

    result = run_with_responses([disclaimer])
    assert result["plan_revision_count"] == 0
    assert result["terminal_status"] == "completed"
    assert result["final_validated_plan"]["generated_by"] == "llm_composer"


def test_grounding_rejects_legacy_generator_label_and_deduplicates_limitations() -> None:
    def legacy_label(payload):
        plan = plan_from_compact_payload(payload)
        return plan.model_copy(update={"generated_by": "llm_phase_2_composer"})

    result = run_with_responses([legacy_label])
    plan = result["final_validated_plan"]
    assert result["plan_revision_count"] == 1
    assert plan["generated_by"] == "llm_revision"
    assert sum("sighting" in item.casefold() for item in plan["unresolved_limitations"]) == 1
    assert sum("population" in item.casefold() for item in plan["unresolved_limitations"]) == 1


def test_model_facing_provenance_strips_coordinate_query_parameters() -> None:
    bundle = SimpleNamespace(
        provenance=[
            SimpleNamespace(
                source_reference=(
                    "https://api.open-meteo.com/v1/forecast?"
                    "latitude=51.4704&longitude=-0.1663&start_date=2026-09-01"
                )
            )
        ]
    )
    assert provenance_references(bundle) == [
        "https://api.open-meteo.com/v1/forecast"
    ]


@pytest.mark.parametrize("unsafe", [invented_site, promoted_contextual_site])
def test_grounding_rejects_invented_site_and_contextual_promotion(unsafe) -> None:
    result = run_with_responses([unsafe])
    assert result["plan_revision_count"] == 1
    ids = {item["site_id"] for item in result["final_validated_plan"]["recommended_sites"]}
    assert "invented-site" not in ids
    assert result["terminal_status"] == "completed"


def test_failed_revision_uses_deterministic_safe_fallback() -> None:
    result = run_with_responses([invented_site, invented_site])
    assert result["plan_revision_count"] == 1
    assert result["terminal_status"] == "completed_with_deterministic_fallback"
    assert result["final_validated_plan"]["generated_by"] == "deterministic_fallback"
    assert result["final_validated_plan"]["recommended_sites"]


@pytest.mark.parametrize("field", ["status", "evidence_citations"])
def test_grounding_rejects_status_disagreement_and_missing_citations(field: str) -> None:
    def unsafe(payload):
        plan = plan_from_compact_payload(payload)
        update = (
            {"status": ExpeditionPlanStatus.cannot_recommend_sites}
            if field == "status"
            else {"evidence_citations": []}
        )
        return plan.model_copy(update=update)

    result = run_with_responses([unsafe])
    assert result["plan_revision_count"] == 1
    assert result["terminal_status"] == "completed"
    assert result["final_validated_plan"][field] != (
        "cannot_recommend_sites" if field == "status" else []
    )
