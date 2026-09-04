"""Privacy-bounded views assembled from exact persisted checkpoints."""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.biodiversity.api.schemas import (
    ConstraintView,
    EvidenceCountsView,
    EvidenceGateCriterionView,
    EvidenceGateView,
    EvidenceQualityView,
    EvidenceView,
    FinalPlanView,
    GeoJSONGeometry,
    HitlOptionView,
    GeocodedLocationCandidateView,
    MapCellFeature,
    MapCellProperties,
    MapEvidenceView,
    MapSiteFeature,
    MapSiteProperties,
    MapStartContext,
    ParsedRequestDraftView,
    PendingDecisionView,
    PlanSiteView,
    ProvenanceView,
    PublicEntranceView,
    RouteConstraintView,
    RouteGeometryView,
    RouteOptionView,
    RouteOptionsView,
    TaxonCandidateView,
    TaxonEvidencePreviewView,
    ValidatedWalkingPlanView,
    WeatherDayView,
)
from app.biodiversity.repositories import SnapshotGreenSpaceRepository
from app.biodiversity.request_parsing import merge_request_draft
from app.biodiversity.runs import BiodiversityRunManager
from app.feasibility.core import STRONG_EVIDENCE_RULE


MAP_ATTRIBUTIONS = [
    "© OpenStreetMap contributors",
    "GBIF.org and contributing datasets",
    "Open-Meteo",
]
MAP_LIMITATIONS = [
    "Historical evidence does not predict or guarantee a sighting.",
    "Record counts are not abundance or population estimates.",
    "Individual occurrence coordinates and identifiers are never shown.",
    "Candidate-site access and opening are not guaranteed.",
    "Straight-line distance is not a walking route.",
]


def _without_query(value: str | None) -> str | None:
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme in {"http", "https"}:
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return value


def _site_view(value: dict[str, Any]) -> PlanSiteView:
    return PlanSiteView(
        site_id=str(value.get("site_id") or "unknown-site"),
        name=str(value.get("name") or "Unnamed site"),
        access_certainty=str(value.get("access_certainty") or "unspecified"),
        approximate_straight_line_distance_km=float(
            value.get("approximate_straight_line_distance_km") or 0
        ),
        evidence_tier=str(value.get("evidence_tier") or "ungrounded"),
    )


def _constraint_view(value: dict[str, Any]) -> ConstraintView:
    return ConstraintView(
        code=str(value.get("code") or "unknown"),
        status=str(value.get("status") or "unresolved"),
        severity=str(value.get("severity") or "information"),
        message=str(value.get("message") or "No detail is available."),
    )


def _entrance_view(value: dict[str, Any]) -> PublicEntranceView:
    point = dict(value.get("point") or {})
    return PublicEntranceView(
        entrance_id=str(value.get("entrance_id") or "unknown-entrance"),
        site_id=str(value.get("site_id") or "unknown-site"),
        label=str(value.get("label") or "Public-site entrance"),
        access_certainty=str(value.get("access_certainty") or "unspecified"),
        longitude=float(point.get("longitude") or 0),
        latitude=float(point.get("latitude") or 0),
        wheelchair=value.get("wheelchair"),
        opening_hours=value.get("opening_hours"),
        association_method="osm_boundary_member",
        limitations=list(value.get("limitations") or []),
    )


def _route_constraint_view(value: dict[str, Any]) -> RouteConstraintView:
    return RouteConstraintView(
        code=str(value.get("code") or "unknown"),
        passed=bool(value.get("passed")),
        actual_value=value.get("actual_value"),
        limit_value=value.get("limit_value"),
        unit=value.get("unit"),
        message=str(value.get("message") or "No detail is available."),
    )


def _route_option_view(value: dict[str, Any]) -> RouteOptionView:
    route = dict(value.get("route") or {})
    outbound = dict(route.get("outbound") or {})
    return_leg = dict(route.get("return_leg") or {})
    return RouteOptionView(
        option_id=str(value.get("option_id") or "unknown-route-option"),
        status=str(value.get("status") or "no_route"),
        site_id=str(value.get("site_id") or "unknown-site"),
        site_name=str(value.get("site_name") or "Unnamed site"),
        entrance=_entrance_view(dict(value.get("entrance") or {})),
        outbound_distance_km=(
            round(float(outbound["distance_m"]) / 1000, 3)
            if outbound.get("distance_m") is not None
            else None
        ),
        return_distance_km=(
            round(float(return_leg["distance_m"]) / 1000, 3)
            if return_leg.get("distance_m") is not None
            else None
        ),
        total_distance_km=(
            round(float(route["total_distance_m"]) / 1000, 3)
            if route.get("total_distance_m") is not None
            else None
        ),
        walking_duration_minutes=(
            round(float(route["total_duration_seconds"]) / 60, 1)
            if route.get("total_duration_seconds") is not None
            else None
        ),
        feasible=bool(value.get("feasible")),
        provider=(str(route["provider"]) if route.get("provider") else None),
        cache_status=(
            str(route["cache_status"]) if route.get("cache_status") else None
        ),
        route_geometry_reference=route.get("route_geometry_reference"),
        constraint_results=[
            _route_constraint_view(dict(item))
            for item in value.get("constraint_results") or []
        ],
        warnings=list(value.get("warnings") or []),
    )


def _walking_plan_view(value: dict[str, Any]) -> ValidatedWalkingPlanView:
    return ValidatedWalkingPlanView(
        status=str(value.get("status") or "no_route"),
        selected_site_id=value.get("selected_site_id"),
        selected_site_name=value.get("selected_site_name"),
        entrance=(
            _entrance_view(dict(value["entrance"])) if value.get("entrance") else None
        ),
        routing_profile=str(value.get("routing_profile") or "foot-walking"),
        outbound_distance_km=value.get("outbound_distance_km"),
        return_distance_km=value.get("return_distance_km"),
        total_distance_km=value.get("total_distance_km"),
        walking_duration_minutes=value.get("walking_duration_minutes"),
        expedition_duration_minutes=float(
            value.get("expedition_duration_minutes") or 1
        ),
        remaining_field_time_minutes=value.get("remaining_field_time_minutes"),
        ascent_m=value.get("ascent_m"),
        descent_m=value.get("descent_m"),
        elevation_profile=list(value.get("elevation_profile") or []),
        route_geometry_reference=value.get("route_geometry_reference"),
        provider=value.get("provider"),
        provider_version=value.get("provider_version"),
        retrieved_at=value.get("retrieved_at"),
        licence=value.get("licence"),
        attribution=value.get("attribution"),
        cache_status=value.get("cache_status"),
        constraint_results=[
            _route_constraint_view(dict(item))
            for item in value.get("constraint_results") or []
        ],
        alternative_feasible_routes=[
            _route_option_view(dict(item))
            for item in value.get("alternative_feasible_routes") or []
        ],
        warnings=list(value.get("warnings") or []),
        limitations=list(value.get("limitations") or []),
    )


def _weather_view(value: dict[str, Any]) -> WeatherDayView:
    status = str(value.get("status") or "weather_unavailable_for_requested_date")
    return WeatherDayView(
        status=status,
        requested_date=value.get("requested_date") or date.today(),
        maximum_temperature_c=value.get("maximum_temperature_c"),
        minimum_temperature_c=value.get("minimum_temperature_c"),
        precipitation_probability_percent=value.get(
            "precipitation_probability_percent"
        ),
        precipitation_amount_mm=value.get("precipitation_amount_mm"),
        weather_code=value.get("weather_code"),
        explanation=str(
            value.get("explanation")
            or (
                "Exact-date daily weather context is available; it is not a sighting probability."
                if status == "available"
                else "Exact-date weather is unavailable; no other date was substituted."
            )
        ),
    )


def evidence_status(outcome: str | None) -> str:
    return {
        "strong_map_evidence": "strong",
        "limited_contextual_evidence": "limited",
        "insufficient_evidence": "insufficient",
        "source_unavailable": "source_failure",
    }.get(outcome, "pending")


class SafeCheckpointViews:
    """Build public views while retaining raw snapshots only inside the service."""

    def __init__(
        self,
        osm_directory: Any,
        *,
        expose_request_details: bool = True,
    ) -> None:
        features, _provenance = SnapshotGreenSpaceRepository(osm_directory).snapshot()
        self._expose_request_details = expose_request_details
        self._site_features = {
            str(feature.get("id")): feature
            for feature in features
            if feature.get("id")
        }

    def pending_decisions(
        self,
        manager: BiodiversityRunManager,
        *,
        thread_id: str,
    ) -> list[PendingDecisionView]:
        summaries = {
            item.checkpoint_id: item for item in manager.history(thread_id=thread_id)
        }
        output: list[PendingDecisionView] = []
        for execution in manager.executions(thread_id=thread_id):
            if not execution.interrupt_kind:
                continue
            snapshot = manager.snapshot(
                thread_id=thread_id,
                checkpoint_id=execution.head_checkpoint_id,
            )
            raw = next(
                (
                    item.value
                    for task in snapshot.tasks
                    for item in task.interrupts
                    if isinstance(item.value, dict)
                ),
                None,
            )
            if not raw:
                continue
            summary = summaries[execution.head_checkpoint_id]
            output.append(
                self._pending_decision(
                    raw,
                    summary,
                    original_request_text=str(
                        snapshot.values.get("original_request_text") or ""
                    ),
                )
            )
        return output

    @staticmethod
    def _candidate(value: dict[str, Any]) -> TaxonCandidateView:
        preview = value.get("evidence_preview")
        return TaxonCandidateView(
            accepted_taxon_key=value["accepted_taxon_key"],
            common_name=value.get("common_name"),
            scientific_name=str(value.get("scientific_name") or "Unknown"),
            canonical_name=str(value.get("canonical_name") or "Unknown"),
            rank=str(value.get("rank") or "UNKNOWN"),
            taxonomic_status=str(value.get("taxonomic_status") or "UNKNOWN"),
            class_name=value.get("class") or value.get("class_name"),
            order=value.get("order"),
            family=value.get("family"),
            genus=value.get("genus"),
            resolution_method=str(value.get("resolution_method") or "unknown"),
            confidence=value.get("confidence"),
            relation_level=value.get("relation_level"),
            relation_basis=value.get("relation_basis"),
            evidence_preview=(
                TaxonEvidencePreviewView(
                    status=str(preview.get("status") or "not_evaluated_budget"),
                    evidence_outcome=preview.get("evidence_outcome"),
                    sampled_count=preview.get("sampled_count"),
                    retained_count=preview.get("retained_count"),
                    ranking_eligible_count=preview.get("ranking_eligible_count"),
                    dataset_count=preview.get("dataset_count"),
                    target_months=list(preview.get("target_months") or []),
                    source_status=str(preview.get("source_status") or "unknown"),
                    limitations=list(preview.get("limitations") or []),
                )
                if isinstance(preview, dict)
                else None
            ),
        )

    def _pending_decision(
        self,
        raw: dict[str, Any],
        summary: Any,
        *,
        original_request_text: str = "",
    ) -> PendingDecisionView:
        kind = str(raw.get("kind"))
        if kind not in {
            "request_clarification",
            "location_correction",
            "bird_input_correction",
            "taxon_selection",
            "actionable_tradeoff",
            "related_taxon_selection",
            "route_tradeoff",
        }:
            raise ValueError("Unsupported interrupt kind")
        question = str(raw.get("question") or "Human input is required.")
        raw_draft = raw.get("parsed_draft")
        parsed_draft = None
        validation_errors = [
            str(item) for item in raw.get("validation_errors") or []
        ]
        if (
            self._expose_request_details
            and kind == "request_clarification"
            and isinstance(raw_draft, dict)
        ):
            recovered = merge_request_draft(raw_draft, original_request_text)
            if recovered is not None:
                raw_draft = recovered.model_dump(mode="python")
                recovered_fields = {
                    name
                    for name in ("bird_input", "target_local_date", "duration_hours")
                    if raw_draft.get(name) not in {None, ""}
                }
                if any(
                    (
                        raw_draft.get("postcode"),
                        raw_draft.get("start_point"),
                        str(raw_draft.get("location_query") or "").strip(),
                    )
                ):
                    recovered_fields.add("postcode_start_point_or_location_query")
                validation_errors = [
                    error
                    for error in validation_errors
                    if error
                    != "The model response did not match the structured request contract."
                    and not any(
                        error
                        == f"Missing or contradictory field: {field}."
                        for field in recovered_fields
                    )
                ]
            parsed_draft = ParsedRequestDraftView(
                bird_input=(
                    str(raw_draft["bird_input"])
                    if raw_draft.get("bird_input")
                    else None
                ),
                postcode=(
                    str(raw_draft["postcode"])
                    if raw_draft.get("postcode")
                    else None
                ),
                location_query=(
                    str(raw_draft["location_query"])
                    if raw_draft.get("location_query")
                    else None
                ),
                has_explicit_start_point=isinstance(
                    raw_draft.get("start_point"), dict
                ),
                target_local_date=raw_draft.get("target_local_date"),
                duration_hours=raw_draft.get("duration_hours"),
            )
        return PendingDecisionView(
            kind=kind,
            question=question,
            checkpoint_id=summary.checkpoint_id,
            branch_id=summary.branch_id,
            execution_id=summary.execution_id,
            validation_errors=validation_errors,
            status=(str(raw["status"]) if raw.get("status") else None),
            rationale=(
                str(raw.get("rationale") or raw.get("reason"))
                if raw.get("rationale") or raw.get("reason")
                else None
            ),
            candidates=[
                self._candidate(dict(item)) for item in raw.get("candidates") or []
            ],
            location_candidates=[
                GeocodedLocationCandidateView(
                    candidate_id=str(item["candidate_id"]),
                    label=str(item["label"]),
                    locality=(str(item["locality"]) if item.get("locality") else None),
                    administrative_district=(
                        str(item["administrative_district"])
                        if item.get("administrative_district")
                        else None
                    ),
                    postcode=(str(item["postcode"]) if item.get("postcode") else None),
                    category=(str(item["category"]) if item.get("category") else None),
                    place_type=(
                        str(item["place_type"]) if item.get("place_type") else None
                    ),
                )
                for item in raw.get("location_candidates") or []
                if isinstance(item, dict)
            ],
            options=[
                HitlOptionView.model_validate(item)
                for item in raw.get("options") or []
            ],
            parsed_draft=parsed_draft,
        )

    def final_plan(self, snapshot: Any) -> FinalPlanView | None:
        raw = snapshot.values.get("final_validated_plan")
        if not raw:
            return None
        value = dict(raw)
        weather = value.get("weather_context")
        location = dict(snapshot.values.get("resolved_location") or {})
        district = location.get("administrative_district") or "Greater London"
        return FinalPlanView(
            status=value["status"],
            target_species=value["target_species"],
            target_date=value["target_date"],
            duration_hours=value["duration_hours"],
            resolved_london_start_context=(
                f"Generalised start context in {district}, within Greater London."
            ),
            recommended_sites=[
                _site_view(item) for item in value.get("recommended_sites") or []
            ],
            contextual_sites=[
                _site_view(item) for item in value.get("contextual_sites") or []
            ],
            weather_context=_weather_view(weather) if weather else None,
            constraints=[
                _constraint_view(item) for item in value.get("constraints") or []
            ],
            unresolved_limitations=list(value.get("unresolved_limitations") or []),
            suggested_next_actions=list(value.get("suggested_next_actions") or []),
            provenance_references=[
                _without_query(item) or "" for item in value.get("provenance_references") or []
            ],
            evidence_attributions=list(value.get("evidence_attributions") or []),
            evidence_citations=list(value.get("evidence_citations") or []),
            evidence_gate_passed=bool(value.get("evidence_gate_passed")),
            low_confidence_accepted=bool(value.get("low_confidence_accepted")),
            low_confidence_notice=value.get("low_confidence_notice"),
            walking_plan=(
                _walking_plan_view(dict(value["walking_plan"]))
                if value.get("walking_plan")
                else None
            ),
            explanation=value["explanation"],
            generated_by=value["generated_by"],
        )

    def checkpoint_plan(
        self,
        manager: BiodiversityRunManager,
        *,
        thread_id: str,
        checkpoint_id: str,
    ) -> FinalPlanView | None:
        """Return the final plan stored at one exact execution checkpoint."""

        snapshot = manager.snapshot(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
        )
        return self.final_plan(snapshot)

    def evidence(
        self,
        manager: BiodiversityRunManager,
        *,
        thread_id: str,
        checkpoint_id: str,
    ) -> EvidenceView:
        snapshot = manager.snapshot(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
        )
        summary = next(
            item
            for item in manager.history(thread_id=thread_id)
            if item.checkpoint_id == checkpoint_id
        )
        values = dict(snapshot.values)
        occurrence = dict(values.get("occurrence_evidence") or {})
        counts = dict(occurrence.get("counts") or {})
        quality = dict(occurrence.get("quality") or {})
        sites = dict(values.get("public_site_search") or {})
        weather = dict(values.get("weather_evidence") or {})
        bundle = dict(values.get("evidence_bundle") or {})
        provenance_values = list(bundle.get("provenance") or [])
        if not provenance_values:
            provenance_values.extend(occurrence.get("evidence_items") or [])
            if weather.get("provenance"):
                provenance_values.append(weather["provenance"])
        provenance: list[ProvenanceView] = []
        seen: set[tuple[str, str]] = set()
        for item in provenance_values:
            key = (str(item.get("source")), str(item.get("source_record_type")))
            if key in seen:
                continue
            seen.add(key)
            provenance.append(
                ProvenanceView(
                    source=key[0],
                    source_record_type=key[1],
                    retrieved_at=item["retrieved_at"],
                    licence=str(item.get("licence") or "Source-specific terms"),
                    attribution=str(item.get("attribution") or key[0]),
                    use_classification=str(
                        item.get("use_classification") or "contextual"
                    ),
                    source_reference=_without_query(item.get("source_reference")),
                    limitations=list(item.get("limitations") or []),
                )
            )
        limitations = list(
            dict.fromkeys(
                [
                    *list(occurrence.get("limitations") or []),
                    *list(bundle.get("safety_and_scientific_limitations") or []),
                ]
            )
        )
        return EvidenceView(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            branch_id=summary.branch_id,
            execution_id=summary.execution_id,
            status=evidence_status(occurrence.get("outcome")),
            occurrence_outcome=occurrence.get("outcome"),
            reason=occurrence.get("reason"),
            counts=EvidenceCountsView(
                server_match_count=int(counts.get("server_match_count") or 0),
                sampled_count=int(counts.get("sampled_count") or 0),
                deduplicated_count=int(counts.get("deduplicated_count") or 0),
                exact_duplicates_removed=int(
                    counts.get("exact_duplicates_removed") or 0
                ),
                possible_duplicates_retained=int(
                    counts.get("possible_duplicates_retained") or 0
                ),
                retained_count=int(counts.get("retained_total_count") or 0),
                rejected_count=int(counts.get("rejected_count") or 0),
                ranking_eligible_count=int(
                    counts.get("ranking_eligible_count") or 0
                ),
            ),
            quality=EvidenceQualityView(
                spatial_cell_count=int(quality.get("spatial_cell_count") or 0),
                safe_map_cell_count=len(occurrence.get("safe_map_cells") or []),
                retained_dataset_count=int(
                    quality.get("retained_dataset_count") or 0
                ),
                ranking_dataset_count=int(
                    quality.get("ranking_dataset_count") or 0
                ),
                dominant_ranking_dataset_share=float(
                    quality.get("dominant_ranking_dataset_share") or 0
                ),
                dominant_basis_of_record_share=float(
                    quality.get("dominant_basis_of_record_share") or 0
                ),
                quality_percentages=dict(quality.get("quality_percentages") or {}),
                year_distribution=dict(quality.get("year_distribution") or {}),
                month_distribution=dict(quality.get("month_distribution") or {}),
                warnings=list(quality.get("warnings") or []),
            ),
            gate=EvidenceGateView(
                passed=(
                    evidence_status(occurrence.get("outcome")) == "strong"
                ),
                criteria=[
                    EvidenceGateCriterionView(
                        key="ranking_eligible_records",
                        label="Ranking-eligible records",
                        value=int(counts.get("ranking_eligible_count") or 0),
                        minimum=STRONG_EVIDENCE_RULE[
                            "minimum_ranking_eligible_records"
                        ],
                        passed=(
                            int(counts.get("ranking_eligible_count") or 0)
                            >= STRONG_EVIDENCE_RULE[
                                "minimum_ranking_eligible_records"
                            ]
                        ),
                    ),
                    EvidenceGateCriterionView(
                        key="spatial_cells_1km",
                        label="Distinct ranking 1 km cells",
                        value=int(quality.get("spatial_cell_count") or 0),
                        minimum=STRONG_EVIDENCE_RULE[
                            "minimum_spatial_cells_1km"
                        ],
                        passed=(
                            int(quality.get("spatial_cell_count") or 0)
                            >= STRONG_EVIDENCE_RULE[
                                "minimum_spatial_cells_1km"
                            ]
                        ),
                    ),
                    EvidenceGateCriterionView(
                        key="ranking_datasets",
                        label="Ranking datasets",
                        value=int(quality.get("ranking_dataset_count") or 0),
                        minimum=STRONG_EVIDENCE_RULE["minimum_datasets"],
                        passed=(
                            int(quality.get("ranking_dataset_count") or 0)
                            >= STRONG_EVIDENCE_RULE["minimum_datasets"]
                        ),
                    ),
                ],
            ),
            seasonal_target_month=occurrence.get("seasonal_target_month"),
            seasonal_months=list(occurrence.get("seasonal_months") or []),
            year_window=(tuple(occurrence["year_window"]) if occurrence.get("year_window") else None),
            candidate_sites=[
                _site_view(item) for item in sites.get("candidates") or []
            ],
            contextual_sites=[
                _site_view(item) for item in sites.get("contextual_sites") or []
            ],
            constraints=[
                _constraint_view(item)
                for item in values.get("deterministic_constraints") or []
            ],
            weather=_weather_view(weather) if weather else None,
            provenance=provenance,
            limitations=limitations,
            suggested_actions=list(sites.get("suggested_actions") or []),
        )

    def map_evidence(
        self,
        manager: BiodiversityRunManager,
        *,
        thread_id: str,
        checkpoint_id: str,
    ) -> MapEvidenceView:
        snapshot = manager.snapshot(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
        )
        summary = next(
            item
            for item in manager.history(thread_id=thread_id)
            if item.checkpoint_id == checkpoint_id
        )
        values = dict(snapshot.values)
        occurrence = dict(values.get("occurrence_evidence") or {})
        status = evidence_status(occurrence.get("outcome"))
        raw_cells = (
            list(occurrence.get("safe_map_cells") or []) if status == "strong" else []
        )
        cells: list[MapCellFeature] = []
        for index, item in enumerate(raw_cells, start=1):
            count = int(item.get("aggregated_record_count") or 0)
            density = "lower" if count < 10 else "medium" if count < 25 else "higher"
            cells.append(
                MapCellFeature(
                    geometry=GeoJSONGeometry.model_validate(item["geometry"]),
                    properties=MapCellProperties(
                        display_id=f"grid-{index:02d}",
                        density_band=density,
                        dataset_diversity_band=(
                            "multiple"
                            if int(item.get("dataset_count") or 0) > 1
                            else "single"
                        ),
                        date_start=item.get("date_start"),
                        date_end=item.get("date_end"),
                        label=f"Aggregate historical-evidence band {density}",
                    ),
                )
            )
        sites = dict(values.get("public_site_search") or {})
        candidate_features = (
            self._site_map_features(
                list(sites.get("candidates") or []), layer="candidate"
            )
            if status == "strong"
            else []
        )
        contextual_features = self._site_map_features(
            list(sites.get("contextual_sites") or []), layer="contextual"
        )
        location = dict(values.get("resolved_location") or {})
        start_context = None
        point = dict(location.get("rounded_start_point") or {})
        if point:
            longitude = round(float(point["longitude"]), 2)
            latitude = round(float(point["latitude"]), 2)
            district = location.get("administrative_district") or "Greater London"
            start_context = MapStartContext(
                geometry=GeoJSONGeometry(
                    type="Point",
                    coordinates=[longitude, latitude],
                ),
                properties={
                    "layer": "generalised_start",
                    "label": f"Generalised start context · {district}",
                    "precision": "rounded to approximately 0.01 degrees",
                },
            )
        walking_plan = dict(values.get("validated_walking_plan") or {})
        entrance = dict(walking_plan.get("entrance") or {})
        entrance_point = dict(entrance.get("point") or {})
        selected_entrance = None
        if entrance_point:
            selected_entrance = MapStartContext(
                geometry=GeoJSONGeometry(
                    type="Point",
                    coordinates=[
                        float(entrance_point["longitude"]),
                        float(entrance_point["latitude"]),
                    ],
                ),
                properties={
                    "layer": "selected_public_entrance",
                    "label": str(entrance.get("label") or "Selected entrance"),
                    "precision": "verified tagged OSM entrance point",
                },
            )
        grid_note = (
            "Only aggregate cells passing the deterministic strong-evidence gate are shown. Density is banded and response-local display IDs replace internal cell identifiers."
            if status == "strong"
            else "No aggregate grid is shown because the deterministic strong-evidence gate did not pass."
        )
        return MapEvidenceView(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            branch_id=summary.branch_id,
            execution_id=summary.execution_id,
            status=status,
            aggregate_grid=cells,
            candidate_sites=candidate_features,
            contextual_sites=contextual_features,
            start_context=start_context,
            selected_entrance=selected_entrance,
            selected_site_id=walking_plan.get("selected_site_id"),
            route_status=walking_plan.get("status"),
            route_geometry_reference=walking_plan.get("route_geometry_reference"),
            attributions=MAP_ATTRIBUTIONS,
            limitations=MAP_LIMITATIONS,
            grid_note=grid_note,
        )

    def route_options(
        self,
        manager: BiodiversityRunManager,
        *,
        thread_id: str,
        checkpoint_id: str,
    ) -> RouteOptionsView:
        snapshot = manager.snapshot(thread_id=thread_id, checkpoint_id=checkpoint_id)
        summary = next(
            item
            for item in manager.history(thread_id=thread_id)
            if item.checkpoint_id == checkpoint_id
        )
        values = dict(snapshot.values)
        walking_plan = dict(values.get("validated_walking_plan") or {})
        options = [
            _route_option_view(dict(item)) for item in values.get("route_options") or []
        ]
        selected_site_id = walking_plan.get("selected_site_id")
        selected_option_id = next(
            (item.option_id for item in options if item.site_id == selected_site_id),
            None,
        )
        return RouteOptionsView(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            branch_id=summary.branch_id,
            execution_id=summary.execution_id,
            status=str(walking_plan.get("status") or "no_route"),
            selected_option_id=selected_option_id,
            options=options,
            provider_status=(
                str(walking_plan.get("provider"))
                if walking_plan.get("provider")
                else "not_called"
            ),
            limitations=list(walking_plan.get("limitations") or []),
        )

    def route_geometry(
        self,
        manager: BiodiversityRunManager,
        geometry_store: Any,
        *,
        thread_id: str,
        checkpoint_id: str,
        route_geometry_reference: str,
        public_demo: bool,
    ) -> RouteGeometryView:
        snapshot = manager.snapshot(thread_id=thread_id, checkpoint_id=checkpoint_id)
        values = dict(snapshot.values)
        allowed_references: set[str] = set()
        walking_plan = dict(values.get("validated_walking_plan") or {})
        if walking_plan.get("route_geometry_reference"):
            allowed_references.add(str(walking_plan["route_geometry_reference"]))
        for item in values.get("route_options") or []:
            route = dict(item.get("route") or {})
            if route.get("route_geometry_reference"):
                allowed_references.add(str(route["route_geometry_reference"]))
        if route_geometry_reference not in allowed_references:
            raise ValueError("route_geometry_not_in_checkpoint")
        geometry = geometry_store.get(route_geometry_reference)
        if geometry is None:
            raise ValueError("route_geometry_unavailable")
        return RouteGeometryView(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            route_geometry_reference=route_geometry_reference,
            origin_visibility=(
                "planned_public_fixture" if public_demo else "local_private"
            ),
            geojson=geometry,
            limitations=[
                "Route geometry is returned only by this dedicated browser endpoint and is omitted from events, logs, reports, and public checkpoint state views.",
                "A route is practical guidance, not a guarantee of access conditions or a wildlife sighting.",
            ],
        )

    def _site_map_features(
        self,
        sites: list[dict[str, Any]],
        *,
        layer: str,
    ) -> list[MapSiteFeature]:
        output: list[MapSiteFeature] = []
        for site in sites:
            feature = self._site_features.get(str(site.get("site_id")))
            geometry = dict(feature.get("geometry") or {}) if feature else {}
            if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
                continue
            output.append(
                MapSiteFeature(
                    geometry=GeoJSONGeometry.model_validate(geometry),
                    properties=MapSiteProperties(
                        site_id=str(site["site_id"]),
                        layer=layer,
                        name=str(site["name"]),
                        site_type=str(site.get("site_type") or "green_space"),
                        access_certainty=str(
                            site.get("access_certainty") or "unspecified"
                        ),
                        evidence_tier=str(site.get("evidence_tier") or "ungrounded"),
                        approximate_straight_line_distance_km=float(
                            site.get("approximate_straight_line_distance_km") or 0
                        ),
                        operator=site.get("operator"),
                        opening_hours=site.get("opening_hours"),
                        website=site.get("website"),
                    ),
                )
            )
        return output
