# Human-in-the-loop and time travel

[Documentation](README.md) · [LangGraph architecture](langgraph-architecture.md)

Human input is requested when it can resolve ambiguity or change a constrained outcome. Each pause is a typed LangGraph interrupt, backed by a saved checkpoint, rather than a free-form edit to the agent's state.

## Typed decisions

| Decision | Accepted response |
| --- | --- |
| Request clarification | Missing or contradictory request fields |
| Location correction | A verified offered place candidate or a corrected postcode |
| Taxonomy selection | One offered accepted taxon |
| Bird input correction | A corrected bird name |
| Evidence trade-off | An offered radius, season, related-taxon or limited-outcome choice |
| Related taxon selection | One validated related taxon |
| Route trade-off | An offered access or walking-limit change, or a no-route outcome |

The server checks the payload, interrupt kind, offered option and exact thread/checkpoint/branch/execution identity before resuming. Stale decisions and arbitrary state patches are rejected. Changes invalidate only the affected downstream evidence, including previously validated route options when routing inputs change.

## Identity and persistence

A thread contains branches; a branch contains executions; each execution has checkpoint history. These identities are separate:

- A **thread** is the expedition across its history.
- A **branch** is a constraint trajectory.
- An **execution** is one run or replay of that trajectory.
- A **checkpoint ID** identifies a persisted state. A step number is an ordering label, not a globally unique checkpoint identity.

The runtime uses SQLite checkpoints, with an in-memory saver for tests. The application catalog separately stores operation and lineage metadata. Safe checkpoint views are allow-listed projections, not raw state dumps. Serialisation does not fall back to unrestricted pickle.

## Resume, replay, fork and compare

| Operation | Behaviour |
| --- | --- |
| Resume | Continues the pending execution after a validated human decision |
| Replay | Restores a historical non-terminal checkpoint and creates a new execution on the same branch |
| Fork | Creates a new branch with an allow-listed constraint change and recomputes invalidated work |
| Compare | Calculates differences in selected plan and constraint fields between two exact checkpoints |

Replay and fork do not overwrite the original final checkpoint. Shared history can appear in more than one execution, so comparisons should use each execution's exact final checkpoint when reviewing completed outcomes. An `Unchanged` comparison means the compared fields match; it is not a claim of full-state equality, identical model drafts or identical execution metadata.

Runtime manifests record workflow, data, model and routing-profile compatibility. Incompatible historical runs remain readable where supported but cannot silently resume under new semantics. The persisted `phase-5.0` and legacy `phase-3.1` strings remain unchanged for this reason; see [ADR 0005](adr/0005-workflow-version-compatibility.md).

## Inspect and reproduce

The browser exposes history, checkpoint state, replay, fork and comparison through the [operation API](frontend-and-api.md). The command-line interface is available through `python -m scripts.manage_biodiversity_runs --help`.

For a credential-free HITL, replay and fork demonstration, with the backend dependencies installed:

```bash
python -m scripts.generate_hitl_demo
python -m pytest tests/test_phase3_demo.py -q
```

New generated artifacts go to the gitignored `reports/runs/fixture-scripted-hitl-time-travel/` directory. The [archived demonstration](../reports/archive/phase-3-hitl-time-travel/README.md) preserves the earlier recorded result and is not overwritten by this command.

Implementation: [run management](../app/biodiversity/runs.py), [run models](../app/biodiversity/run_models.py), [state and reducers](../app/biodiversity/graph/state.py) and [checkpoint lifecycle](../app/biodiversity/graph/checkpoint.py).
