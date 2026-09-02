import { useEffect, useMemo, useState, type FormEvent } from "react";
import type {
  CheckpointSummary,
  ForkRunRequest,
  HistoryView,
  PlanComparison,
  StateView,
} from "../api/contracts";
import { compactDate, humanise, shortId } from "../utils";

interface Props {
  history: HistoryView;
  comparison: PlanComparison | null;
  busy: boolean;
  onInspect: (checkpoint: CheckpointSummary) => void;
  onLoadState: (checkpoint: CheckpointSummary) => Promise<StateView>;
  onReplay: (checkpointId: string) => Promise<void>;
  onFork: (request: ForkRunRequest) => Promise<void>;
  onCompare: (checkpointA: string, checkpointB: string) => Promise<void>;
}

const FIELDS = [
  ["search_radius_km", "Search radius (km)"],
  ["seasonal_window_radius_months", "Seasonal window (months)"],
  ["target_month_override", "Target month override"],
  ["target_local_date", "Target local date"],
  ["rain_preference", "Rain preference"],
  ["duration_hours", "Duration (hours)"],
  ["selected_related_taxon_key", "Validated related taxon key"],
] as const;

export function TimeTravelPanel({ history, comparison, busy, onInspect, onLoadState, onReplay, onFork, onCompare }: Props) {
  const checkpoints = history.checkpoints ?? [];
  const [selected, setSelected] = useState(checkpoints[0]?.checkpoint_id ?? "");
  const [field, setField] = useState<(typeof FIELDS)[number][0]>("search_radius_km");
  const [value, setValue] = useState("");
  const [label, setLabel] = useState("");
  const [compareA, setCompareA] = useState("");
  const [compareB, setCompareB] = useState("");
  const [forkStates, setForkStates] = useState<Record<string, StateView | null>>({});
  const selectedCheckpoint = checkpoints.find((item) => item.checkpoint_id === selected) ?? checkpoints[0];
  const originalFinal = useMemo(
    () => (history.branches ?? []).find((branch) => !branch.parent_branch_id)?.final_checkpoint_id,
    [history.branches],
  );

  useEffect(() => {
    if (!selectedCheckpoint) return;
    if (selectedCheckpoint.checkpoint_id in forkStates) return;
    let active = true;
    void onLoadState(selectedCheckpoint)
      .then((state) => {
        if (active) setForkStates((current) => ({ ...current, [selectedCheckpoint.checkpoint_id]: state }));
      })
      .catch(() => {
        if (active) setForkStates((current) => ({ ...current, [selectedCheckpoint.checkpoint_id]: null }));
      });
    return () => { active = false; };
  }, [forkStates, onLoadState, selectedCheckpoint]);

  const forkState = selectedCheckpoint ? forkStates[selectedCheckpoint.checkpoint_id] : null;
  const loadingForkState = Boolean(selectedCheckpoint && !(selectedCheckpoint.checkpoint_id in forkStates));

  const currentForkValue = field === "selected_related_taxon_key"
    ? forkState?.taxon?.accepted_taxon_key
    : forkState?.request?.[field];
  const isNoop = value !== "" && currentForkValue != null && (
    typeof currentForkValue === "number"
      ? Number(value) === currentForkValue
      : value === String(currentForkValue)
  );

  const fork = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedCheckpoint || !value) return;
    const numericFields = new Set(["search_radius_km", "seasonal_window_radius_months", "target_month_override", "duration_hours", "selected_related_taxon_key"]);
    const parsed: string | number = numericFields.has(field) ? Number(value) : value;
    await onFork({
      checkpoint_id: selectedCheckpoint.checkpoint_id,
      updates: { [field]: parsed },
      branch_label: label || null,
    });
    setValue("");
  };

  const comparisonFields = comparison
    ? [
        "request_constraints",
        "selected_taxon",
        "evidence_outcome",
        "weather_status",
        "plan_status",
        "recommended_site_ids",
        "contextual_site_ids",
        "limitations",
        "provenance_sources",
        "applied_user_decisions",
      ] as const
    : [];

  return (
    <section className="time-travel" aria-labelledby="time-travel-title">
      <div className="section-heading">
        <div><p className="eyebrow">Checkpoint laboratory</p><h2 id="time-travel-title">Time travel</h2></div>
        <span className="count-badge">{checkpoints.length} checkpoints</span>
      </div>
      <p className="microcopy">Thread is the expedition. Branch is a constraint trajectory. Execution is one run of a branch. Checkpoint is one immutable graph state.</p>
      <div className="travel-layout">
        <div className="checkpoint-column">
          <h3>Checkpoint drawer</h3>
          <ol className="checkpoint-list">
            {checkpoints.map((checkpoint) => (
              <li key={checkpoint.checkpoint_id} className={checkpoint.checkpoint_id === selectedCheckpoint?.checkpoint_id ? "selected" : ""}>
                <button onClick={() => setSelected(checkpoint.checkpoint_id)}>
                  <span className="checkpoint-step">{checkpoint.graph_step}</span>
                  <span><strong>{humanise(checkpoint.node_id)}</strong><small>{compactDate(checkpoint.created_at)} · {shortId(checkpoint.checkpoint_id)}</small></span>
                  {checkpoint.checkpoint_id === originalFinal && <em>Original final</em>}
                  {checkpoint.interrupt_kind && <em className="waiting">HITL</em>}
                </button>
              </li>
            ))}
          </ol>
        </div>
        <div className="travel-actions">
          {selectedCheckpoint ? (
            <>
              <div className="selected-checkpoint-card">
                <p className="eyebrow">Selected checkpoint</p>
                <h3>{humanise(selectedCheckpoint.node_id)}</h3>
                <div className="identity-strip"><span>Branch {shortId(selectedCheckpoint.branch_id)}</span><span>Execution {shortId(selectedCheckpoint.execution_id)}</span><span>Checkpoint {shortId(selectedCheckpoint.checkpoint_id)}</span></div>
                <div className="button-row">
                  <button onClick={() => onInspect(selectedCheckpoint)}>Inspect safe state</button>
                  <button disabled={busy || (selectedCheckpoint.next_nodes ?? []).length === 0} onClick={() => onReplay(selectedCheckpoint.checkpoint_id)}>
                    {(selectedCheckpoint.next_nodes ?? []).length === 0 ? "Terminal · replay disabled" : "Replay downstream"}
                  </button>
                </div>
              </div>
              <form className="fork-form" onSubmit={fork}>
                <h3>Fork from checkpoint</h3>
                <p>Only validated request fields can change. Derived evidence and plans are recomputed.</p>
                <label htmlFor="fork-field">Allowed field</label>
                <select id="fork-field" value={field} onChange={(event) => { setField(event.target.value as typeof field); setValue(""); }}>
                  {FIELDS.map(([key, name]) => <option key={key} value={key}>{name}</option>)}
                </select>
                <label htmlFor="fork-value">New value</label>
                {field === "rain_preference" ? (
                  <select id="fork-value" value={value} onChange={(event) => setValue(event.target.value)}><option value="">Select…</option><option value="no_preference">No preference</option><option value="avoid_heavy_rain">Avoid heavy rain</option></select>
                ) : <input id="fork-value" type={field === "target_local_date" ? "date" : "number"} value={value} onChange={(event) => setValue(event.target.value)} />}
                <p className="microcopy" aria-live="polite">
                  {loadingForkState
                    ? "Reading the current allow-listed value…"
                    : `Current value: ${currentForkValue ?? "not set"}${isNoop ? " · choose a different value" : ""}`}
                </p>
                <label htmlFor="fork-label">Branch label (optional)</label>
                <input id="fork-label" value={label} onChange={(event) => setLabel(event.target.value)} maxLength={80} />
                <button className="primary-action" disabled={busy || loadingForkState || !value || isNoop}>Create fork</button>
              </form>
            </>
          ) : <p className="empty-copy">No checkpoint is available.</p>}
        </div>
      </div>
      <div className="compare-panel">
        <div className="compare-controls">
          <h3>Deterministic plan comparison</h3>
          <label htmlFor="compare-a">Checkpoint A</label>
          <select id="compare-a" value={compareA} onChange={(event) => setCompareA(event.target.value)}><option value="">Select…</option>{checkpoints.map((item) => <option key={item.checkpoint_id} value={item.checkpoint_id}>{item.node_id} · {shortId(item.checkpoint_id)}</option>)}</select>
          <label htmlFor="compare-b">Checkpoint B</label>
          <select id="compare-b" value={compareB} onChange={(event) => setCompareB(event.target.value)}><option value="">Select…</option>{checkpoints.map((item) => <option key={item.checkpoint_id} value={item.checkpoint_id}>{item.node_id} · {shortId(item.checkpoint_id)}</option>)}</select>
          <button disabled={busy || !compareA || !compareB || compareA === compareB} onClick={() => onCompare(compareA, compareB)}>Compare plans</button>
          <p className="microcopy">The original final plan remains immutable when replaying or forking.</p>
        </div>
        <div className="comparison-results" aria-live="polite">
          {!comparison ? <p className="empty-copy">Choose two checkpoints to view server-produced deterministic differences.</p> : comparisonFields.map((name) => {
            const compared = comparison[name];
            return (
              <article key={name} className={compared.changed ? "changed" : "unchanged"}>
                <h4>{humanise(name)}<span>{compared.changed ? "Changed" : "Unchanged"}</span></h4>
                <div><pre>{JSON.stringify(compared.checkpoint_a, null, 2)}</pre><pre>{JSON.stringify(compared.checkpoint_b, null, 2)}</pre></div>
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}
