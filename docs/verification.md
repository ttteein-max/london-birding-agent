# Verification

[Back to the README](../README.md#does-it-actually-work)

Verification covers backend behaviour, browser workflows and responsive layout. Install the Python and frontend dependencies using [Quick Start: local development](../README.md#option-b-local-development) before running the checks below.

| Check | How to reproduce it |
| --- | --- |
| Python offline test suite | From the repository root: `python -m pytest -m "not live" -q` |
| Fixture integrity | From the repository root: `python -m scripts.validate_evidence_fixtures` |
| Generated API and graph contracts | `python -m scripts.export_openapi --check` and `python -m scripts.export_workflow_diagrams --check` |
| Frontend unit tests | From `frontend/`: `npm test` |
| Frontend lint and TypeScript checks | From `frontend/`: `npm run lint` and `npm run typecheck` |
| Production frontend build | From `frontend/`: `npm run build` |
| Playwright browser flows | From `frontend/`: `npx playwright install chromium`, then `npm run test:e2e` with the Python virtual environment active |

The [GitHub Actions workflow](../.github/workflows/offline-tests.yml) runs Python lint, byte compilation, offline tests, fixture validation and generated-contract freshness checks on pushes and pull requests; frontend and browser checks are currently run locally. The [dated verification report](../reports/archive/2026-09-05/verification.md) preserves a historical test and timing snapshot, whose counts may differ from later revisions. See the [report index](../reports/README.md) for the full archive.

The browser flows include basemap failure, route HITL, checkpoint forking and a 390 × 844 mobile viewport. Playwright uses separate temporary databases and local test ports. These are engineering and reproducibility checks, not a scientific evaluation of sighting likelihood.

The [8 September 2026 verification record](../reports/verification/2026-09-08.md) documents the checks run for the documentation and naming change, including file-preservation checks. It is a dated result, not a guarantee about later revisions.

Live integration tests are separately marked and opt-in: `python -m pytest -m live -vv`. They may need provider/model configuration, incur provider usage and skip when prerequisites are absent. A passing fixture test does not establish that a live provider is currently available.
