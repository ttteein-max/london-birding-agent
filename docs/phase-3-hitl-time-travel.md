# Phase 3 HITL persistence and time travel

Phase 3 makes the London biodiversity graph resumable across Python processes and adds application-owned checkpoint history, replay, fork and deterministic plan comparison. Phase 1 remains authoritative for taxonomy acceptance, historical-evidence classification, privacy, safe-map eligibility, public-site grounding, constraints and candidate-plan readiness.

The workflow remains London-only, English-only and birds-first. Historical occurrence evidence is not a sighting probability, and record counts are not abundance or population estimates.

## Durable checkpointer lifecycle

`create_biodiversity_checkpointer()` still returns an isolated `InMemorySaver` for unit tests. Terminal workflows use `open_biodiversity_sqlite_checkpointer(path)`, a context manager around the official `langgraph-checkpoint-sqlite` `SqliteSaver`.

```python
with open_biodiversity_sqlite_checkpointer(
    "data/runtime/biodiversity-checkpoints.sqlite"
) as checkpointer:
    graph = build_biodiversity_graph(..., checkpointer=checkpointer)
    graph.invoke(input_state, {"configurable": {"thread_id": "expedition-1"}})
```

The connection remains open while the graph is compiled and used, commits on successful exit and always closes. The database path is injectable, and tests use `tmp_path`. SQLite, WAL and SHM files are ignored and are never committed.

The serializer disables pickle fallback and sets JSON and msgpack module allowlists to strict mode. Checkpoint state therefore uses JSON dictionaries plus LangGraph/LangChain's known safe built-in message types; arbitrary Python modules cannot be revived from the database.

SQLite is appropriate for this local portfolio/demo and synchronous CLI. It is not presented as a horizontally scaled or multi-instance production database.

## Run identifiers and branch lineage

The public contract uses typed models in `app.biodiversity.run_models`, not LangGraph `StateSnapshot`:

- `thread_id` selects the durable checkpoint namespace for one logical expedition.
- `checkpoint_id` selects one immutable historical graph state inside the thread.
- `branch_id` identifies one original or forked trajectory inside that thread.
- `parent_branch_id` identifies the trajectory from which a fork was created.
- `forked_from_checkpoint_id` identifies the exact historical point selected for the fork.

`CheckpointSummary` records checkpoint and parent IDs, creation time, graph step/source, next nodes, interrupt kind, terminal status, applied decisions and the branch's final checkpoint ID. `RunBranch` records its head and final checkpoint IDs plus fork updates. Initial runs and forks remain readable by exact checkpoint ID; a fork never overwrites the original final plan.

## Taxonomy HITL

Ambiguous taxonomy now passes through `prepare_taxon_selection` before `taxon_selection_interrupt`. The preparation node performs all bounded repository I/O, saves the coordinate-free result, and creates a checkpoint. The interrupt node only displays that saved payload, calls `interrupt()` and validates the resume, so resuming cannot repeat candidate API calls.

Candidate payload:

```json
{
  "kind": "taxon_selection",
  "candidate_budget": 3,
  "request_budget": 3,
  "candidates": [
    {
      "accepted_taxon_key": 2489281,
      "common_name": "Lesser Ground-robin",
      "scientific_name": "Amalocichla incerta (Salvadori, 1876)",
      "canonical_name": "Amalocichla incerta",
      "rank": "SPECIES",
      "taxonomic_status": "ACCEPTED",
      "class": null,
      "order": null,
      "family": null,
      "genus": null,
      "resolution_method": "gbif_species_search_related_common_name",
      "confidence": null,
      "evidence_preview": {
        "status": "evaluated",
        "evidence_outcome": "insufficient_evidence",
        "sampled_count": 0,
        "retained_count": 0,
        "ranking_eligible_count": 0,
        "dataset_count": 0,
        "target_months": [1, 2, 12],
        "source_status": "available",
        "limitations": [],
        "provenance_reference": "https://www.gbif.org/developer/occurrence"
      }
    }
  ],
  "resume_schema": {"accepted_taxon_key": "one listed integer key"}
}
```

Fields in the GBIF hierarchy are `null` when the upstream saved response did not provide them; the application never invents them. The preview budget evaluates at most three candidates with one page/request each. Later candidates are `not_evaluated_budget`, which is distinct from no evidence. A source error is `source_failure`, also distinct from an evaluated `insufficient_evidence` result.

Resume:

```json
{"accepted_taxon_key": 2489281}
```

Only a listed accepted key is valid. `BiodiversityRunManager.resume()` validates the resume before invoking LangGraph, so malformed or invented input cannot create a resume checkpoint or mutate the saved interrupt.

## Low-evidence HITL

The deterministic validation node separates London-wide occurrence evidence from local site grounding:

- `expand_search_radius` is offered only when strong occurrence evidence exists but no directly grounded public-site polygon exists inside the current radius. The new radius must increase and stay at or below 25 km. Only public-site evidence and downstream products are recomputed.
- `widen_seasonal_window` is offered only while the request radius is below three months. The new value must increase. Seasonal months wrap across the year boundary and the actual list appears in canonical tool audit/provenance. Occurrence evidence, safe cells, sites, constraints, bundle and plan are recomputed; location, taxonomy and exact-date weather are retained.
- `consider_related_taxa` is offered only when the bounded deterministic GBIF query saved candidates. Same-genus candidates precede same-family candidates and each payload labels its level. Selecting this option produces a second `related_taxon_selection` interrupt. A related taxon is not an ecological substitute and is not asserted to be easier to observe.
- `keep_constraints_accept_low_confidence` records the user's acknowledgement and produces a `context_only` or otherwise low-confidence result with no recommended sites. Contextual sites may remain, and the explanation states that the evidence gate did not pass.

Examples:

```json
{"option": "expand_search_radius", "search_radius_km": 8}
{"option": "widen_seasonal_window", "seasonal_window_radius_months": 2}
{"option": "consider_related_taxa"}
{"option": "keep_constraints_accept_low_confidence"}
```

The follow-up related selection uses the same accepted-key-only schema as taxonomy selection.

## Replay and fork

History comes from `graph.get_state_history(config)` and is translated to `CheckpointSummary`. Exact historical operations use the config carried by the selected history item, including its checkpoint namespace.

Replay:

```python
historical = selected_history_snapshot
graph.invoke(None, historical.config)
```

Replay re-executes nodes after the selected checkpoint. API, model and interrupt nodes after that point can run again. Replaying a final checkpoint is a no-op.

Fork:

```python
fork_config = graph.update_state(
    historical.config,
    values=validated_and_invalidated_state,
    as_node="apply_validated_user_choice",
)
graph.invoke(None, fork_config)
```

`update_state` is not rollback. It creates a new checkpoint that branches from the historical point, passes updates through reducers, and leaves the original checkpoint and final plan intact.

`ForkUpdates` allows only:

- `search_radius_km`
- `seasonal_window_radius_months`
- `target_month_override`
- `target_local_date`
- `rain_preference`
- `duration_hours`
- `selected_related_taxon_key`, provided it is present in saved validated candidates

Timezone, London scope, provenance, evidence counts, tool audit, safe cells, final plan and terminal status cannot be supplied as fork updates. Those are derived again by code.

## Reducers and invalidation

Messages, tool audit, structured evidence and applied decisions use append/merge reducers. Fork code never assumes that `update_state(..., [])` will erase them. Existing audit and decisions are retained, new deterministic refresh records use stable unique call IDs, and exact duplicates are suppressed by reducers.

Replacement-valued derived state is cleared according to this deterministic dependency table:

| Validated change | Recompute | Preserve |
|---|---|---|
| search radius | public sites, constraints, bundle, plan | location, taxonomy, occurrence, weather |
| seasonal radius or month override | occurrence, safe cells, public sites and downstream | location, taxonomy, exact-date weather |
| target date without month override | occurrence, weather, public sites and downstream | location, taxonomy |
| target date with month override | weather, constraints, bundle, plan | location, taxonomy, occurrence, public sites |
| rain preference or duration | constraints, bundle, plan | all source evidence |
| validated related taxon | occurrence, safe cells, public sites and downstream | location, exact-date weather |

## Deterministic comparison

`PlanComparison` reports differences; it never asks an LLM which plan is better. It compares request constraints, selected taxon, evidence outcome, weather status, plan status, recommended and contextual site IDs, limitations, provenance sources and applied user decisions.

Example shape:

```json
{
  "checkpoint_a": "...",
  "checkpoint_b": "...",
  "request_constraints": {
    "checkpoint_a": {"search_radius_km": 5.0},
    "checkpoint_b": {"search_radius_km": 8.0},
    "changed": true
  },
  "changed_fields": ["request_constraints", "applied_user_decisions"]
}
```

## CLI

The original runner remains available and now defaults to the durable SQLite checkpointer:

```bash
python -m scripts.run_biodiversity_agent \
  --request "Plan a two-hour expedition from SW11 4NJ on 15 June 2026 to look for Common woodpigeon." \
  --thread-id expedition-1 \
  --checkpoint-db data/runtime/biodiversity-checkpoints.sqlite
```

Durable management commands:

```bash
python -m scripts.manage_biodiversity_runs start \
  --thread-id expedition-1 --request "..."

python -m scripts.manage_biodiversity_runs resume \
  --thread-id expedition-1 \
  --resume-json '{"accepted_taxon_key":2489281}'

python -m scripts.manage_biodiversity_runs history --thread-id expedition-1

python -m scripts.manage_biodiversity_runs replay \
  --thread-id expedition-1 --checkpoint-id CHECKPOINT_ID

python -m scripts.manage_biodiversity_runs fork \
  --thread-id expedition-1 --checkpoint-id CHECKPOINT_ID \
  --updates-json '{"search_radius_km":8}' \
  --branch-label wider-radius

python -m scripts.manage_biodiversity_runs compare \
  --thread-id expedition-1 \
  --checkpoint-a ORIGINAL_FINAL --checkpoint-b FORK_FINAL
```

`start` refuses an existing thread. `resume` needs no original request and works after the first Python process exits. Missing threads/checkpoints are explicit errors. History and comparison are JSON-safe application contracts. Executing commands continue to save `events.json`, `timings.json`, `metadata.json`, `tool-audit.json` and `final-plan.json`; metadata includes checkpoint and branch lineage.

## Fixture, live and privacy behavior

Fixture/scripted mode reads no OpenAI environment variables and performs no network work. The taxonomy preview and related-taxa snapshots are saved API-shaped, checksummed fixtures. Live preview and related lookup enforce candidate, page and request budgets; default tests remain offline, and live tests remain opt-in.

Public payloads, events, reports, history, fork results and comparisons do not contain occurrence coordinates, occurrence identifiers, `record_ref`, HMAC values or safe-cell associations. Observability events contain only safe IDs, statuses, interrupt kinds, changed field names and error types. They do not copy prompts, raw tool output or source records.

Phase 4 frontend/timeline UI, Phase 5 route provider and Phase 6 conservation scoring remain deferred.
