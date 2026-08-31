# London Biodiversity Expedition Planner

An explainable, evidence-grounded planner for urban birdwatching expeditions in London.

The project is London-only, birds-first and English-only. It identifies areas with stronger historical occurrence evidence; it does **not** turn GBIF record counts into sighting probability, abundance or population estimates.

Phase 0 is complete on the `london-biodiversity-pivot` branch. It proves the data-feasibility gate with reproducible public-API fixtures and a real, versioned OpenStreetMap-derived snapshot. The incident-investigation LangGraph code remains intact as reusable infrastructure and is not yet a biodiversity backend.

## Phase 0 result

The predeclared map-viability gate requires all of:

- at least 50 retained records in the bounded sample;
- at least five distinct, approximately 1 km spatial cells;
- at least two GBIF datasets;
- valid coordinates inside the declared Greater London envelope;
- `occurrenceStatus=PRESENT` where available;
- no fatal GBIF geospatial issue and no known coordinate uncertainty above 10 km.

Common woodpigeon, house sparrow and Eurasian magpie pass. Corncrake fails safely with five retained records; great auk fails safely with none. The common name `robin` remains unresolved because GBIF returns a higher-rank match and multiple species candidates, so a later interface must ask the user to select one. See [the full feasibility report](docs/phase-0-feasibility.md).

The gate measures whether a defensible historical-evidence map can be built. It is not ecological inference and not a promise of a sighting.

## Evidence and deterministic boundaries

The eventual agent may dynamically decide which resolved taxon, time window, candidate sites and explanatory evidence to investigate. Deterministic code must validate taxonomy, London scope, occurrence quality, licence fields, spatial aggregation, minimum evidence, weather schema, access certainty and product safety language before presenting a result.

Phase 0 sources are:

- GBIF Species API for taxonomy (`GBIF.org`; source datasets retain their own terms);
- GBIF Occurrence Search API for historical occurrences (record and media licences are audited independently);
- Postcodes.io for `SW11 4NJ` lookup, with its Royal Mail, Ordnance Survey and ONS attribution requirements;
- Open-Meteo forecast API (`CC BY 4.0`, plus upstream model terms) for weather feasibility;
- OpenStreetMap through a one-off Overpass workflow (`ODbL 1.0`; `© OpenStreetMap contributors`) for public green-space candidates.

GiGL Spaces to Visit is not used. The authenticated GBIF bulk Download API is not used. Overpass is not a runtime dependency.

Fixtures contain compact, sanitised fields. Occurrence coordinates and identifiers are replaced with non-reversible hashes of approximately 1 km cells and source identifiers. Media are neither downloaded nor displayed; each media identifier and licence is audited separately, and a record licence is never treated as a media licence.

The OSM snapshot excludes `access=private` and `access=no`. Missing access tags are marked `unspecified`, not treated as proof of public access. The result is a candidate dataset, not a guaranteed-access dataset.

## Set-up and commands

Python 3.10 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Default validation is deterministic, offline and needs no network, OpenAI key or other API key:

```bash
python -m pytest -q
python -m scripts.phase0_feasibility
```

Live refresh is explicit, sequential and fails non-zero after bounded timeouts and retries:

```bash
python -m scripts.phase0_feasibility --live-refresh
python -m scripts.generate_osm_snapshot --live-refresh
```

The second command is a one-off snapshot-generation workflow. Do not place it in a user-request path. The exact Overpass QL is versioned at `data/osm/london-green-spaces.overpassql`; provenance includes the endpoint, retrieval time, query checksum, processing rules, feature count and regeneration command.

The retained incident-agent demonstration remains offline by default:

```bash
python -m scripts.run_agent \
  "checkout failures increased after a deployment" \
  --mode scripted
```

Its optional live model mode is unrelated to Phase 0. `OPENAI_API_KEY` is only needed for that old demonstration. `OPENAI_BASE_URL`, if set, must be a user-supplied OpenAI-compatible endpoint; it is optional and is not a project dependency.

## Retained LangGraph foundation

The existing explicit model node and `ToolNode` loop, dependency-injected models, scripted offline models, tests, checkpointer support, thread IDs, state-history boundary and application-owned streaming event adapter are preserved. Phase 0 does not rewrite `app/graph/workflow.py`, incident state/schema, investigator tools or streaming.

## Product boundaries

- The application does not guarantee species sightings.
- It does not expose precise sensitive-species locations.
- It does not provide scientific population estimates.
- It does not guarantee that a mapped site is currently open or accessible.
- It does not execute bookings or field actions.

Historical records are affected by observer effort, dataset coverage, duplicate reporting, taxonomy changes, coordinate uncertainty and reporting bias. Weather forecasts change. OSM can be incomplete or outdated. All candidate locations require a current access check.

## Roadmap

- Phase 0: feasibility evidence, fixtures, OSM snapshot and safety boundaries — implemented.
- Phase 1: replace the incident domain with biodiversity state, tools and workflow while retaining the infrastructure — not implemented.
- Later phases: user-facing streaming, richer routing, human-in-the-loop taxon selection, persistent storage, MCP integrations and frontend work — not implemented.
