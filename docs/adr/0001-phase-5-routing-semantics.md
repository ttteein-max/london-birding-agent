# ADR 0001: Phase 5 route distance, duration and authority semantics

- Status: accepted
- Date: 2026-09-05
- Decision owners: deterministic biodiversity backend

## Context

Earlier phases selected public-green-space candidates with a straight-line/projected search radius and deliberately left walking distance unresolved. Treating that distance as a route, routing to a polygon centroid, doubling one leg, or allowing an LLM to select a destination would make the itinerary unsafe and unauditable.

## Decision

`search_radius_km` continues to mean only the projected/straight-line radius used to retrieve candidate-site polygons. It is not a walking distance.

`maximum_walking_distance_km`, when supplied, means the full excursion route distance: provider-computed outbound plus separately provider-computed return. A provider that cannot submit a round trip in one call is called for both directions. The system never substitutes straight-line distance and never assumes `return = outbound × 2`.

`duration_hours` is the total expedition budget. The route passes duration only when provider walking time is strictly less than that budget. `remaining_field_time_minutes` is the subtraction result and is a time budget only; it says nothing about the probability of observing a bird. A park inside the search radius may therefore fail on walking distance or duration.

When no maximum walking distance is supplied, a feasible route may still be returned, but its constraint ledger must say “No explicit walking limit supplied”. No implicit default is invented.

The only route destination is an eligible versioned `PublicEntranceCandidate` with an exact audited association to the candidate site's OSM identity. `entrance=*`, `routing:entrance=*` or `barrier=gate` describes a mapped point but does not prove public access. Explicit `access=yes/designated/permissive/public` is preferred; missing access is uncertain and requires an actionable acknowledgement when it is the only option. `access=no/private`, `foot=no/private`, service, emergency and exit-only points are excluded. Polygon centroids, interior points, safe-cell centres, occurrence coordinates and nearest-road guesses are forbidden destinations.

The deterministic rank is: evidence gate passed; directly-grounded candidate; eligible entrance; distance and duration constraints passed; explicit-public entrance before uncertain entrance; existing evidence-site order; actual route duration then distance; stable site/entrance/option identities. LLM output cannot change this order or any route fact.

Fixture mode replays versioned real API-shaped routes. Live mode uses only configured live providers. Live failure never falls back to fixture. The primary live provider is the current HeiGIT ORS v2 foot-walking GeoJSON endpoint; GraphHopper is optional genuine live failover. Provider/version/profile/options/origin/destination/schema define the cache identity. Credentials do not.

Exact origin and geometry stay in a gitignored private runtime store. Generic events, logs, reports and checkpoint views use response-local references and redacted summaries. Public read-only mode can expose only a curated fixture route from a non-private origin.

## Consequences

The plan may safely report that no route is feasible even when a candidate site exists. Two calls per site can increase latency and quota use, so routing is capped at three ordered candidates and cached. Missing/uncertain entrances are common because the audited snapshot is intentionally conservative. Provider and map failures remain visible rather than being hidden by fixture substitution. Phase 4 executions remain readable but cannot be mutated under a different Phase 5 provider/graph profile.
