# London Biodiversity Expedition Planner

An explainable, evidence-grounded planner for urban birdwatching expeditions in London.

The project is London-only, birds-first and English-only. It identifies areas with stronger historical occurrence evidence. It does not predict sightings, convert record counts into abundance or population estimates, guarantee access, or guarantee that a bird will be observed.

Phase 1 adds a strict, frontend-independent biodiversity backend with deterministic fixture/live repositories, seven typed services, safe aggregated map geometry and a fixed-order non-LLM orchestrator. The incident-investigation LangGraph graph, state, tools, scripted models, checkpointer and streaming adapter remain unchanged as a regression-protected reference. They are not the biodiversity agent.

## Phase 1 quick start

Fixture mode is the default and requires no network or API key:

```bash
python -m pytest -m 'not live' -q

python -m scripts.run_expedition_backend \
  --postcode 'SW11 4NJ' \
  --bird 'Common woodpigeon' \
  --date 2026-06-15 \
  --duration-hours 2 \
  --max-walking-km 3 \
  --compact
```

The output separates London/taxonomy/source status, historical evidence outcome, safe aggregate cell count, candidate-site count, exact-date weather availability and unresolved routing constraints. See [the Phase 1 backend documentation](docs/phase-1-biodiversity-backend.md) for contracts, architecture, privacy, live mode and reproduction commands.

Phase 1.1 hardens site grounding: a candidate plan is ready only when strong evidence has approved safe-map cells and every returned public-site candidate is associated with one of those cells. Strong evidence with no safe cells, an empty grounded search radius, and site-source failure remain distinct typed outcomes.

Phase 1.2 separates directly grounded recommendations from contextual green spaces. A recommendation now requires an OSM polygon or multipolygon footprint intersecting a safe cell; nearby and ordinary ungrounded sites remain explicitly non-recommended. The occurrence fixture was rebuilt end-to-end with the hardened pipeline, restoring same-snapshot safe cells for House sparrow and `Turdus iliacus`.

## Phase 2 quick start

Phase 2 adds a separate, checkpointed biodiversity LangGraph above the unchanged Phase 1.2 authority boundary. It parses natural English, uses a genuine ToolNode evidence loop, interrupts for validated human choices, composes a structured plan and applies deterministic grounding checks with one revision and a safe fallback. Fixture/scripted mode requires no API key:

```bash
python -m scripts.run_biodiversity_agent \
  --request "Plan a two-hour expedition from SW11 4NJ on 15 June 2026 to look for Common woodpigeon." \
  --data-mode fixture \
  --model-mode scripted
```

Use `--thread-id` to set the checkpoint thread, and `--auto-resume` for the documented taxonomy, context-only, uncertain-access and radius trade-off demonstrations. Live model mode uses `OPENAI_API_KEY`, `OPENAI_MODEL` and optional `OPENAI_BASE_URL`; no model or endpoint is hardcoded. See [the Phase 2 LangGraph documentation](docs/phase-2-biodiversity-langgraph.md) for the topology, state reducers, HITL payloads, safety boundary and all CLI commands.

## What Phase 0.1 demonstrates

`GBIFBirdNameResolver` accepts arbitrary user text rather than consulting a supported-species dictionary. It handles English common names, scientific names, case and whitespace differences, ambiguous names and unknown or misspelled input. Its typed outcomes are:

- `resolved`
- `human_selection_required`
- `taxon_not_found`

For example, the actual input `Common woodpigeon` is sent to GBIF and resolves to *Columba palumbus*. `robin` and `eagle` produce candidate lists generated from current GBIF search results; the system does not silently choose a London-likely species. The 12-input evaluation matrix is a regression sample, **not a species whitelist**. A live out-of-matrix check for `Blue tit` independently resolved *Cyanistes caeruleus* and completed the occurrence path.

Occurrence outcomes are:

- `strong_map_evidence`: at least 50 ranking-eligible records, five EPSG:27700 1 km cells and two datasets;
- `limited_contextual_evidence`: at least five retained London records exist but the strong gate is not met;
- `insufficient_evidence`: no retained evidence, or only one to four isolated records, exists in the bounded query;
- `human_selection_required`: taxonomy is ambiguous;
- `taxon_not_found`: no safe accepted bird match.

Only records with known coordinate uncertainty of at most 1,000 metres may contribute to 1 km ranking. Records at 1,001–5,000 metres are broad-zone evidence; those above 5,000 metres are historical context only; missing uncertainty stays auditable but cannot affect ranking. Fatal geospatial issues are rejected. Thresholds are never relaxed to make a taxon pass.

Sparse or rare evidence is a first-class state. One to four records remain insufficient evidence; they may be reported as isolated historical records but never as meaningful limited evidence, a hotspot, a reliable site recommendation or a sighting guarantee.

## London geography and privacy

The GBIF request uses a rectangular envelope only to bound remote work. Final inclusion uses deterministic point-in-polygon validation against the versioned OSM Greater London administrative boundary, relation `175342`.

Ranking uses British National Grid `EPSG:27700`, whose units are metres, with 1,000 m × 1,000 m cells. Coordinates are processed only in memory. Committed fixtures contain no occurrence latitude or longitude and store only HMAC cell/zone references produced with a run-specific secret that is not persisted.

The OSM public green-space candidate snapshot remains a one-off data-generation artifact. It excludes `access=private` and `access=no`; missing access is `unspecified`, not proof of public access. `© OpenStreetMap contributors`, ODbL 1.0.

## Bounded retrieval policy

For each resolved taxon, the default policy is:

- latest five complete calendar years plus the current year;
- target month ±1 month, including year-boundary month wrapping;
- pages of at most 300 GBIF records;
- at most three pages and three occurrence requests per taxon;
- early stop after the strong evidence gate or server exhaustion;
- deterministic first-record deduplication by GBIF key/occurrence ID, with a documented hashed fallback;
- separate server, sampled, deduplicated, quality-tier, retained and rejected counts;
- dataset, year and month distribution reporting.

The workflow never downloads all matches. A large `server_match_count` is neither the sampled count nor evidence of abundance.

## Set-up and offline validation

Python 3.10 or newer is required. `pyproj` supplies the authoritative EPSG:27700 transformation.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python -m pytest -q
python -m pytest -q tests/test_taxonomy_resolver.py
python -m pytest -q tests/test_spatial_quality.py -k coordinate_quality
python -m pytest -q tests/test_occurrence_behaviour.py
python -m scripts.phase0_feasibility
```

These commands are offline and need no API key.

## Explicit live candidate and promotion workflow

Ordinary live refresh never overwrites canonical fixtures:

```bash
python -m scripts.phase0_feasibility \
  --live-refresh \
  --output-dir /tmp/phase01-candidate

python -m scripts.phase0_feasibility \
  --live-check "Blue tit" \
  --target-month 4
```

Review checksums, schema and counts, then explicitly promote if appropriate:

```bash
python -m scripts.phase0_feasibility \
  --promote-candidate /tmp/phase01-candidate
```

Opt-in live pytest checks are excluded by default:

```bash
python -m pytest -m live -q
```

Boundary and OSM regeneration are also explicit:

```bash
python -m scripts.generate_london_boundary --live-refresh
python -m scripts.generate_osm_snapshot --live-refresh
```

All public API requests are sequential, carry a project-specific User-Agent, have timeouts, at most three transport attempts and bounded backoff. Live data changes; exact dated fixture counts are not permanent product expectations.

## Sources and product boundaries

- GBIF Species API: taxonomy.
- GBIF Occurrence Search API: bounded historical evidence; record and media licences are audited separately.
- Postcodes.io: London postcode feasibility.
- Open-Meteo: weather feasibility, not bird prediction.
- OpenStreetMap/Nominatim: versioned Greater London boundary.
- OpenStreetMap/Overpass: one-off green-space candidate snapshot, not a runtime query.

GiGL Spaces to Visit and the authenticated GBIF bulk Download API are not dependencies.

- The application does not guarantee species sightings.
- It does not expose precise sensitive-species locations.
- It does not provide scientific population estimates.
- It does not guarantee that a mapped site is currently open or accessible.
- It does not execute bookings or field actions.

See [the Phase 0.1 feasibility report](docs/phase-0-feasibility.md) and [fixture schema](docs/fixture-schema.md).

## Roadmap boundary

Phase 0, Phase 0.1 and the Phase 1 deterministic biodiversity domain backend are implemented. The dynamic biodiversity LangGraph agent, frontend, routing, HITL nodes, persistence expansion and MCP remain deferred.
