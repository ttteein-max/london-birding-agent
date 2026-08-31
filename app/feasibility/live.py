"""Bounded public-API clients and sanitisation for explicit Phase 0 live refresh."""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.feasibility.core import canonical_sha256

USER_AGENT = (
    "London-Biodiversity-Expedition-Planner-Phase0/0.1 "
    "(local reproducible feasibility study)"
)
REQUEST_TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 3
LONDON_ENVELOPE_WKT = (
    "POLYGON((-0.5103 51.2868,0.3340 51.2868,0.3340 51.6919,"
    "-0.5103 51.6919,-0.5103 51.2868))"
)

TAXON_SCENARIOS = [
    ("abundant_evidence", "Common woodpigeon", "Columba palumbus"),
    ("map_viable", "House sparrow", "Passer domesticus"),
    ("map_viable", "Eurasian magpie", "Pica pica"),
    ("sparse_evidence", "Corncrake", "Crex crex"),
    ("no_usable_evidence", "Great auk", "Pinguinus impennis"),
]

AMBIGUOUS_INPUT = "robin"
AMBIGUOUS_CANDIDATE_QUERIES = [
    "European robin",
    "American robin",
    "Ryukyu robin",
]

FATAL_GEOSPATIAL_ISSUES = {
    "ZERO_COORDINATE",
    "COORDINATE_OUT_OF_RANGE",
    "COORDINATE_INVALID",
    "COUNTRY_COORDINATE_MISMATCH",
}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_json(
    base_url: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    attempts: int = MAX_ATTEMPTS,
) -> tuple[Any, str]:
    """GET JSON sequentially with a timeout and bounded retry/backoff."""

    url = base_url
    if params:
        url = f"{base_url}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed public APIs
                if response.status != 200:
                    raise RuntimeError(f"GET {url} returned HTTP {response.status}")
                return json.load(response), url
        except HTTPError as exc:
            last_error = exc
            if exc.code != 429 and not 500 <= exc.code < 600:
                raise RuntimeError(f"GET {url} failed with HTTP {exc.code}") from exc
        except (TimeoutError, URLError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt < attempts:
            time.sleep(2 ** (attempt - 1))
    raise RuntimeError(f"GET {url} failed after {attempts} attempts: {last_error}")


def _species_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": record.get("acceptedKey") or record.get("usageKey") or record.get("key"),
        "scientific_name": record.get("accepted") or record.get("scientificName"),
        "canonical_name": record.get("canonicalName"),
        "rank": record.get("rank"),
        "status": record.get("status") or record.get("taxonomicStatus"),
        "match_type": record.get("matchType"),
        "confidence": record.get("confidence"),
        "class": record.get("class"),
    }


def fetch_taxonomy_fixture() -> dict[str, Any]:
    resolved: list[dict[str, Any]] = []
    request_log: list[str] = []
    for scenario, common_name, scientific_input in TAXON_SCENARIOS:
        raw, url = get_json(
            "https://api.gbif.org/v1/species/match",
            {"name": scientific_input, "kingdom": "Animalia"},
        )
        request_log.append(url)
        summary = _species_summary(raw)
        if summary["rank"] != "SPECIES" or summary["class"] != "Aves":
            raise RuntimeError(f"GBIF did not resolve {scientific_input!r} to a bird species")
        resolved.append(
            {
                "scenario": scenario,
                "input": common_name,
                "query": scientific_input,
                **summary,
            }
        )

    direct, direct_url = get_json(
        "https://api.gbif.org/v1/species/match",
        {"name": AMBIGUOUS_INPUT, "kingdom": "Animalia"},
    )
    request_log.append(direct_url)
    candidates: list[dict[str, Any]] = []
    for common_name in AMBIGUOUS_CANDIDATE_QUERIES:
        search, url = get_json(
            "https://api.gbif.org/v1/species/search",
            {
                "q": common_name,
                "rank": "SPECIES",
                "highertaxon_key": 212,
                "limit": 5,
            },
        )
        request_log.append(url)
        accepted = next(
            (item for item in search.get("results", []) if not item.get("synonym")),
            None,
        )
        if accepted:
            candidates.append({"common_name": common_name, **_species_summary(accepted)})

    payload = {
        "resolved_taxa": resolved,
        "ambiguous": {
            "input": AMBIGUOUS_INPUT,
            "direct_match": _species_summary(direct),
            "resolution_status": "human_selection_required",
            "candidates": candidates,
            "reason": (
                "The unqualified English common name resolves only to a higher rank; "
                "multiple bird species use robin in their English name."
            ),
        },
    }
    retrieved_at = utc_now()
    return {
        "schema_version": 1,
        "payload": payload,
        "provenance": {
            "source_name": "GBIF Species API",
            "source_url": "https://www.gbif.org/developer/species",
            "endpoint": "https://api.gbif.org/v1/species/match and /species/search",
            "request_parameters": {"requests": request_log},
            "retrieved_at_utc": retrieved_at,
            "snapshot_version": retrieved_at[:10],
            "licence": "GBIF API terms; taxonomy source datasets retain their own terms",
            "attribution": "GBIF.org",
            "checksum_sha256": canonical_sha256(payload),
            "record_counts": {
                "before": len(resolved) + 1 + len(candidates),
                "after": len(resolved) + 1 + len(candidates),
            },
            "filtering_rules": [
                "Retain compact taxonomic fields only",
                "Candidate searches retain the first accepted Aves species",
            ],
            "known_limitations": [
                "GBIF common-name matching is not a complete disambiguation service",
                "Taxonomy can change after snapshot retrieval",
            ],
        },
    }


def _cell_1km(latitude: float, longitude: float) -> str:
    radius = 6_378_137.0
    clipped_latitude = max(min(latitude, 85.05112878), -85.05112878)
    x = radius * math.radians(longitude)
    y = radius * math.log(math.tan(math.pi / 4 + math.radians(clipped_latitude) / 2))
    grid_reference = f"EPSG3857:{math.floor(x / 1000)}:{math.floor(y / 1000)}"
    # Preserve cell equality for viability counts without publishing a reversible
    # location for any occurrence, including potentially sensitive taxa.
    return f"cell-ref:{hashlib.sha256(grid_reference.encode('utf-8')).hexdigest()[:16]}"


def _hashed_identifier(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _sanitise_occurrence(record: dict[str, Any]) -> dict[str, Any]:
    latitude = record.get("decimalLatitude")
    longitude = record.get("decimalLongitude")
    issues = sorted(record.get("issues") or [])
    uncertainty = record.get("coordinateUncertaintyInMeters")
    status = record.get("occurrenceStatus")
    has_coordinates = isinstance(latitude, (int, float)) and isinstance(longitude, (int, float))
    in_envelope = has_coordinates and -0.5103 <= longitude <= 0.3340 and 51.2868 <= latitude <= 51.6919
    acceptable_status = status in (None, "PRESENT")
    acceptable_uncertainty = uncertainty is None or uncertainty <= 10_000
    fatal_issue = bool(FATAL_GEOSPATIAL_ISSUES.intersection(issues))
    retained = bool(
        has_coordinates
        and in_envelope
        and acceptable_status
        and acceptable_uncertainty
        and not fatal_issue
    )
    media = []
    for item in record.get("media") or []:
        media_licence = item.get("license")
        licence_text = (media_licence or "").lower()
        media.append(
            {
                "identifier_ref": _hashed_identifier(item.get("identifier")),
                "type": item.get("type"),
                "licence": media_licence,
                "reusable": bool(media_licence)
                and (
                    "publicdomain/zero" in licence_text
                    or "creativecommons.org/licenses/by/" in licence_text
                ),
            }
        )
    return {
        "record_ref": _hashed_identifier(record.get("key") or record.get("occurrenceID")),
        "has_coordinate": bool(has_coordinates),
        "occurrence_status": status,
        "issues": issues,
        "coordinate_uncertainty_metres": uncertainty,
        "observation_or_event_date": record.get("eventDate") or record.get("dateIdentified"),
        "dataset_key": record.get("datasetKey") or "missing",
        "basis_of_record": record.get("basisOfRecord"),
        "record_licence": record.get("license"),
        "spatial_cell_1km": _cell_1km(latitude, longitude) if has_coordinates else None,
        "media": media,
        "retained": retained,
    }


def fetch_occurrence_fixture(taxonomy_fixture: dict[str, Any]) -> dict[str, Any]:
    taxa: list[dict[str, Any]] = []
    request_log: list[str] = []
    for taxon in taxonomy_fixture["payload"]["resolved_taxa"]:
        raw, url = get_json(
            "https://api.gbif.org/v1/occurrence/search",
            {
                "taxon_key": taxon["key"],
                "has_coordinate": "true",
                "occurrence_status": "present",
                "geometry": LONDON_ENVELOPE_WKT,
                "limit": 300,
            },
        )
        request_log.append(url)
        records = [_sanitise_occurrence(item) for item in raw.get("results", [])]
        taxa.append(
            {
                "scenario": taxon["scenario"],
                "input": taxon["input"],
                "scientific_name": taxon["scientific_name"],
                "taxon_key": taxon["key"],
                "raw_server_count": raw.get("count", 0),
                "records": records,
            }
        )
    payload = {
        "query_area": {
            "name": "Greater London bounding envelope (engineering feasibility only)",
            "wkt": LONDON_ENVELOPE_WKT,
        },
        "sample_limit_per_taxon": 300,
        "taxa": taxa,
    }
    before = sum(item["raw_server_count"] for item in taxa)
    after = sum(sum(record["retained"] for record in item["records"]) for item in taxa)
    retrieved_at = utc_now()
    return {
        "schema_version": 1,
        "payload": payload,
        "provenance": {
            "source_name": "GBIF Occurrence Search API",
            "source_url": "https://www.gbif.org/developer/occurrence",
            "endpoint": "https://api.gbif.org/v1/occurrence/search",
            "request_parameters": {"requests": request_log, "limit_per_taxon": 300},
            "retrieved_at_utc": retrieved_at,
            "snapshot_version": retrieved_at[:10],
            "licence": "Each record and media object retains its own licence field",
            "attribution": "GBIF.org and the contributing datasets named by datasetKey",
            "checksum_sha256": canonical_sha256(payload),
            "record_counts": {"before": before, "after": after},
            "filtering_rules": [
                "Require numeric coordinates inside the declared London envelope",
                "Require occurrenceStatus=PRESENT when the field is available",
                "Exclude ZERO_COORDINATE, COORDINATE_OUT_OF_RANGE, COORDINATE_INVALID and COUNTRY_COORDINATE_MISMATCH",
                "Exclude known coordinate uncertainty above 10,000 metres; retain missing uncertainty but flag the limitation",
                "Replace coordinates and occurrence/media identifiers with non-reversible hashes of approximately 1 km cells and source identifiers",
            ],
            "known_limitations": [
                "The query uses Greater London's bounding envelope, not an administrative-boundary point-in-polygon test",
                "The bounded 300-record sample is not a population or abundance estimate",
                "GBIF issue flags and uncertainty metadata may be incomplete",
                "Missing uncertainty is retained and must not be read as zero uncertainty",
                "Record licences do not automatically license linked media",
            ],
        },
    }


def fetch_postcode_fixture() -> dict[str, Any]:
    raw, url = get_json("https://api.postcodes.io/postcodes/SW114NJ")
    result = raw.get("result") or {}
    payload = {
        "status": raw.get("status"),
        "postcode": result.get("postcode"),
        "admin_district": result.get("admin_district"),
        "region": result.get("region"),
        "country": result.get("country"),
        "latitude": round(result["latitude"], 3),
        "longitude": round(result["longitude"], 3),
        "quality": result.get("quality"),
    }
    retrieved_at = utc_now()
    return {
        "schema_version": 1,
        "payload": payload,
        "provenance": {
            "source_name": "Postcodes.io",
            "source_url": "https://postcodes.io/",
            "endpoint": url.split("?")[0],
            "request_parameters": {"postcode": "SW11 4NJ"},
            "retrieved_at_utc": retrieved_at,
            "snapshot_version": retrieved_at[:10],
            "licence": "Contains Royal Mail, Ordnance Survey and ONS data under their stated terms",
            "attribution": "Postcodes.io; Royal Mail; Ordnance Survey; Office for National Statistics",
            "checksum_sha256": canonical_sha256(payload),
            "record_counts": {"before": 1, "after": 1},
            "filtering_rules": ["Round coordinates to three decimal places", "Retain locality fields needed for London validation"],
            "known_limitations": ["A postcode centroid is not a user's precise position", "Postcode geography can change"],
        },
    }


def fetch_weather_fixture(latitude: float, longitude: float) -> dict[str, Any]:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        "timezone": "Europe/London",
        "forecast_days": 3,
    }
    raw, url = get_json("https://api.open-meteo.com/v1/forecast", params)
    payload = {
        "latitude": raw.get("latitude"),
        "longitude": raw.get("longitude"),
        "timezone": raw.get("timezone"),
        "daily_units": raw.get("daily_units"),
        "daily": raw.get("daily"),
    }
    retrieved_at = utc_now()
    return {
        "schema_version": 1,
        "payload": payload,
        "provenance": {
            "source_name": "Open-Meteo Forecast API",
            "source_url": "https://open-meteo.com/",
            "endpoint": url.split("?")[0],
            "request_parameters": params,
            "retrieved_at_utc": retrieved_at,
            "snapshot_version": retrieved_at[:10],
            "licence": "CC BY 4.0 for Open-Meteo weather data; upstream model terms also apply",
            "attribution": "Weather data by Open-Meteo.com",
            "checksum_sha256": canonical_sha256(payload),
            "record_counts": {"before": len(payload.get("daily", {}).get("time", [])), "after": len(payload.get("daily", {}).get("time", []))},
            "filtering_rules": ["Retain only three daily planning-feasibility fields plus WMO weather code"],
            "known_limitations": ["Forecasts change on refresh", "Weather feasibility does not predict bird sightings"],
        },
    }
