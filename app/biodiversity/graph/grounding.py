"""Compact plan inputs, deterministic grounding checks, and safe fallback."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.biodiversity.agent_models import (
    BiodiversityExpeditionPlan,
    PlanConstraint,
    PlanSiteOption,
    PlanWeatherContext,
)
from app.biodiversity.models import (
    ConstraintStatus,
    ExpeditionEvidenceBundle,
    ExpeditionPlan,
    ExpeditionPlanStatus,
    SiteEvidenceTier,
)


def _site_view(site: Any) -> dict[str, Any]:
    return {
        "site_id": site.site_id,
        "name": site.name,
        "access_certainty": site.access_certainty.value,
        "approximate_straight_line_distance_km": site.approximate_straight_line_distance_km,
        "evidence_tier": site.evidence_tier.value,
    }


def london_start_context(bundle: ExpeditionEvidenceBundle) -> str:
    location = bundle.location
    if location.normalised_postcode:
        district = f", {location.administrative_district}" if location.administrative_district else ""
        return f"Postcode centroid {location.normalised_postcode}{district}, within Greater London."
    return "A user-selected, generalised planning point within Greater London."


def provenance_references(bundle: ExpeditionEvidenceBundle) -> list[str]:
    references: list[str] = []
    for item in bundle.provenance:
        reference = item.source_reference
        if not reference:
            continue
        parts = urlsplit(reference)
        if parts.scheme in {"http", "https"}:
            # Query strings can contain a postcode centroid used for weather lookup.
            # The stable source endpoint is sufficient provenance for model output.
            reference = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        references.append(reference)
    return list(dict.fromkeys(references))


def evidence_attributions(bundle: ExpeditionEvidenceBundle) -> list[str]:
    return list(dict.fromkeys(item.attribution for item in bundle.provenance))


def required_evidence_citations(bundle: ExpeditionEvidenceBundle) -> list[str]:
    citations = ["location", "taxonomy"]
    if bundle.occurrence is not None:
        citations.append("occurrence")
    citations.append("public_sites")
    if bundle.weather is not None:
        citations.append("weather")
    return citations


def weather_plan_context(bundle: ExpeditionEvidenceBundle) -> dict[str, Any] | None:
    weather = bundle.weather
    if weather is None:
        return None
    explanation = (
        "Exact-date weather context is available; it does not indicate the chance of seeing the bird."
        if weather.status.value == "available"
        else "Exact-date weather is unavailable and no different date has been substituted."
    )
    return {
        "status": weather.status.value,
        "requested_date": weather.requested_date.isoformat(),
        "maximum_temperature_c": weather.maximum_temperature_c,
        "minimum_temperature_c": weather.minimum_temperature_c,
        "precipitation_probability_percent": weather.precipitation_probability_percent,
        "precipitation_amount_mm": weather.precipitation_amount_mm,
        "explanation": explanation,
    }


def compact_plan_payload(
    bundle: ExpeditionEvidenceBundle,
    phase1_plan: ExpeditionPlan,
) -> dict[str, Any]:
    """Return only validated coordinate-free facts needed by the composer."""

    return {
        "status": phase1_plan.status.value,
        "target_species": phase1_plan.target_bird,
        "target_date": phase1_plan.target_date.isoformat(),
        "duration_hours": bundle.request.duration_hours,
        "resolved_london_start_context": london_start_context(bundle),
        "directly_grounded_candidates": [
            _site_view(site) for site in bundle.candidate_sites
        ],
        "contextual_sites_non_recommended": [
            _site_view(site) for site in bundle.contextual_sites
        ],
        "weather_context": weather_plan_context(bundle),
        "constraints": [item.model_dump(mode="json") for item in bundle.constraints],
        "unresolved_limitations": bundle.safety_and_scientific_limitations,
        "suggested_next_actions": [
            action.value for action in phase1_plan.suggested_actions
        ],
        "provenance_references": provenance_references(bundle),
        "evidence_attributions": evidence_attributions(bundle),
        "required_evidence_citations": required_evidence_citations(bundle),
        "phase1_evidence_explanation": phase1_plan.evidence_explanation,
    }


def _positive_unsupported_claims(explanation: str) -> list[str]:
    text = explanation.casefold()
    checks = {
        "sighting guarantee or certainty": [
            r"\bguaranteed? (?:bird )?sightings?\b",
            r"\byou will (?:see|spot|find)\b",
            r"\bcertain(?:ly)? (?:see|spot|find|sighting)\b",
        ],
        "abundance or population claim": [
            r"\babundant\b",
            r"\bhigh population\b",
            r"\blarge population\b",
            r"\b(?:records?|evidence) (?:show|shows|indicate|indicates|prove|proves).{0,50}\b(?:abundance|population)\b",
            r"\bpopulation (?:is|of|equals|numbers?)\b",
            r"\babundance (?:is|of|equals)\b",
        ],
        "prediction or hotspot claim": [
            r"\b(?:is|are|known as|reliable|guaranteed) (?:a )?hotspots?\b",
            r"\bpredicts? (?:a )?sightings?\b",
            r"\blikely to (?:see|spot|find)\b",
        ],
        "weather used as sighting probability": [
            r"\bsighting probability\b",
            r"\bchance of (?:seeing|spotting|finding).{0,40}(?:weather|rain|forecast)\b",
            r"\b(?:weather|rain|forecast).{0,40}chance of (?:seeing|spotting|finding)\b",
        ],
        "unsupported access assurance": [
            r"\bconfirmed public access\b",
            r"\baccess is guaranteed\b",
            r"\bguaranteed access\b",
            r"\bdefinitely open\b",
        ],
        "walking-distance or time claim": [
            r"\bwalking distance (?:is|of)\b",
            r"\b\d+(?:\.\d+)?\s*(?:km|kilometres?|minutes?|hours?)\s+(?:walk|walking)\b",
            r"\bwalk(?:ing)? time\b",
        ],
    }
    return [label for label, patterns in checks.items() if any(re.search(pattern, text) for pattern in patterns)]


def validate_grounded_plan(
    bundle: ExpeditionEvidenceBundle,
    phase1_plan: ExpeditionPlan,
    draft: BiodiversityExpeditionPlan,
) -> list[str]:
    """Reject any LLM output that disagrees with deterministic evidence."""

    errors: list[str] = []
    if draft.status != phase1_plan.status:
        errors.append("Plan status disagrees with deterministic Phase 1 status.")
    if draft.target_species != phase1_plan.target_bird:
        errors.append("Target species was altered.")
    if draft.target_date != bundle.request.target_local_date:
        errors.append("Target date was altered.")
    if draft.duration_hours != bundle.request.duration_hours:
        errors.append("Duration was altered.")
    if draft.resolved_london_start_context != london_start_context(bundle):
        errors.append("Resolved London start context was altered or invented.")

    candidates = {site.site_id: site for site in bundle.candidate_sites}
    contextual = {site.site_id: site for site in bundle.contextual_sites}
    recommendation_ids = [site.site_id for site in draft.recommended_sites]
    contextual_ids = [site.site_id for site in draft.contextual_sites]
    if any(site_id not in candidates for site_id in recommendation_ids):
        errors.append("A recommended site ID is not a deterministic candidate.")
    if set(recommendation_ids).intersection(contextual):
        errors.append("A contextual site was promoted to a recommendation.")
    if phase1_plan.status == ExpeditionPlanStatus.candidate_plan_ready and not recommendation_ids:
        errors.append("A candidate-ready plan omitted every grounded candidate.")
    if phase1_plan.status != ExpeditionPlanStatus.candidate_plan_ready and recommendation_ids:
        errors.append("A non-candidate-ready plan contains recommendations.")
    if any(site_id not in contextual for site_id in contextual_ids):
        errors.append("A contextual site ID is not present in deterministic contextual evidence.")

    for plan_site in [*draft.recommended_sites, *draft.contextual_sites]:
        source = candidates.get(plan_site.site_id) or contextual.get(plan_site.site_id)
        if source is None:
            continue
        expected = _site_view(source)
        if plan_site.model_dump(mode="json") != expected:
            errors.append(f"Site facts were altered for {plan_site.site_id}.")

    expected_weather = weather_plan_context(bundle)
    actual_weather = draft.weather_context.model_dump(mode="json") if draft.weather_context else None
    if actual_weather != expected_weather:
        errors.append("Weather status or quantitative weather values were altered.")

    expected_constraints = [
        {
            "code": item.code,
            "status": item.status.value,
            "severity": item.severity.value,
            "message": item.message,
        }
        for item in bundle.constraints
    ]
    if [item.model_dump(mode="json") for item in draft.constraints] != expected_constraints:
        errors.append("Required deterministic constraints were omitted or altered.")
    if draft.unresolved_limitations != bundle.safety_and_scientific_limitations:
        errors.append("Required safety or scientific limitations were omitted or altered.")
    if draft.suggested_next_actions != phase1_plan.suggested_actions:
        errors.append("Suggested actions were omitted, reordered, or invented.")
    if draft.provenance_references != provenance_references(bundle):
        errors.append("Provenance references were omitted, reordered, or invented.")
    if draft.evidence_attributions != evidence_attributions(bundle):
        errors.append("Required evidence attribution was omitted, reordered, or invented.")
    if draft.evidence_citations != required_evidence_citations(bundle):
        errors.append("Evidence citations refer to missing evidence or omit required evidence.")

    # Every structured field above is compared exactly with trusted deterministic
    # input. The explanation is the only free-form model output and therefore the
    # only place where a record identifier or precise coordinate can be invented.
    explanation = draft.explanation.casefold()
    sensitive_value_patterns = [
        r"\b(?:decimallatitude|decimallongitude|occurrenceid|occurrence_id|gbifid|record_ref)\s*[:=]\s*[\"']?[^\s,;\]}]+",
        r"\bhmac(?:\s+(?:reference|ref))?\s*(?::|=|\s)\s*(?:secret|[a-f0-9]{8,})\b",
    ]
    if any(re.search(pattern, explanation) for pattern in sensitive_value_patterns):
        errors.append("The draft exposes an occurrence coordinate or record identifier.")
    if re.search(r"\bbng-1km-\d", explanation):
        errors.append("The draft exposes or invents safe-cell associations.")
    if re.search(
        r"\b(?:associated_safe_cell_ids?|safe[- ]cell (?:id|association))\s*[:=]",
        explanation,
    ):
        errors.append("The draft exposes or invents safe-cell associations.")
    if re.search(r"\b(?:latitude|longitude)\s*[:=]\s*-?\d+\.\d+", explanation):
        errors.append("The draft contains precise labelled coordinates.")
    for label in _positive_unsupported_claims(draft.explanation):
        errors.append(f"The explanation contains an unsupported {label}.")
    return list(dict.fromkeys(errors))


def deterministic_safe_plan(
    bundle: ExpeditionEvidenceBundle,
    phase1_plan: ExpeditionPlan,
) -> BiodiversityExpeditionPlan:
    """Assemble a model-independent plan that is safe by construction."""

    status_explanation = phase1_plan.evidence_explanation
    return BiodiversityExpeditionPlan(
        status=phase1_plan.status,
        target_species=phase1_plan.target_bird,
        target_date=phase1_plan.target_date,
        duration_hours=bundle.request.duration_hours,
        resolved_london_start_context=london_start_context(bundle),
        recommended_sites=[PlanSiteOption.model_validate(_site_view(site)) for site in bundle.candidate_sites],
        contextual_sites=[PlanSiteOption.model_validate(_site_view(site)) for site in bundle.contextual_sites],
        weather_context=(
            PlanWeatherContext.model_validate(weather_plan_context(bundle))
            if bundle.weather is not None
            else None
        ),
        constraints=[
            PlanConstraint(
                code=item.code,
                status=item.status,
                severity=item.severity,
                message=item.message,
            )
            for item in bundle.constraints
        ],
        unresolved_limitations=bundle.safety_and_scientific_limitations,
        suggested_next_actions=phase1_plan.suggested_actions,
        provenance_references=provenance_references(bundle),
        evidence_attributions=evidence_attributions(bundle),
        evidence_citations=required_evidence_citations(bundle),
        explanation=(
            f"{status_explanation} Candidate distances are approximate straight-line "
            "projected distances, not walking distances. Access and opening must be checked independently."
        ),
        generated_by="deterministic_phase_2_fallback",
    )
