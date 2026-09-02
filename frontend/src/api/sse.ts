import { api } from "./client";
import type { AgentRunEvent, AgentRunEventType } from "./contracts";

const EVENT_TYPES: AgentRunEventType[] = [
  "run_started",
  "node_started",
  "node_completed",
  "node_failed",
  "model_started",
  "model_completed",
  "model_failed",
  "tool_started",
  "tool_completed",
  "tool_failed",
  "interrupt_requested",
  "run_resumed",
  "checkpoint_selected",
  "checkpoint_created",
  "replay_started",
  "replay_completed",
  "replay_failed",
  "fork_created",
  "fork_started",
  "fork_completed",
  "fork_failed",
  "comparison_created",
  "run_completed",
  "run_failed",
];

export interface EventStreamHandlers {
  onEvent: (event: AgentRunEvent) => void;
  onConnection: (state: "connected" | "reconnecting" | "closed") => void;
  onTerminal: () => void;
}

export interface EventSourceLike {
  close(): void;
  onopen: ((event: Event) => void) | null;
  onerror: ((event: Event) => void) | null;
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void;
}

export function connectOperationEvents(
  operationId: string,
  handlers: EventStreamHandlers,
  factory: (url: string) => EventSourceLike = (url) => new EventSource(url),
  reconnectDelayMs = 500,
): () => void {
  let lastSequence = 0;
  let source: EventSourceLike | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;

  const open = () => {
    if (stopped) return;
    source = factory(api.eventsUrl(operationId, lastSequence));
    source.onopen = () => handlers.onConnection("connected");
    const receive = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as AgentRunEvent;
      if (event.sequence <= lastSequence) return;
      lastSequence = event.sequence;
      handlers.onEvent(event);
      if (event.event_type === "run_completed" || event.event_type === "run_failed") {
        stopped = true;
        source?.close();
        handlers.onConnection("closed");
        handlers.onTerminal();
      }
    };
    EVENT_TYPES.forEach((eventType) => source?.addEventListener(eventType, receive));
    source.onerror = () => {
      source?.close();
      if (stopped) return;
      handlers.onConnection("reconnecting");
      timer = setTimeout(open, reconnectDelayMs);
    };
  };

  open();
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    source?.close();
    handlers.onConnection("closed");
  };
}
