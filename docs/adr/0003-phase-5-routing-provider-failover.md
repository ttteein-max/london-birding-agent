# ADR 0003: Phase 5 routing provider and failover

- Status: accepted
- Date: 2026-09-05
- Decision owners: deterministic routing backend

## Context

Fixture demonstrations, live provider calls, retry behaviour and failover must have different, auditable semantics. A live provider outage must never be disguised by fixture data.

## Decision

`WalkingRouteProvider` is the provider boundary. Fixture data replays versioned, real API-shaped route responses only in `data_mode=fixture`. Live mode uses the current HeiGIT ORS endpoint, `https://api.heigit.org/openrouteservice/v2/directions/foot-walking/geojson`, through bounded `httpx` timeouts and retries. Errors map to authentication, quota, timeout, malformed response, no route or provider unavailable.

GraphHopper is an optional second live adapter. `FailoverWalkingRouteProvider` attempts it only when `GRAPHHOPPER_API_KEY` configures that adapter. Otherwise a failed ORS attempt ends as `source_unavailable` with `failover_not_configured`; fixture routing is never consulted. Provider attempt, failure, failover, duration and cache status are recorded without request bodies, credentials, origins or geometry.

`ORS_API_KEY` and `GRAPHHOPPER_API_KEY` are backend-only. Provider, provider version, profile, route schema, routing options, origin and entrance define the cache key; API keys do not. Each run considers at most three evidence-ordered sites.

## Consequences

Live failure stays visible and reproducible. Adding another provider requires a live adapter with typed errors, attribution and provenance rather than an implicit fallback. Default tests remain credential-free and offline.
