# ADR 0003: Phase 5 routing provider and failover

- Status: accepted
- Date: 2026-09-05
- Decision owners: deterministic routing backend

## Context

Fixture demonstrations, live provider calls, retry behaviour and failover must have different, auditable semantics. A live provider outage must never be disguised by fixture data.

## Decision

`WalkingRouteProvider` remains the compatibility provider boundary. Fixture data replays versioned, real API-shaped walking responses only in `data_mode=fixture`. The default live profile uses the official TfL endpoint under `https://api.tfl.gov.uk/Journey/JourneyResults` with `journeyPreference=LeastTime` and the public-transport plus walking modes. It uses bounded `httpx` timeouts and retries. Errors map to authentication, quota, timeout, malformed response, no route or provider unavailable.

ORS and GraphHopper remain walking-only adapters. `FailoverWalkingRouteProvider` can pair those two only when an explicit walking-only runtime configures the second provider. They are not fallbacks for a TfL multimodal request because silently removing public transport would change the requested semantics. A failed TfL request therefore ends as `source_unavailable`; fixture routing is never consulted. Provider attempt, failure, duration and cache status are recorded without request bodies, credentials, origins or geometry.

`TFL_API_KEY`, `ORS_API_KEY` and `GRAPHHOPPER_API_KEY` are backend-only. TfL currently permits bounded anonymous requests; a key is optional and recommended for deployed quota. Provider, provider version, profile, route schema, routing options, origin and entrance define the cache key; API keys do not. Each run considers at most three evidence-ordered sites.

## Consequences

Live failure stays visible and reproducible. Adding another provider requires a live adapter with typed errors, attribution and provenance rather than an implicit fallback. Default tests remain credential-free and offline.
