"""Privacy-bounded views assembled from exact persisted checkpoints."""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.biodiversity.api.schemas import (
    ConstraintView,
    EvidenceCountsView,
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
    PendingDecisionView,
    PlanSiteView,
    ProvenanceView,
    TaxonCandidateView,
    TaxonEvidencePreviewView,
    WeatherDayView,
)
from app.biodiversity.repositories import SnapshotGreenSpaceRepository
from app.biodiversity.runs import BiodiversityRunManager


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

    def __init__(self, osm_directory: Any) -> None:
        features, _provenance = SnapshotGreenSpaceRepository(osm_directory).snapshot()
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
            output.append(self._pending_decision(raw, summary))
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

    def _pending_decision(self, raw: dict[str, Any], summary: Any) -> PendingDecisionView:
        kind = str(raw.get("kind"))
        if kind not in {
            "request_clarification",
            "location_correction",
            "bird_input_correction",
            "taxon_selection",
            "actionable_tradeoff",
            "related_taxon_selection",
        }:
            raise ValueError("Unsupported interrupt kind")
        question = str(raw.get("question") or "Human input is required.")
        return PendingDecisionView(
            kind=kind,
            question=question,
            checkpoint_id=summary.checkpoint_id,
            branch_id=summary.branch_id,
            execution_id=summary.execution_id,
            validation_errors=[
                str(item) for item in raw.get("validation_errors") or []
            ],
            status=(str(raw["status"]) if raw.get("status") else None),
            rationale=(str(raw["rationale"]) if raw.get("rationale") else None),
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
            explanation=value["explanation"],
            generated_by=value["generated_by"],
        )

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
            attributions=MAP_ATTRIBUTIONS,
            limitations=MAP_LIMITATIONS,
            grid_note=grid_note,
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
