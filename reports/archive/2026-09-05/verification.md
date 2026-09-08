# Phase 5 verification report

> Historical result from an earlier revision. It is retained for traceability, not presented as current verification. See the [report index](../../README.md).

Verified locally on 2026-09-05 (Asia/Shanghai) from `phase-5-geospatial-routing` before commit. All default checks were credential-free and offline; Playwright fulfilled the OpenFreeMap style request locally and blocked all other public tile requests.

## Results

| Check | Result |
| --- | --- |
| Default Python suite | 202 passed, 9 live tests deselected; 1 third-party AnyIO deprecation warning |
| Phase 5 opt-in live routing suite | 2 skipped: no `ORS_API_KEY`; live/live also lacked required OpenAI credentials |
| Ruff | `ruff check .` passed |
| Python byte compilation | `compileall` passed |
| Frontend ESLint | passed |
| TypeScript project typecheck | passed |
| Vitest | 30 passed in 4 files |
| Vite production build | passed; existing bundle-size advisory only |
| Playwright | 8 passed, including basemap failure, route HITL, checkpoint fork and 390×844 mobile layout |
| Git whitespace validation | `git diff --check` passed |

The browser was also inspected directly at desktop size and 390×844. The route and entrance overlays, full attribution, itinerary metrics, constraint text, elevation chart, provider provenance, warnings and footer disclaimer remained readable. The mobile document had no horizontal overflow.

## Offline fixture timing sample

The sample used the planned non-private SW11 fixture origin, a fresh route cache and `fixture/scripted` mode. Exact geometry stayed in a separate temporary route runtime; the report artifacts contained only redacted route references and facts.

| Metric | Result |
| --- | ---: |
| Total run | 3,659.73 ms |
| Events / timed spans | 98 / 33 |
| Timed span coverage | 20 node, 6 model, 3 tool, 4 provider |
| Slowest node | `evidence_tools`, 3,555.83 ms |
| Slowest nested work | `find_public_green_spaces`, 3,555.20 ms |
| `request_walking_routes` node | 3.88 ms |
| All four provider attempts | 1.23 ms total |
| Cache | 0 hits / 4 misses (fresh-cache verification) |

Provider calls were measured independently: option 06 outbound 0.56 ms, option 06 return 0.18 ms, option 08 outbound 0.21 ms and option 08 return 0.28 ms. Scripted model spans included the request parser, all four evidence-agent turns and the final plan composer; the parser was the slowest model span at 4.12 ms.

## Live status and limitations

The current HeiGIT foot-walking test is present but skipped without `ORS_API_KEY`; it does not require an OpenAI key. The live/live milestone is present but skipped without both routing and OpenAI credentials, so no new live sample was saved.

The committed entrance snapshot is a small audited London demo subset because the attempted full public Overpass response terminated before completion. Entrance and access tags can be incomplete or stale, provider routes and closures can change, and elevation is not survey-grade. These limitations are displayed to the user. Phase 5 makes no rarity, conservation-designation or legal conclusion.
