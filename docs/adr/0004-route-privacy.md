# ADR 0004: Route privacy and safe views

- Status: accepted
- Date: 2026-09-05
- Decision owners: biodiversity API and reporting

## Context

Precise walking or public-transport journey geometry can reveal a user's start. Historical occurrence coordinates, identifiers and safe-cell internals are already prohibited from public output.

## Decision

Provider input may contain only the internal routing origin and an audited public-site entrance. An occurrence coordinate, safe-cell coordinate, polygon centroid, internal sample or nearest-road guess is never a route destination.

Exact geometry is written only to the gitignored private route runtime and addressed by an opaque response-local reference. A local/private browser can request geometry only when that reference belongs to the exact thread and checkpoint. A public read-only demo can expose only the curated fixture geometry from its planned non-private origin. Generic checkpoint views, SSE events, logs and reports contain route summaries and references but never exact private origin or geometry.

Observability rejects sensitive field names and records response-local candidate/route IDs only. Reports are scanned for coordinates, GeoJSON geometry, provider request bodies, API keys and occurrence identifiers. Provider manoeuvres remain deterministic route evidence and are not rewritten by the model.

## Consequences

Map rendering needs a dedicated authorised endpoint and a private runtime store. Public reports remain shareable, but cannot reconstruct a route or user origin. Deleting the local runtime removes cached geometry without damaging auditable plan summaries.
