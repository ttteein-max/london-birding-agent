import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { mapFixture } from "./fixtures";

const addSource = vi.fn();
const addLayer = vi.fn();
const fitBounds = vi.fn();
const remove = vi.fn();

vi.mock("maplibre-gl", () => {
  class Map {
    addControl = vi.fn();
    addSource = addSource;
    addLayer = addLayer;
    fitBounds = fitBounds;
    remove = remove;
    on(_name: string, callback: () => void) { callback(); }
  }
  class NavigationControl {}
  class LngLatBounds {
    private empty = true;
    extend() { this.empty = false; return this; }
    isEmpty() { return this.empty; }
  }
  return { Map, NavigationControl, LngLatBounds };
});

import { EvidenceMap } from "../components/EvidenceMap";

describe("privacy-bounded evidence map", () => {
  it("uses an offline style, exposes a text legend, and renders low-evidence state", async () => {
    render(<EvidenceMap data={{ ...mapFixture, status: "limited" }} loading={false} error={null} />);
    expect(screen.getByLabelText("Map legend")).toHaveTextContent("Aggregate evidence grid");
    expect(screen.getByLabelText("Map of aggregate historical evidence and validated site polygons")).toBeInTheDocument();
    expect(screen.getByText("No grid shown — the strong evidence gate did not pass.")).toBeInTheDocument();
    await waitFor(() => expect(addSource).toHaveBeenCalledWith("aggregate-grid", expect.any(Object)));
    expect(addLayer).toHaveBeenCalled();
  });
});
