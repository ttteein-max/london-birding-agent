import type { components } from "./schema";

export type HealthView = components["schemas"]["HealthView"];
export type RunModeView = components["schemas"]["RunModeView"];
export type CreateRunRequest = components["schemas"]["CreateRunRequest"];
export type OperationAccepted = components["schemas"]["OperationAccepted"];
export type OperationView = components["schemas"]["OperationView"];
export type RunSummary = components["schemas"]["RunSummary"];
export type RunDetail = components["schemas"]["RunDetail"];
export type HistoryView = components["schemas"]["HistoryView"];
export type CheckpointSummary = components["schemas"]["CheckpointSummary"];
export type StateView = components["schemas"]["StateView"];
export type EvidenceView = components["schemas"]["EvidenceView"];
export type MapEvidenceView = components["schemas"]["MapEvidenceView"];
export type PendingDecisionView = components["schemas"]["PendingDecisionView"];
export type FinalPlanView = components["schemas"]["FinalPlanView"];
export type PlanComparison = components["schemas"]["PlanComparison"];
export type ResumeRunRequest = components["schemas"]["ResumeRunRequest"];
export type ForkRunRequest = components["schemas"]["ForkRunRequest"];
export type ReplayRunRequest = components["schemas"]["ReplayRunRequest"];

export type AgentRunEventType =
  | "run_started"
  | "node_started"
  | "node_completed"
  | "node_failed"
  | "model_started"
  | "model_completed"
  | "model_failed"
  | "tool_started"
  | "tool_completed"
  | "tool_failed"
  | "interrupt_requested"
  | "run_resumed"
  | "checkpoint_selected"
  | "checkpoint_created"
  | "replay_started"
  | "replay_completed"
  | "replay_failed"
  | "fork_created"
  | "fork_started"
  | "fork_completed"
  | "fork_failed"
  | "comparison_created"
  | "run_completed"
  | "run_failed";

export interface AgentRunEvent {
  run_id: string;
  sequence: number;
  event_type: AgentRunEventType;
  span_id?: string | null;
  parent_span_id?: string | null;
  node_id?: string | null;
  tool_name?: string | null;
  tool_call_id?: string | null;
  timestamp: string;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  payload: Record<string, unknown>;
}

export interface ApiErrorBody {
  error: { code: string; message: string; fields?: string[] };
}
