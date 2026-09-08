# Evidence and grounding

[Documentation](README.md) · [LangGraph architecture](langgraph-architecture.md)

The application plans birdwatching outings from historical occurrence evidence. It does not estimate sighting probability, abundance or habitat suitability. A route-ready plan requires independently validated location, taxonomy, evidence, site grounding and journey constraints.

## Request and taxonomy

A structured-output model extracts the requested bird, London location, date, duration and optional constraints. Deterministic services validate postcodes or bounded named-place candidates against Greater London. Ambiguity and missing information lead to typed human decisions rather than an invented location.

GBIF taxonomy resolution accepts an Aves species identity, with explicit selection or correction when necessary. The fixture taxonomy is a reproducible cache, not a whitelist of the only names the live implementation can resolve.

## Bounded evidence collection

The evidence agent chooses from three registered tools: `search_occurrences`, `get_weather_context` and `find_public_green_spaces`. LangGraph's ToolNode executes the calls; structured results are recorded into typed state. Reducers merge tool-call results idempotently, and the evidence loop is capped at six rounds.

Occurrence processing separates record retention from ranking eligibility. It applies the requested spatial and seasonal window, resolves usable dates and uncertainty, removes duplicates and calculates provenance-aware quality summaries. Stable publisher identifiers take precedence; records without stable identifiers use a deterministic fallback fingerprint. Distinct stable identifiers are retained and can be flagged as possible duplicates instead of silently merged.

## Quality and privacy gates

| Evidence status | Deterministic requirement |
| --- | --- |
| Strong map evidence | At least 50 ranking-eligible records, five occupied cells and two datasets |
| Limited contextual evidence | At least five retained records, but below the strong gate |
| Insufficient evidence | Zero to four retained records |
| Source error | An upstream failure, kept distinct from a genuine empty result |

Coordinate uncertainty up to 1 km is eligible for ranking; larger or missing uncertainty is handled as broader context or audit information, not silently treated as a precise location. Quality warnings also expose missing uncertainty and dataset concentration.

Public maps contain aggregate 1 km cell polygons, not individual sightings. Each published cell requires at least three ranking records and the overall strong-evidence gate. Raw occurrence coordinates are used in memory during processing; safe reports and API views omit occurrence coordinates, record identities and private associations. See the [fixture schema](fixture-schema.md) for the sanitised storage format.

## Grounding a public site

The public-green-space snapshot is versioned with source metadata and a checksum. Access marked private or prohibited is excluded; missing access remains uncertain.

| Candidate tier | Meaning | Can become a recommended destination? |
| --- | --- | --- |
| Directly grounded | Site polygon intersects a publishable evidence cell | Yes, subject to entrance and route validation |
| Nearby context | Close to an evidence cell without an intersection | No |
| Ungrounded | Within the search radius without sufficient spatial grounding | No |

A successful site lookup alone does not make a plan ready. The workflow requires a resolved London origin and bird, strong evidence, publishable cells and a directly grounded candidate before proceeding to [entrance and journey validation](routing.md).

Weather is date-specific context, not a bird-presence forecast. An unavailable forecast is reported explicitly instead of substituting another date.

## Model composition and final checks

The composer receives a compact validated bundle. It may explain the evidence and itinerary, but it cannot change taxonomy, evidence thresholds, selected sites, entrance eligibility or route facts. Deterministic grounding checks accept the draft, request at most one revision, or return a complete safe fallback. Low evidence and unavailable sources can therefore produce a useful limited outcome without inventing a route or promising a sighting.

Implementation: [domain models](../app/biodiversity/models.py), [repositories](../app/biodiversity/repositories.py), [tools](../app/biodiversity/tools.py) and [graph workflow](../app/biodiversity/graph/workflow.py). Source-specific limitations and attribution are listed in [data sources and licences](data-sources-and-licences.md).
