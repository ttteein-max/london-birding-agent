# Deployment and runtime profiles

[Documentation](README.md) · [Quick Start](../README.md#quick-start)

London Birding Agent supports local development with optional live services and a restricted public fixture demo. The checked-in deployment configurations use the latter; they do not enable visitor-funded or author-funded live model calls.

## Local development

The [Quick Start](../README.md#option-b-local-development) runs FastAPI and Vite separately. The browser sends the selected data/model mode with each new run; the backend validates it against `BIODIVERSITY_ALLOWED_RUN_MODES` before queuing work.

Local development supports `fixture/scripted`, `live/scripted`, `fixture/live` and `live/live`. Live model credentials belong only in the backend environment. Use the explicit `--env-file .env` launch option when reading a local environment file; never bundle credentials in frontend or `VITE_*` settings.

## Public fixture profile

`BIODIVERSITY_PUBLIC_DEMO=true` requires exactly `fixture/scripted` in the allowed modes. Other modes receive `403 mode_not_allowed`. Interactive API documentation and the runtime OpenAPI endpoint are disabled in this profile. The UI shows the single permitted mode explicitly.

The fixture workflow needs no model or routing credentials. Downloading dependencies and displaying the default basemap still require internet access. To remove the basemap dependency, set `BIODIVERSITY_BASEMAP_STYLE_URL=disabled`.

Public geometry views are restricted to the planned non-private fixture origin. The profile does not provide authentication or multi-tenant isolation for arbitrary private live runs.

## Single-container deployment

The [Dockerfile](../Dockerfile) builds the React bundle in a Node stage and serves it alongside FastAPI in the Python runtime. HTML, JSON APIs and SSE share one origin; the static application fallback is mounted after the API routes.

From the repository root:

```bash
docker compose up --build
```

Open [the local demo](http://127.0.0.1:8080). The [Compose configuration](../compose.yaml) mounts the named `biodiversity-demo-data` volume at `/var/lib/biodiversity` for the catalog, checkpoints, route runtime and safe reports. Existing storage names are deliberately retained through the project rename.

Use `docker compose down` to stop the service. Adding `-v` deletes the volume and run history, so do that only when deletion is intended. A renamed clone directory may use a different Compose project prefix; keep the original Compose project/volume when retaining existing history.

## Resource limits and persistence

| Control | Checked-in container setting |
| --- | ---: |
| Concurrent operations | 2 |
| Mutations per minute | 20 |
| Stored demo threads | 100 |
| Runtime storage budget | 192 MB |
| Inactive-run retention | 24 hours |

Startup and periodic cleanup remove expired application records, corresponding LangGraph threads through the public checkpointer lifecycle, and safe report directories. These controls are process-local and the container runs one worker. SQLite and local storage are appropriate for this bounded demonstration, not a distributed service design.

The [Render Blueprint](../render.yaml) defines a Docker service with `/api/v1/health` as its health check. It does not configure a persistent disk. Treat hosted history as disposable unless durable storage is explicitly provisioned; local Compose volume persistence is separate. The Blueprint's display name uses London Birding Agent, but renaming the repository does not itself migrate or redeploy an existing hosted service.

Do not configure model credentials on the public fixture service. Public live modes remain rejected even if a key is accidentally present.

## Publication checks

Check that health advertises only `fixture/scripted`, `/docs` is unavailable, a fixture run streams ordered events, and a handcrafted `live/live` request is rejected. The [automated verification](verification.md) includes public-mode policy, resource controls, safe views and same-origin serving. No deployment or data deletion is required to run the local test suite.
