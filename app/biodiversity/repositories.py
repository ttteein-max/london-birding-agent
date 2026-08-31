"""Injected fixture and live repositories for Phase 1 deterministic tools."""

from __future__ import annotations

import json
import re
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


class OccurrenceRepository(Protocol):
    def search(self, resolution: TaxonomyOutcome, *, target_month: int) -> dict[str, Any]: ...


class PostcodeRepository(Protocol):
    def lookup(self, normalised_postcode: str) -> dict[str, Any]: ...


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
        self.user_agent = "London-Biodiversity-Expedition-Planner-Phase1/1.0"

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


class FixtureOccurrenceRepository:
    reference_year = 2026

    def __init__(
        self, path: Path = FIXTURE_DIR / "gbif-occurrences-london.json"
    ) -> None:
        self.path = path

    def search(self, resolution: TaxonomyOutcome, *, target_month: int) -> dict[str, Any]:
        fixture = _load_fixture(self.path)
        for result in fixture["payload"]["results"]:
            taxonomy = result.get("taxonomy") or {}
            if (
                taxonomy.get("accepted_taxon_key") == resolution.accepted_taxon_key
                and result.get("target_month") == target_month
            ):
                output = deepcopy(result)
                output["fixture_provenance"] = fixture["provenance"]
                return output
        raise SourceFailure(
            ToolErrorCode.missing_or_corrupt_fixture,
            "No saved occurrence response exists for this taxon and seasonal month.",
            source="GBIF occurrence fixture",
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

    def search(self, resolution: TaxonomyOutcome, *, target_month: int) -> dict[str, Any]:
        try:
            return retrieve_occurrences(
                resolution,
                london_boundary=self.boundary,
                policy=RetrievalPolicy(target_month=target_month),
                fetch_json=self.client.get_json,
                current_year=self.current_year,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceFailure(
                ToolErrorCode.malformed_upstream_response,
                "GBIF occurrence response did not match the expected shape.",
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
