import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api/client";
import { connectOperationEvents } from "./api/sse";
import type {
  AgentRunEvent,
  CheckpointSummary,
  EvidenceView,
  ForkRunRequest,
  HealthView,
  HistoryView,
  MapEvidenceView,
  OperationAccepted,
  PlanComparison,
  ResumeRunRequest,
  RunDetail,
  RunModeView,
  RunSummary,
  StateView,
} from "./api/contracts";
import { AgentTrace } from "./components/AgentTrace";
import { AsyncState } from "./components/AsyncState";
import { EvidenceMap } from "./components/EvidenceMap";
import { EvidencePanel } from "./components/EvidencePanel";
import { HitlPanel } from "./components/HitlPanel";
import { PlanPanel } from "./components/PlanPanel";
import { RequestComposer } from "./components/RequestComposer";
import { RunSidebar } from "./components/RunSidebar";
import { SelectedRunRequest } from "./components/SelectedRunRequest";
import { StateInspector } from "./components/StateInspector";
import { StatusHeader } from "./components/StatusHeader";
import { TimeTravelPanel } from "./components/TimeTravelPanel";

type Connection = "idle" | "connected" | "reconnecting" | "closed";

function message(error: unknown): string {
  if (error instanceof ApiError) return `${error.message} (${error.code})`;
  return "The application could not complete that request safely.";
}

function failedRunDetail(detail: RunDetail, events: AgentRunEvent[]): string {
  const failedNode = [...events].reverse().find((event) => event.event_type === "node_failed");
  if (
    failedNode?.node_id === "parse_expedition_request"
    && failedNode.payload.error_type === "ValidationError"
  ) {
    return "The model response did not match the request schema. Use a London postcode or explicit coordinates; place names such as streets require a typed location correction.";
  }
  const errorCode = detail.operations?.[0]?.error_code;
  if (errorCode === "upstream_unavailable") {
    return "A configured live data or model provider was unavailable. Check the provider and retry.";
  }
  if (errorCode === "invalid_operation") {
    return "The operation failed deterministic validation. Review the failed node in Agent trace before retrying.";
  }
  return "The operation stopped without exposing unsafe exception details. Review the safe Agent trace before retrying.";
}

export default function App() {
  const [health, setHealth] = useState<HealthView | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedThread, setSelectedThread] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [history, setHistory] = useState<HistoryView | null>(null);
  const [evidence, setEvidence] = useState<EvidenceView | null>(null);
  const [mapData, setMapData] = useState<MapEvidenceView | null>(null);
  const [events, setEvents] = useState<AgentRunEvent[]>([]);
  const [inspectedState, setInspectedState] = useState<StateView | null>(null);
  const [comparison, setComparison] = useState<PlanComparison | null>(null);
  const [connection, setConnection] = useState<Connection>("idle");
  const [activeOperationStatus, setActiveOperationStatus] = useState<RunDetail["run"]["status"] | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingRun, setLoadingRun] = useState(false);
  const [loadingMap, setLoadingMap] = useState(false);
  const [loadingState, setLoadingState] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stopStream = useRef<(() => void) | null>(null);
  const selectionEpoch = useRef(0);

  const loadCheckpoint = useCallback(async (
    threadId: string,
    checkpointId: string,
    epoch = selectionEpoch.current,
  ) => {
    if (selectionEpoch.current !== epoch) return;
    setLoadingMap(true);
    try {
      const [nextEvidence, nextMap] = await Promise.all([
        api.evidence(threadId, checkpointId),
        api.map(threadId, checkpointId),
      ]);
      if (selectionEpoch.current !== epoch) return;
      setEvidence(nextEvidence);
      setMapData(nextMap);
    } catch (caught) {
      if (selectionEpoch.current !== epoch) return;
      setError(message(caught));
      setEvidence(null);
      setMapData(null);
    } finally {
      if (selectionEpoch.current === epoch) setLoadingMap(false);
    }
  }, []);

  const loadRun = useCallback(async (
    threadId: string,
    epoch = selectionEpoch.current,
  ) => {
    if (selectionEpoch.current !== epoch) return;
    setLoadingRun(true);
    setError(null);
    try {
      const [nextDetail, nextHistory, nextRuns] = await Promise.all([
        api.run(threadId),
        api.history(threadId),
        api.listRuns(),
      ]);
      if (selectionEpoch.current !== epoch) return;
      setSelectedThread(threadId);
      setDetail(nextDetail);
      setHistory(nextHistory);
      setRuns(nextRuns);
      const checkpointId = nextDetail.run.current_checkpoint_id;
      if (checkpointId) await loadCheckpoint(threadId, checkpointId, epoch);
    } catch (caught) {
      if (selectionEpoch.current !== epoch) return;
      setError(message(caught));
    } finally {
      if (selectionEpoch.current === epoch) setLoadingRun(false);
    }
  }, [loadCheckpoint]);

  const watchOperation = useCallback((
    accepted: OperationAccepted,
    settle: boolean,
    epoch = selectionEpoch.current,
  ) => {
    if (selectionEpoch.current !== epoch) return;
    stopStream.current?.();
    setConnection("reconnecting");
    stopStream.current = connectOperationEvents(accepted.operation_id, {
      onEvent: (event) => {
        if (selectionEpoch.current !== epoch) return;
        if (settle && event.event_type === "run_started") setActiveOperationStatus("running");
        setEvents((current) => current.some((item) => item.run_id === event.run_id && item.sequence === event.sequence) ? current : [...current, event]);
      },
      onConnection: (nextConnection) => {
        if (selectionEpoch.current === epoch) setConnection(nextConnection);
      },
      onTerminal: () => {
        if (!settle || selectionEpoch.current !== epoch) return;
        void (async () => {
          for (let attempt = 0; attempt < 100; attempt += 1) {
            if (selectionEpoch.current !== epoch) return;
            const operation = await api.operation(accepted.operation_id);
            if (selectionEpoch.current !== epoch) return;
            if (!["queued", "running"].includes(operation.status)) {
              setBusy(false);
              await loadRun(accepted.thread_id, epoch);
              setActiveOperationStatus(null);
              return;
            }
            await new Promise((resolve) => setTimeout(resolve, 80));
          }
          setBusy(false);
          setActiveOperationStatus(null);
          setError("The operation finished streaming but its durable status is delayed.");
        })();
      },
    });
  }, [loadRun]);

  useEffect(() => {
    void (async () => {
      const epoch = selectionEpoch.current;
      try {
        const [nextHealth, nextRuns] = await Promise.all([api.health(), api.listRuns()]);
        setHealth(nextHealth);
        if (selectionEpoch.current !== epoch) return;
        setRuns(nextRuns);
        if (nextRuns[0]) {
          const threadId = nextRuns[0].thread_id;
          await loadRun(threadId, epoch);
          if (selectionEpoch.current !== epoch) return;
          const loaded = await api.run(threadId);
          const latest = loaded.operations?.[0];
          if (latest) {
            watchOperation({ operation_id: latest.operation_id, thread_id: threadId, status: "queued", events_url: api.eventsUrl(latest.operation_id) }, false, epoch);
          }
        }
      } catch (caught) {
        setError(message(caught));
      }
    })();
    return () => stopStream.current?.();
  }, [loadRun, watchOperation]);

  const begin = async (create: () => Promise<OperationAccepted>) => {
    const epoch = ++selectionEpoch.current;
    stopStream.current?.();
    setBusy(true);
    setActiveOperationStatus("queued");
    setError(null);
    try {
      const accepted = await create();
      if (selectionEpoch.current !== epoch) return;
      if (accepted.thread_id !== selectedThread) {
        setDetail(null);
        setHistory(null);
        setEvidence(null);
        setMapData(null);
      }
      setSelectedThread(accepted.thread_id);
      setEvents([]);
      setInspectedState(null);
      setComparison(null);
      const nextRuns = await api.listRuns();
      if (selectionEpoch.current !== epoch) return;
      setRuns(nextRuns);
      watchOperation(accepted, true, epoch);
    } catch (caught) {
      if (selectionEpoch.current !== epoch) return;
      setBusy(false);
      setActiveOperationStatus(null);
      setError(message(caught));
    }
  };

  const start = async (request: string, mode: RunModeView) => {
    setDetail(null);
    setHistory(null);
    setEvidence(null);
    setMapData(null);
    await begin(() => api.createRun({ request, ...mode }));
  };

  const selectRun = async (threadId: string) => {
    const epoch = ++selectionEpoch.current;
    stopStream.current?.();
    setBusy(false);
    setActiveOperationStatus(null);
    setEvents([]);
    setComparison(null);
    await loadRun(threadId, epoch);
    if (selectionEpoch.current !== epoch) return;
    const loaded = await api.run(threadId);
    if (selectionEpoch.current !== epoch) return;
    const latest = loaded.operations?.[0];
    if (latest) watchOperation({ operation_id: latest.operation_id, thread_id: threadId, status: "queued", events_url: api.eventsUrl(latest.operation_id) }, false, epoch);
  };

  const resume = async (request: ResumeRunRequest) => {
    if (selectedThread) await begin(() => api.resume(selectedThread, request));
  };

  const replay = async (checkpointId: string) => {
    if (selectedThread) await begin(() => api.replay(selectedThread, { checkpoint_id: checkpointId }));
  };

  const fork = async (request: ForkRunRequest) => {
    if (selectedThread) await begin(() => api.fork(selectedThread, request));
  };

  const compare = async (checkpointA: string, checkpointB: string) => {
    if (!selectedThread) return;
    setBusy(true);
    try {
      setComparison(await api.compare(selectedThread, checkpointA, checkpointB));
    } catch (caught) {
      setError(message(caught));
    } finally {
      setBusy(false);
    }
  };

  const inspectCheckpoint = async (checkpoint: CheckpointSummary) => {
    setLoadingState(true);
    setInspectedState(null);
    try {
      setInspectedState(await api.state(checkpoint.thread_id, checkpoint.checkpoint_id, checkpoint.node_id, checkpoint.graph_step ?? -1));
    } catch (caught) {
      setError(message(caught));
    } finally {
      setLoadingState(false);
    }
  };

  const loadSafeState = useCallback(
    (checkpoint: CheckpointSummary) => api.state(
      checkpoint.thread_id,
      checkpoint.checkpoint_id,
      checkpoint.node_id,
      checkpoint.graph_step ?? -1,
    ),
    [],
  );

  const inspectEvent = async (event: AgentRunEvent) => {
    if (!selectedThread || event.event_type !== "checkpoint_created") return;
    const checkpointId = String(event.payload.checkpoint_id ?? "");
    const graphStep = Number(event.payload.graph_step);
    if (!checkpointId || !event.node_id || !Number.isInteger(graphStep)) return;
    await inspectCheckpoint({
      thread_id: selectedThread,
      branch_id: String(event.payload.branch_id ?? "main"),
      execution_id: String(event.payload.execution_id ?? "main"),
      checkpoint_id: checkpointId,
      created_at: event.timestamp,
      node_id: event.node_id,
      graph_step: graphStep,
      next_nodes: Array.isArray(event.payload.next_nodes) ? event.payload.next_nodes.map(String) : [],
      applied_constraint_changes: [],
    });
  };

  const decision = detail?.pending_decisions?.[0];
  const defaultMode: RunModeView = {
    data_mode: health?.default_data_mode ?? "fixture",
    model_mode: health?.default_model_mode ?? "scripted",
  };
  const allowedModes = health?.allowed_run_modes ?? [defaultMode];

  return (
    <div className="app-shell">
      <StatusHeader health={health} run={detail} evidence={evidence} connection={connection} operationStatus={activeOperationStatus} />
      <main>
        <RequestComposer
          busy={busy}
          allowedModes={allowedModes}
          defaultMode={defaultMode}
          onSubmit={start}
        />
        {error && <div className="api-error" role="alert"><strong>Application notice</strong><span>{error}</span><button onClick={() => setError(null)} aria-label="Dismiss error">×</button></div>}
        <SelectedRunRequest detail={detail} />
        {decision && <HitlPanel decision={decision} busy={busy} onResume={resume} />}
        <div className="workspace-grid">
          <RunSidebar runs={runs} selectedThread={selectedThread} detail={detail} onSelect={(id) => void selectRun(id)} />
          <EvidenceMap data={mapData} loading={loadingMap} error={error} />
          <aside className="evidence-rail" aria-label="Plan and evidence notebook">
            {loadingRun && <AsyncState kind="loading" title="Opening run" detail="Reading durable checkpoint history…" />}
            {!loadingRun && detail?.final_plan && <PlanPanel plan={detail.final_plan} />}
            {!loadingRun && evidence && <EvidencePanel evidence={evidence} />}
            {!loadingRun && !detail && <AsyncState kind="empty" title="No expedition selected" detail="Start a fixture example or choose a recent run." />}
            {!loadingRun && detail && !detail.final_plan && !decision && <AsyncState kind={detail.run.status === "failed" ? "error" : "loading"} title={detail.run.status === "failed" ? "Execution failed safely" : "Plan not ready"} detail={detail.run.status === "failed" ? failedRunDetail(detail, events) : "Evidence collection is still in progress."} />}
          </aside>
        </div>
        <AgentTrace events={events} connection={connection} onCheckpoint={(event) => void inspectEvent(event)} />
        {history && <TimeTravelPanel history={history} comparison={comparison} busy={busy} onInspect={(checkpoint) => void inspectCheckpoint(checkpoint)} onLoadState={loadSafeState} onReplay={replay} onFork={fork} onCompare={compare} />}
      </main>
      <footer>
        <span>London-only · birds-first · historical evidence</span>
        <span>No routes, access guarantees, field actions, or sighting predictions</span>
      </footer>
      <StateInspector state={inspectedState} loading={loadingState} onClose={() => setInspectedState(null)} />
    </div>
  );
}
