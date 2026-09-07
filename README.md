# London Biodiversity Expedition Planner

**A full-stack LangGraph case study in stateful orchestration, typed human-in-the-loop workflows, durable execution and agent observability.**

It turns a natural-language London birdwatching request into an evidence-grounded, route-validated field plan—or stops safely when the available evidence or constraints cannot support one.

London-only · birds-first · English-only · historical evidence, not a sighting prediction

[See the demo](#demo) · [Engineering highlights](#engineering-highlights) · [Run it locally](#quick-start) · [Explore the architecture](#architecture-and-core-workflow)

## Demo

[![Watch the London Biodiversity Expedition Planner demo](https://img.youtube.com/vi/yD1BQRdGDL4/maxresdefault.jpg)](https://youtu.be/yD1BQRdGDL4)

**[▶ Watch the full walkthrough on YouTube](https://youtu.be/yD1BQRdGDL4)**

The walkthrough follows one expedition from its original natural-language request through live graph execution, a typed human decision, the evidence map, the validated journey and checkpoint comparison. It then demonstrates checkpoint replay, branching and deterministic comparison without overwriting the original result.

## Engineering highlights

| Capability | What this project implements |
| --- | --- |
| Stateful orchestration | A typed LangGraph state, explicit reducers, conditional routing and a bounded ToolNode evidence loop |
| Typed human-in-the-loop | Seven interrupt paths for incomplete requests, ambiguous locations or taxa, evidence trade-offs and route decisions |
| Durable execution | SQLite-backed checkpoints with exact thread, branch, execution and checkpoint identities |
| Time travel | Resume, replay, fork, targeted downstream invalidation and deterministic plan comparison |
| Observability | Reconnectable SSE events plus node, model, tool and routing-provider timing spans |
| Reliability | Deterministic evidence and routing authority, grounded model revision and a complete safe fallback |
| Full-stack delivery | FastAPI and Pydantic on the backend; React, TypeScript, generated OpenAPI types and MapLibre in the browser |

The model is deliberately not the final authority. It may parse a request, choose from an allow-listed set of evidence tools and compose an explanation. Deterministic code owns the London boundary, taxonomy acceptance, evidence thresholds, site grounding, entrance eligibility, route constraints, route ranking, provenance and final safety checks.

## Does it actually work?

### Reproducible end-to-end example

The following result was reproduced in `fixture/scripted` mode, which uses versioned data and deterministic scripted model responses:

```text
Request
Plan a two-hour expedition from SW11 4NJ on 15 June 2026
to look for Common woodpigeon.

Result
✓ London location and bird taxonomy resolved
✓ Strong historical-evidence gate passed
✓ 8 evidence-grounded candidate sites retained
✓ Kensington Gardens selected via the mapped Palace Gate entrance
✓ 7.85 km validated return walk · 105 minutes travel
✓ 15 minutes of the two-hour outing remain for field observation
```

The result is not a claim that a bird will be seen. It demonstrates that the workflow can reach a complete plan only after its evidence, grounding, entrance and journey constraints pass.

### Verified implementation

The latest recorded offline verification covers the backend, browser and mobile layout:

| Check | Recorded result |
| --- | ---: |
| Python offline test suite | 212 passed · 10 live tests deselected |
| Frontend unit tests | 36 passed |
| Playwright browser flows | 8 passed |
| Python lint, byte compilation and TypeScript checks | Passed |
| Production frontend build | Passed |

The browser flows include basemap failure, route HITL, checkpoint forking and a 390 × 844 mobile viewport. These are engineering and reproducibility checks, not a scientific evaluation of sighting likelihood.

## Quick Start

The default demonstration needs no model or routing API key. After dependency installation it runs against versioned fixtures, without live biodiversity, geocoding, weather or routing requests.

### Option A: single-container demo

```bash
git clone https://github.com/ttteein-max/london-biodiversity-expedition.git
cd london-biodiversity-expedition
docker compose up --build
```

Open `http://127.0.0.1:8080`, select **Strong evidence**, then choose **Start expedition**.

### Option B: local development

Python 3.10+ and Node.js 20+ are recommended.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

cd frontend
npm ci
cd ..
```

Start the API:

```bash
python -m uvicorn app.biodiversity.api.main:app \
  --host 127.0.0.1 \
  --port 8000
```

In a second terminal, start the browser application:

```bash
cd frontend
npm run dev
```

Open `http://127.0.0.1:5173`. The local application exposes four independently selectable run modes:

| Data | Model | Purpose |
| --- | --- | --- |
| fixture | scripted | Fully reproducible orchestration and UI demonstration |
| live | scripted | Exercise current upstream data while holding model behaviour constant |
| fixture | live | Exercise a configured live model against stable evidence |
| live | live | Run the complete model and data integration |

Live model mode reads `OPENAI_API_KEY`, `OPENAI_MODEL` and optional `OPENAI_BASE_URL` from the backend process. Live routing uses the TfL Journey Planner by default; `TFL_API_KEY` is optional for its public allowance and recommended for deployed quota.

## Why LangGraph for this problem?

The workflow is not a linear prompt chain. A planning request can be incomplete, an everyday bird name can resolve to several accepted taxa, evidence can be too weak for spatial ranking, and a candidate site can fail only after its real entrance and return journey are checked.

| Engineering problem | System mechanism |
| --- | --- |
| A request is incomplete or contradictory | Pause with a typed interrupt and resume the same durable thread after validated input |
| A location or bird name is ambiguous | Present only bounded, safe candidates and require an exact offered choice |
| Evidence collection depends on earlier results | Route through conditional edges and a bounded model-directed ToolNode loop |
| External sources are unavailable or malformed | Preserve typed failure states, bounded retries and explicit safe terminal paths |
| A user wants to explore a different constraint | Fork an immutable checkpoint, invalidate only affected downstream state and compare outcomes |
| A model writes an unsupported claim | Apply deterministic grounding, allow one constrained revision and then use a safe fallback |
| A long agent run needs debugging | Persist every graph step and expose ordered, reconnectable lifecycle events and timings |

This is why the graph, checkpoint and HITL layers are part of the product behaviour rather than framework decoration.

## Architecture and core workflow

<!--
FULL LANGGRAPH DIAGRAM PLACEHOLDER

Replace the figure below, or add a clickable full-topology figure above it, when the
updated Phase 5 diagram from the separate design work is available. The replacement
must reflect the current phase-5.0 workflow, SQLite durability, typed HITL, routing
nodes, replay/fork/compare and provider observability.
-->

[![Current Phase 5 evidence-to-route architecture](docs/diagrams/phase-5-geospatial-routing.svg)](docs/diagrams/phase-5-geospatial-routing.svg)

*Current Phase 5 evidence-to-route authority boundary. A complete compiled-graph diagram will be added in this position.*

One successful run moves through eight conceptual stages:

1. **Request intake** — a structured-output model extracts the location, bird, date, duration and optional constraints.
2. **London location** — deterministic postcode or named-place services validate a generalised planning origin inside Greater London.
3. **Bird taxonomy** — GBIF taxonomy resolution accepts one Aves species or pauses for a safe human choice.
4. **Evidence loop** — the model selects only registered tools; occurrence, weather and public-site results are typed, deduplicated and merged into graph state.
5. **Deterministic validation** — code applies evidence-quality thresholds, privacy rules, site-to-cell grounding and request constraints.
6. **Entrances and journeys** — only directly grounded sites with audited OSM entrances reach the route provider; outbound and return journeys are requested separately.
7. **Plan composition** — the model receives a compact validated bundle and writes the user-facing plan without choosing or recalculating route facts.
8. **Grounding and finalisation** — deterministic checks either accept the draft, request one bounded revision or emit a complete fallback plan.

The browser obtains its topology from the compiled backend graph. It does not maintain a second handwritten version of the workflow.

## Human-in-the-loop, durability and time travel

HITL is used only when a person can resolve a real ambiguity or make an actionable trade-off. It is not inserted after every model response.

### Typed decision points

| Interrupt | Human decision |
| --- | --- |
| Request clarification | Supply a missing or contradictory request field |
| Location correction | Select a verified London place candidate or provide a postcode |
| Taxonomy selection or bird correction | Select one accepted taxon or correct the original bird name |
| Evidence trade-off | Expand the search radius or seasonal window, consider a related taxon, or accept a limited outcome |
| Route trade-off | Accept uncertain entrance evidence, raise a walking limit, or finish without a fabricated route |

Every response is checked against the exact thread, checkpoint, branch, execution, interrupt kind and offered option before the graph resumes. Raw arbitrary state edits are not accepted from the browser.

### Durable run model

```text
Thread      one expedition across its complete history
└── Branch  one immutable constraint trajectory
    └── Execution  one run or replay of that trajectory
        └── Checkpoint  one persisted graph state after a step
```

- **Resume** continues the pending execution after a validated human decision.
- **Replay** creates a new execution from a historical non-terminal checkpoint.
- **Fork** creates a new branch with an allow-listed constraint update and recomputes invalidated evidence.
- **Compare** produces server-calculated differences between two exact checkpoints.

The original final checkpoint is never overwritten by replay or fork operations. Runtime manifests also prevent an old workflow or incompatible data/model/provider profile from being silently resumed under new semantics.

## Observability

The product exposes agent execution as a first-class interface, not as console output added after development.

- Every node, model call, tool call, routing-provider attempt and saved checkpoint emits a sequence-numbered lifecycle event.
- Server-Sent Events stream progress to the browser and replay missed events after reconnect without cancelling the underlying run.
- The UI overlays live and persisted execution state on the actual compiled LangGraph topology.
- Checkpoint inspection uses allow-listed views rather than serialising raw LangGraph state.
- Timing reports separate node wall time from nested model, tool and provider spans, making the slowest stage visible.
- Safe operation reports preserve tool audits, timings, plan facts and route decisions without API keys, raw provider requests, individual occurrence locations or private route geometry.

For the recorded Phase 5 fixture run, the timing report captured 98 events and 33 timed spans across nodes, models, tools and routing-provider calls. The slowest nested work was the public green-space lookup, while all four fixture provider attempts completed in 1.23 ms combined. These measurements are diagnostic samples, not performance guarantees.
