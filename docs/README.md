# Documentation

[Project overview and Quick Start](../README.md)

London Birding Agent is a London birdwatching application built to demonstrate stateful agent engineering. Start with the architecture, then follow the areas relevant to your review.

## Current system

| Guide | What it explains |
| --- | --- |
| [LangGraph architecture](langgraph-architecture.md) | The complete compiled topology, seven human decisions and supporting runtime services |
| [Evidence and grounding](evidence-and-grounding.md) | Taxonomy, evidence quality, privacy-safe maps and deterministic planning gates |
| [HITL and time travel](hitl-and-time-travel.md) | Typed interrupts, checkpoint identities, resume, replay, fork and comparison |
| [Routing](routing.md) | Audited entrances, live TfL journeys, fixture walking routes and deterministic constraints |
| [Frontend and API](frontend-and-api.md) | Browser views, asynchronous operations, reconnectable events and generated contracts |
| [Deployment](deployment.md) | Local and public profiles, persistence, credentials and resource limits |
| [Verification](verification.md) | Reproducible checks, CI coverage and dated results |

## Reference material

- [Data sources and licences](data-sources-and-licences.md)
- [Fixture and provenance schema](fixture-schema.md)
- [Current OpenAPI contract](api/openapi.json)
- [Current diagrams](diagrams/README.md)
- [Architecture decision records](adr/README.md)
- [Recorded runs and verification reports](../reports/README.md)

## Repository layout

| Directory | Responsibility |
| --- | --- |
| `app/` | Python domain logic, LangGraph workflow, run management and FastAPI |
| `frontend/` | React application, generated API types and browser tests |
| `data/` | Versioned fixtures, provenance and spatial snapshots; runtime data is gitignored |
| `docs/` | Current guides, contracts, diagrams and architectural decisions |
| `reports/` | Dated verification and safe execution evidence, not a current benchmark |
| `scripts/` | Data maintenance, graph/API exports and reproducible demonstrations |
| `tests/` | Backend verification, including separately marked live tests |
| `.github/workflows/` | Automated offline checks |

Root configuration files remain beside the application so standard tools can find them. The existing `app.biodiversity` package, `BIODIVERSITY_*` settings, storage names and persisted workflow identifiers are retained for compatibility; the public project name is London Birding Agent.

[Historical development archive](archive/README.md) preserves the earlier phase documents and superseded diagrams. Those materials describe their recorded revisions, not the current product contract.
