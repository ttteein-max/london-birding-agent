import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mapFixture } from "./fixtures";

const addSource = vi.fn();
const addLayer = vi.fn();
const fitBounds = vi.fn();
const remove = vi.fn();
const popupContent = vi.fn();
const markerElements: HTMLElement[] = [];
const markerCoordinates: unknown[] = [];

vi.mock("maplibre-gl", () => {
  class Map {
    addControl = vi.fn();
    addSource = addSource;
    addLayer = addLayer;
    fitBounds = fitBounds;
    remove = remove;
    getCanvas = () => ({ style: { cursor: "" } });
    project([longitude, latitude]: [number, number]) {
      return { x: longitude * 10_000, y: -latitude * 10_000 };
    }
    on(name: string, layerOrCallback: string | (() => void), callback?: () => void) {
      const handler = typeof layerOrCallback === "function" ? layerOrCallback : callback;
      if (name === "load") handler?.();
      return this;
    }
  }
  class NavigationControl {}
  class Popup {
    setLngLat() { return this; }
    setDOMContent(value: HTMLElement) { popupContent(value); return this; }
    addTo() { return this; }
    remove() { return this; }
  }
  class Marker {
    constructor(options: { element: HTMLElement }) { markerElements.push(options.element); }
    setLngLat(value: unknown) { markerCoordinates.push(value); return this; }
    addTo() { return this; }
  }
  class LngLatBounds {
    private empty = true;
    extend() { this.empty = false; return this; }
    isEmpty() { return this.empty; }
  }
  return { Map, NavigationControl, Popup, Marker, LngLatBounds };
});

import { EvidenceMap } from "../components/EvidenceMap";

describe("privacy-bounded evidence map", () => {
  beforeEach(() => {
    markerElements.length = 0;
    markerCoordinates.length = 0;
    popupContent.mockClear();
  });

  it("uses an offline style, exposes a text legend, and renders low-evidence state", async () => {
    render(<EvidenceMap data={{ ...mapFixture, status: "limited" }} loading={false} error={null} />);
    expect(screen.getByLabelText("Map legend")).toHaveTextContent("Lower density · 3–9");
    expect(screen.getByLabelText("Map legend")).toHaveTextContent("Higher density · 25+");
    expect(screen.getByLabelText("Map legend")).toHaveTextContent("Contextual site — not recommended");
    expect(screen.getByLabelText("Map of aggregate historical evidence and validated site polygons")).toBeInTheDocument();
    expect(screen.getByText("No grid shown — the strong evidence gate did not pass.")).toBeInTheDocument();
    await waitFor(() => expect(addSource).toHaveBeenCalledWith("aggregate-grid", expect.any(Object)));
    expect(addLayer).toHaveBeenCalled();
  });

  it("keeps pins inside the largest site polygon and hides them when the footprint is too small", async () => {
    render(<EvidenceMap data={{
      ...mapFixture,
      candidate_sites: [{
        type: "Feature",
        geometry: { type: "MultiPolygon", coordinates: [
          [[[-0.30, 51.40], [-0.299, 51.40], [-0.299, 51.401], [-0.30, 51.401], [-0.30, 51.40]]],
          [[[-0.12, 51.50], [-0.10, 51.50], [-0.10, 51.52], [-0.12, 51.52], [-0.12, 51.50]]],
        ] },
        properties: {
          site_id: "candidate-one",
          layer: "candidate",
          name: "Evidence Garden",
          site_type: "park",
          access_certainty: "explicit_public",
          evidence_tier: "directly_grounded",
          approximate_straight_line_distance_km: 1.2,
        },
      }],
      contextual_sites: [{
        type: "Feature",
        geometry: { type: "Polygon", coordinates: [[[-0.14, 51.50], [-0.139, 51.50], [-0.139, 51.501], [-0.14, 51.501], [-0.14, 51.50]]] },
        properties: {
          site_id: "context-one",
          layer: "contextual",
          name: "Context Meadow",
          site_type: "garden",
          access_certainty: "unspecified",
          evidence_tier: "ungrounded",
          approximate_straight_line_distance_km: 1.8,
        },
      }],
    }} loading={false} error={null} />);

    await waitFor(() => expect(markerElements).toHaveLength(2));
    const contextual = markerElements.find((element) => element.classList.contains("marker-contextual"));
    const candidate = markerElements.find((element) => element.classList.contains("marker-candidate"));
    expect(contextual).toHaveAttribute("aria-label", "Open contextual site · Context Meadow");
    expect(candidate).toHaveAttribute("aria-label", "Open candidate site · Evidence Garden");
    expect(candidate).not.toHaveAttribute("hidden");
    expect(contextual).toHaveAttribute("hidden");
    const [candidateLongitude, candidateLatitude] = markerCoordinates[1] as [number, number];
    expect(candidateLongitude).toBeGreaterThanOrEqual(-0.12);
    expect(candidateLongitude).toBeLessThanOrEqual(-0.10);
    expect(candidateLatitude).toBeGreaterThanOrEqual(51.50);
    expect(candidateLatitude).toBeLessThanOrEqual(51.52);
    candidate?.click();
    expect(popupContent).toHaveBeenCalledWith(expect.objectContaining({ textContent: "Evidence GardenEvidence-grounded candidate site" }));
  });
});
