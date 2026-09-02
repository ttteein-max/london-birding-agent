"""Bounded GBIF occurrence retrieval and evidence-quality classification."""

from __future__ import annotations

import hashlib
import secrets
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from app.feasibility.core import STRONG_EVIDENCE_RULE
from app.feasibility.live import FATAL_GEOSPATIAL_ISSUES, get_json
from app.feasibility.spatial import (
    location_quality_tier,
    metric_cell,
    metric_cell_polygon_wgs84,
    opaque_cell_reference,
    point_in_geometry,
)
from app.feasibility.taxonomy import JsonFetcher, TaxonomyOutcome

EvidenceOutcome = Literal[
    "strong_map_evidence",
    "limited_contextual_evidence",
    "insufficient_evidence",
    "human_selection_required",
    "taxon_not_found",
    "source_unavailable",
]

@dataclass(frozen=True)
class RetrievalPolicy:
    """Explicit page, request, temporal and seasonal budget for one taxon."""

    page_size: int = 300
    max_pages: int = 3
    request_budget: int = 3
    complete_years: int = 5
    target_month: int = 6
    seasonal_window_radius_months: int = 1

    def __post_init__(self) -> None:
        if not 1 <= self.page_size <= 300:
            raise ValueError("GBIF page_size must be between 1 and 300")
        if self.max_pages < 1 or self.request_budget < 1:
            raise ValueError("page and request budgets must be positive")
        if not 1 <= self.target_month <= 12:
            raise ValueError("target_month must be between 1 and 12")
        if not 1 <= self.seasonal_window_radius_months <= 3:
            raise ValueError("seasonal_window_radius_months must be between 1 and 3")

    @property
    def effective_page_budget(self) -> int:
        return min(self.max_pages, self.request_budget)

    def year_window(self, current_year: int | None = None) -> tuple[int, int]:
        year = current_year or datetime.now(UTC).year
        return year - self.complete_years, year

    def seasonal_months(self) -> list[int]:
        radius = self.seasonal_window_radius_months
        return sorted(
            {
                ((self.target_month + offset - 1) % 12) + 1
                for offset in range(-radius, radius + 1)
            }
        )


def _normalised_identifier(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return " ".join(str(value).strip().casefold().split()) or None


def _source_identifier(record: dict[str, Any]) -> str | None:
    """Return a publisher-stable identifier other than occurrenceID when possible."""

    for field in ("organismID", "materialSampleID"):
        value = _normalised_identifier(record.get(field))
        if value:
            return f"{field}:{value}"
    catalogue = _normalised_identifier(record.get("catalogNumber"))
    if catalogue:
        namespace = (
            _normalised_identifier(record.get("institutionCode"))
            or _normalised_identifier(record.get("collectionCode"))
            or _normalised_identifier(record.get("datasetKey"))
            or "unknown"
        )
        return f"catalogue:{namespace}:{catalogue}"
    return None


def _generalised_location(record: dict[str, Any]) -> str:
    longitude = record.get("decimalLongitude")
    latitude = record.get("decimalLatitude")
    if not isinstance(longitude, (int, float)) or not isinstance(latitude, (int, float)):
        return ""
    try:
        cell = metric_cell(longitude, latitude, size_metres=1_000)
    except (TypeError, ValueError):
        return ""
    return f"bng1km:{cell[0]}:{cell[1]}"


def _record_fingerprint(record: dict[str, Any]) -> str | None:
    """Build a cautious cross-source fingerprint without collapsing cell/month peers."""

    event = _normalised_identifier(
        record.get("eventDate") or record.get("eventTime") or record.get("dateIdentified")
    )
    location = _generalised_location(record)
    dataset = _normalised_identifier(record.get("datasetKey"))
    taxon = _normalised_identifier(
        record.get("taxonKey") or record.get("speciesKey") or record.get("scientificName")
    )
    if not event or not location or not dataset:
        return None
    components = (
        taxon or "unknown-taxon",
        event,
        dataset,
        location,
        _normalised_identifier(record.get("basisOfRecord")) or "",
        _normalised_identifier(record.get("recordedBy")) or "",
    )
    return hashlib.sha256("|".join(components).encode("utf-8")).hexdigest()


def deduplicate_occurrences(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Deduplicate by publisher identity before GBIF key, retaining uncertain matches."""

    retained: list[dict[str, Any]] = []
    seen_occurrence_ids: set[str] = set()
    seen_source_ids: set[str] = set()
    seen_gbif_keys: set[str] = set()
    seen_fingerprints_without_stable_id: set[str] = set()
    fingerprints_with_stable_id: Counter[str] = Counter()
    removed_by_method: Counter[str] = Counter()
    possible_duplicates_retained = 0

    for record in records:
        occurrence_id = _normalised_identifier(record.get("occurrenceID"))
        source_id = _source_identifier(record)
        gbif_key = _normalised_identifier(record.get("key"))
        fingerprint = _record_fingerprint(record)
        stable_id = occurrence_id or source_id or gbif_key
        duplicate_method: str | None = None
        if occurrence_id and occurrence_id in seen_occurrence_ids:
            duplicate_method = "occurrence_id"
        elif source_id and source_id in seen_source_ids:
            duplicate_method = "source_identifier"
        elif gbif_key and gbif_key in seen_gbif_keys:
            duplicate_method = "gbif_key"
        elif not stable_id and fingerprint and fingerprint in seen_fingerprints_without_stable_id:
            duplicate_method = "deterministic_fingerprint"

        if duplicate_method:
            removed_by_method[duplicate_method] += 1
            continue

        if stable_id and fingerprint and (
            fingerprints_with_stable_id[fingerprint]
            or fingerprint in seen_fingerprints_without_stable_id
        ):
            possible_duplicates_retained += 1
        retained.append(record)
        if occurrence_id:
            seen_occurrence_ids.add(occurrence_id)
        if source_id:
            seen_source_ids.add(source_id)
        if gbif_key:
            seen_gbif_keys.add(gbif_key)
        if fingerprint:
            if stable_id:
                fingerprints_with_stable_id[fingerprint] += 1
            else:
                seen_fingerprints_without_stable_id.add(fingerprint)

    return retained, {
        "exact_duplicates_removed": sum(removed_by_method.values()),
        "possible_duplicates_retained": possible_duplicates_retained,
        "removed_by_method": dict(sorted(removed_by_method.items())),
        "method": (
            "publisher occurrenceID; stable source identifier; exact repeated GBIF key; "
            "deterministic fingerprint for records lacking stable identifiers. Similar "
            "fingerprints with distinct stable identifiers are retained and counted."
        ),
    }


def _hashed_identifier(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _event_year_month(record: dict[str, Any]) -> tuple[int | None, int | None]:
    year = record.get("year")
    month = record.get("month")
    if isinstance(year, int) and isinstance(month, int):
        return year, month
    date = record.get("eventDate") or ""
    try:
        return int(date[:4]), int(date[5:7])
    except (TypeError, ValueError):
        return year if isinstance(year, int) else None, month if isinstance(month, int) else None


def _media_audit(record: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for item in record.get("media") or []:
        licence = item.get("license")
        licence_text = (licence or "").lower()
        output.append(
            {
                "identifier_ref": _hashed_identifier(item.get("identifier")),
                "type": item.get("type"),
                "licence": licence,
                "reusable": bool(licence)
                and (
                    "publicdomain/zero" in licence_text
                    or "creativecommons.org/licenses/by/" in licence_text
                ),
            }
        )
    return output


def sanitise_occurrence(
    record: dict[str, Any],
    *,
    london_boundary: dict[str, Any],
    cell_secret: bytes,
) -> dict[str, Any]:
    """Filter in memory and return only generalised, auditable fields."""

    latitude = record.get("decimalLatitude")
    longitude = record.get("decimalLongitude")
    has_coordinate = isinstance(latitude, (int, float)) and isinstance(longitude, (int, float))
    issues = sorted(record.get("issues") or [])
    status = record.get("occurrenceStatus")
    rejection_reasons: list[str] = []
    if not has_coordinate:
        rejection_reasons.append("missing_or_invalid_coordinate")
    if status not in (None, "PRESENT"):
        rejection_reasons.append("occurrence_not_present")
    if FATAL_GEOSPATIAL_ISSUES.intersection(issues):
        rejection_reasons.append("fatal_geospatial_issue")
    if has_coordinate and not point_in_geometry(longitude, latitude, london_boundary):
        rejection_reasons.append("outside_greater_london_boundary")

    uncertainty = record.get("coordinateUncertaintyInMeters")
    try:
        quality = location_quality_tier(uncertainty)
    except (TypeError, ValueError):
        quality = "unknown"
        rejection_reasons.append("invalid_coordinate_uncertainty")
    rejected = bool(rejection_reasons)
    ranking_eligible = not rejected and quality == "strong"
    if rejected:
        use_class = "rejected"
    elif quality == "strong":
        use_class = "spatial_ranking"
    elif quality == "weak":
        use_class = "broad_zone_only"
    elif quality == "context_only":
        use_class = "historical_context_only"
    else:
        use_class = "audit_only"

    cell_ref = None
    zone_ref = None
    if has_coordinate and not rejected:
        zone_ref = opaque_cell_reference(
            longitude,
            latitude,
            size_metres=10_000,
            secret=cell_secret,
        )
        if ranking_eligible:
            cell_ref = opaque_cell_reference(
                longitude,
                latitude,
                size_metres=1_000,
                secret=cell_secret,
            )
    year, month = _event_year_month(record)
    return {
        "record_ref": _hashed_identifier(record.get("key") or record.get("occurrenceID")),
        "has_coordinate": has_coordinate,
        "occurrence_status": status,
        "issues": issues,
        "coordinate_uncertainty_metres": uncertainty,
        "location_quality": quality,
        "evidence_use": use_class,
        "ranking_eligible": ranking_eligible,
        "retained": not rejected,
        "rejection_reasons": rejection_reasons,
        "observation_or_event_date": record.get("eventDate") or record.get("dateIdentified"),
        "year": year,
        "month": month,
        "dataset_key": record.get("datasetKey") or "missing",
        "basis_of_record": record.get("basisOfRecord"),
        "record_licence": record.get("license"),
        "spatial_cell_1km_ref": cell_ref,
        "generalised_zone_10km_ref": zone_ref,
        "media": _media_audit(record),
    }


def occurrence_counts(
    records: list[dict[str, Any]],
    *,
    server_match_count: int,
    sampled_count: int,
    duplicates_removed: int,
) -> dict[str, Any]:
    retained = [record for record in records if record["retained"]]
    rejected = [record for record in records if not record["retained"]]
    quality = Counter(record["location_quality"] for record in retained)
    rejection_counts = Counter(
        reason for record in rejected for reason in record["rejection_reasons"]
    )
    return {
        "server_match_count": server_match_count,
        "sampled_count": sampled_count,
        "deduplicated_count": len(records),
        "duplicates_removed": duplicates_removed,
        "ranking_eligible_count": sum(record["ranking_eligible"] for record in records),
        "weak_count": quality["weak"],
        "context_only_count": quality["context_only"],
        "unknown_uncertainty_count": quality["unknown"],
        "rejected_count": len(rejected),
        "retained_total_count": len(retained),
        "rejection_counts_by_reason": dict(sorted(rejection_counts.items())),
    }


def validate_occurrence_counts(counts: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {
        "server_match_count",
        "sampled_count",
        "deduplicated_count",
        "duplicates_removed",
        "ranking_eligible_count",
        "weak_count",
        "context_only_count",
        "unknown_uncertainty_count",
        "rejected_count",
        "retained_total_count",
        "rejection_counts_by_reason",
    }
    missing = required.difference(counts)
    if missing:
        return [f"missing occurrence count: {name}" for name in sorted(missing)]
    if counts["sampled_count"] - counts["duplicates_removed"] != counts["deduplicated_count"]:
        errors.append("sampled_count - duplicates_removed must equal deduplicated_count")
    if counts["retained_total_count"] + counts["rejected_count"] != counts["deduplicated_count"]:
        errors.append("retained_total_count + rejected_count must equal deduplicated_count")
    classified_retained = (
        counts["ranking_eligible_count"]
        + counts["weak_count"]
        + counts["context_only_count"]
        + counts["unknown_uncertainty_count"]
    )
    if classified_retained != counts["retained_total_count"]:
        errors.append("location-quality counts must sum to retained_total_count")
    if counts["sampled_count"] > counts["server_match_count"]:
        errors.append("sampled_count cannot exceed server_match_count")
    return errors


def classify_evidence(records: list[dict[str, Any]]) -> EvidenceOutcome:
    retained = [record for record in records if record["retained"]]
    ranking = [record for record in retained if record["ranking_eligible"]]
    cells = {record["spatial_cell_1km_ref"] for record in ranking}
    datasets = {record["dataset_key"] for record in ranking}
    if (
        len(ranking) >= STRONG_EVIDENCE_RULE["minimum_ranking_eligible_records"]
        and len(cells) >= STRONG_EVIDENCE_RULE["minimum_spatial_cells_1km"]
        and len(datasets) >= STRONG_EVIDENCE_RULE["minimum_datasets"]
    ):
        return "strong_map_evidence"
    if len(retained) >= 5:
        return "limited_contextual_evidence"
    return "insufficient_evidence"


def _safe_map_cells(
    raw_records: list[dict[str, Any]],
    sanitised_records: list[dict[str, Any]],
    *,
    evidence_outcome: EvidenceOutcome,
    minimum_records_per_cell: int = 3,
) -> list[dict[str, Any]]:
    """Aggregate eligible records before exposing rounded cell polygons."""

    if evidence_outcome != "strong_map_evidence":
        return []
    grouped: dict[tuple[int, int], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for raw, sanitised in zip(raw_records, sanitised_records, strict=True):
        if not sanitised["ranking_eligible"]:
            continue
        cell = metric_cell(
            raw["decimalLongitude"], raw["decimalLatitude"], size_metres=1_000
        )
        grouped.setdefault(cell, []).append((raw, sanitised))
    cells: list[dict[str, Any]] = []
    for (cell_easting, cell_northing), items in grouped.items():
        if len(items) < minimum_records_per_cell:
            continue
        dates = sorted(
            item[1]["observation_or_event_date"]
            for item in items
            if item[1]["observation_or_event_date"]
        )
        datasets = {item[1]["dataset_key"] for item in items}
        cells.append(
            {
                "cell_id": f"bng-1km-{cell_easting}-{cell_northing}",
                "crs": "EPSG:27700",
                "cell_size_metres": 1_000,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        metric_cell_polygon_wgs84(cell_easting, cell_northing)
                    ],
                },
                "record_count": len(items),
                "dataset_count": len(datasets),
                "date_start": dates[0] if dates else None,
                "date_end": dates[-1] if dates else None,
                "evidence_quality": "ranking_eligible_aggregate",
                "limitations": [
                    "Generalised 1 km historical-evidence cell; not an occurrence location.",
                    "Does not predict a sighting or imply public access.",
                ],
            }
        )
    cells.sort(key=lambda item: (-item["record_count"], item["cell_id"]))
    return cells[:50]


def _quality_and_sampling_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    retained = [record for record in records if record["retained"]]
    ranking = [record for record in retained if record["ranking_eligible"]]
    total = len(retained)
    quality_counts = Counter(record["location_quality"] for record in retained)
    records_by_dataset = Counter(record["dataset_key"] for record in retained)
    ranking_by_dataset = Counter(record["dataset_key"] for record in ranking)
    basis_counts = Counter(record.get("basis_of_record") or "missing" for record in retained)
    dominant_dataset_count = max(ranking_by_dataset.values(), default=0)
    dominant_basis_count = max(basis_counts.values(), default=0)
    dominant_dataset_share = dominant_dataset_count / len(ranking) if ranking else 0.0
    dominant_basis_share = dominant_basis_count / total if total else 0.0
    warnings: list[str] = []
    if total and quality_counts["unknown"] / total > 0.5:
        warnings.append("coordinate_quality_limited")
    if dominant_dataset_share > 0.8:
        warnings.append("dataset_concentration_warning")
    if 0 < len(ranking_by_dataset) and len(ranking_by_dataset) >= 2:
        ordered = sorted(ranking_by_dataset.values(), reverse=True)
        if len(ordered) > 1 and ordered[1] == 1:
            warnings.append("minimal_second_dataset_contribution")
    return {
        "quality_counts": {
            quality: quality_counts[quality]
            for quality in ("strong", "weak", "context_only", "unknown")
        },
        "quality_percentages": {
            quality: round(100 * quality_counts[quality] / total, 2) if total else 0.0
            for quality in ("strong", "weak", "context_only", "unknown")
        },
        "record_count_by_dataset": dict(sorted(records_by_dataset.items())),
        "ranking_eligible_count_by_dataset": dict(sorted(ranking_by_dataset.items())),
        "dominant_ranking_dataset_share": round(dominant_dataset_share, 4),
        "dominant_basis_of_record_share": round(dominant_basis_share, 4),
        "warnings": warnings,
    }


def retrieve_occurrences(
    resolution: TaxonomyOutcome,
    *,
    london_boundary: dict[str, Any],
    policy: RetrievalPolicy | None = None,
    fetch_json: JsonFetcher = get_json,
    current_year: int | None = None,
    cell_secret: bytes | None = None,
) -> dict[str, Any]:
    if resolution.outcome != "resolved":
        return {
            "input": resolution.original_input,
            "taxonomy": resolution.model_dump(mode="json", exclude={"request_urls"}),
            "evidence_outcome": resolution.outcome,
            "records": [],
            "counts": None,
            "retrieval": None,
        }
    selected_policy = policy or RetrievalPolicy()
    start_year, end_year = selected_policy.year_window(current_year)
    months = selected_policy.seasonal_months()
    secret = cell_secret or secrets.token_bytes(32)
    sampled: list[dict[str, Any]] = []
    request_urls: list[str] = []
    server_match_count = 0
    stopped_early = False
    stop_reason = "page_budget_exhausted"
    deduplicated: list[dict[str, Any]] = []
    deduplication = {
        "exact_duplicates_removed": 0,
        "possible_duplicates_retained": 0,
        "removed_by_method": {},
        "method": "",
    }
    for page_index in range(selected_policy.effective_page_budget):
        raw, url = fetch_json(
            "https://api.gbif.org/v1/occurrence/search",
            {
                "taxon_key": resolution.accepted_taxon_key,
                "has_coordinate": "true",
                "occurrence_status": "present",
                "geometry": (
                    "POLYGON((-0.5103 51.2868,0.3340 51.2868,0.3340 51.6919,"
                    "-0.5103 51.6919,-0.5103 51.2868))"
                ),
                "year": f"{start_year},{end_year}",
                "month": months,
                "limit": selected_policy.page_size,
                "offset": page_index * selected_policy.page_size,
            },
        )
        request_urls.append(url)
        if page_index == 0:
            server_match_count = int(raw.get("count", 0))
        page_records = raw.get("results", [])
        sampled.extend(page_records)
        deduplicated, deduplication = deduplicate_occurrences(sampled)
        sanitised_so_far = [
            sanitise_occurrence(record, london_boundary=london_boundary, cell_secret=secret)
            for record in deduplicated
        ]
        if classify_evidence(sanitised_so_far) == "strong_map_evidence":
            stopped_early = True
            stop_reason = "strong_evidence_gate_reached"
            break
        if raw.get("endOfRecords") or not page_records:
            stopped_early = True
            stop_reason = "server_results_exhausted"
            break

    records = [
        sanitise_occurrence(record, london_boundary=london_boundary, cell_secret=secret)
        for record in deduplicated
    ]
    counts = occurrence_counts(
        records,
        server_match_count=server_match_count,
        sampled_count=len(sampled),
        duplicates_removed=deduplication["exact_duplicates_removed"],
    )
    retained = [record for record in records if record["retained"]]
    ranking = [record for record in records if record["ranking_eligible"]]
    outcome = classify_evidence(records)
    quality_summary = _quality_and_sampling_summary(records)
    return {
        "input": resolution.original_input,
        "taxonomy": resolution.model_dump(mode="json", exclude={"request_urls"}),
        "evidence_outcome": outcome,
        "evidence_reason": (
            "isolated_records_only"
            if 1 <= len(retained) <= 4
            else "strong_gate_met"
            if outcome == "strong_map_evidence"
            else "below_strong_gate"
            if outcome == "limited_contextual_evidence"
            else "no_retained_records"
        ),
        "evidence_rationale": (
            "Only strong (≤1,000 m uncertainty) records contribute to 1 km spatial "
            "ranking; weaker, context-only and unknown-uncertainty records cannot."
        ),
        "records": records,
        "safe_map_cells": _safe_map_cells(
            deduplicated, records, evidence_outcome=outcome
        ),
        "counts": counts,
        "dataset_diversity": {
            "retained_dataset_count": len({record["dataset_key"] for record in retained}),
            "ranking_dataset_count": len({record["dataset_key"] for record in ranking}),
            "record_count_by_dataset": quality_summary["record_count_by_dataset"],
            "ranking_eligible_count_by_dataset": quality_summary[
                "ranking_eligible_count_by_dataset"
            ],
            "dominant_ranking_dataset_share": quality_summary[
                "dominant_ranking_dataset_share"
            ],
            "dominant_basis_of_record_share": quality_summary[
                "dominant_basis_of_record_share"
            ],
        },
        "quality_summary": quality_summary,
        "year_distribution": dict(sorted(Counter(str(record["year"]) for record in retained if record["year"]).items())),
        "month_distribution": dict(sorted(Counter(str(record["month"]) for record in retained if record["month"]).items())),
        "retrieval": {
            "year_window": [start_year, end_year],
            "seasonal_months": months,
            "page_size": selected_policy.page_size,
            "page_budget": selected_policy.max_pages,
            "request_budget": selected_policy.request_budget,
            "pages_requested": len(request_urls),
            "stopped_early": stopped_early,
            "stop_reason": stop_reason,
            "request_urls": request_urls,
            "deduplication": deduplication,
            "deduplication_rule": deduplication["method"],
        },
    }
