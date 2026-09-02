"""Fixed-order Phase 1 orchestration; no LLM or LangGraph tool routing."""

from __future__ import annotations

from dataclasses import dataclass

from app.biodiversity.models import (
    BackendResult,
    ConstraintStatus,
    EvidenceOutcome,
    ExpeditionEvidenceBundle,
    ExpeditionPlan,
    ExpeditionPlanStatus,
    ExpeditionRequest,
    LocationStatus,
    PublicSiteSearchStatus,
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


def build_deterministic_expedition_plan(
    bundle: ExpeditionEvidenceBundle,
) -> ExpeditionPlan:
    """Apply the authoritative Phase 1 readiness and explanation rules."""

    request = bundle.request
    location = bundle.location
    taxon = bundle.taxon
    occurrence = bundle.occurrence
    sites = bundle.site_search
    constraints = bundle.constraints
    unresolved = [
        item
        for item in constraints
        if item.status in {ConstraintStatus.unresolved, ConstraintStatus.violated}
    ]
    grounded_candidates = (
        sites.status == PublicSiteSearchStatus.success
        and bool(sites.candidates)
        and all(site.associated_safe_cell_ids for site in sites.candidates)
    )
    candidate_plan_ready = (
        location.status == LocationStatus.resolved
        and occurrence is not None
        and occurrence.outcome == EvidenceOutcome.strong_map_evidence
        and bool(occurrence.safe_map_cells)
        and grounded_candidates
    )
    status = (
        ExpeditionPlanStatus.candidate_plan_ready
        if candidate_plan_ready
        else ExpeditionPlanStatus.context_only
        if occurrence is not None
        and occurrence.outcome == EvidenceOutcome.limited_contextual_evidence
        and location.status == LocationStatus.resolved
        else ExpeditionPlanStatus.cannot_recommend_sites
    )
    strong_explanation = {
        PublicSiteSearchStatus.success: (
            "The bounded historical sample passes the strong gate, and every candidate "
            "site has a source polygon intersecting an approved aggregated safe-map cell."
        ),
        PublicSiteSearchStatus.safe_map_unavailable: (
            "The historical sample passes the record-quality gate, but no approved "
            "safe-map cells are available, so site recommendations are suppressed."
        ),
        PublicSiteSearchStatus.no_suitable_public_sites: (
            "Strong evidence and safe-map cells exist, but no grounded public-site "
            "polygon was found within the requested search radius; contextual sites "
            "remain explicitly non-recommended."
        ),
        PublicSiteSearchStatus.source_unavailable: (
            "Strong evidence and safe-map cells exist, but the public-site source was "
            "unavailable, so no candidate plan can be produced."
        ),
    }.get(
        sites.status,
        "Strong historical evidence alone is insufficient for a grounded site recommendation.",
    )
    if occurrence is None:
        explanation = "Required occurrence evidence was not collected."
    else:
        explanation = {
            EvidenceOutcome.strong_map_evidence: strong_explanation,
            EvidenceOutcome.limited_contextual_evidence: (
                "At least five retained historical records exist, but the strong spatial "
                "gate is not met; no site-level candidate recommendation is made."
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
    return ExpeditionPlan(
        status=status,
        target_bird=taxon.canonical_name or request.bird_input,
        target_date=request.target_local_date,
        candidate_sites=sites.candidates,
        contextual_sites=sites.contextual_sites,
        suggested_actions=sites.suggested_actions,
        evidence_explanation=explanation,
        unresolved_constraints=unresolved,
        limitations=bundle.safety_and_scientific_limitations,
    )

class ExpeditionBackend:
    def __init__(self, dependencies: BackendDependencies) -> None:
        self.dependencies = dependencies

    def run(self, request: ExpeditionRequest) -> BackendResult:
        """Execute the deterministic backend in the documented fixed order."""

        location = lookup_uk_postcode(request, repository=self.dependencies.postcode)
        taxon = resolve_bird_taxon(
            request.bird_input, repository=self.dependencies.taxonomy
        )
        occurrence = search_occurrences(
            taxon,
            target_month=request.seasonal_target_month,
            seasonal_window_radius_months=request.seasonal_window_radius_months,
            repository=self.dependencies.occurrences,
        )
        weather = get_weather_context(
            location,
            request.target_local_date,
            repository=self.dependencies.weather,
        )
        sites = find_public_green_spaces(
            location,
            search_radius_km=request.search_radius_km,
            occurrence=occurrence,
            repository=self.dependencies.green_spaces,
        )
        constraints = validate_expedition_constraints(
            request, location, taxon, occurrence, weather, sites
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
        return BackendResult(
            bundle=bundle,
            plan=build_deterministic_expedition_plan(bundle),
        )


def run_expedition_backend(
    request: ExpeditionRequest, *, mode: str = "fixture"
) -> BackendResult:
    if mode not in {"fixture", "live"}:
        raise ValueError("mode must be fixture or live")
    dependencies = (
        BackendDependencies.fixture()
        if mode == "fixture"
        else BackendDependencies.live()
    )
    return ExpeditionBackend(dependencies).run(request)
