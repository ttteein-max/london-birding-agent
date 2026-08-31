"""Bounded public-API transport and non-bird Phase 0 fixture refreshers."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.feasibility.core import canonical_sha256

USER_AGENT = (
    "London-Biodiversity-Expedition-Planner-Phase0.1/0.1 "
    "(local reproducible data-hardening study)"
)
REQUEST_TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 3
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
        url = f"{base_url}?{urlencode(params, doseq=True)}"
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
            "filtering_rules": [
                "Round coordinates to three decimal places",
                "Retain locality fields needed for London validation",
            ],
            "known_limitations": [
                "A postcode centroid is not a user's precise position",
                "Postcode geography can change",
            ],
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
    count = len(payload.get("daily", {}).get("time", []))
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
            "record_counts": {"before": count, "after": count},
            "filtering_rules": [
                "Retain only three daily planning-feasibility fields plus WMO weather code"
            ],
            "known_limitations": [
                "Forecasts change on refresh",
                "Weather feasibility does not predict bird sightings",
            ],
        },
    }
