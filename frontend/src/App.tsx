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
import { StateInspector } from "./components/StateInspector";
import { StatusHeader } from "./components/StatusHeader";
import { TimeTravelPanel } from "./components/TimeTravelPanel";

type Connection = "idle" | "connected" | "reconnecting" | "closed";

function message(error: unknown): string {
  if (error instanceof ApiError) return `${error.message} (${error.code})`;
  return "The application could not complete that request safely.";
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

  const loadCheckpoint = useCallback(async (threadId: string, checkpointId: string) => {
    setLoadingMap(true);
    try {
      const [nextEvidence, nextMap] = await Promise.all([
        api.evidence(threadId, checkpointId),
        api.map(threadId, checkpointId),
      ]);
      setEvidence(nextEvidence);
      setMapData(nextMap);
    } catch (caught) {
      setError(message(caught));
      setEvidence(null);
      setMapData(null);
    } finally {
      setLoadingMap(false);
    }
  }, []);

  const loadRun = useCallback(async (threadId: string) => {
    setLoadingRun(true);
    setError(null);
    try {
      const [nextDetail, nextHistory, nextRuns] = await Promise.all([
        api.run(threadId),
        api.history(threadId),
        api.listRuns(),
      ]);
      setSelectedThread(threadId);
      setDetail(nextDetail);
      setHistory(nextHistory);
      setRuns(nextRuns);
      const checkpointId = nextDetail.run.current_checkpoint_id;
      if (checkpointId) await loadCheckpoint(threadId, checkpointId);
    } catch (caught) {
      setError(message(caught));
    } finally {
      setLoadingRun(false);
    }
  }, [loadCheckpoint]);

  const watchOperation = useCallback((accepted: OperationAccepted, settle: boolean) => {
    stopStream.current?.();
    setConnection("reconnecting");
    stopStream.current = connectOperationEvents(accepted.operation_id, {
      onEvent: (event) => {
        if (settle && event.event_type === "run_started") setActiveOperationStatus("running");
        setEvents((current) => current.some((item) => item.run_id === event.run_id && item.sequence === event.sequence) ? current : [...current, event]);
      },
      onConnection: setConnection,
      onTerminal: () => {
        if (!settle) return;
        void (async () => {
          for (let attempt = 0; attempt < 100; attempt += 1) {
            const operation = await api.operation(accepted.operation_id);
            if (!["queued", "running"].includes(operation.status)) {
              setBusy(false);
              await loadRun(accepted.thread_id);
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
      try {
        const [nextHealth, nextRuns] = await Promise.all([api.health(), api.listRuns()]);
        setHealth(nextHealth);
        setRuns(nextRuns);
        if (nextRuns[0]) {
          const threadId = nextRuns[0].thread_id;
          await loadRun(threadId);
          const loaded = await api.run(threadId);
          const latest = loaded.operations?.[0];
          if (latest) {
            watchOperation({ operation_id: latest.operation_id, thread_id: threadId, status: "queued", events_url: api.eventsUrl(latest.operation_id) }, false);
          }
        }
      } catch (caught) {
        setError(message(caught));
      }
    })();
    return () => stopStream.current?.();
  }, [loadRun, watchOperation]);

  const begin = async (create: () => Promise<OperationAccepted>) => {
    setBusy(true);
    setActiveOperationStatus("queued");
    setError(null);
    try {
      const accepted = await create();
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
      setRuns(await api.listRuns());
      watchOperation(accepted, true);
    } catch (caught) {
      setBusy(false);
      setActiveOperationStatus(null);
      setError(message(caught));
    }
  };

  const start = async (request: string) => {
    setDetail(null);
    setHistory(null);
    setEvidence(null);
    setMapData(null);
    await begin(() => api.createRun({ request, data_mode: "fixture", model_mode: "scripted" }));
  };

  const selectRun = async (threadId: string) => {
    stopStream.current?.();
    setEvents([]);
    setComparison(null);
    await loadRun(threadId);
    const loaded = await api.run(threadId);
    const latest = loaded.operations?.[0];
    if (latest) watchOperation({ operation_id: latest.operation_id, thread_id: threadId, status: "queued", events_url: api.eventsUrl(latest.operation_id) }, false);
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

  return (
    <div className="app-shell">
      <StatusHeader health={health} run={detail} evidence={evidence} connection={connection} operationStatus={activeOperationStatus} />
      <main>
        <RequestComposer busy={busy} onSubmit={start} />
        {error && <div className="api-error" role="alert"><strong>Application notice</strong><span>{error}</span><button onClick={() => setError(null)} aria-label="Dismiss error">×</button></div>}
        {decision && <HitlPanel decision={decision} busy={busy} onResume={resume} />}
        <div className="workspace-grid">
          <RunSidebar runs={runs} selectedThread={selectedThread} detail={detail} onSelect={(id) => void selectRun(id)} />
          <EvidenceMap data={mapData} loading={loadingMap} error={error} />
          <aside className="evidence-rail" aria-label="Plan and evidence notebook">
            {loadingRun && <AsyncState kind="loading" title="Opening run" detail="Reading durable checkpoint history…" />}
            {!loadingRun && detail?.final_plan && <PlanPanel plan={detail.final_plan} />}
            {!loadingRun && evidence && <EvidencePanel evidence={evidence} />}
            {!loadingRun && !detail && <AsyncState kind="empty" title="No expedition selected" detail="Start a fixture example or choose a recent run." />}
            {!loadingRun && detail && !detail.final_plan && !decision && <AsyncState kind={detail.run.status === "failed" ? "error" : "loading"} title={detail.run.status === "failed" ? "Execution failed safely" : "Plan not ready"} detail={detail.run.status === "failed" ? "Inspect the safe operation status and retry when the configured source is available." : "Evidence collection is still in progress."} />}
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
