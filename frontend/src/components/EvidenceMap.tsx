import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { GeoJSONSourceSpecification, StyleSpecification } from "maplibre-gl";
import type {
  MapEvidenceView,
  RouteGeometryView,
  RouteOptionsView,
} from "../api/contracts";
import { humanise } from "../utils";

interface Props {
  data: MapEvidenceView | null;
  loading: boolean;
  error: string | null;
  basemapStyleUrl?: string | null;
  basemapAttributions?: string[];
  routeGeometry?: RouteGeometryView | null;
  routeOptions?: RouteOptionsView | null;
  routeLoading?: boolean;
  onRouteSelect?: (reference: string) => void;
}

const EMPTY_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "paper", type: "background", paint: { "background-color": "#e8e6dc" } }],
};

function collection(features: unknown[]): GeoJSONSourceSpecification["data"] {
  return { type: "FeatureCollection", features } as GeoJSONSourceSpecification["data"];
}

function extendBounds(bounds: maplibregl.LngLatBounds, value: unknown): void {
  if (!Array.isArray(value)) return;
  if (value.length >= 2 && typeof value[0] === "number" && typeof value[1] === "number") {
    bounds.extend([value[0], value[1]]);
    return;
  }
  value.forEach((item) => extendBounds(bounds, item));
}

type Position = [number, number];
type PolygonCoordinates = Position[][];

function asPosition(value: unknown): Position | null {
  if (
    !Array.isArray(value)
    || value.length < 2
    || typeof value[0] !== "number"
    || typeof value[1] !== "number"
  ) return null;
  return [value[0], value[1]];
}

function asRing(value: unknown): Position[] {
  if (!Array.isArray(value)) return [];
  return value.map(asPosition).filter((point): point is Position => point !== null);
}

function asPolygon(value: unknown): PolygonCoordinates {
  if (!Array.isArray(value)) return [];
  return value.map(asRing).filter((ring) => ring.length >= 4);
}

function sitePolygons(type: string, value: unknown): PolygonCoordinates[] {
  if (type === "Polygon") {
    const polygon = asPolygon(value);
    return polygon.length > 0 ? [polygon] : [];
  }
  if (type !== "MultiPolygon" || !Array.isArray(value)) return [];
  return value.map(asPolygon).filter((polygon) => polygon.length > 0);
}

function ringArea(ring: Position[]): number {
  return Math.abs(ring.reduce((total, [x1, y1], index) => {
    const [x2, y2] = ring[(index + 1) % ring.length];
    return total + x1 * y2 - x2 * y1;
  }, 0) / 2);
}

function pointInRing([x, y]: Position, ring: Position[]): boolean {
  let inside = false;
  for (let index = 0, previous = ring.length - 1; index < ring.length; previous = index++) {
    const [x1, y1] = ring[index];
    const [x2, y2] = ring[previous];
    if ((y1 > y) !== (y2 > y) && x < ((x2 - x1) * (y - y1)) / (y2 - y1) + x1) {
      inside = !inside;
    }
  }
  return inside;
}

function pointInPolygon(point: Position, polygon: PolygonCoordinates): boolean {
  return pointInRing(point, polygon[0])
    && !polygon.slice(1).some((hole) => pointInRing(point, hole));
}

function distanceToSegmentSquared(
  [x, y]: Position,
  [x1, y1]: Position,
  [x2, y2]: Position,
): number {
  const dx = x2 - x1;
  const dy = y2 - y1;
  if (dx === 0 && dy === 0) return (x - x1) ** 2 + (y - y1) ** 2;
  const ratio = Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / (dx ** 2 + dy ** 2)));
  return (x - (x1 + ratio * dx)) ** 2 + (y - (y1 + ratio * dy)) ** 2;
}

function distanceToPolygonSquared(point: Position, polygon: PolygonCoordinates): number {
  return Math.min(...polygon.flatMap((ring) => ring.map((vertex, index) => (
    distanceToSegmentSquared(point, vertex, ring[(index + 1) % ring.length])
  ))));
}

function interiorPoint(polygon: PolygonCoordinates): Position | null {
  const outer = polygon[0];
  if (!outer) return null;
  const longitudes = outer.map(([longitude]) => longitude);
  const latitudes = outer.map(([, latitude]) => latitude);
  const west = Math.min(...longitudes);
  const east = Math.max(...longitudes);
  const south = Math.min(...latitudes);
  const north = Math.max(...latitudes);
  let best: Position | null = null;
  let bestDistance = -1;
  const divisions = 20;
  for (let xIndex = 0; xIndex <= divisions; xIndex += 1) {
    for (let yIndex = 0; yIndex <= divisions; yIndex += 1) {
      const point: Position = [
        west + ((east - west) * (xIndex + 0.5)) / (divisions + 1),
        south + ((north - south) * (yIndex + 0.5)) / (divisions + 1),
      ];
      if (!pointInPolygon(point, polygon)) continue;
      const distance = distanceToPolygonSquared(point, polygon);
      if (distance > bestDistance) {
        best = point;
        bestDistance = distance;
      }
    }
  }
  return best;
}

function markerFitsPolygon(
  map: maplibregl.Map,
  polygon: PolygonCoordinates,
  anchor: Position,
): boolean {
  const projected = polygon.map((ring) => ring.map((point) => {
    const pixel = map.project(point);
    return [pixel.x, pixel.y] as Position;
  }));
  const pixelAnchor = map.project(anchor);
  const markerEnvelope: Position[] = [
    [pixelAnchor.x - 13, pixelAnchor.y - 4],
    [pixelAnchor.x + 13, pixelAnchor.y - 4],
    [pixelAnchor.x - 13, pixelAnchor.y - 27],
    [pixelAnchor.x + 13, pixelAnchor.y - 27],
  ];
  return markerEnvelope.every((point) => pointInPolygon(point, projected));
}

export function EvidenceMap({
  data,
  loading,
  error,
  basemapStyleUrl = null,
  basemapAttributions = [],
  routeGeometry = null,
  routeOptions = null,
  routeLoading = false,
  onRouteSelect,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapInstance = useRef<maplibregl.Map | null>(null);
  const [basemapStatus, setBasemapStatus] = useState<"loading" | "ready" | "unavailable">(
    basemapStyleUrl ? "loading" : "unavailable",
  );

  const focusFeature = (coordinates: unknown) => {
    const map = mapInstance.current;
    if (!map) return;
    const bounds = new maplibregl.LngLatBounds();
    extendBounds(bounds, coordinates);
    if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 90, maxZoom: 14, duration: 450 });
  };

  useEffect(() => {
    if (!container.current || !data) return;
    setBasemapStatus(basemapStyleUrl ? "loading" : "unavailable");
    const map = new maplibregl.Map({
      container: container.current,
      style: basemapStyleUrl ?? EMPTY_STYLE,
      center: [-0.13, 51.51],
      zoom: 10.2,
      attributionControl: { compact: true },
    });
    mapInstance.current = map;
    let activePopup: maplibregl.Popup | null = null;
    let usingFallback = !basemapStyleUrl;
    let overlaysAdded = false;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

    const addOverlays = () => {
      if (overlaysAdded) return;
      overlaysAdded = true;
      map.addSource("aggregate-grid", { type: "geojson", data: collection(data.aggregate_grid ?? []) });
      map.addLayer({ id: "aggregate-fill", type: "fill", source: "aggregate-grid", paint: { "fill-color": ["match", ["get", "density_band"], "higher", "#d6573e", "medium", "#dc9b45", "#e5cf84"], "fill-opacity": 0.58 } });
      map.addLayer({ id: "aggregate-outline", type: "line", source: "aggregate-grid", paint: { "line-color": "#63382e", "line-width": 1.2, "line-dasharray": [2, 1] } });
      map.addSource("contextual-sites", { type: "geojson", data: collection(data.contextual_sites ?? []) });
      map.addLayer({ id: "contextual-fill", type: "fill", source: "contextual-sites", paint: { "fill-color": "#809588", "fill-opacity": 0.28 } });
      map.addLayer({ id: "contextual-line", type: "line", source: "contextual-sites", paint: { "line-color": "#52675c", "line-width": 2, "line-dasharray": [3, 2] } });
      map.addSource("candidate-sites", { type: "geojson", data: collection(data.candidate_sites ?? []) });
      map.addLayer({ id: "candidate-fill", type: "fill", source: "candidate-sites", paint: { "fill-color": ["case", ["==", ["get", "site_id"], data.selected_site_id ?? ""], "#0b382f", "#174f43"], "fill-opacity": ["case", ["==", ["get", "site_id"], data.selected_site_id ?? ""], 0.74, 0.52] } });
      map.addLayer({ id: "candidate-line", type: "line", source: "candidate-sites", paint: { "line-color": "#0b382f", "line-width": ["case", ["==", ["get", "site_id"], data.selected_site_id ?? ""], 4, 2.2] } });
      if (data.start_context) {
        map.addSource("start-context", { type: "geojson", data: collection([data.start_context]) });
        map.addLayer({ id: "start-point", type: "circle", source: "start-context", paint: { "circle-radius": 7, "circle-color": "#f7f1df", "circle-stroke-color": "#1c2d29", "circle-stroke-width": 3 } });
      }
      if (routeGeometry?.geojson && Array.isArray(routeGeometry.geojson.features)) {
        map.addSource("walking-route", {
          type: "geojson",
          data: routeGeometry.geojson as GeoJSONSourceSpecification["data"],
        });
        map.addLayer({ id: "walking-route-shadow", type: "line", source: "walking-route", paint: { "line-color": "#f7f1df", "line-width": 7, "line-opacity": 0.86 } });
        map.addLayer({ id: "walking-route-outbound", type: "line", source: "walking-route", filter: ["==", ["get", "direction"], "outbound"], paint: { "line-color": "#315d73", "line-width": 4 } });
        map.addLayer({ id: "walking-route-return", type: "line", source: "walking-route", filter: ["==", ["get", "direction"], "return"], paint: { "line-color": "#d6573e", "line-width": 4, "line-dasharray": [2, 1] } });
      }
      if (data.selected_entrance) {
        map.addSource("selected-entrance", { type: "geojson", data: collection([data.selected_entrance]) });
        map.addLayer({ id: "selected-entrance-point", type: "circle", source: "selected-entrance", paint: { "circle-radius": 8, "circle-color": "#d89c49", "circle-stroke-color": "#1c2d29", "circle-stroke-width": 3 } });
      }
      const bounds = new maplibregl.LngLatBounds();
      [
        ...(data.aggregate_grid ?? []),
        ...(data.candidate_sites ?? []),
        ...(data.contextual_sites ?? []),
        ...(data.start_context ? [data.start_context] : []),
        ...(data.selected_entrance ? [data.selected_entrance] : []),
        ...(
          Array.isArray(routeGeometry?.geojson.features)
            ? routeGeometry.geojson.features as Array<{ geometry?: { coordinates?: unknown } }>
            : []
        ),
      ].forEach((feature) => extendBounds(bounds, feature.geometry?.coordinates));
      if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 48, maxZoom: 13, duration: 0 });

      const showPopup = (
        coordinates: maplibregl.LngLatLike,
        titleText: string,
        detailText: string,
      ) => {
        const popup = document.createElement("div");
        popup.className = "map-popup-copy";
        const title = document.createElement("strong");
        title.textContent = titleText;
        const detail = document.createElement("span");
        detail.textContent = detailText;
        popup.append(title, detail);
        activePopup?.remove();
        activePopup = new maplibregl.Popup({ closeButton: false, offset: 8 })
          .setLngLat(coordinates)
          .setDOMContent(popup)
          .addTo(map);
      };
      const popupFor = (event: maplibregl.MapLayerMouseEvent) => {
        const properties = event.features?.[0]?.properties ?? {};
        let detail = "Contextual site · not a recommendation";
        if (properties.layer === "aggregate_evidence") {
          detail = `${humanise(String(properties.density_band))} density · aggregate historical records`;
        } else if (properties.layer === "candidate") {
          detail = "Evidence-grounded candidate site";
        }
        showPopup(
          event.lngLat,
          String(properties.name ?? properties.label ?? "Mapped evidence"),
          detail,
        );
      };
      ["aggregate-fill", "candidate-fill", "contextual-fill"].forEach((layerId) => {
        map.on("click", layerId, popupFor);
        map.on("mouseenter", layerId, () => { map.getCanvas().style.cursor = "pointer"; });
        map.on("mouseleave", layerId, () => { map.getCanvas().style.cursor = ""; });
      });

      const addSiteMarker = (
        site: NonNullable<MapEvidenceView["candidate_sites"]>[number],
        layer: "candidate" | "contextual",
      ) => {
        const polygon = sitePolygons(site.geometry.type, site.geometry.coordinates)
          .sort((left, right) => ringArea(right[0]) - ringArea(left[0]))[0];
        const coordinates = polygon ? interiorPoint(polygon) : null;
        if (!coordinates) return;
        const markerElement = document.createElement("button");
        markerElement.type = "button";
        markerElement.className = `map-site-marker marker-${layer}`;
        markerElement.title = site.properties.name;
        markerElement.setAttribute(
          "aria-label",
          `Open ${layer} site · ${site.properties.name}`,
        );
        markerElement.addEventListener("click", (event) => {
          event.stopPropagation();
          showPopup(
            coordinates,
            site.properties.name,
            layer === "candidate"
              ? "Evidence-grounded candidate site"
              : "Contextual site · not a recommendation",
          );
        });
        new maplibregl.Marker({ element: markerElement, anchor: "bottom" })
          .setLngLat(coordinates)
          .addTo(map);
        const updateVisibility = () => {
          markerElement.hidden = !markerFitsPolygon(map, polygon, coordinates);
        };
        updateVisibility();
        map.on("moveend", updateVisibility);
      };
      (data.contextual_sites ?? []).forEach((site) => addSiteMarker(site, "contextual"));
      (data.candidate_sites ?? []).forEach((site) => addSiteMarker(site, "candidate"));
    };

    map.on("load", () => {
      if (!usingFallback) setBasemapStatus("ready");
      addOverlays();
    });
    map.on("style.load", () => addOverlays());
    map.on("error", () => {
      if (usingFallback) return;
      usingFallback = true;
      overlaysAdded = false;
      setBasemapStatus("unavailable");
      map.setStyle(EMPTY_STYLE);
    });
    return () => {
      activePopup?.remove();
      mapInstance.current = null;
      map.remove();
    };
  }, [basemapStyleUrl, data, routeGeometry]);

  const feasibleRoutes = (routeOptions?.options ?? []).filter(
    (option) => option.feasible && option.route_geometry_reference,
  );

  return (
    <section className="map-card" aria-labelledby="map-title">
      <div className="map-heading">
        <div><p className="eyebrow">Spatial evidence</p><h2 id="map-title">Evidence map</h2></div>
        <span className={`map-status map-${data?.status ?? "pending"}`}>{humanise(data?.status ?? "pending")}</span>
      </div>
      <div className="map-stage">
        <div ref={container} className="map-canvas" aria-label="Map of aggregate historical evidence and validated site polygons" />
        {loading && <div className="map-message" role="status">Loading map evidence…</div>}
        {error && <div className="map-message error" role="alert">{error}</div>}
        {basemapStatus === "loading" && data && <div className="basemap-notice" role="status">Loading basemap…</div>}
        {basemapStatus === "unavailable" && data && <div className="basemap-notice error" role="status">Basemap unavailable — evidence overlays remain available</div>}
        {routeLoading && <div className="route-loading" role="status">Loading selected walking route…</div>}
        {!loading && !error && !data && <div className="map-message">Start or select a run to load its checkpoint map.</div>}
        {data && !loading && (data.aggregate_grid ?? []).length === 0 && <div className="map-message map-low">No grid shown — the strong evidence gate did not pass.</div>}
        <div className="map-legend" aria-label="Map legend">
          <strong>Legend</strong>
          <div className="legend-block">
            <span className="legend-group">Evidence grid · records per 1 km cell</span>
            <div className="legend-children">
              <span><i className="legend-swatch grid-lower" /> Lower density · 3–9</span>
              <span><i className="legend-swatch grid-medium" /> Medium density · 10–24</span>
              <span><i className="legend-swatch grid-higher" /> Higher density · 25+</span>
            </div>
          </div>
          <div className="legend-block">
            <span className="legend-group">Site locations</span>
            <span><i className="legend-swatch candidate" /><i className="legend-mini-pin candidate" /> Evidence-grounded candidate</span>
            <span><i className="legend-swatch contextual" /><i className="legend-mini-pin contextual" /> Contextual site — not recommended</span>
            <span><i className="legend-swatch start" /> Generalised start</span>
            <span><i className="legend-swatch entrance" /> Verified public-site entrance</span>
            <span><i className="legend-swatch route-outbound" /> Outbound walking route</span>
            <span><i className="legend-swatch route-return" /> Return walking route</span>
          </div>
          <small>Density is relative record count, not abundance.</small>
        </div>
      </div>
      {data && (
        <div className="map-ledger">
          <p>{data.grid_note}</p>
          <div className="map-site-ledger" aria-label="Mapped sites as text">
            {(data.candidate_sites ?? []).map((site) => <button type="button" className="candidate-site" key={site.properties.site_id} onClick={() => focusFeature(site.geometry.coordinates)}><strong>Candidate</strong> {site.properties.name}</button>)}
            {(data.contextual_sites ?? []).map((site) => <button type="button" className="contextual-site" key={site.properties.site_id} onClick={() => focusFeature(site.geometry.coordinates)}><strong>Context</strong> {site.properties.name}</button>)}
          </div>
          {data.selected_entrance && (
            <p className="route-text-alternative">
              <strong>Selected entrance:</strong> {data.selected_entrance.properties.label}. Route status: {humanise(data.route_status ?? "not requested")}.
            </p>
          )}
          {feasibleRoutes.length > 1 && (
            <div className="route-switcher" aria-label="Alternative feasible walking routes">
              <strong>Show route</strong>
              {feasibleRoutes.map((option) => (
                <button
                  type="button"
                  key={option.option_id}
                  aria-pressed={routeGeometry?.route_geometry_reference === option.route_geometry_reference}
                  onClick={() => option.route_geometry_reference && onRouteSelect?.(option.route_geometry_reference)}
                >
                  {option.site_name} · {option.total_distance_km?.toFixed(2)} km
                </button>
              ))}
            </div>
          )}
          <div className="attribution-row">
            {[...basemapAttributions, ...data.attributions]
              .filter((item, index, items) => items.indexOf(item) === index)
              .map((item) => <span key={item}>{item}</span>)}
          </div>
        </div>
      )}
    </section>
  );
}
