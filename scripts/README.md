# Maintenance and verification commands

[Documentation](../docs/README.md)

Run these modules from the repository root with the backend dependencies installed. Data refresh and promotion are explicit maintenance operations; they are not part of normal agent execution.

| Command | Purpose |
| --- | --- |
| `python -m scripts.validate_evidence_fixtures` | Validate canonical offline evidence fixtures and provenance |
| `python -m scripts.generate_safe_map_candidate --help` | Prepare a candidate occurrence snapshot for review |
| `python -m scripts.generate_osm_snapshot --help` | Maintain the public-green-space snapshot |
| `python -m scripts.generate_entrance_snapshot --help` | Maintain the audited entrance snapshot |
| `python -m scripts.generate_london_boundary --help` | Maintain the London boundary fixture |
| `python -m scripts.run_expedition_backend --help` | Run the deterministic evidence backend |
| `python -m scripts.run_biodiversity_agent --help` | Run the complete agent workflow |
| `python -m scripts.manage_biodiversity_runs --help` | Inspect history and perform explicit run operations |
| `python -m scripts.generate_hitl_demo` | Generate a credential-free HITL/time-travel example in `reports/runs/` |
| `python -m scripts.export_openapi` | Export the current API contract; `--check` verifies it without writing |
| `python -m scripts.export_workflow_diagrams` | Export the compiled topology without executing an agent; `--check` verifies it |

After exporting OpenAPI, run `npm run api:types --prefix frontend`. To render the current LangGraph PNG/SVG assets, install the isolated renderer dependencies with `npm ci --prefix scripts/diagrams`, then run `npm run build --prefix scripts/diagrams`.

Earlier phase-named commands in the archive refer to their historical revisions. Current scripts use the functional entry points above; internal package names and persisted identifiers are unchanged.
