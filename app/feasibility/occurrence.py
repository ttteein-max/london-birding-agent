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
]

@dataclass(frozen=True)
class RetrievalPolicy:
    """Explicit page, request, temporal and seasonal budget for one taxon."""

    page_size: int = 300
    max_pages: int = 3
    request_budget: int = 3
    complete_years: int = 5
    target_month: int = 6

    def __post_init__(self) -> None:
        if not 1 <= self.page_size <= 300:
            raise ValueError("GBIF page_size must be between 1 and 300")
        if self.max_pages < 1 or self.request_budget < 1:
            raise ValueError("page and request budgets must be positive")
        if not 1 <= self.target_month <= 12:
            raise ValueError("target_month must be between 1 and 12")

    @property
    def effective_page_budget(self) -> int:
        return min(self.max_pages, self.request_budget)

    def year_window(self, current_year: int | None = None) -> tuple[int, int]:
        year = current_year or datetime.now(UTC).year
        return year - self.complete_years, year

    def seasonal_months(self) -> list[int]:
        return sorted({((self.target_month + offset - 1) % 12) + 1 for offset in (-1, 0, 1)})


def _record_identity(record: dict[str, Any]) -> str:
    stable = record.get("key") or record.get("occurrenceID")
    if stable not in (None, ""):
        return f"source:{stable}"
    fallback = "|".join(
        str(record.get(field) or "")
        for field in (
            "datasetKey",
            "institutionCode",
            "catalogNumber",
            "eventDate",
            "decimalLatitude",
            "decimalLongitude",
        )
    )
    return f"fallback:{hashlib.sha256(fallback.encode('utf-8')).hexdigest()}"


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
    if retained:
        return "limited_contextual_evidence"
    return "insufficient_evidence"


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
    deduplicated: dict[str, dict[str, Any]] = {}
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
        for record in page_records:
            deduplicated.setdefault(_record_identity(record), record)
        sanitised_so_far = [
            sanitise_occurrence(record, london_boundary=london_boundary, cell_secret=secret)
            for record in deduplicated.values()
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
        for record in deduplicated.values()
    ]
    counts = occurrence_counts(
        records,
        server_match_count=server_match_count,
        sampled_count=len(sampled),
        duplicates_removed=len(sampled) - len(deduplicated),
    )
    retained = [record for record in records if record["retained"]]
    ranking = [record for record in records if record["ranking_eligible"]]
    return {
        "input": resolution.original_input,
        "taxonomy": resolution.model_dump(mode="json", exclude={"request_urls"}),
        "evidence_outcome": classify_evidence(records),
        "evidence_rationale": (
            "Only strong (≤1,000 m uncertainty) records contribute to 1 km spatial "
            "ranking; weaker, context-only and unknown-uncertainty records cannot."
        ),
        "records": records,
        "counts": counts,
        "dataset_diversity": {
            "retained_dataset_count": len({record["dataset_key"] for record in retained}),
            "ranking_dataset_count": len({record["dataset_key"] for record in ranking}),
        },
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
            "deduplication_rule": (
                "First occurrence per GBIF key/occurrenceID; fallback SHA-256 over "
                "dataset, institution, catalogue number, date and coordinates."
            ),
        },
    }
