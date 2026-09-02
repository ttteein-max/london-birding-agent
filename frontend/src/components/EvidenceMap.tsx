import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import type { GeoJSONSourceSpecification, StyleSpecification } from "maplibre-gl";
import type { MapEvidenceView } from "../api/contracts";
import { humanise } from "../utils";

interface Props { data: MapEvidenceView | null; loading: boolean; error: string | null }

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

export function EvidenceMap({ data, loading, error }: Props) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!container.current || !data) return;
    const style = import.meta.env.VITE_MAP_STYLE_URL || EMPTY_STYLE;
    const map = new maplibregl.Map({
      container: container.current,
      style,
      center: [-0.13, 51.51],
      zoom: 10.2,
      attributionControl: false,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.on("load", () => {
      map.addSource("aggregate-grid", { type: "geojson", data: collection(data.aggregate_grid ?? []) });
      map.addLayer({ id: "aggregate-fill", type: "fill", source: "aggregate-grid", paint: { "fill-color": ["match", ["get", "density_band"], "higher", "#d6573e", "medium", "#dc9b45", "#e5cf84"], "fill-opacity": 0.58 } });
      map.addLayer({ id: "aggregate-outline", type: "line", source: "aggregate-grid", paint: { "line-color": "#63382e", "line-width": 1.2, "line-dasharray": [2, 1] } });
      map.addSource("candidate-sites", { type: "geojson", data: collection(data.candidate_sites ?? []) });
      map.addLayer({ id: "candidate-fill", type: "fill", source: "candidate-sites", paint: { "fill-color": "#174f43", "fill-opacity": 0.58 } });
      map.addLayer({ id: "candidate-line", type: "line", source: "candidate-sites", paint: { "line-color": "#0b382f", "line-width": 2.2 } });
      map.addSource("contextual-sites", { type: "geojson", data: collection(data.contextual_sites ?? []) });
      map.addLayer({ id: "contextual-fill", type: "fill", source: "contextual-sites", paint: { "fill-color": "#809588", "fill-opacity": 0.16 } });
      map.addLayer({ id: "contextual-line", type: "line", source: "contextual-sites", paint: { "line-color": "#64776d", "line-width": 1.5, "line-dasharray": [3, 2] } });
      if (data.start_context) {
        map.addSource("start-context", { type: "geojson", data: collection([data.start_context]) });
        map.addLayer({ id: "start-point", type: "circle", source: "start-context", paint: { "circle-radius": 7, "circle-color": "#f7f1df", "circle-stroke-color": "#1c2d29", "circle-stroke-width": 3 } });
      }
      const bounds = new maplibregl.LngLatBounds();
      [...(data.aggregate_grid ?? []), ...(data.candidate_sites ?? []), ...(data.contextual_sites ?? []), ...(data.start_context ? [data.start_context] : [])].forEach((feature) => extendBounds(bounds, feature.geometry.coordinates));
      if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 48, maxZoom: 13, duration: 0 });
    });
    return () => map.remove();
  }, [data]);

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
        {!loading && !error && !data && <div className="map-message">Start or select a run to load its checkpoint map.</div>}
        {data && !loading && (data.aggregate_grid ?? []).length === 0 && <div className="map-message map-low">No grid shown — the strong evidence gate did not pass.</div>}
        <div className="map-legend" aria-label="Map legend">
          <strong>Legend</strong>
          <span><i className="legend-swatch grid" /> Aggregate evidence grid</span>
          <span><i className="legend-swatch candidate" /> Evidence-grounded candidate</span>
          <span><i className="legend-swatch contextual" /> Contextual site — not recommended</span>
          <span><i className="legend-swatch start" /> Generalised start</span>
        </div>
      </div>
      {data && (
        <div className="map-ledger">
          <p>{data.grid_note}</p>
          <div className="map-site-ledger" aria-label="Mapped sites as text">
            {(data.candidate_sites ?? []).map((site) => <span key={site.properties.site_id}><strong>Candidate</strong> {site.properties.name}</span>)}
            {(data.contextual_sites ?? []).map((site) => <span key={site.properties.site_id}><strong>Context</strong> {site.properties.name}</span>)}
          </div>
          <div className="attribution-row">{data.attributions.map((item) => <span key={item}>{item}</span>)}</div>
        </div>
      )}
    </section>
  );
}
