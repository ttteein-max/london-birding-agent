# ADR 0005: Phase 5 workflow-version compatibility

- Status: accepted
- Date: 2026-09-05
- Decision owners: LangGraph run management

## Context

Phase 5 adds route nodes, route state and provider configuration. Resuming an older checkpoint into that graph, or resuming under another provider/profile, could reuse missing or stale deterministic facts.

## Decision

Phase 5 uses workflow `phase-5.0`, state schema `3` and route schema `2`. Every new `RunManifest` records data/model mode, model and endpoint fingerprints, routing provider and version, routing profile, route schema and routing endpoint fingerprint. Resume, replay and fork validate exact thread, checkpoint, branch and execution identity plus current runtime compatibility. Route-schema-1 executions remain readable through a compatibility view, but they are not silently re-ranked or resumed under schema 2.

Route-affecting decisions clear prior options and validated walking evidence before returning to the deterministic route nodes. Phase 4 manifests using `phase-3.1` and state schema `2` remain parseable for history and safe read-only views. They cannot be mutated in the Phase 5 graph and return an explicit instruction to create a new Phase 5 run. No silent migration is performed.

## Consequences

Historical runs remain inspectable while route evidence cannot cross workflow, provider, profile or schema boundaries. A future incompatible graph must make another explicit version decision rather than widening compatibility implicitly.
