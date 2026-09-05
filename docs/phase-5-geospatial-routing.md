# Phase 5: geospatial and routing hardening

Phase 5 extends the existing `BiodiversityExpeditionPlan`; it does not create a competing top-level plan. A plan may now contain an optional `walking_plan: ValidatedWalkingPlan`. Phase 4 plans without that field remain readable. The product remains London-only, English-only and birds-first, uses historical evidence without producing a species-occurrence probability, and never guarantees a sighting.

![Phase 5 route authority and privacy boundaries](diagrams/phase-5-geospatial-routing.svg)

The diagram is also checked in as [Mermaid source](diagrams/phase-5-geospatial-routing.mmd) and a PNG for review.

The normative decisions are split into reviewable records:

- [walking-distance and route-authority semantics](adr/0001-phase-5-routing-semantics.md);
- [basemap delivery and CSP](adr/0002-phase-5-basemap.md);
- [routing provider and failover](adr/0003-phase-5-routing-provider-failover.md);
- [route privacy and safe views](adr/0004-phase-5-route-privacy.md);
- [workflow-version compatibility](adr/0005-phase-5-workflow-version-compatibility.md).

## Route authority boundary

The deterministic route pipeline runs only after Phase 4 deterministic evidence validation:

1. `resolve_public_site_entrances` considers only directly-grounded candidate sites and joins them to audited OSM boundary-member entrance identities.
2. `request_walking_routes` sends at most the first three evidence-ordered candidates to the configured journey provider. Provider inputs contain the private routing origin and a mapped entrance only; occurrence coordinates and safe-cell coordinates are never eligible inputs.
3. `validate_route_constraints` evaluates actual outbound plus actual return distance and duration.
4. `rank_route_options` uses stable code-defined ordering: passed evidence gate, directly-grounded site, eligible entrance, passed route constraints (including an outside-in entrance approach), explicit-public before uncertain access, least complete travel time, walking distance, evidence-site order and stable identities.
5. `route_tradeoff_interrupt` appears only when a typed action can alter the outcome: accept an uncertain entrance, raise a walking limit to at least a computed route, or keep the constraints and finish without a fabricated route.
6. `compose_expedition_plan` receives a compact typed route summary after validation. It may write British English explanation and itinerary prose, but cannot select a route or change entrance, geometry, distance, duration, elevation, constraints or provider facts.
7. `grounding_and_safety_checks` compares every route fact to deterministic state. A composer exception, schema failure or unsafe revision ends in `deterministic_plan_fallback`, which retains the complete validated nested walking plan.

Low evidence, a missing candidate site and entrance-source failure never call a route provider. A missing eligible entrance, no route, exhausted duration or excessive walking distance returns a typed safe status rather than a synthetic route.

## What the model says vs what routing code validates

| Concern | Deterministic routing code | Model may say |
| --- | --- | --- |
| Site and entrance | Selects one directly-grounded site and one eligible audited entrance using stable ranking | Names and explains those supplied identities |
| Route shape and directions | Stores provider geometry and provider manoeuvres behind a private reference | Summarises the itinerary; does not invent turn-by-turn directions |
| Distance and duration | Parses provider units, separates walking from complete travel, sums independent outbound/return journeys and subtracts total travel from the outing budget | Restates only the supplied validated totals in British English |
| Elevation | Parses provider samples and explicit missing/partial state | Describes only the supplied ascent, descent and limitations |
| Constraints and alternatives | Applies walking-limit/duration gates and deterministic tie-breaks | Explains the passed/failed results and supplied alternatives |
| Provenance and limitations | Fixes provider, retrieval time, licence, attribution, cache state and warnings | Organises those facts without changing them |
| Failure | Returns a typed no-route/source-unavailable status and deterministic fallback | Cannot turn a failure into a route or sighting promise |

## Public entrance snapshot

Normal runtime reads `data/osm/london-public-green-space-entrances-2026-09-04.geojson`. Its provenance sidecar records the source, Overpass endpoint, query path and hash, retrieval time, ODbL licence, OpenStreetMap attribution, payload checksum, counts, generation command, filters and limitations. `london-green-space-entrances.overpassql` uses `entrance=*`, `routing:entrance=*`, `barrier=gate`, `access=*`, `foot=*` and `wheelchair=*` around London green-space identities.

The runtime repository requires an auditable site association with matching OSM type and ID. It excludes `access=no/private`, `foot=no/private`, and service/emergency/exit-only entries. `routing:entrance=main` or `main_entrance` ranks first; explicit-public access ranks ahead of missing access. Missing access is `unspecified`, never inferred public. An entrance tag identifies a mapped physical access point, not legal access, current opening or route availability.

No code path derives an entrance from a polygon centroid, polygon interior sample or nearest road. The checked-in fixture covers multiple entrances, explicit-public access, uncertain access, prohibited candidates removed during generation and sites with no valid entrance. It is an audited demonstration subset, not complete London coverage.

Runtime never calls Overpass. Refresh is an explicit networked data-maintenance command:

```bash
python -m scripts.generate_entrance_snapshot --live-refresh
shasum -a 256 data/osm/london-public-green-space-entrances-2026-09-04.geojson
```

## Provider, cache and geometry storage

`WalkingRouteProvider` is implemented by:

- `FixtureWalkingRouteProvider`, which replays saved real API-shaped outbound and return responses;
- `TfLJourneyProvider`, the default live least-time public-transport-and-walking provider using the official TfL Unified API;
- `OpenRouteServiceWalkingProvider`, using `https://api.heigit.org/openrouteservice/v2/directions/foot-walking/geojson` with `httpx` timeouts, at most two attempts and typed authentication/quota/timeout/malformed/no-route/unavailable errors;
- optional `GraphHopperWalkingProvider`, available only as a configured second live adapter;
- `FailoverWalkingRouteProvider`, which attempts the second live provider only when it exists.

Fixture mode uses only the versioned walking fixture. Live mode uses TfL public transport plus walking and never silently falls back to fixture data or changes to walking-only. ORS/GraphHopper failover remains available only inside an explicitly configured walking-only provider chain.

`TFL_API_KEY`, `ORS_API_KEY` and `GRAPHHOPPER_API_KEY` are read by backend adapters only. They are absent from browser DTOs, logs, checkpoints, reports, cache keys and `VITE_*` configuration. TfL's bounded anonymous access means the first key is optional locally, but configured quota is recommended for deployment. A cache key contains route schema version, provider and provider version, profile, origin, public entrance, date/time and routing options. Cache and exact route GeoJSON live under the gitignored `data/runtime/routes/` directory (or `BIODIVERSITY_ROUTE_RUNTIME`). API keys are not cached.

Outbound and return are requested separately and summed; the code never assumes the return is exactly twice the outbound. If their geometries are exact reverses, the map draws one shared line. Provider manoeuvres and journey legs are the only permitted source for turn-by-turn or service instructions.

## Walking and duration semantics

The normative decision record is [ADR 0001](adr/0001-phase-5-routing-semantics.md). In brief:

- `search_radius_km` is the straight-line/projected candidate-site retrieval radius and is not a walking-distance promise.
- `maximum_walking_distance_km` is the whole excursion's outbound-plus-return route distance.
- `duration_hours` is the complete outing budget, including outbound and return travel. `remaining_field_time = outing duration − provider total travel duration` and is time only, never sighting probability.
- Travel that consumes or exceeds the complete duration fails the route constraint.
- A site may pass the search radius and fail actual walking constraints.
- Without a walking limit a route may be ready, with an explicit “No explicit walking limit supplied” constraint result.

## Typed contracts and safe API views

The strict domain models are `RouteStatus`, `PublicEntranceCandidate`, `WalkingRouteRequest`, `RouteLeg`, `JourneySegment`, `ElevationSample`, `WalkingRouteEvidence`, `RouteConstraintResult`, `RouteOption` and `ValidatedWalkingPlan`. A ready plan requires a site and entrance identity, access certainty, profile, both leg distances, walking and complete travel durations, expedition and remaining field time, geometry reference, provider/version/provenance, cache status, deterministic selection rationale and passing constraints. Elevation, ascent/descent, alternatives, warnings and limitations are typed and may explicitly be absent.

FastAPI exposes route summaries and options separately from geometry. `/api/v1/runs/{thread_id}/checkpoints/{checkpoint_id}/route-geometry/{reference}` accepts only a reference present in that exact checkpoint. Local/private runs may receive their own geometry. Public read-only mode serves geometry only for the planned, non-private fixture origin. Route geometry is absent from generic state, SSE, logs, reports and public checkpoint views. Occurrence coordinates and IDs remain absent from every API.

The checked-in [Phase 5 OpenAPI document](phase-5-openapi.json) generates `frontend/src/api/schema.d.ts`; hand-written TypeScript contracts alias the generated DTOs.

## Basemap and attribution

MapLibre remains the map framework. The backend defaults to the official keyless OpenFreeMap Liberty style at `https://tiles.openfreemap.org/styles/liberty`; `BIODIVERSITY_BASEMAP_STYLE_URL=disabled` selects the local empty style. Secret-looking style query parameters (`token`, `key`, `api_key`, `apikey`, `access_token`) are rejected. Browser-bundled `VITE_MAP_STYLE_URL` is not used.

MapLibre's attribution control is enabled. The UI also renders OpenFreeMap, OpenMapTiles and OpenStreetMap attribution outside the map legend. Evidence grids, candidate/context polygons, selected-site highlight, generalised start, selected entrance and outbound/return polylines are GeoJSON sources/layers above the style, so they stay aligned during pan and zoom. Style load failure produces the exact visible state “Basemap unavailable — evidence overlays remain available” and re-adds the overlays on the empty style.

The CSP permits the configured style origin plus the strictly validated HTTPS origins in `BIODIVERSITY_BASEMAP_RESOURCE_ORIGINS`; the default allowlist contains only the OpenFreeMap/OpenStreetMap resource families used by the baseline. This supports custom styles without accepting raw CSP fragments. No Google map tile, screenshot or restricted map asset is copied or embedded. Unit tests mock MapLibre; Playwright intercepts the public style URL with a local style and blocks all remaining public tile requests.

## Checkpoints, invalidation and compatibility

The workflow is `phase-5.0`, state schema `3`, route schema `1`. `RunManifest` records data/model identities plus provider, provider version, route profile, route schema and routing endpoint fingerprint. Resume validates the exact thread/checkpoint/branch/execution and current runtime profile, so an older live ORS execution is readable but cannot be resumed under the TfL profile. Route-affecting fork or HITL changes clear route options and validated evidence before recalculation.

Phase 4 manifests (`phase-3.1`, schema `2`) remain parseable for history and safe read-only views. They are never silently migrated or resumed into the Phase 5 graph; mutation returns an explicit instruction to start a new Phase 5 run.

## Observability and reports

Phase 5 extends `AgentRunEvent` with entrance resolution, provider request, provider attempt/failover, cache hit/miss, route validation, ranking, route HITL and finalisation events. Provider work is a distinct span kind and reports start/end/duration, typed status/error category, redacted response-local candidate/route identity and cache status. Existing Phase 3/4 events remain parseable.

Reports add:

- `route-audit.json`: redacted route status, constraints, provider and decision audit;
- `route-plan.json`: display-safe nested plan facts without coordinates or geometry;
- `provider-cache-timings.json`: provider/cache lifecycle timing summary.

No event/report contains credentials, provider request bodies, occurrence identities/coordinates, a private precise origin or route geometry. `timings.json` continues to provide node/model/tool/provider durations for finding the slowest stage.

## Operation and deployment

The relevant backend settings are:

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `BIODIVERSITY_BASEMAP_STYLE_URL` | OpenFreeMap Liberty | HTTPS, browser-visible, secret-free MapLibre style; use `disabled` for empty style |
| `BIODIVERSITY_BASEMAP_RESOURCE_ORIGINS` | OpenFreeMap/OSM HTTPS origins | Additional CSP origins for custom style sprites, glyphs and tiles |
| `BIODIVERSITY_ROUTE_RUNTIME` | `data/runtime/routes` | Gitignored cache and private geometry directory |
| `ORS_API_KEY` | unset | Backend-only ORS live-routing credential |
| `GRAPHHOPPER_API_KEY` | unset | Backend-only optional second live provider |
| `TFL_API_KEY` | unset | Optional backend-only TfL quota key for the default live multimodal provider |
| `BIODIVERSITY_DATA_MODE` | `fixture` | Fixture or live upstream data/routing |

Compose and Render set the keyless basemap, fixture/scripted public policy and persistent route-runtime directory. Public demo mode remains read-only/fixture-only and does not need a model or routing key.

## Verification

```bash
python -m pytest -q
python -m pytest -m live tests/test_phase5_live_routing.py -vv
python -m ruff check .
python -m compileall -q app scripts tests

cd frontend
npm run lint
npm run typecheck
npm test -- --run
npm run build
npm run test:e2e
```

The live tests are opt-in and skip without credentials. Default pytest, frontend unit tests and Playwright do not access routing, Overpass or tile networks.

## Known limitations and Phase 6 boundary

- Entrance coverage is intentionally incomplete and OSM access/opening/wheelchair tags can be missing or stale. Field checks remain necessary.
- Route provider results can change with network data, closures and provider algorithms. Elevation can be partial and is not survey-grade.
- The route cache is local filesystem storage, not a distributed multi-tenant cache.
- The public fixture origin and compact route geometry are demonstrations, not personal travel advice.
- Phase 5 does not calculate rarity, habitat suitability, conservation designation, protected status or legal conclusions. Those are Phase 6 concerns.
