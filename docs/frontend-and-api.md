# Frontend and API

[Documentation](README.md) · [Current OpenAPI contract](api/openapi.json)

The browser combines an evidence map, plan, execution trace, human decisions and time travel. React renders safe server-calculated views; it does not independently resolve taxonomy, rank routes or reconstruct agent state.

## Application boundary

FastAPI delegates graph control to the run manager. Starting, resuming, replaying or forking returns `202 Accepted` with an operation identity. Graph work runs off the request loop, with mutations serialised per thread and bounded concurrency across independent threads.

The application catalog tracks operations separately from LangGraph's checkpoint store. Both use SQLite. On service restart, unfinished queued/running operations are marked failed rather than presented as still running; completed history and pending human decisions remain inspectable.

## API surface

All application endpoints are under `/api/v1`. The [generated contract](api/openapi.json) defines exact request, response and error schemas.

| Area | Endpoints |
| --- | --- |
| Runtime | `GET /health`, `GET /workflow/topology` |
| Runs | `POST /runs`, `GET /runs`, `GET /runs/{thread_id}` |
| History | `GET /runs/{thread_id}/history` |
| Checkpoint views | `GET /runs/{thread_id}/checkpoints/{checkpoint_id}/{state,evidence,plan,map,routes}` |
| Route geometry | `GET /runs/{thread_id}/checkpoints/{checkpoint_id}/route-geometry/{route_geometry_reference}` |
| Mutations | `POST /runs/{thread_id}/{resume,replay,fork}` |
| Comparison | `GET /runs/{thread_id}/compare` |
| Operations | `GET /operations/{operation_id}` and its `/events` stream |

State inspection validates the checkpoint, node and step selection. Resume, replay and fork also validate lineage and compatibility. Error responses distinguish missing resources, stale/conflicting operations, invalid payloads and unavailable services without exposing raw internal exceptions.

## Reconnectable events and safe views

SSE events carry monotonically increasing sequence IDs within an operation. Reconnect uses `Last-Event-ID` or `after_sequence`; the server replays missed events and the browser deduplicates them. Closing the stream does not cancel the underlying operation. Persisted safe reports support inspection after refresh or restart.

The trace uses the actual compiled graph supplied by `/workflow/topology`. Checkpoint views and events are allow-listed projections: raw prompts, unrestricted state, individual occurrence coordinates and private route geometry are not generic inspection payloads. Timing views distinguish outer node time from nested model, tool and provider spans; those durations should not be added as independent work.

## Contract generation

There is one current contract and one exporter. With backend and frontend dependencies installed, run from the repository root:

```bash
python -m scripts.export_openapi
npm run api:types --prefix frontend
python -m scripts.export_openapi --check
```

The exporter reads the current FastAPI application without starting a server or executing an agent. `frontend/src/api/schema.d.ts` is generated; handwritten contracts alias its DTOs. Historical OpenAPI snapshots in the archive are not inputs to the frontend build.

See [verification](verification.md) for unit and browser checks, and [deployment](deployment.md) for same-origin static serving, public-mode restrictions and persistence.
