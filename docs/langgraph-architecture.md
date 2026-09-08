# LangGraph architecture

[Back to the README overview](../README.md#architecture-and-core-workflow) · [Open full-size topology PNG](diagrams/langgraph-topology.png?raw=true) · [Topology SVG](diagrams/langgraph-topology.svg?raw=true)

The README introduces the workflow as grouped stages. This diagram expands those stages into the current `phase-5.0` graph: **29 workflow nodes, START/END, 50 edges and seven typed HITL nodes**. Its node and edge set is exported from the same compiled graph used by the application; the layout is curated for a consistent top-to-bottom reading order.

## Complete compiled topology

[![Complete LangGraph topology with all current request, evidence, HITL, journey and planning branches](diagrams/langgraph-topology.png)](diagrams/langgraph-topology.png?raw=true)

**Click the diagram or [Open full-size topology PNG](diagrams/langgraph-topology.png?raw=true) to view the original 3000 × 6060 image.** The link opens image content directly, outside GitHub's file viewer. If the browser initially fits the whole image into the window, click it to inspect it at its original size, then scroll. The SVG is available for vector viewing or download.

## How to read it

- Follow the central column from request parsing through context resolution, evidence validation, journeys, plan composition and final checks.
- Blue nodes contain model calls or the evidence `ToolNode`. White nodes contain deterministic code or explicit outcomes. Amber nodes pause for human input.
- Solid arrows are unconditional graph edges; dashed arrows are conditional routes. Crossing lines do not create a junction or an extra graph node.
- Read the short English title first. The exact Python node identifier appears below it and matches the [workflow definition](../app/biodiversity/graph/workflow.py).
- Follow side branches for corrections, evidence trade-offs, route choices and model revision. Source failure, evidence-loop limits and deterministic validation can also end the run early.

The routes are declared by the compiled graph; their guards live in [the routing functions](../app/biodiversity/graph/routing.py). The JSON export preserves conditional status and any explicit route labels. In particular, dashed edges to `END` from validation and final checks are the `terminal` and `complete` routes respectively.

## What the overview groups together

| README stage | Expanded implementation |
| --- | --- |
| Parse request & resolve context | Structured request parsing; request clarification; named-place geocoding; London validation; taxonomy resolution, previews and corrections |
| Bounded evidence loop | `evidence_agent` → `evidence_tools` → `record_structured_evidence` → `evidence_agent`, with an explicit loop-limit exit |
| Validate evidence & site grounding | Deterministic evidence gates; typed trade-offs; validated choices; related-taxon selection and affected-evidence refresh |
| Validate & rank journeys | Public-site entrances; bounded provider requests; route constraints; deterministic ranking; route HITL and validated route choices |
| Compose expedition plan | Model composition from the validated evidence and journey bundle |
| Check grounding & finalise | Deterministic checks; at most one model revision; a complete deterministic fallback when required |

`request_walking_routes` retains its existing Python identifier. Its current user-facing label is **Request journeys**: live routing defaults to TfL public transport plus walking, while the reproducible fixture uses walking routes. The diagram does not imply that every live journey is walking-only.

The seven human interrupt nodes are:

1. `request_clarification_interrupt`
2. `location_correction_interrupt`
3. `taxon_selection_interrupt`
4. `bird_input_correction_interrupt`
5. `actionable_tradeoff_interrupt`
6. `related_taxon_selection_interrupt`
7. `route_tradeoff_interrupt`

Each decision resumes only after its payload and exact run/checkpoint identity have been validated. The overview groups these seven nodes into request/identity, evidence and journey decision areas.

## Runtime support

These services support the workflow throughout execution. They are shown beneath the README overview and are not additional nodes in the compiled topology.

| Capability | Implementation and role |
| --- | --- |
| Typed state and reducers | [BiodiversityAgentState](../app/biodiversity/graph/state.py) holds request, evidence, plan and execution lineage; reducers control how updates merge |
| Durable checkpoints | [SQLite checkpoint lifecycle](../app/biodiversity/graph/checkpoint.py) persists graph state; tests can use an in-memory saver |
| Resume, replay, fork and compare | [Run management](../app/biodiversity/runs.py) validates identity and compatibility, preserves recorded history and applies targeted invalidation |
| Observability | [Lifecycle events and timing spans](../app/biodiversity/observability.py) cover nodes, models, tools, providers and checkpoints; the API streams ordered events and replays missed events on reconnect |

The browser also reads its topology from [the compiled-graph presentation service](../app/biodiversity/api/services/topology.py). For more detail, see [durable HITL and time travel](phase-3-hitl-time-travel.md) and [geospatial routing](phase-5-geospatial-routing.md).

## Reproduce the diagrams

From the repository root, with the Python environment installed and activated:

```bash
python -m scripts.export_workflow_diagrams
npm ci --prefix scripts/diagrams
npm run build --prefix scripts/diagrams
python -m scripts.export_workflow_diagrams --check
```

Exporting compiles fixture/scripted dependencies without executing an expedition, creating a checkpoint database or calling external services. Installing the diagram-only Node dependencies requires package access; rendering then runs locally. These dependencies are separate from the application frontend.

The exporter writes [langgraph-topology.json](diagrams/langgraph-topology.json). The renderer produces an SVG and a high-resolution PNG for each diagram. It verifies that every compiled node and edge has a layout entry and rejects stale or missing entries; changes to graph structure therefore require an explicit layout update.

- [Python exporter](../scripts/export_workflow_diagrams.py)
- [Diagram renderer](../scripts/diagrams/render.mjs)
- [Overview PNG](diagrams/langgraph-overview.png?raw=true) and [SVG](diagrams/langgraph-overview.svg?raw=true)
- [Complete topology PNG](diagrams/langgraph-topology.png?raw=true) and [SVG](diagrams/langgraph-topology.svg?raw=true)
