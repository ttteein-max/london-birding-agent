# Phase 1: typed biodiversity backend and deterministic tools

## Scope and architecture

Phase 1 adds a frontend-independent backend under `app/biodiversity/`. It is parallel to, and does not import or modify, the incident LangGraph implementation. It uses a fixed service order and no LLM:

1. validate `ExpeditionRequest`;
2. resolve and London-check the postcode or rounded map point;
3. resolve the bird taxon;
4. retrieve bounded historical occurrences when taxonomy is resolved;
5. retrieve exact-date weather context;
6. find OSM snapshot candidates only when strong spatial evidence supports site candidates;
7. validate constraints;
8. build an `ExpeditionEvidenceBundle` and deterministic `ExpeditionPlan`.

`models.py` contains strict Pydantic contracts. `repositories.py` owns fixture/live boundaries and the bounded HTTP client. `tools.py` contains the seven typed deterministic services. `orchestration.py` wires them in a fixed order and is intentionally not a LangGraph graph.

## Domain contracts

The public contracts are:

- `ExpeditionRequest`: one postcode or WGS84 map point, local date, duration, independent search radius, optional walking constraint and rain preference;
- `ResolvedLocation`: normalised postcode where applicable, rounded WGS84 point, EPSG:27700 point, Greater London result, district and provenance;
- `TaxonCandidate` and `ResolvedTaxon`: accepted GBIF key, names, resolution method, ambiguity state, candidates and provenance;
- `EvidenceItem`: retrieval time, source, record type, licence, attribution, permitted use and limitations;
- `OccurrenceEvidence` and `EvidenceQualitySummary`: outcome, bounded counts, coordinate tiers, distributions, sampling concentration, deduplication and safe cells;
- `SafeSpatialCell`: EPSG:27700 1 km aggregate represented as a rounded WGS84 polygon, with counts and dates but no occurrence IDs;
- `WeatherEvidence`: exact requested date, `Europe/London`, temperatures in Celsius, rain fields, WMO code and availability status;
- `PublicSiteCandidate`: OSM snapshot site, access certainty, approximate projected centre distance and metadata;
- `ConstraintViolation`: deterministic satisfied, violated or unresolved result;
- `ExpeditionEvidenceBundle` and `ExpeditionPlan`: complete structured evidence and a non-LLM plan skeleton.

All models forbid unexpected fields. Expected upstream problems have explicit states. Unexpected programming exceptions are allowed to surface.

## Tool contracts

The independently testable functions are:

- `lookup_uk_postcode(ExpeditionRequest, repository=...) -> ResolvedLocation`
- `resolve_bird_taxon(str, repository=...) -> ResolvedTaxon`
- `search_occurrences(ResolvedTaxon, target_month=..., repository=...) -> OccurrenceEvidence`
- `get_weather_context(ResolvedLocation, date, repository=...) -> WeatherEvidence`
- `find_public_green_spaces(ResolvedLocation, search_radius_km=..., occurrence=..., repository=...) -> PublicSiteSearchResult`
- `validate_expedition_constraints(...) -> list[ConstraintViolation]`
- `build_expedition_evidence_bundle(...) -> ExpeditionEvidenceBundle`

Fixture and live repositories implement the same boundaries. Fixture mode does not read environment variables or API keys. Live HTTP uses sequential calls, an explicit timeout, no more than three attempts and bounded backoff.

## Taxonomy, evidence and sampling

The live taxonomy path reuses `GBIFBirdNameResolver`, accepts arbitrary English common or scientific names, and preserves `resolved`, `human_selection_required` and `taxon_not_found`. It never chooses a locally likely interpretation of `robin` or `eagle`. The fixture repository is a deterministic cache of saved resolver outcomes, not a supported-species whitelist.

Occurrence retrieval reuses the Phase 0.1 bounded query: five complete years plus current year, target month ±1, maximum 300 records per page, maximum three pages/requests, local Greater London point-in-polygon filtering, and EPSG:27700 aggregation. The request date supplies the normal seasonal target month; an explicit override exists for evaluation.

Outcomes are:

- `strong_map_evidence`: at least 50 ranking-eligible records, five 1 km cells and two ranking datasets;
- `limited_contextual_evidence`: at least five retained records below the strong gate;
- `insufficient_evidence`: zero evidence or one to four isolated retained records;
- taxonomy and source states: `human_selection_required`, `taxon_not_found`, `source_unavailable`.

Only uncertainty ≤1,000 m is ranking-eligible. Values from 1,001–5,000 m are broad-zone context, values above 5,000 m are historical context, and missing uncertainty is audit-only. Counts and percentages for every tier are exposed. More than 50% missing uncertainty emits `coordinate_quality_limited`.

Record and ranking counts per dataset, dominant ranking dataset share, dominant basis-of-record share, and year/month distributions are exposed. A ranking dataset share above 80% emits `dataset_concentration_warning`; a second ranking dataset with only one record emits `minimal_second_dataset_contribution`. These warnings do not silently lower or replace the strong gate.

## Deduplication

The deterministic hierarchy is:

1. stable publisher `occurrenceID`;
2. other source identifiers (`organismID`, `materialSampleID`, or a namespaced catalogue number);
3. exact repeated GBIF key;
4. a taxon/date/dataset/1 km location/basis/recorder fingerprint only when no stable identifier exists.

Similar fingerprints carrying distinct stable identifiers are retained and counted as possible duplicates. This avoids merging distinct observations merely because they share a cell and month. Outputs report counts before/after, exact duplicates by method, possible duplicates retained and the method description.

## Privacy and safe maps

Three representations have different purposes:

- raw live occurrence coordinates exist only in memory while validating London membership and deriving a metric cell;
- sanitised Phase 0.1 records retain run-specific HMAC cell equality references whose secret is discarded, so those references cannot be placed on a map;
- application map output contains only aggregated EPSG:27700 1 km cells transformed as polygons to WGS84 after aggregation.

A public cell requires at least three ranking-eligible records, contains only aggregate record count, dataset count and date range, and never contains occurrence IDs. Safe cells are emitted only for `strong_map_evidence`; limited and insufficient evidence cannot create false hotspot cells. A cell remains historical context, not a sighting prediction, access claim or route.

The saved fixture currently contains 24 safe cells for Common woodpigeon and 18 for Eurasian magpie. House sparrow and `Turdus iliacus` were suppressed because a bounded live refresh no longer matched the older sanitised fixture after the hardened deduplication; this coverage gap is recorded in fixture provenance rather than mixing samples.

## Public sites, weather and constraints

Green spaces come only from the committed, checksum-validated OSM snapshot. Runtime Overpass requests are prohibited. `access=private` and `access=no` are excluded; `explicit_public` and `unspecified` are preserved. Distances are EPSG:27700 centre-point proximity and are always labelled approximate, never walking distance. Sites can be associated only with safe aggregate cells, never occurrence coordinates.

Phase 1.1 makes that association a hard grounding gate inside the site tool itself. `find_public_green_spaces` returns no candidates unless evidence is `strong_map_evidence`, at least one approved safe-map cell exists, and the site centre lies both inside the search radius and one of those allowed cells. Every successful candidate therefore has at least one `associated_safe_cell_id`. A strong record gate without safe cells is `safe_map_unavailable`, while an empty grounded radius is `no_suitable_public_sites`; source failure is separate. The complete typed `PublicSiteSearchResult` is retained in the evidence bundle.

`candidate_plan_ready` now requires all of the following: resolved Greater London location, strong evidence, non-empty safe-map cells, successful site search, and at least one candidate associated with an allowed cell. Strong occurrence evidence alone is never sufficient for a site plan.

Weather comes from Open-Meteo fixture/live repositories. A requested date must be present; another date is never substituted. Weather can produce a rain warning but is never used as sighting probability.

A maximum walking distance is accepted as a user constraint and returned as unresolved with `routing_not_available`. Phase 1 has no route provider. London scope, taxonomy validity, evidence quality, access certainty and source truth remain deterministic constraints.

## What a future agent may decide

A Phase 2 agent may decide which evidence tools are relevant, when to request clarification, and how to explain a validated bundle in British English. It must never override taxonomy validity, Greater London scope, coordinate-quality policy, the strong evidence gate, provenance, access certainty, privacy suppression, or the truth that walking distance has not been route-validated. Deterministic code remains the authority for those facts.

## Reproduction

Default fixture mode needs no network or key:

```bash
python -m pytest -m 'not live' -q

python -m scripts.run_expedition_backend \
  --postcode 'SW11 4NJ' --bird 'Common woodpigeon' \
  --date 2026-06-15 --duration-hours 2 --max-walking-km 3 --compact

python -m scripts.run_expedition_backend \
  --postcode 'SW11 4NJ' --bird 'Common swift' \
  --date 2026-07-15 --duration-hours 2 --compact

python -m scripts.run_expedition_backend \
  --postcode 'SW11 4NJ' --bird robin \
  --date 2026-01-15 --duration-hours 2 --compact

python -m scripts.run_expedition_backend \
  --postcode 'OX1 1AA' --bird 'Common woodpigeon' \
  --date 2026-06-15 --duration-hours 2 --compact
```

Live mode is explicit:

```bash
python -m scripts.run_expedition_backend \
  --postcode 'SW11 4NJ' --bird 'Blue tit' \
  --date 2026-09-01 --duration-hours 2 --mode live --compact

python -m scripts.generate_phase1_safe_map_candidate \
  --output-dir /tmp/phase1-safe-map-candidate
python -m scripts.phase0_feasibility \
  --promote-candidate /tmp/phase1-safe-map-candidate
```

The 2026-08-31 bounded live evaluation found one retained, unknown-uncertainty record for `Jynx torquilla` in target month 5. It correctly remained `insufficient_evidence` with no map cell. Live data changes, so this is evaluation evidence, not an eternal expected count.

## Deferred work

Dynamic LLM evidence-agent behaviour, LangGraph `ToolNode` orchestration, interrupts, checkpoint time travel, API/SSE, frontend maps, routing providers, MCP, conservation-law conclusions, bookings and field actions remain deferred to Phase 2 or later. Phase 1.1 adds only deterministic grounding gates and typed routing-ready statuses.
