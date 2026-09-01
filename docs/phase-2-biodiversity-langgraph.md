# Phase 2 biodiversity LangGraph

Phase 2 adds a checkpointed, natural-English LangGraph agent above the deterministic Phase 1.2 London biodiversity backend. Phase 1.2 remains authoritative for taxonomy, London scope, occurrence quality, privacy, safe-map eligibility, site grounding, access certainty, weather truth, constraints, suggested actions and `candidate_plan_ready`.

The language model can parse a request, choose which evidence tool to call next and explain a validated result. It cannot replace any scientific, spatial, privacy or safety decision.

## Graph topology

```text
START
  │
  ▼
parse_expedition_request ──missing/contradictory──► request_clarification_interrupt
  │                                                        │ Command(resume=...)
  └────────────────────────────────────────────────────────┘
  ▼
resolve_location ──invalid/outside London──► location_correction_interrupt
  │                                                  │ Command(resume=...)
  └──────────────────────────────────────────────────┘
  ▼
resolve_taxon ──ambiguous──► taxon_selection_interrupt
  │               unknown──► bird_input_correction_interrupt
  │                              │ Command(resume=...)
  └──────────────────────────────┘
  ▼
evidence_agent ──tool calls──► evidence_tools (LangGraph ToolNode)
  ▲                                  │
  └──── record_structured_evidence ◄─┘
  │ no tool calls
  ▼
deterministic_validation ──actionable choice──► actionable_tradeoff_interrupt
  ▲                                                  │
  │                                 apply_validated_user_choice
  └──────────────────────── minimum necessary rerun ┘
  │ validated
  ▼
compose_expedition_plan
  ▼
grounding_and_safety_checks ──invalid once──► revise_expedition_plan
  │                                              │
  ├────────────── valid ◄────────────────────────┘
  └──invalid twice──► deterministic_plan_fallback
  ▼
END
```

Required-source failures and the six-round evidence limit end in a typed safe failure. They do not become `insufficient_evidence` and do not produce an unsupported plan.

## State and persistence

`BiodiversityAgentState` is a dedicated `TypedDict`. Checkpointed domain values use Pydantic JSON dictionaries rather than repository, model or other runtime objects.

| Field group | Representation | Update semantics |
|---|---|---|
| `messages` | LangChain messages | `add_messages` |
| original and parsed request | text and JSON dictionary | replacement |
| validated request, location and taxon | Phase 1 model JSON dictionaries | replacement |
| occurrence, weather and public-site evidence | Phase 1 model JSON dictionaries | replacement after validation |
| pending tool results and audit | compact JSON dictionaries keyed by tool-call ID | idempotent merge |
| structured evidence and tool errors | compact JSON dictionaries | unique append |
| constraints and grounding errors | JSON lists | explicit replacement |
| applied user decisions and recorded tool-call IDs | JSON lists | unique append |
| evidence bundle and Phase 1 plan | JSON dictionaries | replacement |
| draft and final Phase 2 plans | JSON dictionaries | replacement |
| loop and revision counts | integers | replacement |
| HITL kind and payload | text and JSON dictionary | replacement/clear |
| terminal status and result | text and JSON dictionary | replacement |

`create_biodiversity_checkpointer()` provides an in-memory checkpointer for tests and the CLI. Every checkpointed run needs a stable `configurable.thread_id`; a resume must use the same ID. Separate IDs remain isolated. Tool results are recorded by call ID, so replay and resume do not duplicate evidence.

## Node contracts and authority

`parse_expedition_request` uses an injected model with structured output to create an optional-field `ExpeditionRequestDraft`. The parser prompt treats embedded instructions as user data. Deterministic construction of `ExpeditionRequest` then enforces required values, exactly one location and `Europe/London`. Missing data causes an interrupt; the parser never fills it silently.

`resolve_location` and `resolve_taxon` call `lookup_uk_postcode` and `resolve_bird_taxon` directly. The model cannot provide repositories, change the boundary or select an ambiguous taxon.

`evidence_agent` has three parameter-free, English-described tools:

- `search_occurrences`: uses the resolved taxon and trusted seasonal target month;
- `get_weather_context`: uses the resolved location and exact requested date;
- `find_public_green_spaces`: uses the trusted radius, resolved location and already-recorded occurrence evidence.

The tools are executed by a genuine LangGraph `ToolNode`. Their repositories are bound in closures. Tool messages contain compact summaries only. Full Phase 1 results are kept as JSON state, validated in `record_structured_evidence`, and never include raw occurrence records. Model-visible summaries omit occurrence coordinates, occurrence identifiers, `record_ref`, HMAC references, site centre coordinates and safe-cell associations.

Identical calls use canonical trusted arguments. A completed non-retryable call is suppressed; an explicitly retryable failure may be retried. Public-site search before occurrence evidence returns a typed ordering error. The sixth evidence-agent round cannot start another tool execution.

`deterministic_validation` reuses `validate_expedition_constraints`, `build_expedition_evidence_bundle` and the extracted Phase 1 `build_deterministic_expedition_plan` readiness function. Thus `candidate_plan_ready` still requires:

- a resolved location inside Greater London;
- `strong_map_evidence`;
- non-empty approved safe-map cells;
- successful public-site search;
- at least one directly grounded Polygon/MultiPolygon candidate;
- deterministic safe-cell associations for every candidate.

Strong occurrence evidence alone is not enough. Nearby and ungrounded sites remain contextual. `unspecified` access stays uncertain. Projected distance stays distinct from walking distance. Exact-date weather remains typed as unavailable when the requested date is unsupported.

## HITL payloads and resumes

All resumes are validated and written to `applied_user_decisions`. Malformed, unknown and invented values raise an error without applying state changes.

Request clarification:

```python
graph.invoke(
    Command(resume={"updates": {"target_local_date": "2026-06-15"}}),
    {"configurable": {"thread_id": "expedition-1"}},
)
```

Taxon selection exposes only the GBIF candidates and accepts only a listed key:

```python
graph.invoke(
    Command(resume={"accepted_taxon_key": 2489281}),
    {"configurable": {"thread_id": "expedition-1"}},
)
```

Location and bird corrections:

```python
Command(resume={"postcode": "SW11 4NJ"})
Command(resume={"bird_input": "Common woodpigeon"})
```

Actionable trade-offs are built from deterministic suggested actions and constraints. Examples are:

```python
Command(resume={"option": "expand_search_radius", "search_radius_km": 5.0})
Command(resume={"option": "accept_context_only"})
Command(resume={"option": "accept_uncertain_access"})
Command(resume={"option": "revise_rain_preference"})
```

The graph does not offer user choices for source failure, unavailable routing, unsupported non-London operation, privacy thresholds or missing safe-map data.

## Composition, grounding and fallback

The composer receives a coordinate-free validated bundle containing exact status, taxon, date, duration, London start context, candidate/contextual site views, weather, constraints, limitations, actions, provenance references and evidence-citation names. It does not receive the original request or raw occurrence records.

The strict `BiodiversityExpeditionPlan` separates recommended and contextual sites. Each site copies a deterministic ID, name, access certainty, evidence tier and approximate straight-line distance. Evidence attributions and provenance references are separate required fields and are checked exactly.

Post-generation checks reject:

- invented site IDs or contextual promotion;
- status disagreement with Phase 1;
- changed dates, durations, distances, access values, weather values or constraints;
- invented or exposed safe cells and associations;
- occurrence coordinates, IDs, `record_ref` or HMAC text;
- access/opening assurances, walking-route claims, abundance/population claims, predictions, hotspot claims, sighting probabilities or guarantees;
- missing limitations, attribution, provenance or evidence citations.

One revision call receives the explicit failures. A second failure returns `deterministic_phase_2_fallback`, assembled solely from the Phase 1 bundle. An unsafe model draft is never returned.

## Model configuration

Graph construction accepts either one injected chat model or separate parser, evidence and composer models. Construction itself reads no environment variables. Live mode uses the existing `create_live_chat_model()` factory and therefore only:

- `OPENAI_API_KEY`;
- `OPENAI_MODEL`;
- optional `OPENAI_BASE_URL`.

No model name, endpoint or credential is embedded in Phase 2 code. Scripted mode uses biodiversity-specific fake models and requires no key.

## Commands

Strong fixture/scripted plan:

```bash
python -m scripts.run_biodiversity_agent \
  --request "Plan a two-hour expedition from SW11 4NJ on 15 June 2026 to look for Common woodpigeon." \
  --data-mode fixture \
  --model-mode scripted
```

Context-only plan with a safe automatic acknowledgement:

```bash
python -m scripts.run_biodiversity_agent \
  --request "Plan a two-hour expedition from SW11 4NJ on 15 July 2026 to look for Common swift." \
  --auto-resume
```

Taxonomy interrupt and resume demonstration:

```bash
python -m scripts.run_biodiversity_agent \
  --request "Plan a two-hour expedition from SW11 4NJ on 15 January 2026 to look for robin." \
  --auto-resume
```

No-suitable-site radius trade-off:

```bash
python -m scripts.run_biodiversity_agent \
  --request "Plan a two-hour expedition from SW11 4NJ on 15 June 2026 to look for Common woodpigeon with a search radius of 0.1 km." \
  --auto-resume
```

Explicit live model and data mode:

```bash
OPENAI_API_KEY=... OPENAI_MODEL=... \
python -m scripts.run_biodiversity_agent \
  --request "Plan a two-hour expedition from SW11 4NJ on 1 September 2026 to look for Common woodpigeon." \
  --data-mode live \
  --model-mode live
```

Offline validation:

```bash
python -m pytest -m 'not live' -q
python -m compileall -q app scripts tests
python -m scripts.phase0_feasibility
git diff --check
```

The opt-in live smoke test additionally requires `RUN_LIVE_BIODIVERSITY_AGENT=1`. It asserts typed outcomes and privacy invariants, not permanent counts or exact prose.

## Limitations and deferred work

Phase 2 remains terminal-only and London-only. It does not implement a frontend, map UI, HTTP/SSE API, MCP, runtime Overpass query, route provider, bookings, population estimation, occurrence prediction, conservation-law conclusions or fixture promotion. Maximum walking distance remains recorded but unresolved because no routing provider exists. Public access and opening must be checked independently. Checkpoint storage is in memory for local and test use; production durable storage is deferred.
