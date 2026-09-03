"""Injected fixture and live repositories for Phase 1 deterministic tools."""

from __future__ import annotations

import json
import re
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from pydantic import ValidationError

from app.biodiversity.models import SourceFailure, ToolErrorCode
from app.feasibility.core import (
    FIXTURE_DIR,
    PROJECT_ROOT,
    canonical_sha256,
    file_sha256,
    validate_provenance,
)
from app.feasibility.occurrence import RetrievalPolicy, retrieve_occurrences
from app.feasibility.spatial import load_london_boundary
from app.feasibility.taxonomy import GBIFBirdNameResolver, TaxonomyOutcome, normalise_name


class TaxonomyRepository(Protocol):
    def resolve(self, bird_input: str) -> TaxonomyOutcome: ...

    def related(
        self,
        accepted_taxon_key: int,
        *,
        limit: int = 6,
        request_budget: int = 3,
    ) -> list[dict[str, Any]]: ...


class OccurrenceRepository(Protocol):
    def search(
        self,
        resolution: TaxonomyOutcome,
        *,
        target_month: int,
        seasonal_window_radius_months: int = 1,
    ) -> dict[str, Any]: ...

    def preview(
        self,
        resolution: TaxonomyOutcome,
        *,
        target_month: int,
        seasonal_window_radius_months: int = 1,
    ) -> dict[str, Any]: ...


class PostcodeRepository(Protocol):
    def lookup(self, normalised_postcode: str) -> dict[str, Any]: ...


class PlaceGeocoderRepository(Protocol):
    def search(self, query: str, *, limit: int = 3) -> dict[str, Any]: ...


class WeatherRepository(Protocol):
    def daily(self, longitude: float, latitude: float, requested_date: str) -> dict[str, Any]: ...


class GreenSpaceRepository(Protocol):
    def snapshot(self) -> tuple[list[dict[str, Any]], dict[str, Any]]: ...


class BoundedJsonClient:
    """Sequential JSON GET client with explicit timeouts and bounded retries."""

    def __init__(self, *, timeout_seconds: float = 15, max_attempts: int = 3) -> None:
        if timeout_seconds <= 0 or not 1 <= max_attempts <= 3:
            raise ValueError("timeout must be positive and max_attempts must be 1..3")
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.user_agent = "London-Biodiversity-Expedition-Planner/4.0"

    def get_json(
        self, base_url: str, params: dict[str, Any] | None = None
    ) -> tuple[Any, str]:
        url = base_url
        if params:
            url = f"{base_url}?{urlencode(params, doseq=True)}"
        request = Request(
            url,
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
        )
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                    if response.status != 200:
                        raise SourceFailure(
                            ToolErrorCode.source_unavailable,
                            f"GET returned HTTP {response.status}",
                            source=base_url,
                            retryable=response.status >= 500,
                        )
                    try:
                        return json.load(response), url
                    except json.JSONDecodeError as exc:
                        raise SourceFailure(
                            ToolErrorCode.malformed_upstream_response,
                            "Upstream response was not valid JSON.",
                            source=base_url,
                        ) from exc
            except HTTPError as exc:
                if exc.code == 429:
                    last_error = SourceFailure(
                        ToolErrorCode.source_rate_limit,
                        "Upstream source rate limited the request.",
                        source=base_url,
                        retryable=True,
                    )
                elif 500 <= exc.code < 600:
                    last_error = SourceFailure(
                        ToolErrorCode.source_unavailable,
                        f"Upstream source returned HTTP {exc.code}.",
                        source=base_url,
                        retryable=True,
                    )
                else:
                    raise SourceFailure(
                        ToolErrorCode.source_unavailable,
                        f"Upstream source returned HTTP {exc.code}.",
                        source=base_url,
                    ) from exc
            except TimeoutError:
                last_error = SourceFailure(
                    ToolErrorCode.source_timeout,
                    "Upstream source timed out.",
                    source=base_url,
                    retryable=True,
                )
            except URLError as exc:
                last_error = SourceFailure(
                    ToolErrorCode.source_unavailable,
                    f"Upstream source could not be reached: {exc.reason}",
                    source=base_url,
                    retryable=True,
                )
            if attempt < self.max_attempts:
                time.sleep(2 ** (attempt - 1))
        assert last_error is not None
        raise last_error


def _load_fixture(path: Path) -> dict[str, Any]:
    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceFailure(
            ToolErrorCode.missing_or_corrupt_fixture,
            f"Fixture is missing or corrupt: {path.name}",
            source=str(path),
        ) from exc
    schema_version = fixture.get("schema_version")
    provenance = fixture.get("provenance")
    payload = fixture.get("payload")
    if not isinstance(provenance, dict) or validate_provenance(
        provenance, schema_version=schema_version
    ):
        raise SourceFailure(
            ToolErrorCode.missing_or_corrupt_fixture,
            f"Fixture provenance is invalid: {path.name}",
            source=str(path),
        )
    if canonical_sha256(payload) != provenance.get("checksum_sha256"):
        raise SourceFailure(
            ToolErrorCode.missing_or_corrupt_fixture,
            f"Fixture checksum does not match: {path.name}",
            source=str(path),
        )
    return fixture


class FixtureTaxonomyRepository:
    """Saved resolver outcomes; the live path continues to use GBIFBirdNameResolver."""

    def __init__(self, path: Path = FIXTURE_DIR / "gbif-species.json") -> None:
        self.path = path

    def resolve(self, bird_input: str) -> TaxonomyOutcome:
        fixture = _load_fixture(self.path)
        query = normalise_name(bird_input)
        for result in fixture["payload"]["results"]:
            if result.get("normalised_input") == query:
                allowed = TaxonomyOutcome.model_fields.keys()
                return TaxonomyOutcome.model_validate(
                    {key: value for key, value in result.items() if key in allowed}
                )
        raise SourceFailure(
            ToolErrorCode.missing_or_corrupt_fixture,
            "No saved taxonomy response exists for this input in fixture mode.",
            source="GBIF taxonomy fixture",
        )

    def evidence_provenance(self) -> dict[str, Any]:
        return deepcopy(_load_fixture(self.path)["provenance"])

    def related(
        self,
        accepted_taxon_key: int,
        *,
        limit: int = 6,
        request_budget: int = 3,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 6 or not 1 <= request_budget <= 3:
            raise ValueError("related taxonomy limits exceed the deterministic budget")
        fixture = _load_fixture(self.path.with_name("gbif-related-taxa.json"))
        for result in fixture["payload"]["results"]:
            if result.get("accepted_taxon_key") == accepted_taxon_key:
                return deepcopy(result.get("candidates", []))[:limit]
        return []


class LiveTaxonomyRepository:
    def __init__(self, client: BoundedJsonClient | None = None) -> None:
        self.client = client or BoundedJsonClient()
        self.resolver = GBIFBirdNameResolver(self.client.get_json)

    def resolve(self, bird_input: str) -> TaxonomyOutcome:
        try:
            return self.resolver.resolve(bird_input)
        except (KeyError, TypeError, ValidationError) as exc:
            raise SourceFailure(
                ToolErrorCode.malformed_upstream_response,
                "GBIF taxonomy response did not match the expected shape.",
                source="GBIF Species API",
            ) from exc

    def related(
        self,
        accepted_taxon_key: int,
        *,
        limit: int = 6,
        request_budget: int = 3,
    ) -> list[dict[str, Any]]:
        """Return accepted Aves species from the same genus, then family."""

        if not 1 <= limit <= 6 or not 2 <= request_budget <= 3:
            raise ValueError("related taxonomy limits exceed the deterministic budget")
        source, _ = self.client.get_json(
            f"https://api.gbif.org/v1/species/{accepted_taxon_key}"
        )
        if not isinstance(source, dict) or source.get("class") != "Aves":
            raise SourceFailure(
                ToolErrorCode.malformed_upstream_response,
                "GBIF related-taxon source record was missing Aves hierarchy.",
                source="GBIF Species API",
            )

        candidates: dict[int, dict[str, Any]] = {}

        def collect(higher_key: Any, relation_level: str, relation_basis: str) -> None:
            if higher_key is None or len(candidates) >= limit:
                return
            response, _ = self.client.get_json(
                "https://api.gbif.org/v1/species/search",
                {
                    "highertaxon_key": higher_key,
                    "rank": "SPECIES",
                    "status": "ACCEPTED",
                    "limit": 50,
                },
            )
            for record in response.get("results", []):
                key = record.get("acceptedKey") or record.get("speciesKey") or record.get("key")
                status = record.get("taxonomicStatus") or record.get("status")
                if (
                    key in (None, accepted_taxon_key)
                    or record.get("class") != "Aves"
                    or record.get("rank") != "SPECIES"
                    or status != "ACCEPTED"
                ):
                    continue
                english = next(
                    (
                        item.get("vernacularName")
                        for item in record.get("vernacularNames", [])
                        if item.get("vernacularName")
                        and item.get("language") in (None, "", "eng")
                    ),
                    None,
                )
                candidates[int(key)] = {
                    "accepted_taxon_key": int(key),
                    "common_name": english,
                    "scientific_name": record.get("scientificName"),
                    "canonical_name": record.get("canonicalName") or record.get("species"),
                    "rank": record.get("rank"),
                    "taxonomic_status": status,
                    "class_name": record.get("class"),
                    "order": record.get("order"),
                    "family": record.get("family"),
                    "genus": record.get("genus"),
                    "resolution_method": "gbif_related_taxonomy_query",
                    "confidence": record.get("confidence"),
                    "relation_level": relation_level,
                    "relation_basis": relation_basis,
                }

        collect(source.get("genusKey"), "same_genus", f"genus:{source.get('genus')}")
        if len(candidates) < limit and request_budget >= 3:
            collect(source.get("familyKey"), "same_family", f"family:{source.get('family')}")
        return sorted(
            candidates.values(),
            key=lambda item: (
                item["relation_level"] != "same_genus",
                str(item["canonical_name"]).casefold(),
                item["accepted_taxon_key"],
            ),
        )[:limit]


class FixtureOccurrenceRepository:
    reference_year = 2026

    def __init__(
        self, path: Path = FIXTURE_DIR / "gbif-occurrences-london.json"
    ) -> None:
        self.path = path

    def search(
        self,
        resolution: TaxonomyOutcome,
        *,
        target_month: int,
        seasonal_window_radius_months: int = 1,
    ) -> dict[str, Any]:
        fixture = _load_fixture(self.path)
        for result in fixture["payload"]["results"]:
            taxonomy = result.get("taxonomy") or {}
            if (
                taxonomy.get("accepted_taxon_key") == resolution.accepted_taxon_key
                and result.get("target_month") == target_month
                and (
                    seasonal_window_radius_months == 1
                    or result.get("seasonal_window_radius_months")
                    == seasonal_window_radius_months
                )
            ):
                output = deepcopy(result)
                output["fixture_provenance"] = fixture["provenance"]
                return output
        try:
            return self.preview(
                resolution,
                target_month=target_month,
                seasonal_window_radius_months=seasonal_window_radius_months,
            )
        except SourceFailure:
            pass
        raise SourceFailure(
            ToolErrorCode.missing_or_corrupt_fixture,
            "No saved occurrence response exists for this taxon and seasonal month.",
            source="GBIF occurrence fixture",
        )

    def preview(
        self,
        resolution: TaxonomyOutcome,
        *,
        target_month: int,
        seasonal_window_radius_months: int = 1,
    ) -> dict[str, Any]:
        preview_path = self.path.with_name("gbif-occurrence-previews.json")
        fixture = _load_fixture(preview_path)
        for result in fixture["payload"]["results"]:
            if (
                result.get("accepted_taxon_key") == resolution.accepted_taxon_key
                and result.get("target_month") == target_month
                and result.get("seasonal_window_radius_months", 1)
                == seasonal_window_radius_months
            ):
                api_response = result.get("api_response") or {}
                if not isinstance(api_response.get("results"), list):
                    raise SourceFailure(
                        ToolErrorCode.missing_or_corrupt_fixture,
                        "Saved preview omitted its API-shaped results list.",
                        source="GBIF occurrence preview fixture",
                    )
                sampled = len(api_response["results"])
                output = {
                    "evidence_outcome": "insufficient_evidence",
                    "evidence_reason": "no_retained_records",
                    "records": [],
                    "safe_map_cells": [],
                    "counts": {
                        "server_match_count": int(api_response.get("count", 0)),
                        "sampled_count": sampled,
                        "deduplicated_count": sampled,
                        "duplicates_removed": 0,
                        "rejected_count": 0,
                        "retained_total_count": sampled,
                        "ranking_eligible_count": 0,
                    },
                    "quality_summary": {"warnings": []},
                    "dataset_diversity": {
                        "retained_dataset_count": 0,
                        "ranking_dataset_count": 0,
                    },
                    "retrieval": {
                        "seasonal_months": sorted(
                            {
                                ((target_month + offset - 1) % 12) + 1
                                for offset in range(
                                    -seasonal_window_radius_months,
                                    seasonal_window_radius_months + 1,
                                )
                            }
                        ),
                        "year_window": [2021, 2026],
                        "page_size": int(api_response.get("limit", 100)),
                        "page_budget": 1,
                        "request_budget": 1,
                        "pages_requested": 1,
                        "stopped_early": True,
                        "stop_reason": "server_results_exhausted",
                        "request_urls": [],
                        "deduplication": {
                            "exact_duplicates_removed": 0,
                            "possible_duplicates_retained": 0,
                            "method": "No records to deduplicate.",
                        },
                    },
                }
                output["fixture_provenance"] = fixture["provenance"]
                return output
        raise SourceFailure(
            ToolErrorCode.missing_or_corrupt_fixture,
            "No saved bounded evidence preview exists for this candidate.",
            source="GBIF occurrence preview fixture",
        )


class LiveOccurrenceRepository:
    def __init__(
        self,
        client: BoundedJsonClient | None = None,
        *,
        current_year: int | None = None,
    ) -> None:
        self.client = client or BoundedJsonClient()
        self.current_year = current_year
        self.boundary = load_london_boundary()

    def search(
        self,
        resolution: TaxonomyOutcome,
        *,
        target_month: int,
        seasonal_window_radius_months: int = 1,
    ) -> dict[str, Any]:
        try:
            return retrieve_occurrences(
                resolution,
                london_boundary=self.boundary,
                policy=RetrievalPolicy(
                    target_month=target_month,
                    seasonal_window_radius_months=seasonal_window_radius_months,
                ),
                fetch_json=self.client.get_json,
                current_year=self.current_year,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceFailure(
                ToolErrorCode.malformed_upstream_response,
                "GBIF occurrence response did not match the expected shape.",
                source="GBIF Occurrence Search API",
            ) from exc

    def preview(
        self,
        resolution: TaxonomyOutcome,
        *,
        target_month: int,
        seasonal_window_radius_months: int = 1,
    ) -> dict[str, Any]:
        """Use exactly one bounded page/request for an interrupt preview."""

        try:
            return retrieve_occurrences(
                resolution,
                london_boundary=self.boundary,
                policy=RetrievalPolicy(
                    page_size=100,
                    max_pages=1,
                    request_budget=1,
                    target_month=target_month,
                    seasonal_window_radius_months=seasonal_window_radius_months,
                ),
                fetch_json=self.client.get_json,
                current_year=self.current_year,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceFailure(
                ToolErrorCode.malformed_upstream_response,
                "GBIF occurrence preview did not match the expected shape.",
                source="GBIF Occurrence Search API",
            ) from exc


class FixturePostcodeRepository:
    def __init__(self, directory: Path = FIXTURE_DIR) -> None:
        self.directory = directory

    def lookup(self, normalised_postcode: str) -> dict[str, Any]:
        for path in sorted(self.directory.glob("postcodes-*.json")):
            fixture = _load_fixture(path)
            if normalise_postcode(fixture["payload"].get("postcode", "")) == normalised_postcode:
                return deepcopy(fixture)
        raise SourceFailure(
            ToolErrorCode.postcode_not_found,
            "Postcode is not present in the deterministic fixture set.",
            source="Postcodes.io fixture",
        )


class LivePostcodeRepository:
    def __init__(self, client: BoundedJsonClient | None = None) -> None:
        self.client = client or BoundedJsonClient()

    def lookup(self, normalised_postcode: str) -> dict[str, Any]:
        try:
            raw, url = self.client.get_json(
                f"https://api.postcodes.io/postcodes/{quote(normalised_postcode.replace(' ', ''))}"
            )
        except SourceFailure as exc:
            if "HTTP 404" in exc.message:
                raise SourceFailure(
                    ToolErrorCode.postcode_not_found,
                    "Postcodes.io did not find that postcode.",
                    source="Postcodes.io",
                ) from exc
            raise
        result = raw.get("result") if isinstance(raw, dict) else None
        if raw.get("status") == 404 or result is None:
            raise SourceFailure(
                ToolErrorCode.postcode_not_found,
                "Postcodes.io did not find that postcode.",
                source="Postcodes.io",
            )
        required = ("postcode", "latitude", "longitude")
        if any(result.get(field) is None for field in required):
            raise SourceFailure(
                ToolErrorCode.malformed_upstream_response,
                "Postcodes.io response omitted required fields.",
                source="Postcodes.io",
            )
        return {
            "schema_version": 1,
            "payload": {
                "status": raw.get("status"),
                "postcode": result["postcode"],
                "admin_district": result.get("admin_district"),
                "region": result.get("region"),
                "country": result.get("country"),
                "latitude": result["latitude"],
                "longitude": result["longitude"],
                "quality": result.get("quality"),
            },
            "live_url": url,
        }


class FixturePlaceGeocoderRepository:
    """Versioned Nominatim results used by offline tests and demonstrations."""

    def __init__(
        self,
        path: Path = FIXTURE_DIR / "nominatim-london-places.json",
    ) -> None:
        self.path = path

    def search(self, query: str, *, limit: int = 3) -> dict[str, Any]:
        if not 1 <= limit <= 5:
            raise ValueError("geocoder result limit must be 1..5")
        fixture = _load_fixture(self.path)
        normalised = " ".join(query.casefold().split())
        for result in fixture["payload"]["results"]:
            if result.get("normalised_query") == normalised:
                return {
                    "schema_version": fixture["schema_version"],
                    "payload": {
                        "query": query,
                        "candidates": deepcopy(result.get("candidates") or [])[:limit],
                    },
                    "provenance": deepcopy(fixture["provenance"]),
                }
        return {
            "schema_version": fixture["schema_version"],
            "payload": {"query": query, "candidates": []},
            "provenance": deepcopy(fixture["provenance"]),
        }


class LivePlaceGeocoderRepository:
    """Low-rate Nominatim client for submitted London place searches."""

    _request_lock = threading.Lock()
    _last_request_started = 0.0
    _minimum_interval_seconds = 1.0

    def __init__(self, client: BoundedJsonClient | None = None) -> None:
        self.client = client or BoundedJsonClient(max_attempts=2)

    @classmethod
    def _wait_for_public_service_budget(cls) -> None:
        elapsed = time.monotonic() - cls._last_request_started
        if elapsed < cls._minimum_interval_seconds:
            time.sleep(cls._minimum_interval_seconds - elapsed)
        cls._last_request_started = time.monotonic()

    @staticmethod
    def _candidate(record: dict[str, Any]) -> dict[str, Any] | None:
        address = record.get("address")
        if not isinstance(address, dict) or address.get("country_code") != "gb":
            return None
        try:
            longitude = float(record["lon"])
            latitude = float(record["lat"])
        except (KeyError, TypeError, ValueError):
            return None
        label = (
            record.get("name")
            or address.get("road")
            or address.get("neighbourhood")
            or address.get("suburb")
            or address.get("city")
        )
        if not isinstance(label, str) or not label.strip():
            return None
        locality = (
            address.get("neighbourhood")
            or address.get("suburb")
            or address.get("quarter")
        )
        district = (
            address.get("city_district")
            or address.get("borough")
            or address.get("county")
            or address.get("city")
        )
        return {
            "label": label.strip(),
            "locality": str(locality).strip() if locality else None,
            "administrative_district": str(district).strip() if district else None,
            "postcode": (
                str(address["postcode"]).strip() if address.get("postcode") else None
            ),
            "category": (
                str(record["category"]).strip() if record.get("category") else None
            ),
            "place_type": (
                str(record["type"]).strip() if record.get("type") else None
            ),
            "longitude": longitude,
            "latitude": latitude,
        }

    def search(self, query: str, *, limit: int = 3) -> dict[str, Any]:
        if not 1 <= limit <= 5:
            raise ValueError("geocoder result limit must be 1..5")
        with self._request_lock:
            self._wait_for_public_service_budget()
            raw, _url = self.client.get_json(
                "https://nominatim.openstreetmap.org/search",
                {
                    "q": f"{query}, London, United Kingdom",
                    "format": "jsonv2",
                    "addressdetails": 1,
                    "limit": limit,
                    "countrycodes": "gb",
                    "viewbox": "-0.5103,51.2868,0.3340,51.6919",
                    "bounded": 1,
                    "accept-language": "en",
                    "layer": "address",
                    "dedupe": 1,
                },
            )
        if not isinstance(raw, list):
            raise SourceFailure(
                ToolErrorCode.malformed_upstream_response,
                "Nominatim response was not a result list.",
                source="Nominatim Search API",
            )
        candidates = [
            candidate
            for record in raw[:limit]
            if isinstance(record, dict)
            if (candidate := self._candidate(record)) is not None
        ]
        if raw and not candidates:
            raise SourceFailure(
                ToolErrorCode.malformed_upstream_response,
                "Nominatim results did not match the expected safe shape.",
                source="Nominatim Search API",
            )
        return {
            "schema_version": 1,
            "payload": {"query": query, "candidates": candidates},
            "provenance": {
                "source_name": "Nominatim Search API",
                "source_url": "https://nominatim.org/release-docs/latest/api/Search/",
                "retrieved_at_utc": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
                "licence": "Open Database Licence (ODbL) 1.0",
                "attribution": "© OpenStreetMap contributors",
                "known_limitations": [
                    "A geocoder match is a representative planning point, not a precise entrance.",
                    "Roads may be split into multiple candidate segments.",
                ],
            },
        }


class FixtureWeatherRepository:
    def __init__(self, path: Path = FIXTURE_DIR / "open-meteo-london.json") -> None:
        self.path = path

    def daily(self, longitude: float, latitude: float, requested_date: str) -> dict[str, Any]:
        fixture = _load_fixture(self.path)
        payload = fixture["payload"]
        if abs(payload["longitude"] - longitude) > 0.05 or abs(payload["latitude"] - latitude) > 0.05:
            raise SourceFailure(
                ToolErrorCode.missing_or_corrupt_fixture,
                "Saved weather context is not spatially suitable for this start point.",
                source="Open-Meteo fixture",
            )
        output = deepcopy(fixture)
        output["requested_date"] = requested_date
        return output


class LiveWeatherRepository:
    def __init__(self, client: BoundedJsonClient | None = None) -> None:
        self.client = client or BoundedJsonClient()

    def daily(self, longitude: float, latitude: float, requested_date: str) -> dict[str, Any]:
        raw, url = self.client.get_json(
            "https://api.open-meteo.com/v1/forecast",
            {
                "latitude": latitude,
                "longitude": longitude,
                "daily": (
                    "temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max,precipitation_sum,weather_code"
                ),
                "timezone": "Europe/London",
                "start_date": requested_date,
                "end_date": requested_date,
            },
        )
        if not isinstance(raw, dict) or not isinstance(raw.get("daily"), dict):
            raise SourceFailure(
                ToolErrorCode.malformed_upstream_response,
                "Open-Meteo response omitted daily weather data.",
                source="Open-Meteo",
            )
        return {"schema_version": 1, "payload": raw, "live_url": url}


class SnapshotGreenSpaceRepository:
    def __init__(self, directory: Path = PROJECT_ROOT / "data" / "osm") -> None:
        self.directory = directory

    def snapshot(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        artifacts = sorted(self.directory.glob("london-green-space-candidates-*.geojson"))
        provenance_paths = sorted(
            self.directory.glob("london-green-space-candidates-*.provenance.json")
        )
        if len(artifacts) != 1 or len(provenance_paths) != 1:
            raise SourceFailure(
                ToolErrorCode.missing_or_corrupt_fixture,
                "Expected exactly one versioned OSM green-space snapshot.",
                source=str(self.directory),
            )
        provenance = json.loads(provenance_paths[0].read_text(encoding="utf-8"))
        if file_sha256(artifacts[0]) != provenance.get("checksum_sha256"):
            raise SourceFailure(
                ToolErrorCode.missing_or_corrupt_fixture,
                "OSM green-space snapshot checksum does not match provenance.",
                source=str(artifacts[0]),
            )
        artifact = json.loads(artifacts[0].read_text(encoding="utf-8"))
        features = artifact.get("features")
        if not isinstance(features, list) or len(features) != provenance.get("feature_count"):
            raise SourceFailure(
                ToolErrorCode.missing_or_corrupt_fixture,
                "OSM green-space snapshot count is inconsistent.",
                source=str(artifacts[0]),
            )
        return features, provenance


def normalise_postcode(value: str) -> str:
    compact = "".join(value.upper().split())
    normalised = f"{compact[:-3]} {compact[-3:]}" if len(compact) >= 3 else compact
    if not re.fullmatch(
        r"(?:GIR 0AA|[A-Z]{1,2}[0-9][0-9A-Z]? [0-9][A-Z]{2})",
        normalised,
    ):
        raise ValueError("postcode must contain a valid outward and inward code shape")
    return normalised
