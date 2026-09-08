# Recorded runs and verification

[Project overview](../README.md) · [Current verification commands](../docs/verification.md)

These records are evidence from specific revisions and dates, not a continuously updated benchmark. Historical model/provider settings and test counts can differ from the current application. The current live walkthrough is linked from the [README demo](../README.md#demo).

| Record | Scope |
| --- | --- |
| [2026-09-08 verification](verification/2026-09-08.md) | Documentation/naming change: 212 backend, 36 frontend and eight browser tests; contract and archive checks |
| [2026-09-01 live run](live-runs/2026-09-01-gemini-3.7-flash/) | Safe plan, model metadata and tool audit from an earlier live run |
| [2026-09-02 live run](live-runs/2026-09-02-live-live/) | Recorded live data/model execution, events and timings |
| [Archived HITL demonstration](archive/phase-3-hitl-time-travel/README.md) | Earlier fixture/scripted taxonomy, evidence trade-off, replay, fork and comparison |
| [2026-09-05 verification](archive/2026-09-05/verification.md) | Dated backend/frontend/browser results and an offline routing timing sample |
| [2026-08-31 verification](archive/2026-08-31/verification.md) | Early deterministic backend checks and the matching [JUnit XML](archive/2026-08-31/pytest-offline.xml) |

Original JSON, trace, image and XML artifacts are retained unchanged when relocated. Archived prose retains historical claims; only navigation and archive notices are updated.

New local operation reports and generated HITL examples belong under the gitignored `reports/runs/` directory. Regeneration does not overwrite these archived examples.
