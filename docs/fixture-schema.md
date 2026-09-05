# Phase 0.1 fixture and provenance schema

## Snapshot model

Canonical fixtures are dated evidence snapshots. Their checksums prove snapshot integrity; they are not assertions that future live API responses must have identical counts.

`gbif-species.json` and `gbif-occurrences-london.json` use `schema_version: 2`. Postcodes.io and Open-Meteo retain version 1 because their count meaning did not change. Boundary and OSM GeoJSON artifacts have separate file-checksum provenance sidecars.

Every provenance object contains source name/URL, endpoint, request parameters, UTC retrieval time, snapshot version, licence, attribution, SHA-256, count data, filtering rules and known limitations.

## Taxonomy schema version 2

Each matrix result contains the regression scenario/reason and a typed outcome:

- `resolved`: original and normalised input, matched common name when available, scientific/canonical names, accepted key, rank, status, method, optional confidence and rationale;
- `human_selection_required`: original input, rationale and API-generated accepted Aves candidates;
- `taxon_not_found`: original input and safe-failure rationale.

The matrix is not a species whitelist. Runtime resolution uses user text and current GBIF results.

## Occurrence schema version 2

Each resolved scenario records taxonomy, outcome, sanitised records, count object, dataset diversity, year/month distributions and the exact temporal/seasonal/page stopping metadata.

Sanitised records may contain record/media hashes, event date, year/month, dataset, basis, licences, GBIF issues, uncertainty, location-quality tier, permitted evidence use, rejection reasons and opaque cell/zone references. They never contain occurrence latitude/longitude or the HMAC secret.

Count fields mean:

- `server_match_count`: GBIF's total for the bounded year/month/taxon/envelope query;
- `sampled_count`: result objects actually received across requested pages, before deduplication;
- `deduplicated_count`: unique records processed;
- `duplicates_removed`: sampled minus deduplicated;
- `ranking_eligible_count`: retained records with known uncertainty ≤1,000 m;
- `weak_count`: retained 1,001–5,000 m records;
- `context_only_count`: retained records above 5,000 m;
- `unknown_uncertainty_count`: retained records with missing uncertainty;
- `rejected_count`: deduplicated records rejected by boundary/status/coordinate/fatal-issue rules;
- `retained_total_count`: all non-rejected quality tiers;
- `rejection_counts_by_reason`: auditable reason frequencies (a record may have more than one reason).

Server matches are never assumed to have been downloaded. Context-only and unknown records are never ranking eligible.

## Live candidate lifecycle

`--live-refresh` writes all candidate fixtures plus `candidate-manifest.json` to a temporary/versioned output directory. It does not mutate `data/fixtures`.

`--promote-candidate` is a distinct explicit action. It requires all canonical filenames, occurrence schema version 2 and internally consistent counts before copying. Offline validation then rechecks schema, provenance and payload checksums.

## Phase 5 entrance and route fixtures

`london-public-green-space-entrances-2026-09-04.geojson` is a point-feature snapshot with an OSM identity, source tags, access certainty and one or more exact green-space OSM boundary-member associations. Its provenance sidecar records the saved Overpass query hash and the GeoJSON SHA-256. Missing `access` remains uncertain; excluded access/foot/service tags never become runtime candidates. The fixture never synthesises a centroid or nearest-road entrance.

`ors-foot-walking-routes.json` stores saved real, API-shaped outbound and return GeoJSON responses for a planned non-private demo origin and audited entrance identities. Its payload checksum is verified before use. Exact geometry is converted at runtime to a reference backed by the gitignored route store; general API/report payloads contain the reference and route summary, not geometry or a private origin.
