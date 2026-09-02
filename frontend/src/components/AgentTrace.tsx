import type { AgentRunEvent } from "../api/contracts";
import { duration, humanise } from "../utils";

interface Props {
  events: AgentRunEvent[];
  connection: "idle" | "connected" | "reconnecting" | "closed";
  onCheckpoint: (event: AgentRunEvent) => void;
}

function kind(event: AgentRunEvent): "node" | "model" | "tool" | "system" {
  if (event.event_type.startsWith("node_")) return "node";
  if (event.event_type.startsWith("model_")) return "model";
  if (event.event_type.startsWith("tool_")) return "tool";
  return "system";
}

export function AgentTrace({ events, connection, onCheckpoint }: Props) {
  return (
    <section className="trace-panel" aria-labelledby="trace-title">
      <div className="trace-header">
        <div><p className="eyebrow">Live execution</p><h2 id="trace-title">Agent trace</h2></div>
        <span className={`connection connection-${connection}`} role="status"><i />{humanise(connection)}</span>
      </div>
      {events.length === 0 ? (
        <p className="empty-copy">Node, model, tool and checkpoint events will appear here.</p>
      ) : (
        <ol className="trace-list" aria-label="Ordered agent events">
          {events.map((event) => {
            const eventKind = kind(event);
            const label = event.node_id || event.tool_name || String(event.payload.model ?? event.event_type);
            const checkpoint = event.event_type === "checkpoint_created";
            return (
              <li key={`${event.run_id}-${event.sequence}`} className={`trace-${eventKind} ${event.parent_span_id ? "nested" : ""}`}>
                <span className="trace-sequence">{String(event.sequence).padStart(2, "0")}</span>
                <span className={`trace-dot trace-dot-${eventKind}`} aria-hidden="true" />
                <div className="trace-copy">
                  <strong>{humanise(event.event_type)}</strong>
                  <span>{label}</span>
                  {(event.payload.graph_step ?? event.payload.langgraph_step) != null && (
                    <small>Graph step {String(event.payload.graph_step ?? event.payload.langgraph_step)}</small>
                  )}
                </div>
                <span className="trace-duration">{duration(event.duration_ms)}</span>
                {checkpoint && (
                  <button className="text-action" onClick={() => onCheckpoint(event)} aria-label={`Inspect state for ${event.node_id}`}>
                    Inspect state
                  </button>
                )}
              </li>
            );
          })}
        </ol>
      )}
      <p className="microcopy">Model and tool spans are nested inside node wall time; nested durations are not added to parent elapsed time.</p>
    </section>
  );
}
