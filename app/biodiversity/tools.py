"""Seven deterministic, typed Phase 1 biodiversity tools/services."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime
from math import hypot
from typing import Any

from app.biodiversity.agent_models import GeocodedLocationCandidate
from app.biodiversity.models import (
    AccessCertainty,
    BritishNationalGridPoint,
    ConstraintSeverity,
    ConstraintStatus,
    ConstraintViolation,
    EvidenceItem,
    EvidenceOutcome,
    EvidenceQualitySummary,
    EvidenceUse,
    ExpeditionEvidenceBundle,
    ExpeditionRequest,
    LocationStatus,
    OccurrenceCounts,
    OccurrenceEvidence,
    PublicSiteCandidate,
    PublicSiteSearchResult,
    PublicSiteSearchStatus,
    ResolvedLocation,
    ResolvedTaxon,
    SafeMapGeometry,
    SafeSpatialCell,
    SiteEvidenceTier,
    SiteSearchAction,
    SourceFailure,
    TaxonCandidate,
    TaxonStatus,
    ToolError,
    ToolErrorCode,
    WeatherEvidence,
    WeatherStatus,
    WGS84Point,
)
from app.biodiversity.repositories import (
    GreenSpaceRepository,
    OccurrenceRepository,
    PostcodeRepository,
    TaxonomyRepository,
    WeatherRepository,
    normalise_postcode,
)
from app.feasibility.spatial import (
    british_national_grid,
    geometries_intersect,
    geometry_distance_metres,
    load_london_boundary,
    point_in_geometry,
)
from app.feasibility.taxonomy import TaxonomyOutcome


GBIF_LIMITATIONS = [
    "Historical occurrence records do not predict or guarantee a sighting.",
    "Record counts are not population or abundance estimates.",
    "Only records with known uncertainty of at most 1,000 m support spatial ranking.",
]


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _parse_retrieved_at(value: str | None) -> datetime:
    if not value:
        return _now()
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _evidence_item(
    provenance: dict[str, Any],
    *,
    record_type: str,
    use: EvidenceUse,
    source_reference: str | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        source=provenance.get("source_name") or "Unknown public source",
        source_record_type=record_type,
        retrieved_at=_parse_retrieved_at(provenance.get("retrieved_at_utc")),
        licence=provenance.get("licence") or "See source terms",
        attribution=provenance.get("attribution")
        or provenance.get("source_name")
        or "Unknown",
        use_classification=use,
        limitations=list(provenance.get("known_limitations") or []),
        source_reference=source_reference or provenance.get("source_url"),
    )


def _failure(tool: str, exc: SourceFailure) -> ToolError:
    return ToolError(
        tool=tool,
        code=exc.code,
        message=exc.message,
        retryable=exc.retryable,
        source=exc.source,
    )


def lookup_uk_postcode(
    request: ExpeditionRequest,
    *,
    repository: PostcodeRepository,
    boundary: dict[str, Any] | None = None,
) -> ResolvedLocation:
    """Resolve a postcode or generalised map point and enforce Greater London scope."""

    london_boundary = boundary or load_london_boundary()
    if request.start_point is not None:
        point = WGS84Point(
            longitude=round(request.start_point.longitude, 4),
            latitude=round(request.start_point.latitude, 4),
        )
        inside = point_in_geometry(point.longitude, point.latitude, london_boundary)
        if not inside:
            return ResolvedLocation(
                status=LocationStatus.outside_supported_area,
                input_kind="map_selected_point",
                rounded_start_point=point,
                within_greater_london=False,
                message="The selected map point is outside the supported Greater London boundary.",
            )
        easting, northing = british_national_grid(point.longitude, point.latitude)
        provenance = EvidenceItem(
            source="User-selected map point and versioned Greater London boundary",
            source_record_type="generalised_start_point",
            retrieved_at=_now(),
            licence="User input; Greater London boundary © OpenStreetMap contributors, ODbL",
            attribution="User input; © OpenStreetMap contributors",
            use_classification=EvidenceUse.validation,
            limitations=[
                "The rounded map point is a planning start point, not a precise home address.",
            ],
            source_reference="data/boundaries/greater-london-2026-08-31.geojson",
        )
        return ResolvedLocation(
            status=LocationStatus.resolved,
            input_kind="map_selected_point",
            rounded_start_point=point,
            british_national_grid=BritishNationalGridPoint(
                easting=round(easting, 1), northing=round(northing, 1)
            ),
            within_greater_london=True,
            provenance=provenance,
            message="Map-selected planning point resolved within Greater London.",
        )

    assert request.postcode is not None
    try:
        normalised = normalise_postcode(request.postcode)
    except ValueError as exc:
        return ResolvedLocation(
            status=LocationStatus.invalid_input,
            input_kind="postcode",
            message=str(exc),
        )
    try:
        fixture = repository.lookup(normalised)
    except SourceFailure as exc:
        status = (
            LocationStatus.postcode_not_found
            if exc.code == ToolErrorCode.postcode_not_found
            else LocationStatus.malformed_upstream_response
            if exc.code == ToolErrorCode.malformed_upstream_response
            else LocationStatus.fixture_unavailable
            if exc.code == ToolErrorCode.missing_or_corrupt_fixture
            else LocationStatus.source_unavailable
        )
        return ResolvedLocation(
            status=status,
            input_kind="postcode",
            normalised_postcode=normalised,
            tool_error=_failure("lookup_uk_postcode", exc),
            message=exc.message,
        )
    payload = fixture["payload"]
    point = WGS84Point(
        longitude=round(float(payload["longitude"]), 4),
        latitude=round(float(payload["latitude"]), 4),
    )
    inside = point_in_geometry(point.longitude, point.latitude, london_boundary)
    provenance_data = fixture.get("provenance") or {
        "source_name": "Postcodes.io",
        "licence": "Contains Royal Mail, Ordnance Survey and ONS data under their terms",
        "attribution": "Postcodes.io",
        "known_limitations": ["A postcode centroid is not a precise address."],
        "source_url": fixture.get("live_url", "https://postcodes.io/"),
    }
    provenance = _evidence_item(
        provenance_data, record_type="postcode_centroid", use=EvidenceUse.validation
    )
    if not inside:
        return ResolvedLocation(
            status=LocationStatus.outside_supported_area,
            input_kind="postcode",
            normalised_postcode=normalised,
            rounded_start_point=point,
            within_greater_london=False,
            administrative_district=payload.get("admin_district"),
            provenance=provenance,
            message="The postcode is valid but outside the supported Greater London boundary.",
        )
    easting, northing = british_national_grid(point.longitude, point.latitude)
    return ResolvedLocation(
        status=LocationStatus.resolved,
        input_kind="postcode",
        normalised_postcode=normalised,
        rounded_start_point=point,
        british_national_grid=BritishNationalGridPoint(
            easting=round(easting, 1), northing=round(northing, 1)
        ),
        within_greater_london=True,
        administrative_district=payload.get("admin_district"),
        provenance=provenance,
        message="Postcode centroid resolved and validated within Greater London.",
    )


def resolve_geocoded_location(
    candidate: GeocodedLocationCandidate | dict[str, Any],
    *,
    provenance: dict[str, Any],
    boundary: dict[str, Any] | None = None,
) -> ResolvedLocation:
    """Validate a user-selected geocoder centroid against Greater London."""

    selected = GeocodedLocationCandidate.model_validate(candidate)
    point = WGS84Point(
        longitude=round(selected.longitude, 4),
        latitude=round(selected.latitude, 4),
    )
    inside = point_in_geometry(
        point.longitude,
        point.latitude,
        boundary or load_london_boundary(),
    )
    evidence = _evidence_item(
        provenance,
        record_type="forward_geocoded_planning_point",
        use=EvidenceUse.validation,
        source_reference="https://nominatim.org/release-docs/latest/api/Search/",
    )
    if not inside:
        return ResolvedLocation(
            status=LocationStatus.outside_supported_area,
            input_kind="geocoded_place",
            normalised_postcode=selected.postcode,
            rounded_start_point=point,
            within_greater_london=False,
            administrative_district=selected.administrative_district,
            provenance=evidence,
            message="The selected geocoder match is outside the supported Greater London boundary.",
        )
    easting, northing = british_national_grid(point.longitude, point.latitude)
    return ResolvedLocation(
        status=LocationStatus.resolved,
        input_kind="geocoded_place",
        normalised_postcode=selected.postcode,
        rounded_start_point=point,
        british_national_grid=BritishNationalGridPoint(
            easting=round(easting, 1),
            northing=round(northing, 1),
        ),
        within_greater_london=True,
        administrative_district=selected.administrative_district,
        provenance=evidence,
        message=(
            "User-confirmed geocoder match resolved as a representative planning "
            "point within Greater London."
        ),
    )


def resolve_bird_taxon(
    bird_input: str, *, repository: TaxonomyRepository
) -> ResolvedTaxon:
    """Resolve arbitrary common/scientific input without a species whitelist."""

    try:
        outcome = repository.resolve(bird_input)
    except SourceFailure as exc:
        return ResolvedTaxon(
            status=(
                TaxonStatus.fixture_unavailable
                if exc.code == ToolErrorCode.missing_or_corrupt_fixture
                else TaxonStatus.malformed_upstream_response
                if exc.code == ToolErrorCode.malformed_upstream_response
                else TaxonStatus.source_unavailable
            ),
            original_input=bird_input,
            normalised_input=" ".join(bird_input.casefold().split()),
            tool_error=_failure("resolve_bird_taxon", exc),
            rationale=exc.message,
        )
    provenance_provider = getattr(repository, "evidence_provenance", None)
    if callable(provenance_provider):
        provenance = _evidence_item(
            provenance_provider(),
            record_type="accepted_taxon_resolution",
            use=EvidenceUse.validation,
        )
    else:
        provenance = EvidenceItem(
            source="GBIF Species API",
            source_record_type="accepted_taxon_resolution",
            retrieved_at=_now(),
            licence="GBIF API terms; source datasets retain their own terms",
            attribution="GBIF.org",
            use_classification=EvidenceUse.validation,
            limitations=["Taxonomy and vernacular-name coverage can change over time."],
            source_reference="https://www.gbif.org/developer/species",
        )
    candidates = [
        TaxonCandidate(
            accepted_taxon_key=item.accepted_taxon_key,
            common_name=item.matched_common_name,
            scientific_name=item.scientific_name,
            canonical_name=item.canonical_name,
            rank=item.rank,
            taxonomic_status=item.taxonomic_status,
            class_name=item.class_name,
            order=item.order,
            family=item.family,
            genus=item.genus,
            resolution_method=item.search_method,
            confidence=item.match_confidence,
        )
        for item in outcome.candidates
    ]
    return ResolvedTaxon(
        status=TaxonStatus(outcome.outcome),
        original_input=outcome.original_input,
        normalised_input=outcome.normalised_input,
        accepted_taxon_key=outcome.accepted_taxon_key,
        common_name=outcome.matched_common_name,
        scientific_name=outcome.scientific_name,
        canonical_name=outcome.canonical_name,
        rank=outcome.rank,
        taxonomic_status=outcome.taxonomic_status,
        class_name=outcome.class_name,
        order=outcome.order,
        family=outcome.family,
        genus=outcome.genus,
        resolution_method=outcome.match_method,
        confidence=outcome.match_confidence,
        candidates=candidates,
        provenance=provenance,
        rationale=outcome.rationale,
    )


def _empty_occurrence(
    outcome: EvidenceOutcome,
    reason: str,
    target_month: int,
    reference_year: int,
    tool_error: ToolError | None = None,
    seasonal_window_radius_months: int = 1,
) -> OccurrenceEvidence:
    months = sorted(
        {
            ((target_month + offset - 1) % 12) + 1
            for offset in range(
                -seasonal_window_radius_months,
                seasonal_window_radius_months + 1,
            )
        }
    )
    year = reference_year
    return OccurrenceEvidence(
        outcome=outcome,
        reason=reason,
        seasonal_target_month=target_month,
        seasonal_months=months,
        year_window=(year - 5, year),
        counts=OccurrenceCounts(
            server_match_count=0,
            sampled_count=0,
            deduplicated_count=0,
            exact_duplicates_removed=0,
            possible_duplicates_retained=0,
            retained_total_count=0,
            rejected_count=0,
            ranking_eligible_count=0,
        ),
        quality=EvidenceQualitySummary(
            ranking_eligible_count=0,
            broad_zone_count=0,
            historical_context_count=0,
            unknown_uncertainty_count=0,
            spatial_cell_count=0,
            retained_dataset_count=0,
            ranking_dataset_count=0,
            dominant_ranking_dataset_share=0,
            dominant_basis_of_record_share=0,
        ),
        deduplication_method="No occurrence retrieval was performed.",
        limitations=GBIF_LIMITATIONS,
        tool_error=tool_error,
    )


def search_occurrences(
    taxon: ResolvedTaxon,
    *,
    target_month: int,
    repository: OccurrenceRepository,
    seasonal_window_radius_months: int = 1,
    preview: bool = False,
) -> OccurrenceEvidence:
    """Retrieve and translate bounded occurrence evidence without public coordinates."""

    if taxon.status == TaxonStatus.human_selection_required:
        return _empty_occurrence(
            EvidenceOutcome.human_selection_required,
            taxon.rationale,
            target_month,
            getattr(repository, "reference_year", _now().year),
            seasonal_window_radius_months=seasonal_window_radius_months,
        )
    if taxon.status == TaxonStatus.taxon_not_found:
        return _empty_occurrence(
            EvidenceOutcome.taxon_not_found,
            taxon.rationale,
            target_month,
            getattr(repository, "reference_year", _now().year),
            seasonal_window_radius_months=seasonal_window_radius_months,
        )
    if taxon.status != TaxonStatus.resolved:
        return _empty_occurrence(
            EvidenceOutcome.source_unavailable,
            taxon.rationale,
            target_month,
            getattr(repository, "reference_year", _now().year),
            seasonal_window_radius_months=seasonal_window_radius_months,
        )
    taxonomy = TaxonomyOutcome(
        outcome="resolved",
        original_input=taxon.original_input,
        normalised_input=taxon.normalised_input,
        matched_common_name=taxon.common_name,
        scientific_name=taxon.scientific_name,
        canonical_name=taxon.canonical_name,
        accepted_taxon_key=taxon.accepted_taxon_key,
        rank=taxon.rank,
        taxonomic_status=taxon.taxonomic_status,
        class_name=taxon.class_name,
        order=taxon.order,
        family=taxon.family,
        genus=taxon.genus,
        match_method=taxon.resolution_method,
        match_confidence=taxon.confidence,
        rationale=taxon.rationale,
    )
    try:
        if preview:
            raw = repository.preview(
                taxonomy,
                target_month=target_month,
                seasonal_window_radius_months=seasonal_window_radius_months,
            )
        elif seasonal_window_radius_months == 1:
            raw = repository.search(taxonomy, target_month=target_month)
        else:
            raw = repository.search(
                taxonomy,
                target_month=target_month,
                seasonal_window_radius_months=seasonal_window_radius_months,
            )
    except SourceFailure as exc:
        return _empty_occurrence(
            EvidenceOutcome.source_unavailable,
            exc.message,
            target_month,
            getattr(repository, "reference_year", _now().year),
            _failure("search_occurrences", exc),
            seasonal_window_radius_months=seasonal_window_radius_months,
        )

    counts = raw.get("counts") or {}
    records = raw.get("records") or []
    retained = [record for record in records if record.get("retained")]
    ranking = [record for record in retained if record.get("ranking_eligible")]
    quality_counts = Counter(record.get("location_quality") for record in retained)
    quality_raw = raw.get("quality_summary") or {}
    diversity = raw.get("dataset_diversity") or {}
    ranking_dataset_counts = Counter(
        record.get("dataset_key", "missing") for record in ranking
    )
    record_dataset_counts = Counter(
        record.get("dataset_key", "missing") for record in retained
    )
    basis_counts = Counter(
        record.get("basis_of_record") or "missing" for record in retained
    )
    dominant_dataset_share = (
        max(ranking_dataset_counts.values(), default=0) / len(ranking)
        if ranking
        else 0.0
    )
    dominant_basis_share = (
        max(basis_counts.values(), default=0) / len(retained) if retained else 0.0
    )
    warnings = list(quality_raw.get("warnings") or [])
    if retained and quality_counts["unknown"] / len(retained) > 0.5:
        warnings.append("coordinate_quality_limited")
    if dominant_dataset_share > 0.8:
        warnings.append("dataset_concentration_warning")
    ordered_ranking_datasets = sorted(ranking_dataset_counts.values(), reverse=True)
    if len(ordered_ranking_datasets) >= 2 and ordered_ranking_datasets[1] == 1:
        warnings.append("minimal_second_dataset_contribution")
    warnings = list(dict.fromkeys(warnings))
    cell_count = len(
        {
            record.get("spatial_cell_1km_ref")
            for record in ranking
            if record.get("spatial_cell_1km_ref")
        }
    )
    safe_cells = [
        SafeSpatialCell(
            cell_id=cell["cell_id"],
            geometry=SafeMapGeometry.model_validate(cell["geometry"]),
            cell_size_metres=cell.get("cell_size_metres", 1000),
            aggregated_record_count=cell["record_count"],
            dataset_count=cell["dataset_count"],
            date_start=cell.get("date_start"),
            date_end=cell.get("date_end"),
            evidence_quality=cell["evidence_quality"],
            limitations=cell.get("limitations", []),
        )
        for cell in raw.get("safe_map_cells", [])
    ]
    fixture_provenance = raw.get("fixture_provenance")
    evidence_items = []
    if fixture_provenance:
        evidence_items.append(
            _evidence_item(
                fixture_provenance,
                record_type="sanitised_occurrence_aggregate",
                use=EvidenceUse.contextual,
            )
        )
    else:
        evidence_items.append(
            EvidenceItem(
                source="GBIF Occurrence Search API",
                source_record_type="bounded_occurrence_sample_and_aggregate",
                retrieved_at=_now(),
                licence="Each GBIF record and media item retains its own licence",
                attribution="GBIF.org and contributing datasets",
                use_classification=EvidenceUse.contextual,
                limitations=GBIF_LIMITATIONS,
                source_reference="https://www.gbif.org/developer/occurrence",
                dataset_references=sorted(record_dataset_counts),
            )
        )
    retrieval = raw.get("retrieval") or {}
    dedup = retrieval.get("deduplication") or {}
    duplicate_count = counts.get("duplicates_removed", 0)
    possible_count = dedup.get("possible_duplicates_retained", 0)
    total = len(retained)
    quality_percentages = quality_raw.get("quality_percentages") or {
        key: round(100 * quality_counts[key] / total, 2) if total else 0.0
        for key in ("strong", "weak", "context_only", "unknown")
    }
    outcome = EvidenceOutcome(raw["evidence_outcome"])
    if outcome != EvidenceOutcome.strong_map_evidence:
        safe_cells = []
    reason = raw.get("evidence_reason")
    if not reason:
        reason = (
            "isolated_records_only"
            if 1 <= len(retained) <= 4
            else "strong_gate_met"
            if outcome == EvidenceOutcome.strong_map_evidence
            else "below_strong_gate"
            if outcome == EvidenceOutcome.limited_contextual_evidence
            else "no_retained_records"
        )
    limitations = list(GBIF_LIMITATIONS)
    if "coordinate_quality_limited" in warnings:
        limitations.append(
            "More than half of retained evidence lacks coordinate uncertainty; a large retained count does not imply strong site-level evidence."
        )
    if "dataset_concentration_warning" in warnings:
        limitations.append(
            "More than 80% of ranking-eligible records come from one dataset, so sampling concentration may affect interpretation."
        )
    if "minimal_second_dataset_contribution" in warnings:
        limitations.append(
            "The second ranking dataset contributes only one record and is not meaningful diversity on its own."
        )
    return OccurrenceEvidence(
        outcome=outcome,
        reason=reason,
        seasonal_target_month=target_month,
        seasonal_months=list(retrieval.get("seasonal_months") or []),
        year_window=tuple(
            retrieval.get("year_window") or (_now().year - 5, _now().year)
        ),
        counts=OccurrenceCounts(
            server_match_count=counts.get("server_match_count", 0),
            sampled_count=counts.get("sampled_count", 0),
            deduplicated_count=counts.get("deduplicated_count", 0),
            exact_duplicates_removed=duplicate_count,
            possible_duplicates_retained=possible_count,
            retained_total_count=counts.get("retained_total_count", 0),
            rejected_count=counts.get("rejected_count", 0),
            ranking_eligible_count=counts.get("ranking_eligible_count", 0),
        ),
        quality=EvidenceQualitySummary(
            ranking_eligible_count=counts.get("ranking_eligible_count", 0),
            broad_zone_count=counts.get("weak_count", 0),
            historical_context_count=counts.get("context_only_count", 0),
            unknown_uncertainty_count=counts.get("unknown_uncertainty_count", 0),
            quality_percentages=quality_percentages,
            spatial_cell_count=cell_count,
            retained_dataset_count=diversity.get(
                "retained_dataset_count", len(record_dataset_counts)
            ),
            ranking_dataset_count=diversity.get(
                "ranking_dataset_count", len(ranking_dataset_counts)
            ),
            record_count_by_dataset=dict(
                diversity.get("record_count_by_dataset") or record_dataset_counts
            ),
            ranking_eligible_count_by_dataset=dict(
                diversity.get("ranking_eligible_count_by_dataset")
                or ranking_dataset_counts
            ),
            dominant_ranking_dataset_share=diversity.get(
                "dominant_ranking_dataset_share", round(dominant_dataset_share, 4)
            ),
            dominant_basis_of_record_share=diversity.get(
                "dominant_basis_of_record_share", round(dominant_basis_share, 4)
            ),
            year_distribution=raw.get("year_distribution") or {},
            month_distribution=raw.get("month_distribution") or {},
            warnings=warnings,
        ),
        safe_map_cells=safe_cells,
        deduplication_method=(
            dedup.get("method")
            or retrieval.get("deduplication_rule")
            or "Publisher identifier hierarchy with deterministic fallback."
        ),
        evidence_items=[
            item.model_copy(
                update={
                    "query_months": list(retrieval.get("seasonal_months") or [])
                }
            )
            for item in evidence_items
        ],
        limitations=limitations,
    )


def get_weather_context(
    location: ResolvedLocation,
    requested_date: date,
    *,
    repository: WeatherRepository,
) -> WeatherEvidence:
    """Return exact-date weather context or an explicit unavailable state."""

    if (
        location.status != LocationStatus.resolved
        or location.rounded_start_point is None
    ):
        return WeatherEvidence(
            status=WeatherStatus.not_requested_location_unresolved,
            requested_date=requested_date,
            limitations=[
                "Weather was not requested because the location did not resolve."
            ],
        )
    try:
        fixture = repository.daily(
            location.rounded_start_point.longitude,
            location.rounded_start_point.latitude,
            requested_date.isoformat(),
        )
    except SourceFailure as exc:
        return WeatherEvidence(
            status=(
                WeatherStatus.fixture_unavailable
                if exc.code == ToolErrorCode.missing_or_corrupt_fixture
                else WeatherStatus.malformed_upstream_response
                if exc.code == ToolErrorCode.malformed_upstream_response
                else WeatherStatus.source_unavailable
            ),
            requested_date=requested_date,
            limitations=[
                exc.message,
                "Weather context is not a bird-sighting probability.",
            ],
            tool_error=_failure("get_weather_context", exc),
        )
    payload = fixture["payload"]
    daily = payload.get("daily") or {}
    dates = daily.get("time") or []
    if requested_date.isoformat() not in dates:
        return WeatherEvidence(
            status=WeatherStatus.weather_unavailable_for_requested_date,
            requested_date=requested_date,
            limitations=[
                "The requested date is outside the saved/supported forecast dates; no unrelated date was substituted.",
                "Weather context is not a bird-sighting probability.",
            ],
            tool_error=ToolError(
                tool="get_weather_context",
                code=ToolErrorCode.weather_unavailable_for_requested_date,
                message=(
                    "The requested date is outside the saved/supported forecast dates; "
                    "no unrelated date was substituted."
                ),
            ),
        )
    index = dates.index(requested_date.isoformat())

    def daily_value(field: str) -> Any:
        values = daily.get(field) or []
        return values[index] if index < len(values) else None

    provenance_data = fixture.get("provenance") or {
        "source_name": "Open-Meteo Forecast API",
        "licence": "CC BY 4.0",
        "attribution": "Weather data by Open-Meteo.com",
        "known_limitations": ["Forecasts change."],
        "source_url": fixture.get("live_url", "https://open-meteo.com/"),
    }
    return WeatherEvidence(
        status=WeatherStatus.available,
        requested_date=requested_date,
        maximum_temperature_c=daily_value("temperature_2m_max"),
        minimum_temperature_c=daily_value("temperature_2m_min"),
        precipitation_probability_percent=daily_value("precipitation_probability_max"),
        precipitation_amount_mm=daily_value("precipitation_sum"),
        weather_code=daily_value("weather_code"),
        provenance=_evidence_item(
            provenance_data, record_type="daily_forecast", use=EvidenceUse.contextual
        ),
        limitations=[
            "Forecasts can change after retrieval.",
            "Weather context must not be interpreted as bird-sighting probability.",
        ],
    )


def find_public_green_spaces(
    location: ResolvedLocation,
    *,
    search_radius_km: float,
    occurrence: OccurrenceEvidence | None,
    repository: GreenSpaceRepository,
    limit: int = 8,
    contextual_limit: int = 8,
    nearby_context_distance_km: float = 1.0,
) -> PublicSiteSearchResult:
    """Separate polygon-grounded recommendations from explicitly contextual sites."""

    if nearby_context_distance_km <= 0:
        raise ValueError("nearby_context_distance_km must be positive")

    if (
        location.status != LocationStatus.resolved
        or location.british_national_grid is None
    ):
        return PublicSiteSearchResult(
            candidates=[],
            searched_radius_km=search_radius_km,
            status=PublicSiteSearchStatus.not_applicable_location_unresolved,
            suggested_actions=[SiteSearchAction.correct_or_select_location],
        )
    try:
        features, provenance_data = repository.snapshot()
    except SourceFailure as exc:
        return PublicSiteSearchResult(
            candidates=[],
            searched_radius_km=search_radius_km,
            status=PublicSiteSearchStatus.source_unavailable,
            suggested_actions=[SiteSearchAction.retry_site_source],
            error=ToolError(
                tool="find_public_green_spaces",
                code=ToolErrorCode.source_unavailable,
                message=exc.message,
                retryable=exc.retryable,
                source=exc.source,
            ),
        )
    safe_geometries = (
        {
            cell.cell_id: cell.geometry.model_dump(mode="json")
            for cell in occurrence.safe_map_cells
        }
        if occurrence and occurrence.outcome == EvidenceOutcome.strong_map_evidence
        else {}
    )
    grounded_ranked: list[tuple[float, PublicSiteCandidate]] = []
    contextual_ranked: list[tuple[int, float, PublicSiteCandidate]] = []
    provenance = _evidence_item(
        provenance_data,
        record_type="versioned_green_space_candidate_snapshot",
        use=EvidenceUse.contextual,
        source_reference="data/osm/london-green-space-candidates-2026-08-31.geojson",
    )
    for feature in features:
        properties = feature.get("properties") or {}
        tags = properties.get("source_tags") or {}
        if tags.get("access") in {"private", "no"}:
            continue
        geometry = feature.get("geometry") or {}
        geometry_type = geometry.get("type")
        centre = properties.get("centre_point")
        if centre is None and geometry_type == "Point":
            centre = geometry.get("coordinates")
        if not isinstance(centre, list) or len(centre) != 2:
            continue
        if geometry_type not in {"Point", "Polygon", "MultiPolygon"}:
            continue
        longitude, latitude = float(centre[0]), float(centre[1])
        easting, northing = british_national_grid(longitude, latitude)
        distance_km = (
            hypot(
                easting - location.british_national_grid.easting,
                northing - location.british_national_grid.northing,
            )
            / 1000
        )
        if distance_km > search_radius_km:
            continue
        associated_cells = sorted(
            cell_id
            for cell_id, safe_geometry in safe_geometries.items()
            if geometry_type in {"Polygon", "MultiPolygon"}
            and geometries_intersect(geometry, safe_geometry)
        )
        nearest_safe_distance_km = (
            min(
                geometry_distance_metres(geometry, safe_geometry)
                for safe_geometry in safe_geometries.values()
            )
            / 1000
            if safe_geometries
            else None
        )
        if associated_cells:
            evidence_tier = SiteEvidenceTier.directly_grounded
        elif (
            nearest_safe_distance_km is not None
            and nearest_safe_distance_km <= nearby_context_distance_km
        ):
            evidence_tier = SiteEvidenceTier.nearby_context
        else:
            evidence_tier = SiteEvidenceTier.ungrounded
        site_type = next(
            (
                str(tags[key])
                for key in ("leisure", "boundary", "landuse")
                if tags.get(key)
            ),
            "green_space",
        )
        candidate = PublicSiteCandidate(
            site_id=str(feature.get("id") or f"osm-{properties.get('osm_id')}"),
            name=tags.get("name:en") or tags.get("name") or "Unnamed green space",
            site_type=site_type,
            access_certainty=AccessCertainty(
                properties.get("access_certainty", "unspecified")
            ),
            centre_point=WGS84Point(longitude=longitude, latitude=latitude),
            approximate_straight_line_distance_km=round(distance_km, 2),
            operator=tags.get("operator"),
            opening_hours=tags.get("opening_hours"),
            website=tags.get("website"),
            evidence_tier=evidence_tier,
            source_geometry_type=geometry_type,
            distance_to_nearest_safe_cell_km=(
                0.0
                if associated_cells
                else round(nearest_safe_distance_km, 2)
                if nearest_safe_distance_km is not None
                else None
            ),
            associated_safe_cell_ids=associated_cells,
            provenance=provenance,
            limitations=[
                "Distance is projected centre-point proximity, not walking-route distance.",
                "The OSM candidate snapshot does not guarantee current access or opening.",
                (
                    "The source polygon intersects a historical aggregate safe-map cell; this does not place an occurrence inside the site."
                    if evidence_tier == SiteEvidenceTier.directly_grounded
                    else "This contextual site is not a bird-evidence-grounded recommendation."
                ),
            ],
        )
        if evidence_tier == SiteEvidenceTier.directly_grounded:
            grounded_ranked.append((distance_km, candidate))
        else:
            contextual_ranked.append(
                (
                    0 if evidence_tier == SiteEvidenceTier.nearby_context else 1,
                    distance_km,
                    candidate,
                )
            )
    grounded_ranked.sort(
        key=lambda item: (
            0 if item[1].access_certainty == AccessCertainty.explicit_public else 1,
            item[0],
            item[1].name.casefold(),
        )
    )
    contextual_ranked.sort(
        key=lambda item: (
            item[0],
            0 if item[2].access_certainty == AccessCertainty.explicit_public else 1,
            item[1],
            item[2].name.casefold(),
        )
    )
    candidates = [item[1] for item in grounded_ranked[:limit]]
    contextual_sites = [item[2] for item in contextual_ranked[:contextual_limit]]
    if occurrence is None or occurrence.outcome != EvidenceOutcome.strong_map_evidence:
        actions_by_outcome = {
            EvidenceOutcome.human_selection_required: [SiteSearchAction.select_taxon],
            EvidenceOutcome.taxon_not_found: [SiteSearchAction.correct_bird_input],
            EvidenceOutcome.source_unavailable: [
                SiteSearchAction.retry_occurrence_source
            ],
            EvidenceOutcome.limited_contextual_evidence: [
                SiteSearchAction.change_target_month,
                SiteSearchAction.refresh_live_evidence,
            ],
            EvidenceOutcome.insufficient_evidence: [
                SiteSearchAction.change_target_month,
                SiteSearchAction.refresh_live_evidence,
            ],
        }
        actions = (
            actions_by_outcome[occurrence.outcome]
            if occurrence is not None
            else [SiteSearchAction.retry_occurrence_source]
        )
        return PublicSiteSearchResult(
            candidates=[],
            contextual_sites=contextual_sites,
            searched_radius_km=search_radius_km,
            status=PublicSiteSearchStatus.not_applicable_without_strong_evidence,
            suggested_actions=actions,
        )
    if not safe_geometries:
        message = (
            "Strong occurrence evidence exists, but no approved public safe-map cells "
            "are available for grounded site recommendations."
        )
        return PublicSiteSearchResult(
            candidates=[],
            contextual_sites=contextual_sites,
            searched_radius_km=search_radius_km,
            status=PublicSiteSearchStatus.safe_map_unavailable,
            suggested_actions=[SiteSearchAction.refresh_live_evidence],
            error=ToolError(
                tool="find_public_green_spaces",
                code=ToolErrorCode.safe_map_unavailable,
                message=message,
            ),
        )
    if not candidates:
        message = (
            "No public green-space snapshot candidate lies within the search radius "
            "with a polygon intersecting an allowed safe-map cell."
        )
        return PublicSiteSearchResult(
            candidates=[],
            contextual_sites=contextual_sites,
            searched_radius_km=search_radius_km,
            status=PublicSiteSearchStatus.no_suitable_public_sites,
            suggested_actions=[SiteSearchAction.expand_search_radius],
            error=ToolError(
                tool="find_public_green_spaces",
                code=ToolErrorCode.no_suitable_public_sites,
                message=message,
            ),
        )
    return PublicSiteSearchResult(
        candidates=candidates,
        contextual_sites=contextual_sites,
        searched_radius_km=search_radius_km,
        status=PublicSiteSearchStatus.success,
    )


def validate_expedition_constraints(
    request: ExpeditionRequest,
    location: ResolvedLocation,
    taxon: ResolvedTaxon,
    occurrence: OccurrenceEvidence | None,
    weather: WeatherEvidence | None,
    site_search: PublicSiteSearchResult,
) -> list[ConstraintViolation]:
    """Apply non-negotiable scientific and product-safety constraints."""

    results: list[ConstraintViolation] = []
    results.append(
        ConstraintViolation(
            code="greater_london_scope",
            status=(
                ConstraintStatus.satisfied
                if location.status == LocationStatus.resolved
                else ConstraintStatus.violated
            ),
            severity=(
                ConstraintSeverity.information
                if location.status == LocationStatus.resolved
                else ConstraintSeverity.error
            ),
            field="postcode" if request.postcode else "start_point",
            message=location.message,
        )
    )
    taxon_status = (
        ConstraintStatus.satisfied
        if taxon.status == TaxonStatus.resolved
        else ConstraintStatus.unresolved
        if taxon.status == TaxonStatus.human_selection_required
        else ConstraintStatus.violated
    )
    results.append(
        ConstraintViolation(
            code="taxon_resolution",
            status=taxon_status,
            severity=(
                ConstraintSeverity.information
                if taxon_status == ConstraintStatus.satisfied
                else ConstraintSeverity.warning
                if taxon_status == ConstraintStatus.unresolved
                else ConstraintSeverity.error
            ),
            field="bird_input",
            message=taxon.rationale,
        )
    )
    if occurrence:
        results.append(
            ConstraintViolation(
                code="historical_evidence_quality",
                status=(
                    ConstraintStatus.satisfied
                    if occurrence.outcome == EvidenceOutcome.strong_map_evidence
                    else ConstraintStatus.unresolved
                    if occurrence.outcome == EvidenceOutcome.limited_contextual_evidence
                    else ConstraintStatus.violated
                ),
                severity=(
                    ConstraintSeverity.information
                    if occurrence.outcome == EvidenceOutcome.strong_map_evidence
                    else ConstraintSeverity.warning
                ),
                message=(
                    "Historical evidence passes the deterministic map gate."
                    if occurrence.outcome == EvidenceOutcome.strong_map_evidence
                    else f"Historical evidence outcome: {occurrence.outcome.value} ({occurrence.reason})."
                ),
            )
        )
    site_constraint = {
        PublicSiteSearchStatus.success: ConstraintViolation(
            code="grounded_public_site_candidates",
            status=ConstraintStatus.satisfied,
            severity=ConstraintSeverity.information,
            message="Every recommended site is associated with an allowed safe-map cell.",
        ),
        PublicSiteSearchStatus.safe_map_unavailable: ConstraintViolation(
            code="safe_map_unavailable",
            status=ConstraintStatus.unresolved,
            severity=ConstraintSeverity.warning,
            message=(
                "Strong occurrence evidence exists, but safe-map cells are unavailable; "
                "site recommendations are suppressed."
            ),
        ),
        PublicSiteSearchStatus.no_suitable_public_sites: ConstraintViolation(
            code="no_suitable_public_sites",
            status=ConstraintStatus.unresolved,
            severity=ConstraintSeverity.warning,
            message=(
                "No grounded public-site candidate was found within the requested search radius."
            ),
        ),
        PublicSiteSearchStatus.source_unavailable: ConstraintViolation(
            code="site_source_unavailable",
            status=ConstraintStatus.unresolved,
            severity=ConstraintSeverity.error,
            message="The public green-space source was unavailable; no sites are recommended.",
        ),
    }.get(site_search.status)
    if site_constraint is not None:
        results.append(site_constraint)
    if site_search.contextual_sites:
        results.append(
            ConstraintViolation(
                code="contextual_sites_not_recommendations",
                status=ConstraintStatus.not_applicable,
                severity=ConstraintSeverity.information,
                message=(
                    "Nearby green spaces are retained as contextual options only; they are "
                    "not included in grounded recommendations or candidate-plan readiness."
                ),
            )
        )
    results.append(
        ConstraintViolation(
            code="duration_within_phase1_bounds",
            status=ConstraintStatus.satisfied,
            severity=ConstraintSeverity.information,
            field="duration_hours",
            message=f"Requested duration of {request.duration_hours:g} hours is accepted.",
        )
    )
    results.append(
        ConstraintViolation(
            code="search_radius",
            status=ConstraintStatus.satisfied,
            severity=ConstraintSeverity.information,
            field="search_radius_km",
            message=(
                f"Candidate search uses a {request.search_radius_km:g} km projected radius; "
                "this is distinct from walking distance."
            ),
        )
    )
    if request.maximum_walking_distance_km is not None:
        results.append(
            ConstraintViolation(
                code="routing_not_available",
                status=ConstraintStatus.unresolved,
                severity=ConstraintSeverity.warning,
                field="maximum_walking_distance_km",
                message=(
                    f"The {request.maximum_walking_distance_km:g} km walking limit is accepted "
                    "as a user constraint but is not route-validated in Phase 1."
                ),
            )
        )
    if request.rain_preference.value == "avoid_heavy_rain":
        if weather and weather.status == WeatherStatus.available:
            probability = weather.precipitation_probability_percent
            results.append(
                ConstraintViolation(
                    code="rain_preference",
                    status=(
                        ConstraintStatus.unresolved
                        if probability is not None and probability >= 70
                        else ConstraintStatus.satisfied
                    ),
                    severity=(
                        ConstraintSeverity.warning
                        if probability is not None and probability >= 70
                        else ConstraintSeverity.information
                    ),
                    field="rain_preference",
                    message=(
                        f"Forecast maximum precipitation probability is {probability}%."
                        if probability is not None
                        else "Forecast does not provide precipitation probability."
                    ),
                )
            )
        else:
            results.append(
                ConstraintViolation(
                    code="rain_preference_unvalidated",
                    status=ConstraintStatus.unresolved,
                    severity=ConstraintSeverity.warning,
                    field="rain_preference",
                    message="Rain preference cannot be evaluated because exact-date weather is unavailable.",
                )
            )
    if site_search.candidates and any(
        site.access_certainty == AccessCertainty.unspecified
        for site in site_search.candidates
    ):
        results.append(
            ConstraintViolation(
                code="site_access_uncertain",
                status=ConstraintStatus.unresolved,
                severity=ConstraintSeverity.warning,
                message="One or more candidate sites have unspecified access; public access is not claimed.",
            )
        )
    return results


def build_expedition_evidence_bundle(
    request: ExpeditionRequest,
    location: ResolvedLocation,
    taxon: ResolvedTaxon,
    occurrence: OccurrenceEvidence | None,
    weather: WeatherEvidence | None,
    sites_result: PublicSiteSearchResult,
    constraints: list[ConstraintViolation],
) -> ExpeditionEvidenceBundle:
    """Assemble the public evidence bundle without hiding expected source failures."""

    outcome = (
        occurrence.outcome
        if occurrence is not None
        else EvidenceOutcome.source_unavailable
    )
    provenance: list[EvidenceItem] = []
    if location.provenance:
        provenance.append(location.provenance)
    if taxon.provenance:
        provenance.append(taxon.provenance)
    if occurrence:
        provenance.extend(occurrence.evidence_items)
    if weather and weather.provenance:
        provenance.append(weather.provenance)
    source_sites = sites_result.candidates or sites_result.contextual_sites
    if source_sites:
        provenance.append(source_sites[0].provenance)
    tool_errors: list[ToolError] = []
    if sites_result.error:
        tool_errors.append(sites_result.error)
    if location.tool_error:
        tool_errors.append(location.tool_error)
    elif location.status not in {
        LocationStatus.resolved,
        LocationStatus.outside_supported_area,
    }:
        tool_errors.append(
            ToolError(
                tool="lookup_uk_postcode",
                code=(
                    ToolErrorCode.postcode_not_found
                    if location.status == LocationStatus.postcode_not_found
                    else ToolErrorCode.invalid_input
                    if location.status == LocationStatus.invalid_input
                    else ToolErrorCode.source_unavailable
                ),
                message=location.message,
            )
        )
    if location.status == LocationStatus.outside_supported_area:
        tool_errors.append(
            ToolError(
                tool="lookup_uk_postcode",
                code=ToolErrorCode.outside_supported_area,
                message=location.message,
            )
        )
    if taxon.tool_error:
        tool_errors.append(taxon.tool_error)
    elif taxon.status != TaxonStatus.resolved:
        tool_errors.append(
            ToolError(
                tool="resolve_bird_taxon",
                code=(
                    ToolErrorCode.human_selection_required
                    if taxon.status == TaxonStatus.human_selection_required
                    else ToolErrorCode.taxon_not_found
                    if taxon.status == TaxonStatus.taxon_not_found
                    else ToolErrorCode.source_unavailable
                ),
                message=taxon.rationale,
            )
        )
    if occurrence and occurrence.tool_error:
        tool_errors.append(occurrence.tool_error)
    elif occurrence and occurrence.outcome == EvidenceOutcome.source_unavailable:
        tool_errors.append(
            ToolError(
                tool="search_occurrences",
                code=ToolErrorCode.source_unavailable,
                message=occurrence.reason,
                retryable=False,
            )
        )
    if weather and weather.tool_error:
        tool_errors.append(weather.tool_error)
    elif weather and weather.status not in {
        WeatherStatus.available,
        WeatherStatus.not_requested_location_unresolved,
    }:
        tool_errors.append(
            ToolError(
                tool="get_weather_context",
                code=(
                    ToolErrorCode.weather_unavailable_for_requested_date
                    if weather.status
                    == WeatherStatus.weather_unavailable_for_requested_date
                    else ToolErrorCode.source_unavailable
                ),
                message=weather.limitations[0],
            )
        )
    limitations = list(
        occurrence.limitations
        if occurrence
        else [
            "Historical occurrence evidence does not guarantee a sighting.",
            "The backend does not estimate bird populations or abundance.",
        ]
    )
    limitations.extend(
        [
            "No individual occurrence coordinates are included.",
            "Candidate-site access and opening are not guaranteed.",
            "Projected proximity is not a walking route or walking-distance validation.",
            "This output is not legal or conservation advice and executes no field action.",
        ]
    )
    return ExpeditionEvidenceBundle(
        request=request,
        location=location,
        taxon=taxon,
        occurrence=occurrence,
        weather=weather,
        site_search=sites_result,
        candidate_sites=sites_result.candidates,
        contextual_sites=sites_result.contextual_sites,
        constraints=constraints,
        evidence_outcome=outcome,
        safety_and_scientific_limitations=list(dict.fromkeys(limitations)),
        provenance=provenance,
        tool_errors=tool_errors,
    )
