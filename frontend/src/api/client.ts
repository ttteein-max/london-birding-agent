import type {
  ApiErrorBody,
  CreateRunRequest,
  EvidenceView,
  FinalPlanView,
  ForkRunRequest,
  HealthView,
  HistoryView,
  MapEvidenceView,
  MapConfigView,
  OperationAccepted,
  OperationView,
  PlanComparison,
  ReplayRunRequest,
  RouteGeometryView,
  RouteOptionsView,
  ResumeRunRequest,
  RunDetail,
  RunSummary,
  StateView,
  WorkflowTopologyView,
} from "./contracts";

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields: string[];

  constructor(status: number, body: ApiErrorBody | unknown) {
    const payload = body && typeof body === "object" ? body as Record<string, unknown> : {};
    const nested = payload.error && typeof payload.error === "object"
      ? payload.error as Record<string, unknown>
      : {};
    const detail = typeof payload.detail === "string" ? payload.detail : null;
    const errorMessage = typeof nested.message === "string"
      ? nested.message
      : detail ?? `Request failed with HTTP ${status}`;
    const errorCode = typeof nested.code === "string"
      ? nested.code
      : `http_${status}`;
    const errorFields = Array.isArray(nested.fields)
      ? nested.fields.filter((field): field is string => typeof field === "string")
      : [];
    super(errorMessage);
    this.name = "ApiError";
    this.status = status;
    this.code = errorCode;
    this.fields = errorFields;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  const body = (await response.json()) as T | ApiErrorBody | unknown;
  if (!response.ok) {
    throw new ApiError(response.status, body);
  }
  return body as T;
}

function encoded(value: string): string {
  return encodeURIComponent(value);
}

export const api = {
  health: () => request<HealthView>("/api/v1/health"),
  mapConfig: () => request<MapConfigView>("/api/v1/map/config"),
  topology: () => request<WorkflowTopologyView>("/api/v1/workflow/topology"),
  listRuns: () => request<RunSummary[]>("/api/v1/runs"),
  createRun: (body: CreateRunRequest) =>
    request<OperationAccepted>("/api/v1/runs", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  run: (threadId: string) =>
    request<RunDetail>(`/api/v1/runs/${encoded(threadId)}`),
  history: (threadId: string) =>
    request<HistoryView>(`/api/v1/runs/${encoded(threadId)}/history`),
  operation: (operationId: string) =>
    request<OperationView>(`/api/v1/operations/${encoded(operationId)}`),
  state: (
    threadId: string,
    checkpointId: string,
    nodeId: string,
    graphStep: number,
  ) => {
    const query = new URLSearchParams({
      node_id: nodeId,
      graph_step: String(graphStep),
    });
    return request<StateView>(
      `/api/v1/runs/${encoded(threadId)}/checkpoints/${encoded(checkpointId)}/state?${query}`,
    );
  },
  evidence: (threadId: string, checkpointId: string) =>
    request<EvidenceView>(
      `/api/v1/runs/${encoded(threadId)}/checkpoints/${encoded(checkpointId)}/evidence`,
    ),
  plan: (threadId: string, checkpointId: string) =>
    request<FinalPlanView | null>(
      `/api/v1/runs/${encoded(threadId)}/checkpoints/${encoded(checkpointId)}/plan`,
    ),
  map: (threadId: string, checkpointId: string) =>
    request<MapEvidenceView>(
      `/api/v1/runs/${encoded(threadId)}/checkpoints/${encoded(checkpointId)}/map`,
    ),
  routes: (threadId: string, checkpointId: string) =>
    request<RouteOptionsView>(
      `/api/v1/runs/${encoded(threadId)}/checkpoints/${encoded(checkpointId)}/routes`,
    ),
  routeGeometry: (
    threadId: string,
    checkpointId: string,
    routeGeometryReference: string,
  ) => request<RouteGeometryView>(
    `/api/v1/runs/${encoded(threadId)}/checkpoints/${encoded(checkpointId)}/route-geometry/${encoded(routeGeometryReference)}`,
  ),
  resume: (threadId: string, body: ResumeRunRequest) =>
    request<OperationAccepted>(`/api/v1/runs/${encoded(threadId)}/resume`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  replay: (threadId: string, body: ReplayRunRequest) =>
    request<OperationAccepted>(`/api/v1/runs/${encoded(threadId)}/replay`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  fork: (threadId: string, body: ForkRunRequest) =>
    request<OperationAccepted>(`/api/v1/runs/${encoded(threadId)}/fork`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  compare: async (threadId: string, checkpointA: string, checkpointB: string) => {
    const query = new URLSearchParams({
      checkpoint_a: checkpointA,
      checkpoint_b: checkpointB,
    });
    const result = await request<{ comparison: PlanComparison }>(
      `/api/v1/runs/${encoded(threadId)}/compare?${query}`,
    );
    return result.comparison;
  },
  eventsUrl: (operationId: string, afterSequence = 0) =>
    `${API_BASE}/api/v1/operations/${encoded(operationId)}/events?after_sequence=${afterSequence}`,
};
