import { useMemo } from "react";
import type {
  AgentRunEvent,
  CheckpointSummary,
  HistoryView,
  WorkflowEdgeView,
  WorkflowNodeView,
  WorkflowTopologyView,
} from "../api/contracts";
import { duration, humanise, shortId } from "../utils";

interface Props {
  topology: WorkflowTopologyView | null;
  history: HistoryView | null;
  activeExecutionId: string | null;
  selectedExecutionId: string | null;
  events: AgentRunEvent[];
  connection: "idle" | "connected" | "reconnecting" | "closed";
  onCheckpoint: (event: AgentRunEvent) => void;
  onInspectCheckpoint: (checkpoint: CheckpointSummary) => void;
  onExecutionChange: (executionId: string) => void;
}

type NodeStatus = "not-visited" | "completed" | "running" | "waiting" | "failed";

const STAGES: Array<WorkflowNodeView["stage"]> = [
  "entry", "intake", "location", "taxonomy", "evidence", "validation", "planning", "outcome",
];

const STAGE_LABELS: Record<WorkflowNodeView["stage"], string> = {
  entry: "Entry",
  intake: "Request intake",
  location: "London location",
  taxonomy: "Bird taxonomy",
  evidence: "Evidence loop",
  validation: "Validation + HITL",
  planning: "Plan + grounding",
  outcome: "Outcome",
};

function eventKind(event: AgentRunEvent): "node" | "model" | "tool" | "system" {
  if (event.event_type.startsWith("node_")) return "node";
  if (event.event_type.startsWith("model_")) return "model";
  if (event.event_type.startsWith("tool_")) return "tool";
  return "system";
}

function routeKey(source: string, target: string): string {
  return `${source}\u0000${target}`;
}

function latestByNode(checkpoints: CheckpointSummary[]): Map<string, CheckpointSummary> {
  const output = new Map<string, CheckpointSummary>();
  checkpoints.forEach((checkpoint) => {
    const current = output.get(checkpoint.node_id);
    if (!current || (checkpoint.graph_step ?? -1) > (current.graph_step ?? -1)) {
      output.set(checkpoint.node_id, checkpoint);
    }
  });
  return output;
}

function eventStatus(events: AgentRunEvent[]): Map<string, { status: NodeStatus; reason?: string }> {
  const output = new Map<string, { status: NodeStatus; reason?: string }>();
  [...events].sort((a, b) => a.sequence - b.sequence).forEach((event) => {
    if (!event.node_id || !event.event_type.startsWith("node_")) return;
    if (event.event_type === "node_started") output.set(event.node_id, { status: "running" });
    if (event.event_type === "node_completed") output.set(event.node_id, { status: "completed" });
    if (event.event_type === "node_failed") {
      output.set(event.node_id, {
        status: "failed",
        reason: String(event.payload.error_type ?? "Execution failed safely"),
      });
    }
  });
  return output;
}

function GraphNode({
  node,
  outgoing,
  activeRoutes,
  status,
  failureReason,
  checkpoint,
  checkpointCount,
  onInspectCheckpoint,
}: {
  node: WorkflowNodeView;
  outgoing: WorkflowEdgeView[];
  activeRoutes: Set<string>;
  status: NodeStatus;
  failureReason?: string;
  checkpoint?: CheckpointSummary;
  checkpointCount: number;
  onInspectCheckpoint: (checkpoint: CheckpointSummary) => void;
}) {
  return (
    <article className={`graph-node node-kind-${node.kind} node-status-${status}`} aria-label={`${node.label}: ${humanise(status.replace("-", "_"))}`}>
      <div className="graph-node-heading">
        <span className="graph-node-status" aria-hidden="true" />
        <strong>{node.label}</strong>
        <span className="node-kind-label">{node.kind === "hitl" ? "HITL" : humanise(node.kind)}</span>
      </div>
      <p>{failureReason ? `Stopped: ${humanise(failureReason)}` : node.summary}</p>
      <div className="graph-node-meta">
        <code>{node.node_id}</code>
        {checkpoint && (
          <button type="button" onClick={() => onInspectCheckpoint(checkpoint)} aria-label={`Open latest of ${checkpointCount} saved state${checkpointCount === 1 ? "" : "s"} · ${node.label}`}>
            CP {checkpoint.graph_step ?? "—"}{checkpointCount > 1 ? ` · ×${checkpointCount}` : ""}
          </button>
        )}
      </div>
      {outgoing.length > 0 && (
        <div className="graph-routes" aria-label={`Routes from ${node.label}`}>
          {outgoing.map((edge) => (
            <span
              key={`${edge.source}-${edge.target}-${edge.route_label ?? ""}`}
              className={activeRoutes.has(routeKey(edge.source, edge.target)) ? "route-active" : ""}
              title={`${edge.conditional ? "Conditional route" : "Route"} to ${edge.target}`}
            >
              {edge.conditional ? "◇" : "→"} {edge.route_label ? `${humanise(edge.route_label)} · ` : ""}{humanise(edge.target)}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}

export function AgentTrace({
  topology,
  history,
  activeExecutionId,
  selectedExecutionId,
  events,
  connection,
  onCheckpoint,
  onInspectCheckpoint,
  onExecutionChange,
}: Props) {
  const selectedExecution = history?.executions.find(
    (item) => item.execution_id === selectedExecutionId,
  ) ?? history?.executions.find(
    (item) => item.execution_id === activeExecutionId,
  ) ?? history?.executions[0];
  const executionId = selectedExecution?.execution_id ?? selectedExecutionId ?? activeExecutionId;
  const checkpoints = useMemo(
    () => (history?.checkpoints ?? [])
      .filter((item) => !executionId || item.execution_id === executionId)
      .filter((item) => topology?.nodes.some((node) => node.node_id === item.node_id))
      .sort((a, b) => (a.graph_step ?? -1) - (b.graph_step ?? -1)),
    [executionId, history?.checkpoints, topology?.nodes],
  );
  const checkpointsByNode = useMemo(() => latestByNode(checkpoints), [checkpoints]);
  const checkpointCounts = useMemo(() => {
    const output = new Map<string, number>();
    checkpoints.forEach((checkpoint) => output.set(checkpoint.node_id, (output.get(checkpoint.node_id) ?? 0) + 1));
    return output;
  }, [checkpoints]);
  const liveStatuses = useMemo(
    () => eventStatus(executionId === activeExecutionId ? events : []),
    [activeExecutionId, events, executionId],
  );
  const activeRoutes = useMemo(() => {
    const output = new Set<string>();
    checkpoints.forEach((checkpoint, index) => {
      const next = checkpoints[index + 1];
      if (next) output.add(routeKey(checkpoint.node_id, next.node_id));
    });
    return output;
  }, [checkpoints]);
  const waitingNodes = useMemo(() => {
    const output = new Set<string>();
    if (!selectedExecution?.interrupt_kind) return output;
    const head = (history?.checkpoints ?? []).find((item) => item.checkpoint_id === selectedExecution.head_checkpoint_id);
    head?.next_nodes?.forEach((nodeId) => output.add(nodeId));
    return output;
  }, [history?.checkpoints, selectedExecution]);

  const statusFor = (node: WorkflowNodeView): { status: NodeStatus; reason?: string } => {
    const live = liveStatuses.get(node.node_id);
    if (live?.status === "failed" || live?.status === "running") return live;
    if (waitingNodes.has(node.node_id)) return { status: "waiting" };
    if (live?.status === "completed" || checkpointsByNode.has(node.node_id)) return { status: "completed" };
    if (node.kind === "end" && selectedExecution?.terminal_status && !selectedExecution.interrupt_kind) return { status: "completed" };
    return { status: "not-visited" };
  };

  const visitedCount = topology?.nodes.filter((node) => statusFor(node).status !== "not-visited").length ?? 0;

  return (
    <section className="trace-panel" aria-labelledby="trace-title">
      <div className="trace-header">
        <div><p className="eyebrow">Compiled graph + live execution</p><h2 id="trace-title">LangGraph execution map</h2></div>
        <div className="trace-header-actions">
          {history && history.executions.length > 0 && (
            <label>
              Execution
              <select value={executionId ?? ""} onChange={(event) => onExecutionChange(event.target.value)}>
                {history.executions.map((execution) => (
                  <option key={execution.execution_id} value={execution.execution_id}>
                    {shortId(execution.execution_id)} · {shortId(execution.branch_id)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <span className={`connection connection-${connection}`} role="status"><i />{humanise(connection)}</span>
        </div>
      </div>

      <div className="graph-legend" aria-label="Graph status key">
        <span><i className="not-visited" /> Not visited</span>
        <span><i className="completed" /> Traversed</span>
        <span><i className="hitl" /> HITL traversed / waiting</span>
        <span><i className="running" /> Running</span>
        <span><i className="failed" /> Failed</span>
        <span><b>CP</b> Saved state after a graph step</span>
        {topology && <small>{visitedCount} / {topology.nodes.length} nodes touched · {topology.edges.length} compiled routes · {topology.workflow_version}</small>}
      </div>

      {!topology ? (
        <p className="empty-copy">Loading the compiled LangGraph topology…</p>
      ) : (
        <div className="graph-board" aria-label="Complete LangGraph topology with execution state">
          {STAGES.map((stage) => {
            const nodes = topology.nodes.filter((node) => node.stage === stage);
            return (
              <section className="graph-stage" key={stage} aria-labelledby={`graph-stage-${stage}`}>
                <div className="graph-stage-heading">
                  <span>{String(STAGES.indexOf(stage) + 1).padStart(2, "0")}</span>
                  <h3 id={`graph-stage-${stage}`}>{STAGE_LABELS[stage]}</h3>
                </div>
                <div className="graph-stage-nodes">
                  {nodes.map((node) => {
                    const runtime = statusFor(node);
                    return (
                      <GraphNode
                        key={node.node_id}
                        node={node}
                        outgoing={topology.edges.filter((edge) => edge.source === node.node_id)}
                        activeRoutes={activeRoutes}
                        status={runtime.status}
                        failureReason={runtime.reason}
                        checkpoint={checkpointsByNode.get(node.node_id)}
                        checkpointCount={checkpointCounts.get(node.node_id) ?? 0}
                        onInspectCheckpoint={onInspectCheckpoint}
                      />
                    );
                  })}
                </div>
              </section>
            );
          })}
        </div>
      )}

      <details className="event-audit">
        <summary>Event audit · {events.length} live events</summary>
        {events.length === 0 ? (
          <p className="empty-copy">Node, model, tool and checkpoint events will appear during an operation.</p>
        ) : (
          <ol className="trace-list" aria-label="Ordered agent events">
            {events.map((event) => {
              const kind = eventKind(event);
              const label = event.node_id || event.tool_name || String(event.payload.model ?? event.event_type);
              const checkpoint = event.event_type === "checkpoint_created";
              const failed = event.event_type.endsWith("_failed");
              return (
                <li key={`${event.run_id}-${event.sequence}`} className={`trace-${kind} ${event.parent_span_id ? "nested" : ""} ${failed ? "trace-failed" : ""}`}>
                  <span className="trace-sequence">{String(event.sequence).padStart(2, "0")}</span>
                  <span className={`trace-dot trace-dot-${kind}`} aria-hidden="true" />
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
      </details>
      <p className="microcopy">The topology comes from the compiled backend graph. A label such as CP 17 · ×3 means the latest saved state for that node is graph step 17 and this execution saved three checkpoints there. Click it to inspect accumulated state. The event audit supplies live node, model and tool spans; model and tool spans are nested inside node wall time.</p>
    </section>
  );
}
