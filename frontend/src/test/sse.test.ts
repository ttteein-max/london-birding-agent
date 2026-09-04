import { afterEach, describe, expect, it, vi } from "vitest";
import { connectOperationEvents, type EventSourceLike } from "../api/sse";
import type { AgentRunEvent } from "../api/contracts";

class FakeEventSource implements EventSourceLike {
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  listeners = new Map<string, (event: MessageEvent<string>) => void>();
  closed = false;

  close() { this.closed = true; }
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) { this.listeners.set(type, listener); }
  emit(event: AgentRunEvent) { this.listeners.get(event.event_type)?.({ data: JSON.stringify(event) } as MessageEvent<string>); }
}

afterEach(() => vi.useRealTimers());

describe("SSE reconnect", () => {
  it("reconnects after the last sequence and suppresses duplicate events", () => {
    vi.useFakeTimers();
    const sources: FakeEventSource[] = [];
    const urls: string[] = [];
    const received: AgentRunEvent[] = [];
    const states: string[] = [];
    const stop = connectOperationEvents(
      "operation-one",
      { onEvent: (event) => received.push(event), onConnection: (state) => states.push(state), onTerminal: vi.fn() },
      (url) => { urls.push(url); const source = new FakeEventSource(); sources.push(source); return source; },
      25,
    );
    const event: AgentRunEvent = { run_id: "operation-one", sequence: 1, event_type: "node_started", node_id: "resolve_location", timestamp: "2026-09-02T00:00:00Z", payload: {} };
    sources[0].emit(event);
    sources[0].emit(event);
    expect(received).toHaveLength(1);
    sources[0].onerror?.(new Event("error"));
    expect(states).toContain("reconnecting");
    vi.advanceTimersByTime(25);
    expect(urls[1]).toContain("after_sequence=1");
    sources[1].emit(event);
    expect(received).toHaveLength(1);
    stop();
  });
});
