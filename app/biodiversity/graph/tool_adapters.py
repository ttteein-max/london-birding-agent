"""Thin ToolNode adapters over the authoritative Phase 1 deterministic services."""

import json
from typing import Any

from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime, tool
from langgraph.types import Command

from app.biodiversity.models import (
    ExpeditionRequest,
    OccurrenceEvidence,
    ResolvedLocation,
    ResolvedTaxon,
)
from app.biodiversity.orchestration import BackendDependencies
from app.biodiversity.tools import (
    find_public_green_spaces as phase1_find_public_green_spaces,
    get_weather_context as phase1_get_weather_context,
    search_occurrences as phase1_search_occurrences,
)


def _prior_audit(state: dict[str, Any], name: str, canonical_arguments: str) -> dict[str, Any] | None:
    matches = [
        item
        for item in state.get("executed_tool_call_audit", [])
        if item.get("tool_name") == name
        and item.get("canonical_arguments") == canonical_arguments
        and item.get("status") != "suppressed_duplicate"
    ]
    return matches[-1] if matches else None


def _tool_command(
    *,
    runtime: ToolRuntime,
    tool_name: str,
    result_kind: str | None,
    result: dict[str, Any] | None,
    summary: dict[str, Any],
    retryable: bool = False,
    error: dict[str, Any] | None = None,
    status: str = "success",
    canonical_arguments: str = "{}",
) -> Command:
    if runtime.tool_call_id is None:
        raise RuntimeError(f"{tool_name} must be executed by LangGraph ToolNode")
    call_id = runtime.tool_call_id
    pending = {
        "call_id": call_id,
        "tool_name": tool_name,
        "canonical_arguments": canonical_arguments,
        "status": status,
        "retryable": retryable,
        "result_kind": result_kind,
        "result": result,
        "summary": summary,
        "error": error,
    }
    return Command(
        update={
            "pending_tool_results": [pending],
            "messages": [
                ToolMessage(
                    content=json.dumps(summary, ensure_ascii=False, sort_keys=True),
                    tool_call_id=call_id,
                    name=tool_name,
                    status="error" if status != "success" else "success",
                )
            ],
        }
    )


def _duplicate_or_none(runtime: ToolRuntime, tool_name: str, canonical_arguments: str) -> Command | None:
    prior = _prior_audit(runtime.state, tool_name, canonical_arguments)
    if prior is None or prior.get("retryable") is True:
        return None
    return _tool_command(
        runtime=runtime,
        tool_name=tool_name,
        result_kind=None,
        result=None,
        status="suppressed_duplicate",
        summary={
            "status": "suppressed_duplicate",
            "message": "This identical completed evidence call was not executed again.",
        },
        error={
            "tool": tool_name,
            "code": "duplicate_tool_call",
            "message": "Identical non-retryable tool call suppressed.",
            "retryable": False,
        },
        canonical_arguments=canonical_arguments,
    )


def build_evidence_tools(dependencies: BackendDependencies) -> list[Any]:
    """Bind repositories in closures so the model can never supply them."""

    @tool("search_occurrences")
    def search_occurrences_adapter(runtime: ToolRuntime) -> Command:
        """Collect privacy-safe historical occurrence evidence for the resolved taxon and requested month."""

        request_data = runtime.state.get("expedition_request") or {}
        taxon_data = runtime.state.get("resolved_taxon") or {}
        target_month = request_data.get("target_month_override") or str(
            request_data.get("target_local_date", "")
        )[5:7]
        radius = request_data.get("seasonal_window_radius_months", 1)
        try:
            month_number = int(target_month)
            radius_number = int(radius)
            seasonal_months = sorted(
                {
                    ((month_number + offset - 1) % 12) + 1
                    for offset in range(-radius_number, radius_number + 1)
                }
            )
        except (TypeError, ValueError):
            seasonal_months = []
        canonical = json.dumps(
            {
                "accepted_taxon_key": taxon_data.get("accepted_taxon_key"),
                "target_month": target_month,
                "seasonal_window_radius_months": radius,
                "seasonal_months": seasonal_months,
            },
            sort_keys=True,
        )
        duplicate = _duplicate_or_none(runtime, "search_occurrences", canonical)
        if duplicate is not None:
            return duplicate
        if not request_data or not taxon_data:
            return _tool_command(
                runtime=runtime,
                tool_name="search_occurrences",
                result_kind=None,
                result=None,
                status="error",
                summary={"status": "invalid_state", "message": "Resolved request and taxon are required."},
                error={"tool": "search_occurrences", "code": "invalid_state", "message": "Resolved request and taxon are required.", "retryable": False},
                canonical_arguments=canonical,
            )
        request = ExpeditionRequest.model_validate(request_data)
        taxon = ResolvedTaxon.model_validate(taxon_data)
        evidence = phase1_search_occurrences(
            taxon,
            target_month=request.seasonal_target_month,
            seasonal_window_radius_months=request.seasonal_window_radius_months,
            repository=dependencies.occurrences,
        )
        error = evidence.tool_error.model_dump(mode="json") if evidence.tool_error else None
        return _tool_command(
            runtime=runtime,
            tool_name="search_occurrences",
            result_kind="occurrence_evidence",
            result=evidence.model_dump(mode="json"),
            retryable=bool(evidence.tool_error and evidence.tool_error.retryable),
            status="error" if evidence.tool_error else "success",
            error=error,
            summary={
                "outcome": evidence.outcome.value,
                "reason": evidence.reason,
                "seasonal_target_month": evidence.seasonal_target_month,
                "seasonal_months": evidence.seasonal_months,
                "retained_record_count": evidence.counts.retained_total_count,
                "ranking_eligible_count": evidence.counts.ranking_eligible_count,
                "safe_map_cell_count": len(evidence.safe_map_cells),
                "limitations": evidence.limitations,
                "source_error": error,
            },
            canonical_arguments=canonical,
        )

    @tool("get_weather_context")
    def get_weather_context_adapter(runtime: ToolRuntime) -> Command:
        """Collect exact-date London weather context; it is never a bird-sighting probability."""

        request_data = runtime.state.get("expedition_request") or {}
        location_data = runtime.state.get("resolved_location") or {}
        canonical = json.dumps(
            {
                "requested_date": request_data.get("target_local_date"),
                "location": location_data.get("normalised_postcode") or location_data.get("input_kind"),
            },
            sort_keys=True,
        )
        duplicate = _duplicate_or_none(runtime, "get_weather_context", canonical)
        if duplicate is not None:
            return duplicate
        if not request_data or not location_data:
            return _tool_command(
                runtime=runtime,
                tool_name="get_weather_context",
                result_kind=None,
                result=None,
                status="error",
                summary={"status": "invalid_state", "message": "Resolved request and location are required."},
                error={"tool": "get_weather_context", "code": "invalid_state", "message": "Resolved request and location are required.", "retryable": False},
                canonical_arguments=canonical,
            )
        request = ExpeditionRequest.model_validate(request_data)
        location = ResolvedLocation.model_validate(location_data)
        evidence = phase1_get_weather_context(
            location,
            request.target_local_date,
            repository=dependencies.weather,
        )
        error = evidence.tool_error.model_dump(mode="json") if evidence.tool_error else None
        return _tool_command(
            runtime=runtime,
            tool_name="get_weather_context",
            result_kind="weather_evidence",
            result=evidence.model_dump(mode="json"),
            retryable=bool(evidence.tool_error and evidence.tool_error.retryable),
            status="error" if evidence.tool_error else "success",
            error=error,
            summary={
                "status": evidence.status.value,
                "requested_date": evidence.requested_date.isoformat(),
                "maximum_temperature_c": evidence.maximum_temperature_c,
                "minimum_temperature_c": evidence.minimum_temperature_c,
                "precipitation_probability_percent": evidence.precipitation_probability_percent,
                "precipitation_amount_mm": evidence.precipitation_amount_mm,
                "limitations": evidence.limitations,
                "source_error": error,
            },
            canonical_arguments=canonical,
        )

    @tool("find_public_green_spaces")
    def find_public_green_spaces_adapter(runtime: ToolRuntime) -> Command:
        """Find snapshot green spaces using the trusted request radius and deterministic safe-cell gate."""

        request_data = runtime.state.get("expedition_request") or {}
        location_data = runtime.state.get("resolved_location") or {}
        occurrence_data = runtime.state.get("occurrence_evidence") or {}
        canonical = json.dumps(
            {
                "search_radius_km": request_data.get("search_radius_km"),
                "location": location_data.get("normalised_postcode") or location_data.get("input_kind"),
                "occurrence_outcome": occurrence_data.get("outcome"),
                "target_month": occurrence_data.get("seasonal_target_month"),
            },
            sort_keys=True,
        )
        duplicate = _duplicate_or_none(runtime, "find_public_green_spaces", canonical)
        if duplicate is not None:
            return duplicate
        if not request_data or not location_data:
            message = "Resolved request and location are required before site search."
            return _tool_command(
                runtime=runtime,
                tool_name="find_public_green_spaces",
                result_kind=None,
                result=None,
                status="error",
                summary={"status": "invalid_state", "message": message},
                error={"tool": "find_public_green_spaces", "code": "invalid_state", "message": message, "retryable": False},
                canonical_arguments=canonical,
            )
        if not occurrence_data:
            message = "Occurrence evidence is required before grounded site search."
            return _tool_command(
                runtime=runtime,
                tool_name="find_public_green_spaces",
                result_kind=None,
                result=None,
                status="error",
                summary={"status": "occurrence_evidence_required", "message": message},
                error={"tool": "find_public_green_spaces", "code": "occurrence_evidence_required", "message": message, "retryable": False},
                canonical_arguments=canonical,
            )
        request = ExpeditionRequest.model_validate(request_data)
        location = ResolvedLocation.model_validate(location_data)
        occurrence = OccurrenceEvidence.model_validate(occurrence_data)
        result = phase1_find_public_green_spaces(
            location,
            search_radius_km=request.search_radius_km,
            occurrence=occurrence,
            repository=dependencies.green_spaces,
        )
        error = result.error.model_dump(mode="json") if result.error else None
        retryable = bool(result.error and result.error.retryable)
        return _tool_command(
            runtime=runtime,
            tool_name="find_public_green_spaces",
            result_kind="public_site_search",
            result=result.model_dump(mode="json"),
            retryable=retryable,
            status="error" if retryable else "success",
            error=error,
            summary={
                "status": result.status.value,
                "searched_radius_km": result.searched_radius_km,
                "recommended_candidates": [
                    {
                        "site_id": site.site_id,
                        "name": site.name,
                        "access_certainty": site.access_certainty.value,
                        "approximate_straight_line_distance_km": site.approximate_straight_line_distance_km,
                        "evidence_tier": site.evidence_tier.value,
                    }
                    for site in result.candidates
                ],
                "contextual_sites": [
                    {
                        "site_id": site.site_id,
                        "name": site.name,
                        "access_certainty": site.access_certainty.value,
                        "approximate_straight_line_distance_km": site.approximate_straight_line_distance_km,
                        "evidence_tier": site.evidence_tier.value,
                    }
                    for site in result.contextual_sites
                ],
                "suggested_actions": [action.value for action in result.suggested_actions],
                "source_error": error,
            },
            canonical_arguments=canonical,
        )

    return [
        search_occurrences_adapter,
        get_weather_context_adapter,
        find_public_green_spaces_adapter,
    ]
