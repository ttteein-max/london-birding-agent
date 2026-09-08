# Phase 3 fixture/scripted HITL + Time Travel demo

> Historical result from an earlier revision. It is retained for traceability, not presented as current verification. See the [report index](../../README.md).

This offline, reproducible demonstration complements the live/live API-integration
sample. It uses fixture data and scripted models so the Phase 3 control flow can be
reviewed without credentials, network access, or upstream variability.

## Flow exercised

1. `start` pauses at `taxon_selection` for the ambiguous input `robin`.
2. The saved candidate key is resumed; the run then pauses at the low-evidence
   `actionable_tradeoff`.
3. The user choice `keep_constraints_accept_low_confidence` is resumed and creates
   the original final plan.
4. A checkpoint immediately before composition is replayed into a distinct
   `execution_id` without replacing the original branch result.
5. The original final checkpoint is forked with a wider public-site search radius.
6. The original and fork final checkpoints are compared with `PlanComparison`.

## Files

- `demo-summary.json`: short operation-by-operation guide and coverage checks.
- `events.json`: ordered live-channel-compatible events, including every checkpoint.
- `timings.json`: node/model/tool timing spans.
- `history.json`: application-owned checkpoint summaries, not raw snapshots.
- `state-views.json`: allow-listed, coordinate-free `StateView` objects for every
  checkpoint.
- `branches.json` and `executions.json`: stable branch/execution identities.
- `comparison.json`: deterministic original-versus-fork comparison.
- `original-final-plan.json` and `fork-final-plan.json`: the two safe plan outputs.

The demonstration intentionally does not save raw LangGraph state, prompts, model
output, occurrence coordinates or identifiers, HMAC values, or safe-cell
associations.

Regenerate and validate it with:

```bash
python -m scripts.generate_phase3_demo
python -m pytest tests/test_phase3_demo.py -q
```
