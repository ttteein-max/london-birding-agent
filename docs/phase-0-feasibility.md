# Phase 0 feasibility: London Biodiversity Expedition Planner

## Decision

Phase 0 passes the engineering feasibility gate for a London-only, birds-first, English-only product.

The product is positioned as: **“London Biodiversity Expedition Planner — an explainable, evidence-grounded planner for urban birdwatching expeditions in London.”** It identifies areas with stronger historical occurrence evidence. It does not predict sightings, estimate populations, guarantee access or guarantee that a bird will be observed.

The live evidence was retrieved on 31 August 2026 UTC. Default checks replay sanitised fixtures and require no network or API key.

## Predeclared map-viability rule

A resolved taxon is map-viable only if its bounded London sample has all of:

1. at least 50 retained occurrence records;
2. at least five distinct approximately 1 km spatial cells;
3. evidence from at least two GBIF datasets;
4. numeric coordinates inside the Greater London bounding envelope;
5. `occurrenceStatus=PRESENT` where the field is present;
6. none of `ZERO_COORDINATE`, `COORDINATE_OUT_OF_RANGE`, `COORDINATE_INVALID` or `COUNTRY_COORDINATE_MISMATCH`;
7. no known `coordinateUncertaintyInMeters` above 10,000 metres.

Missing uncertainty is retained but remains an explicit limitation; it is not interpreted as zero. Coordinates and identifiers in committed fixtures are replaced with non-reversible hashes of approximately 1 km cells and source identifiers. The GBIF search is limited to 300 results per taxon, so retained counts describe a bounded engineering sample, not abundance.

## Taxon matrix

GBIF scientific taxa were resolved live rather than using unsupported hard-coded identifiers.

| Behaviour | Input | GBIF accepted taxon | Server count | Sampled | Retained | 1 km cells | Datasets | Result |
|---|---|---|---:|---:|---:|---:|---:|---|
| Abundant evidence | Common woodpigeon | *Columba palumbus* (2495455) | 146,572 | 300 | 295 | 187 | 4 | Map-viable |
| Another viable taxon | House sparrow | *Passer domesticus* (5231190) | 53,533 | 300 | 294 | 163 | 4 | Map-viable |
| Another viable taxon | Eurasian magpie | *Pica pica* (5229490) | 133,843 | 300 | 294 | 175 | 3 | Map-viable |
| Sparse London evidence | Corncrake | *Crex crex* (4408498) | 5 | 5 | 5 | 4 | 3 | Insufficient evidence |
| Valid taxon, no usable London evidence | Great auk | *Pinguinus impennis* (5229273) | 0 | 0 | 0 | 0 | 0 | Insufficient evidence |
| Ambiguous English name | robin | Direct match: Animalia (higher rank) | — | — | — | — | — | Human selection required |

For `robin`, the preserved GBIF candidate list includes European robin (*Erithacus rubecula*, 2492462), American robin (*Turdus migratorius*, 9510564) and Ryukyu robin (*Erithacus komadori*, 2492463). Phase 0 does not silently choose the London-likely species. A later human-in-the-loop interface must present the alternatives.

The three passing taxa satisfy the declared gate. Corncrake and great auk terminate safely with an explicit `insufficient_evidence` classification.

## GBIF quality and media audit

Every sampled record preserves these sanitised audit fields: `has_coordinate`, `occurrence_status`, `issues`, `coordinate_uncertainty_metres`, observation/event date, dataset key, basis of record, record licence, a hashed media identifier and media-level licence when media exists.

All 905 sampled records had coordinates and `PRESENT` status because these were bounded search parameters. Seventeen high-uncertainty records were rejected: five woodpigeon, six house sparrow and six magpie records exceeded 10 km. Missing uncertainty occurred in the fixtures and remains flagged as unknown. Common non-fatal GBIF issues included `CONTINENT_DERIVED_FROM_COORDINATES`, `COORDINATE_ROUNDED` and taxon identifier warnings; they are retained for audit and are not converted into confidence scores.

The sample contains human observations, a small number of machine observations and one preserved Corncrake specimen. Record licences include CC BY, CC BY-NC and CC0. Linked media were not downloaded or displayed. The audit keeps each media licence independently; only clearly identified CC0 or CC BY media are marked reusable for possible later review. CC BY-NC, missing or unfamiliar terms are not automatically approved. The occurrence record's licence never substitutes for the media object's licence.

The fixture contains no decimal coordinates and no reversible cell coordinates. This is especially important for sparse or potentially sensitive occurrences.

## Postcode and weather feasibility

The real Postcodes.io response for `SW11 4NJ` resolved to Wandsworth, London, England. Its centroid is rounded to three decimals in the fixture. The result demonstrates London scope validation but is not a user's precise position.

A representative three-day Open-Meteo request used the rounded postcode centroid, the `Europe/London` timezone, daily maximum/minimum temperature, maximum precipitation probability and WMO weather code. It demonstrates weather-schema feasibility only. Forecast weather cannot predict whether a bird will be observed.

## OSM public green-space candidate snapshot

The versioned snapshot is `data/osm/london-green-space-candidates-2026-08-31.geojson`.

- Retrieval: `2026-08-31T03:38:19Z`
- Endpoint: `https://overpass-api.de/api/interpreter`
- Input elements: 3,620
- Retained candidate features: 3,364
- Excluded `access=private` or `access=no`: 256
- Access marked `explicit_public`: 212
- Access marked `unspecified`: 3,152
- SHA-256: `82512b8921c37287917f9adb6067317c090b9285f19169eaf1f7bd93b87bd443`
- Licence: Open Database Licence (ODbL) 1.0
- Attribution: `© OpenStreetMap contributors`

The exact Overpass QL, provenance sidecar and generator are committed. The processing retains named park, garden, nature reserve, recreation ground, forest, meadow and protected-area candidates; excludes explicit private/no access; retains compact relevant tags; and converts each OSM element to a centre point. Missing access is `unspecified`, never inferred as public. This is not a guaranteed-access dataset.

Regenerate explicitly and sequentially with:

```bash
python -m scripts.generate_osm_snapshot --live-refresh
```

Overpass is used only for this snapshot workflow, with at most three attempts and backoff. It is not queried per user request. If regeneration repeatedly returns 429, 5xx or times out, retain the query and generator and use an OSM-derived GeoJSON or London OSM extract with equivalent provenance as the manual fallback.

## Provenance format

Each fixture or snapshot provenance record contains:

- source name and URL;
- endpoint and request parameters;
- UTC retrieval timestamp;
- snapshot/source version where available;
- licence and attribution;
- SHA-256 checksum;
- counts before and after filtering;
- filtering rules;
- known limitations.

OSM provenance additionally records relevant source tags, query checksum, exclusions, feature count and the exact regeneration command. Payload checksums are validated offline; the OSM sidecar validates the complete GeoJSON file.

## Reproduction and limitations

```bash
python -m pytest -q
python -m scripts.phase0_feasibility
python -m scripts.phase0_feasibility --live-refresh
python -m scripts.generate_osm_snapshot --live-refresh
```

The first two commands are offline. Live commands are explicit, sequential, rate-conscious and use a project-specific User-Agent, 30-second API request timeouts (150 seconds for Overpass), at most three attempts and bounded exponential backoff. Failures exit non-zero.

Known limitations include observer-effort bias, dataset coverage and duplication, changing taxonomy, incomplete uncertainty fields, the use of a Greater London bounding envelope rather than a boundary point-in-polygon filter, a 300-record sample cap, forecast volatility, centroid simplification and incomplete OSM access metadata. None of the results is a probability, population estimate, access guarantee or field-action instruction.

## Phase boundary

Phase 0 adds feasibility evidence only. The incident `app/graph/workflow.py`, state/schema, investigator tools, streaming adapter, scripted models, checkpointer and thread-ID infrastructure remain available. Biodiversity domain replacement, frontend, routing, HITL, persistence expansion and MCP work belong to later phases and are not implemented here.
