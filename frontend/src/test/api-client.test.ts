import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../api/client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("API error compatibility", () => {
  it("preserves a useful error when FastAPI returns its native detail envelope", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "Not Found" }),
      { status: 404, headers: { "Content-Type": "application/json" } },
    )));

    const error = await api.plan("thread", "checkpoint").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      message: "Not Found",
      status: 404,
      code: "http_404",
      fields: [],
    });
  });
});
