import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api/client";
import { connectOperationEvents } from "./api/sse";
import type {
  AgentRunEvent,
  CheckpointSummary,
  EvidenceView,
  FinalPlanView,
  ForkRunRequest,
  HealthView,
  HistoryView,
  MapConfigView,
  MapEvidenceView,
  OperationAccepted,
  PlanComparison,
  ResumeRunRequest,
  RouteGeometryView,
  RouteOptionsView,
  RunDetail,
  RunModeView,
  RunSummary,
  StateView,
  WorkflowTopologyView,
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
  const [topology, setTopology] = useState<WorkflowTopologyView | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedThread, setSelectedThread] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [history, setHistory] = useState<HistoryView | null>(null);
  const [evidence, setEvidence] = useState<EvidenceView | null>(null);
  const [mapData, setMapData] = useState<MapEvidenceView | null>(null);
  const [mapConfig, setMapConfig] = useState<MapConfigView | null>(null);
  const [routeOptions, setRouteOptions] = useState<RouteOptionsView | null>(null);
  const [routeGeometry, setRouteGeometry] = useState<RouteGeometryView | null>(null);
  const [mapCheckpointId, setMapCheckpointId] = useState<string | null>(null);
  const [plan, setPlan] = useState<FinalPlanView | null>(null);
  const [executionState, setExecutionState] = useState<StateView | null>(null);
  const [selectedExecutionId, setSelectedExecutionId] = useState<string | null>(null);
  const [events, setEvents] = useState<AgentRunEvent[]>([]);
  const [inspectedState, setInspectedState] = useState<StateView | null>(null);
  const [comparison, setComparison] = useState<PlanComparison | null>(null);
  const [connection, setConnection] = useState<Connection>("idle");
  const [activeOperationStatus, setActiveOperationStatus] = useState<RunDetail["run"]["status"] | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingRun, setLoadingRun] = useState(false);
  const [loadingMap, setLoadingMap] = useState(false);
  const [loadingRoute, setLoadingRoute] = useState(false);
  const [loadingState, setLoadingState] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stopStream = useRef<(() => void) | null>(null);
  const selectionEpoch = useRef(0);
  const checkpointViewEpoch = useRef(0);

  const loadCheckpoint = useCallback(async (
    threadId: string,
    checkpointId: string,
    epoch = selectionEpoch.current,
    fallbackPlan: FinalPlanView | null = null,
    checkpoint: CheckpointSummary | null = null,
  ) => {
    if (selectionEpoch.current !== epoch) return;
    const viewEpoch = ++checkpointViewEpoch.current;
    setLoadingMap(true);
    setExecutionState(null);
    try {
      const [nextEvidence, nextMap, nextRoutes, nextState] = await Promise.all([
        api.evidence(threadId, checkpointId),
        api.map(threadId, checkpointId),
        api.routes(threadId, checkpointId),
        checkpoint && Number.isInteger(checkpoint.graph_step)
          ? api.state(threadId, checkpointId, checkpoint.node_id, checkpoint.graph_step ?? -1)
          : Promise.resolve(null),
      ]);
      let nextPlan = fallbackPlan;
      try {
        nextPlan = await api.plan(threadId, checkpointId);
      } catch (caught) {
        // Keep evidence and map usable during a rolling deployment where the
        // browser updates before the checkpoint-plan endpoint is available.
        if (!(caught instanceof ApiError && caught.status === 404)) throw caught;
      }
      if (selectionEpoch.current !== epoch || checkpointViewEpoch.current !== viewEpoch) return;
      setEvidence(nextEvidence);
      setMapData(nextMap);
      setMapCheckpointId(checkpointId);
      setRouteOptions(nextRoutes);
      setPlan(nextPlan);
      setExecutionState(nextState);
      const routeReference = nextMap.route_geometry_reference;
      if (routeReference) {
        setLoadingRoute(true);
        try {
          const geometry = await api.routeGeometry(
            threadId,
            checkpointId,
            routeReference,
          );
          if (selectionEpoch.current === epoch && checkpointViewEpoch.current === viewEpoch) {
            setRouteGeometry(geometry);
          }
        } catch {
          if (selectionEpoch.current === epoch && checkpointViewEpoch.current === viewEpoch) {
            setRouteGeometry(null);
          }
        } finally {
          if (selectionEpoch.current === epoch && checkpointViewEpoch.current === viewEpoch) {
            setLoadingRoute(false);
          }
        }
      } else {
        setRouteGeometry(null);
      }
    } catch (caught) {
      if (selectionEpoch.current !== epoch || checkpointViewEpoch.current !== viewEpoch) return;
      setError(message(caught));
      setEvidence(null);
      setMapData(null);
      setRouteOptions(null);
      setRouteGeometry(null);
      setMapCheckpointId(null);
      setPlan(null);
      setExecutionState(null);
    } finally {
      if (selectionEpoch.current === epoch && checkpointViewEpoch.current === viewEpoch) setLoadingMap(false);
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
      const selectedExecution = nextHistory.executions.find(
        (item) => item.execution_id === nextDetail.run.execution_id,
      ) ?? nextHistory.executions[0];
      setSelectedExecutionId(selectedExecution?.execution_id ?? null);
      const checkpointId = selectedExecution?.final_checkpoint_id
        ?? selectedExecution?.head_checkpoint_id
        ?? nextDetail.run.current_checkpoint_id;
      if (checkpointId) {
        const selectedCheckpoint = nextHistory.checkpoints.find(
          (item) => item.checkpoint_id === checkpointId,
        ) ?? null;
        const fallbackPlan = selectedExecution?.execution_id === nextDetail.run.execution_id
          ? nextDetail.final_plan
          : null;
        await loadCheckpoint(threadId, checkpointId, epoch, fallbackPlan, selectedCheckpoint);
      } else {
        checkpointViewEpoch.current += 1;
        setEvidence(null);
        setMapData(null);
        setPlan(null);
        setExecutionState(null);
      }
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
        const [nextHealth, nextMapConfig, nextTopology, nextRuns] = await Promise.all([
          api.health(),
          api.mapConfig(),
          api.topology(),
          api.listRuns(),
        ]);
        setHealth(nextHealth);
        setMapConfig(nextMapConfig);
        setTopology(nextTopology);
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
      setDetail(null);
      setHistory(null);
      setEvidence(null);
      setMapData(null);
      setRouteOptions(null);
      setRouteGeometry(null);
      setMapCheckpointId(null);
      setPlan(null);
      setExecutionState(null);
      setSelectedThread(accepted.thread_id);
      setEvents([]);
      setSelectedExecutionId(null);
      checkpointViewEpoch.current += 1;
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
    setRouteOptions(null);
    setRouteGeometry(null);
    setMapCheckpointId(null);
    setPlan(null);
    setExecutionState(null);
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

  const selectExecution = async (executionId: string) => {
    const execution = history?.executions.find((item) => item.execution_id === executionId);
    const checkpointId = execution?.final_checkpoint_id ?? execution?.head_checkpoint_id;
    if (!selectedThread || !execution || !checkpointId) return;
    setSelectedExecutionId(executionId);
    setError(null);
    const fallbackPlan = execution.execution_id === detail?.run.execution_id
      ? detail.final_plan
      : null;
    const checkpoint = history?.checkpoints.find(
      (item) => item.checkpoint_id === checkpointId,
    ) ?? null;
    await loadCheckpoint(selectedThread, checkpointId, selectionEpoch.current, fallbackPlan, checkpoint);
  };

  const selectRoute = async (reference: string) => {
    if (!selectedThread || !mapCheckpointId) return;
    setLoadingRoute(true);
    try {
      setRouteGeometry(await api.routeGeometry(
        selectedThread,
        mapCheckpointId,
        reference,
      ));
    } catch (caught) {
      setError(message(caught));
    } finally {
      setLoadingRoute(false);
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
        <SelectedRunRequest detail={detail} state={executionState} />
        {decision && <HitlPanel key={decision.checkpoint_id} decision={decision} busy={busy} onResume={resume} />}
        <div className="workspace-grid">
          <RunSidebar runs={runs} selectedThread={selectedThread} detail={detail} onSelect={(id) => void selectRun(id)} />
          <EvidenceMap
            data={mapData}
            loading={loadingMap}
            error={error}
            basemapStyleUrl={mapConfig?.style_url}
            basemapAttributions={mapConfig?.attributions}
            routeGeometry={routeGeometry}
            routeOptions={routeOptions}
            routeLoading={loadingRoute}
            onRouteSelect={(reference) => void selectRoute(reference)}
          />
          <aside className="evidence-rail" aria-label="Plan and evidence notebook">
            {(loadingRun || loadingMap) && <AsyncState kind="loading" title="Opening execution" detail="Reading its durable final checkpoint…" />}
            {!loadingRun && !loadingMap && plan && <PlanPanel plan={plan} />}
            {!loadingRun && !loadingMap && evidence && <EvidencePanel evidence={evidence} />}
            {!loadingRun && !detail && <AsyncState kind="empty" title="No expedition selected" detail="Start a fixture example or choose a recent run." />}
            {!loadingRun && !loadingMap && detail && !plan && !decision && <AsyncState kind={detail.run.status === "failed" ? "error" : "loading"} title={detail.run.status === "failed" ? "Execution failed safely" : "Plan not ready"} detail={detail.run.status === "failed" ? failedRunDetail(detail, events) : "Evidence collection is still in progress."} />}
          </aside>
        </div>
        <AgentTrace
          topology={topology}
          history={history}
          activeExecutionId={detail?.run.execution_id ?? null}
          selectedExecutionId={selectedExecutionId}
          events={events}
          connection={connection}
          onCheckpoint={(event) => void inspectEvent(event)}
          onInspectCheckpoint={(checkpoint) => void inspectCheckpoint(checkpoint)}
          onExecutionChange={(executionId) => void selectExecution(executionId)}
        />
        {history && <TimeTravelPanel history={history} comparison={comparison} busy={busy} onInspect={(checkpoint) => void inspectCheckpoint(checkpoint)} onLoadState={loadSafeState} onReplay={replay} onFork={fork} onCompare={compare} />}
      </main>
      <footer>
        <span>London-only · birds-first · historical evidence</span>
        <span>Routes end at audited public-site entrances; access conditions and sightings are never guaranteed</span>
      </footer>
      <StateInspector state={inspectedState} loading={loadingState} onClose={() => setInspectedState(null)} />
    </div>
  );
}
