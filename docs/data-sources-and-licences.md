# Data sources, attribution and licences

This page describes the sources used by the London Biodiversity Expedition Planner. Source availability and a data licence do not establish site access, opening, species presence or legal advice.

| Source | Use | Licence / attribution | Runtime mode and limitations |
| --- | --- | --- | --- |
| GBIF Species API | Taxonomy resolution | GBIF API terms; contributing taxonomy sources retain their terms; attribute GBIF.org | Fixture or bounded live lookup; not a London species whitelist |
| GBIF Occurrence Search API | Historical bird evidence | Record and media licences remain item-specific; attribute GBIF.org and contributing datasets | Bounded sample; no abundance, population or sighting probability; occurrence coordinates/IDs are never public |
| OpenStreetMap Greater London boundary | Final London containment | ODbL 1.0; `© OpenStreetMap contributors` | Versioned snapshot |
| Postcodes.io | Postcode planning origin | Contains Royal Mail, Ordnance Survey and ONS data under their stated terms | Fixture or bounded live lookup; an origin is not evidence or a site entrance |
| Nominatim | Named-place planning candidates | ODbL 1.0; `© OpenStreetMap contributors` | Submitted search only, no autocomplete; public service is rate-limited and not a production guarantee |
| OpenStreetMap/Overpass green spaces | Candidate/context polygons | ODbL 1.0; `© OpenStreetMap contributors` | Versioned snapshot only; runtime does not query Overpass |
| OpenStreetMap/Overpass entrances | Audited entrance candidates | ODbL 1.0; `© OpenStreetMap contributors` | Versioned conservative subset; entrance/access tags can be incomplete and do not prove a legal right of access |
| OpenFreeMap Liberty | Default MapLibre basemap style | Attribute OpenFreeMap, OpenMapTiles and `© OpenStreetMap contributors` as supplied by the style | Keyless browser resource; failure falls back to an empty style with overlays retained |
| openrouteservice by HeiGIT | Primary live foot-walking route | Provider terms; underlying OpenStreetMap data under ODbL; attribute openrouteservice/HeiGIT and OSM contributors | Opt-in live data mode; backend `ORS_API_KEY`; routes and elevation require field verification |
| GraphHopper | Optional second live foot-walking route | GraphHopper terms; underlying OpenStreetMap data under ODbL; attribute GraphHopper and OSM contributors | Used only when backend `GRAPHHOPPER_API_KEY` is configured |
| Saved walking-route fixture | Offline deterministic route validation | Source/provenance recorded in `data/fixtures/ors-foot-walking-routes.json`; OSM ODbL applies | Planned public demo origin; compact/simplified geometry and sparse elevation |
| Open-Meteo | Exact-date weather context | CC BY 4.0 for Open-Meteo data; upstream model terms also apply | Weather is context, not evidence of presence or observation probability |

No Google Maps tile, static screenshot or other restricted mapping asset is copied or embedded. API keys remain backend-only and are not attribution substitutes. For reproducibility, each versioned geospatial snapshot has a provenance sidecar with retrieval time, endpoint/query, checksum, generation command and known limitations.
