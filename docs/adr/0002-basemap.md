# ADR 0002: Basemap delivery

- Status: accepted
- Date: 2026-09-05
- Decision owners: biodiversity API and browser UI

## Context

The evidence map needs geographic context without changing MapLibre, embedding restricted map assets or putting a secret-bearing style URL into the browser bundle. Tests must remain offline.

## Decision

The backend publishes one validated browser-safe style URL. The keyless default is OpenFreeMap Liberty at `https://tiles.openfreemap.org/styles/liberty`; `BIODIVERSITY_BASEMAP_STYLE_URL=disabled` selects the local empty style. Query parameters whose names indicate a token or API key are rejected. A custom style's own HTTPS origin is added to Content Security Policy automatically, while additional tile, sprite and glyph origins must be supplied as HTTPS origins in `BIODIVERSITY_BASEMAP_RESOURCE_ORIGINS`. Raw CSP fragments are rejected.

MapLibre attribution control remains enabled and the page also displays OpenFreeMap, OpenMapTiles and OpenStreetMap attribution. Evidence and route GeoJSON are separate sources and layers above the style. A style error swaps to `EMPTY_STYLE`, re-adds overlays and displays “Basemap unavailable — evidence overlays remain available”. Default unit and browser tests use mocked styles and block the public tile host.

## Consequences

The default demo has a real keyless basemap when network access is available and a useful overlay-only state when it is not. Operators of a custom style must enumerate its resource origins. The application does not copy Google Maps tiles, screenshots or other restricted resources.
