# Standalone repository verification report

Generated: 2026-08-31 (Asia/Shanghai)

Scope: Phase 0 through Phase 1.2 biodiversity code plus standalone repository metadata. Live public-API checks are intentionally excluded from the default offline run.

## Offline test suite

Command:

```bash
python -m pytest -m 'not live' --junitxml=reports/pytest-offline.xml -q
```

Result: **64 passed, 3 deselected in 17.45s**.

The three deselected tests are explicitly opt-in live checks. The JUnit XML result is saved as `reports/pytest-offline.xml`.

## Python compile check

Command:

```bash
python -m compileall -q app scripts tests
```

Result: **passed**.

## Phase 0.1 feasibility validation

Command:

```bash
python -m scripts.phase0_feasibility
```

Result: **passed** with the deterministic canonical snapshots.

Outcome summary:

```json
{
  "taxonomy_outcomes": {
    "resolved": 9,
    "human_selection_required": 2,
    "taxon_not_found": 1
  },
  "evidence_outcomes": {
    "strong_map_evidence": 4,
    "limited_contextual_evidence": 2,
    "insufficient_evidence": 3,
    "human_selection_required": 2,
    "taxon_not_found": 1
  }
}
```

## Repository boundary

The standalone suite intentionally omits the unrelated incident-agent tests and the former protected-path diff test. The published repository contains the biodiversity feasibility layer, deterministic backend, fixtures, generators, documentation and their own tests only.
