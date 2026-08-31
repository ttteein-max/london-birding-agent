# Phase 0.1 data-hardening report

## Decision and scope

Phase 0.1 passes. The evidence layer now supports arbitrary English common-name and scientific-name bird input, dynamic ambiguity/not-found outcomes, bounded representative retrieval, explicit coordinate-quality use tiers, a real London polygon, EPSG:27700 cells and internally consistent schema-v2 provenance.

This remains pre-Phase 1. The protected incident LangGraph graph, state/schema, investigator tools, streaming adapter, scripted models and checkpointer were not changed.

The product remains: **“London Biodiversity Expedition Planner — an explainable, evidence-grounded planner for urban birdwatching expeditions in London.”** Historical occurrence evidence is not sighting probability, abundance, a population estimate or a guarantee.

## Dynamic taxonomy resolution

The reusable resolver normalises case and whitespace while preserving the original input. It first attempts GBIF Species Match, then searches up to 100 current species results and restricts candidates to accepted, species-rank `Aves` taxa. It has no fixed supported-species dictionary.

Examples from the 31 August 2026 live run:

| User input | Outcome | Result |
|---|---|---|
| `Common woodpigeon` | `resolved` | *Columba palumbus*, GBIF 2495455, matched English name “Common Woodpigeon” |
| `Falco subbuteo` | `resolved` | *Falco subbuteo*, GBIF 2481035, exact scientific match |
| `robin` | `human_selection_required` | 12 API-generated accepted bird candidates retained |
| `eagle` | `human_selection_required` | 12 API-generated accepted bird candidates retained |
| `londun sky parrott xyz` | `taxon_not_found` | no safe exact bird match |

Ambiguous candidates come from the current API response. The resolver does not issue predeclared “European/American/Ryukyu robin” searches and does not substitute a common or London-likely species.

## Evaluation matrix

The matrix is a set of regression scenarios, **not a species whitelist**. Counts below belong to the dated 2026-08-31 snapshot and are not permanent expectations.

| Scenario | Input | Taxonomy/evidence outcome | Server matches | Sampled | Ranking eligible | Retained |
|---|---|---|---:|---:|---:|---:|
| Abundant/common | Common woodpigeon | strong | 24,804 | 300 | 227 | 280 |
| Abundant/common | House sparrow | strong | 12,476 | 300 | 93 | 208 |
| Abundant/common | Eurasian magpie | strong | 26,676 | 300 | 219 | 271 |
| Moderate/seasonal | Common swift | limited contextual | 11,711 | 900 | 31 | 717 |
| Moderate/seasonal, scientific input | *Falco subbuteo* | limited contextual | 1,246 | 900 | 7 | 743 |
| Moderate/seasonal, scientific input | *Turdus iliacus* | strong | 11,406 | 300 | 86 | 191 |
| Rare/sparse | Corncrake | insufficient | 0 | 0 | 0 | 0 |
| Rare/sparse | Cirl bunting | insufficient | 0 | 0 | 0 | 0 |
| Extant/negligible London evidence | Kakapo | insufficient | 0 | 0 | 0 | 0 |
| Ambiguous | robin | human selection required | — | — | — | — |
| Ambiguous | eagle | human selection required | — | — | — | — |
| Unknown/misspelled | londun sky parrott xyz | taxon not found | — | — | — | — |

The rare scenarios were selected because earlier/all-time London evidence was sparse or absent; the hardened recent seasonal query found zero records, which is an honest insufficient-evidence result. Synthetic behaviour tests separately prove that a small number of valid records yields `limited_contextual_evidence`, never a hotspot or reliable site recommendation.

An arbitrary input outside the matrix, `Blue tit`, resolved live to *Cyanistes caeruleus* (GBIF 2487879) and returned `strong_map_evidence`: server matches 22,895, sampled 300, ranking eligible 205, retained 257, two ranking datasets. This demonstrates that the matrix is not a whitelist.

## Evidence outcomes

- `strong_map_evidence`: at least 50 ranking-eligible records, five distinct EPSG:27700 1 km cells and two ranking datasets.
- `limited_contextual_evidence`: at least one retained London record but the strong gate is not met. Wording must state whether evidence is old, seasonal, imprecise, sparse or dataset-concentrated.
- `insufficient_evidence`: no retained evidence in the bounded recent seasonal query.
- `human_selection_required`: multiple reasonable accepted bird taxa.
- `taxon_not_found`: no safe accepted bird match.

Strong status is calculated only from location-quality `strong` records. A large server count or many unknown-uncertainty records cannot make a taxon strong.

## Coordinate-quality use tiers

| Uncertainty | Tier | Permitted use |
|---|---|---|
| ≤1,000 m | `strong` | site-level/1 km ranking |
| 1,001–5,000 m | `weak` | broad-zone or borough context only |
| >5,000 m | `context_only` | London historical context only |
| missing | `unknown` | audit only; excluded from ranking |

Exact tests cover 1,000, 1,001, 5,000, 5,001 metres and missing uncertainty. Fatal GBIF geospatial issues and points outside the actual London polygon are rejected. Thresholds are never loosened.

Aggregate dated snapshot counts:

- ranking eligible (`strong`): 663
- weak: 15
- context only: 16
- unknown uncertainty: 1,716
- rejected: 590, all outside the final Greater London polygon
- retained total: 2,410

## Greater London boundary and metric cells

The remote API envelope reduces query work only. Every coordinate is tested locally against `data/boundaries/greater-london-2026-08-31.geojson`, sourced from OSM relation 175342 through Nominatim.

- Geometry: Polygon, outer ring 12,919 points
- Retrieved: `2026-08-31T04:28:45Z`
- SHA-256: `7f3e3e39fe5c89ca9ff7a49b6e7e32f348d9ebf8881accc305b720f601df6cae`
- Licence: ODbL 1.0
- Attribution: `© OpenStreetMap contributors`

The metric grid is British National Grid EPSG:27700. Each documented cell is exactly 1,000 × 1,000 projected metres. In-memory cell equality is stored as a run-specific HMAC reference; the secret and occurrence coordinates are never persisted. Weak/context/unknown records have no 1 km ranking reference.

## Representative retrieval and stopping budget

For each resolved taxon:

1. Query the latest five complete years plus current year (2021–2026 for this snapshot).
2. Query target month ±1 month; January correctly wraps to December/January/February.
3. Request sequential 300-record pages.
4. Stop after at most three pages and three occurrence requests.
5. Stop early when the strong gate is reached or the server is exhausted.
6. Deduplicate by GBIF key or occurrence ID; when absent, use a SHA-256 fingerprint over dataset, institution, catalogue number, date and in-memory coordinates. Keep the first record deterministically.
7. Report dataset diversity plus retained year and month distributions.

The dated matrix used 13 occurrence requests. Strong common scenarios stopped after one page; Common swift and *Falco subbuteo* used the full three-page budget. No query attempted to download all server matches.

## Corrected provenance semantics

Occurrence fixture schema is version 2. Its aggregate count example is:

```json
{
  "server_match_count": 88319,
  "sampled_count": 3000,
  "deduplicated_count": 3000,
  "duplicates_removed": 0,
  "ranking_eligible_count": 663,
  "weak_count": 15,
  "context_only_count": 16,
  "unknown_uncertainty_count": 1716,
  "rejected_count": 590,
  "retained_total_count": 2410,
  "rejection_counts_by_reason": {
    "outside_greater_london_boundary": 590
  }
}
```

The validator enforces:

- `sampled - duplicates = deduplicated`;
- `retained + rejected = deduplicated`;
- strong/ranking + weak + context-only + unknown = retained;
- sampled never exceeds server matches.

Thus 88,319 server matches are explicitly not represented as downloaded or filtered records.

## Stable validation and live refresh boundary

Default pytest contains behaviour tests with synthetic GBIF-shaped data and separate dated snapshot-integrity tests. It does not assert permanent live counts or an eternal OSM feature count. Two `live`-marked integration checks are excluded by `pytest.ini` unless explicitly selected.

Live refresh writes candidate fixtures to a supplied temporary/versioned directory and records `canonical_replaced: false`. Canonical replacement requires the separate `--promote-candidate` command after checksum and count validation.

```bash
python -m pytest -q
python -m scripts.phase0_feasibility
python -m scripts.phase0_feasibility --live-refresh --output-dir /tmp/phase01-candidate
python -m scripts.phase0_feasibility --live-check "Blue tit" --target-month 4
python -m scripts.phase0_feasibility --promote-candidate /tmp/phase01-candidate
python -m pytest -m live -q
```

Public API responses and counts will change. All requests have timeouts, at most three transport attempts, bounded backoff, a project User-Agent and explicit request/page budgets.

## Scientific, privacy and access limitations

GBIF is affected by observer effort, reporting duplication, dataset composition, taxonomy drift, incomplete uncertainty and licence metadata. Evidence can be seasonal, old or dominated by one dataset. Record licences do not automatically license linked media.

No precise occurrence coordinates are committed or printed. Rare/sensitive results use generalised context only. One or a few records are never called a hotspot. No related species is substituted without user approval. OSM green-space candidates do not guarantee current access or opening.
