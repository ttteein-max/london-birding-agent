"""Fixed-order Phase 1 orchestration; no LLM or LangGraph tool routing."""

from __future__ import annotations

from dataclasses import dataclass

from app.biodiversity.models import (
    BackendResult,
    ConstraintStatus,
    EvidenceOutcome,
    ExpeditionPlan,
    ExpeditionRequest,
    LocationStatus,
    PublicSiteSearchResult,
)
from app.biodiversity.repositories import (
    FixtureOccurrenceRepository,
    FixturePostcodeRepository,
    FixtureTaxonomyRepository,
    FixtureWeatherRepository,
    GreenSpaceRepository,
    LiveOccurrenceRepository,
    LivePostcodeRepository,
    LiveTaxonomyRepository,
    LiveWeatherRepository,
    OccurrenceRepository,
    PostcodeRepository,
    SnapshotGreenSpaceRepository,
    TaxonomyRepository,
    WeatherRepository,
)
from app.biodiversity.tools import (
    build_expedition_evidence_bundle,
    find_public_green_spaces,
    get_weather_context,
    lookup_uk_postcode,
    resolve_bird_taxon,
    search_occurrences,
    validate_expedition_constraints,
)


@dataclass(frozen=True)
class BackendDependencies:
    postcode: PostcodeRepository
    taxonomy: TaxonomyRepository
    occurrences: OccurrenceRepository
    weather: WeatherRepository
    green_spaces: GreenSpaceRepository

    @classmethod
    def fixture(cls) -> "BackendDependencies":
        return cls(
            postcode=FixturePostcodeRepository(),
            taxonomy=FixtureTaxonomyRepository(),
            occurrences=FixtureOccurrenceRepository(),
            weather=FixtureWeatherRepository(),
            green_spaces=SnapshotGreenSpaceRepository(),
        )

    @classmethod
    def live(cls) -> "BackendDependencies":
        return cls(
            postcode=LivePostcodeRepository(),
            taxonomy=LiveTaxonomyRepository(),
            occurrences=LiveOccurrenceRepository(),
            weather=LiveWeatherRepository(),
            green_spaces=SnapshotGreenSpaceRepository(),
        )


class ExpeditionBackend:
    def __init__(self, dependencies: BackendDependencies) -> None:
        self.dependencies = dependencies

    def run(self, request: ExpeditionRequest) -> BackendResult:
        """Execute the deterministic backend in the documented fixed order."""

        location = lookup_uk_postcode(
            request, repository=self.dependencies.postcode
        )
        taxon = resolve_bird_taxon(
            request.bird_input, repository=self.dependencies.taxonomy
        )
        occurrence = search_occurrences(
            taxon,
            target_month=request.seasonal_target_month,
            repository=self.dependencies.occurrences,
        )
        weather = get_weather_context(
            location,
            request.target_local_date,
            repository=self.dependencies.weather,
        )
        if (
            location.status == LocationStatus.resolved
            and occurrence.outcome == EvidenceOutcome.strong_map_evidence
        ):
            sites = find_public_green_spaces(
                location,
                search_radius_km=request.search_radius_km,
                occurrence=occurrence,
                repository=self.dependencies.green_spaces,
            )
        else:
            sites = PublicSiteSearchResult(
                candidates=[],
                searched_radius_km=request.search_radius_km,
                status="not_applicable_without_strong_spatial_evidence",
            )
        constraints = validate_expedition_constraints(
            request, location, taxon, occurrence, weather, sites.candidates
        )
        bundle = build_expedition_evidence_bundle(
            request,
            location,
            taxon,
            occurrence,
            weather,
            sites,
            constraints,
        )
        unresolved = [
            item
            for item in constraints
            if item.status in {ConstraintStatus.unresolved, ConstraintStatus.violated}
        ]
        status = (
            "candidate_plan_ready"
            if occurrence.outcome == EvidenceOutcome.strong_map_evidence
            and location.status == LocationStatus.resolved
            else "context_only"
            if occurrence.outcome == EvidenceOutcome.limited_contextual_evidence
            and location.status == LocationStatus.resolved
            else "cannot_recommend_sites"
        )
        explanation = {
            EvidenceOutcome.strong_map_evidence: (
                "The bounded historical sample passes the 50-record, five-cell and "
                "two-ranking-dataset gate; candidate sites use only aggregated safe cells."
            ),
            EvidenceOutcome.limited_contextual_evidence: (
                "At least five retained historical records exist, but the strong spatial "
                "gate is not met; no site-level hotspot recommendation is made."
            ),
            EvidenceOutcome.insufficient_evidence: (
                "No retained evidence or only isolated records were found; no site-level "
                "recommendation is supported."
            ),
            EvidenceOutcome.human_selection_required: (
                "The bird input is ambiguous and requires an explicit human taxon choice."
            ),
            EvidenceOutcome.taxon_not_found: (
                "No accepted bird species match was found for the supplied input."
            ),
            EvidenceOutcome.source_unavailable: (
                "A required evidence source was unavailable; this is not treated as no records."
            ),
        }[occurrence.outcome]
        plan = ExpeditionPlan(
            status=status,
            target_bird=taxon.canonical_name or request.bird_input,
            target_date=request.target_local_date,
            candidate_sites=sites.candidates,
            evidence_explanation=explanation,
            unresolved_constraints=unresolved,
            limitations=bundle.safety_and_scientific_limitations,
        )
        return BackendResult(bundle=bundle, plan=plan)


def run_expedition_backend(
    request: ExpeditionRequest, *, mode: str = "fixture"
) -> BackendResult:
    if mode not in {"fixture", "live"}:
        raise ValueError("mode must be fixture or live")
    dependencies = (
        BackendDependencies.fixture() if mode == "fixture" else BackendDependencies.live()
    )
    return ExpeditionBackend(dependencies).run(request)
