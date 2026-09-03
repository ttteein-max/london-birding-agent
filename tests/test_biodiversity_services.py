"""Location, weather, public-site and constraint service tests."""

from datetime import date
from typing import Any

from app.biodiversity.models import (
    ExpeditionRequest,
    LocationStatus,
    PublicSiteSearchResult,
    PublicSiteSearchStatus,
    RainPreference,
    ResolvedTaxon,
    SiteEvidenceTier,
    SiteSearchAction,
    TaxonStatus,
    WeatherStatus,
    WGS84Point,
)
from app.biodiversity.repositories import (
    FixturePlaceGeocoderRepository,
    FixturePostcodeRepository,
    FixtureWeatherRepository,
    LivePlaceGeocoderRepository,
    SnapshotGreenSpaceRepository,
    normalise_postcode,
)
from app.biodiversity.orchestration import run_expedition_backend
from app.biodiversity.tools import (
    find_public_green_spaces,
    get_weather_context,
    lookup_uk_postcode,
    resolve_geocoded_location,
    validate_expedition_constraints,
)


def request(**overrides: Any) -> ExpeditionRequest:
    values: dict[str, Any] = {
        "bird_input": "Common woodpigeon",
        "postcode": "sw114nj",
        "target_local_date": date(2026, 8, 31),
        "duration_hours": 2,
    }
    values.update(overrides)
    return ExpeditionRequest(**values)


def test_postcode_normalisation_and_london_boundary() -> None:
    assert normalise_postcode(" sw11  4nj ") == "SW11 4NJ"
    location = lookup_uk_postcode(request(), repository=FixturePostcodeRepository())
    assert location.status == LocationStatus.resolved
    assert location.normalised_postcode == "SW11 4NJ"
    assert location.within_greater_london is True
    assert location.british_national_grid.crs == "EPSG:27700"

    outside_postcode = lookup_uk_postcode(
        request(postcode="OX1 1AA"), repository=FixturePostcodeRepository()
    )
    assert outside_postcode.status == LocationStatus.outside_supported_area
    assert outside_postcode.administrative_district == "Oxford"

    outside = lookup_uk_postcode(
        request(
            postcode=None, start_point=WGS84Point(longitude=-0.1278, latitude=51.752)
        ),
        repository=FixturePostcodeRepository(),
    )
    assert outside.status == LocationStatus.outside_supported_area


def test_map_point_is_rounded_and_not_treated_as_address() -> None:
    location = lookup_uk_postcode(
        request(
            postcode=None,
            start_point=WGS84Point(longitude=-0.12761234, latitude=51.50745678),
        ),
        repository=FixturePostcodeRepository(),
    )
    assert location.status == LocationStatus.resolved
    assert location.rounded_start_point.longitude == -0.1276
    assert location.administrative_district is None
    assert "not a precise home address" in location.provenance.limitations[0]


def test_fixture_named_place_is_real_geocoder_data_and_london_validated() -> None:
    response = FixturePlaceGeocoderRepository().search("Kensal Road")
    candidates = response["payload"]["candidates"]
    assert [item["postcode"] for item in candidates] == [
        "W10 5DD",
        "W10 5BA",
        "W10 5DA",
    ]
    selected = {"candidate_id": "place-1", **candidates[0]}
    location = resolve_geocoded_location(
        selected,
        provenance=response["provenance"],
    )
    assert location.status == LocationStatus.resolved
    assert location.input_kind == "geocoded_place"
    assert location.administrative_district == (
        "Royal Borough of Kensington and Chelsea"
    )
    assert location.provenance.attribution == "© OpenStreetMap contributors"
    assert "representative planning point" in location.message


def test_live_geocoder_uses_bounded_london_query_and_drops_provider_ids(
    monkeypatch,
) -> None:
    class StubClient:
        def __init__(self) -> None:
            self.call: tuple[str, dict[str, Any]] | None = None

        def get_json(self, base_url: str, params: dict[str, Any]):
            self.call = (base_url, params)
            return ([{
                "place_id": 123,
                "osm_id": 456,
                "lat": "51.5257048",
                "lon": "-0.2075391",
                "category": "highway",
                "type": "unclassified",
                "name": "Kensal Road",
                "address": {
                    "road": "Kensal Road",
                    "suburb": "North Kensington",
                    "city_district": "Royal Borough of Kensington and Chelsea",
                    "postcode": "W10 5DD",
                    "country_code": "gb",
                },
            }], "https://nominatim.example/search?redacted")

    monkeypatch.setattr(
        LivePlaceGeocoderRepository,
        "_minimum_interval_seconds",
        0,
    )
    client = StubClient()
    result = LivePlaceGeocoderRepository(client=client).search("Kensal Road")
    assert client.call is not None
    base_url, params = client.call
    assert base_url == "https://nominatim.openstreetmap.org/search"
    assert params["countrycodes"] == "gb"
    assert params["bounded"] == 1
    assert params["limit"] == 3
    candidate = result["payload"]["candidates"][0]
    assert candidate["label"] == "Kensal Road"
    assert "place_id" not in candidate
    assert "osm_id" not in candidate


def test_weather_requires_exact_requested_date() -> None:
    location = lookup_uk_postcode(request(), repository=FixturePostcodeRepository())
    available = get_weather_context(
        location, date(2026, 8, 31), repository=FixtureWeatherRepository()
    )
    assert available.status == WeatherStatus.available
    assert available.maximum_temperature_c == 21.0
    unavailable = get_weather_context(
        location, date(2026, 7, 1), repository=FixtureWeatherRepository()
    )
    assert unavailable.status == WeatherStatus.weather_unavailable_for_requested_date
    assert unavailable.maximum_temperature_c is None


def test_public_site_access_and_distance_language() -> None:
    result = run_expedition_backend(request(target_local_date=date(2026, 6, 15)))
    assert result.bundle.site_search.status == PublicSiteSearchStatus.success
    assert result.bundle.site_search.candidates
    assert {
        item.access_certainty.value for item in result.bundle.site_search.candidates
    } <= {
        "explicit_public",
        "unspecified",
    }
    assert all(
        "walking-route distance" in item.limitations[0]
        for item in result.bundle.site_search.candidates
    )


def test_occurrence_none_cannot_produce_site_recommendations() -> None:
    location = lookup_uk_postcode(request(), repository=FixturePostcodeRepository())
    result = find_public_green_spaces(
        location,
        search_radius_km=2,
        occurrence=None,
        repository=SnapshotGreenSpaceRepository(),
        limit=20,
    )
    assert (
        result.status == PublicSiteSearchStatus.not_applicable_without_strong_evidence
    )
    assert result.candidates == []
    assert result.contextual_sites
    assert all(
        site.evidence_tier == SiteEvidenceTier.ungrounded
        and not site.associated_safe_cell_ids
        for site in result.contextual_sites
    )
    assert result.suggested_actions == [SiteSearchAction.retry_occurrence_source]


def test_walking_limit_is_accepted_but_never_route_validated() -> None:
    req = request(
        maximum_walking_distance_km=3,
        rain_preference=RainPreference.avoid_heavy_rain,
    )
    location = lookup_uk_postcode(req, repository=FixturePostcodeRepository())
    weather = get_weather_context(
        location, req.target_local_date, repository=FixtureWeatherRepository()
    )
    taxon = ResolvedTaxon(
        status=TaxonStatus.resolved,
        original_input="Common woodpigeon",
        normalised_input="common woodpigeon",
        accepted_taxon_key=2495455,
        scientific_name="Columba palumbus",
        canonical_name="Columba palumbus",
        rank="SPECIES",
        taxonomic_status="ACCEPTED",
        resolution_method="fixture",
        rationale="Resolved.",
    )
    constraints = validate_expedition_constraints(
        req,
        location,
        taxon,
        None,
        weather,
        PublicSiteSearchResult(
            candidates=[],
            searched_radius_km=req.search_radius_km,
            status=PublicSiteSearchStatus.not_applicable_without_strong_evidence,
        ),
    )
    routing = next(item for item in constraints if item.code == "routing_not_available")
    assert routing.status.value == "unresolved"
    assert "not route-validated" in routing.message
    radius = next(item for item in constraints if item.code == "search_radius")
    assert "distinct from walking distance" in radius.message
