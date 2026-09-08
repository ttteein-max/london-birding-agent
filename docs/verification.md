# Verification

[Back to the README](../README.md#does-it-actually-work)

Verification covers backend behaviour, browser workflows and responsive layout. Install the Python and frontend dependencies using [Quick Start: local development](../README.md#option-b-local-development) before running the checks below.

| Check | How to reproduce it |
| --- | --- |
| Python offline test suite | From the repository root: `python -m pytest -m "not live" -q` |
| Frontend unit tests | From `frontend/`: `npm test` |
| Frontend lint and TypeScript checks | From `frontend/`: `npm run lint` and `npm run typecheck` |
| Production frontend build | From `frontend/`: `npm run build` |
| Playwright browser flows | From `frontend/`: `npx playwright install chromium`, then `npm run test:e2e` with the Python virtual environment active |

The [GitHub Actions workflow](../.github/workflows/offline-tests.yml) runs Python lint, byte compilation, offline tests and feasibility validation on pushes and pull requests; frontend and browser checks are currently run locally. The [dated Phase 5 verification report](../reports/phase5-verification.md) preserves a historical test and timing snapshot, whose counts may differ from later revisions.

The browser flows include basemap failure, route HITL, checkpoint forking and a 390 × 844 mobile viewport. Playwright uses separate temporary databases and local test ports. These are engineering and reproducibility checks, not a scientific evaluation of sighting likelihood.
