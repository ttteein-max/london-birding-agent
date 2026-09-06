import { useEffect, useMemo, useState, type FormEvent } from "react";
import type {
  CheckpointSummary,
  ForkRunRequest,
  HistoryView,
  PlanComparison,
  StateView,
} from "../api/contracts";
import { compactDate, compareCreatedAt, humanise, shortId } from "../utils";

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

type ComparisonFieldName =
  | "request_constraints"
  | "selected_taxon"
  | "evidence_outcome"
  | "weather_status"
  | "plan_status"
  | "recommended_site_ids"
  | "contextual_site_ids"
  | "limitations"
  | "provenance_sources"
  | "applied_user_decisions"
  | "route_status"
  | "selected_route_site_id"
  | "total_walking_distance_km"
  | "remaining_field_time_minutes"
  | "route_constraints";

const COMPARISON_FIELDS: readonly ComparisonFieldName[] = [
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
  "route_status",
  "selected_route_site_id",
  "total_walking_distance_km",
  "remaining_field_time_minutes",
  "route_constraints",
];

const FIELDS = [
  ["search_radius_km", "Search radius (km)"],
  ["seasonal_window_radius_months", "Seasonal window (months)"],
  ["target_month_override", "Target month override"],
  ["target_local_date", "Target local date"],
  ["rain_preference", "Rain preference"],
  ["duration_hours", "Total outing duration (hours)"],
  ["maximum_walking_distance_km", "Maximum round-trip walking (km)"],
  ["selected_related_taxon_key", "Validated related taxon key"],
] as const;

type Branch = NonNullable<HistoryView["branches"]>[number];

const FORK_COMPARISON_LABELS: Record<string, { label: string; unit?: string }> = {
  search_radius_km: { label: "Search radius", unit: " km" },
  seasonal_window_radius_months: { label: "Seasonal window", unit: " months" },
  target_month_override: { label: "Target month" },
  target_local_date: { label: "Target date" },
  rain_preference: { label: "Rain preference" },
  duration_hours: { label: "Outing duration", unit: " hours" },
  maximum_walking_distance_km: { label: "Maximum walking distance", unit: " km" },
  selected_related_taxon_key: { label: "Related taxon" },
};

function branchDisplayName(branch: Branch, branches: Branch[]): string {
  if (!branch.parent_branch_id) return "Original branch";
  const forkIndex = branches.filter((item) => item.parent_branch_id).findIndex((item) => item.branch_id === branch.branch_id);
  return `Fork branch ${forkIndex + 1}`;
}

function branchComparisonName(branch: Branch, branches: Branch[]): string {
  const type = branchDisplayName(branch, branches);
  return branch.branch_label ? `${type} “${branch.branch_label}”` : type;
}

function compareBranches(left: Branch, right: Branch): number {
  const originalOrder = Number(Boolean(left.parent_branch_id)) - Number(Boolean(right.parent_branch_id));
  return originalOrder || compareCreatedAt(left, right);
}

function branchForkSummary(branch: Branch): string {
  const entries = Object.entries(branch.fork_updates ?? {});
  if (entries.length === 0) return "original constraints";
  return entries.map(([key, value]) => {
    const field = FORK_COMPARISON_LABELS[key];
    return `${field?.label ?? humanise(key)} → ${String(value)}${field?.unit ?? ""}`;
  }).join(" · ");
}

function checkpointOptionLabel(checkpoint: CheckpointSummary, branch?: Branch): string {
  const finality = checkpoint.checkpoint_id === branch?.final_checkpoint_id ? "Final" : "Intermediate";
  return `CP ${checkpoint.graph_step ?? "—"} · ${finality} · ${humanise(checkpoint.node_id)} · ${shortId(checkpoint.checkpoint_id, 12)}`;
}

export function TimeTravelPanel({ history, comparison, busy, onInspect, onLoadState, onReplay, onFork, onCompare }: Props) {
  const checkpoints = useMemo(() => history.checkpoints ?? [], [history.checkpoints]);
  const branches = useMemo(() => [...(history.branches ?? [])].sort(compareBranches), [history.branches]);
  const [selected, setSelected] = useState(checkpoints[0]?.checkpoint_id ?? "");
  const [field, setField] = useState<(typeof FIELDS)[number][0]>("search_radius_km");
  const [value, setValue] = useState("");
  const [label, setLabel] = useState("");
  const [compareA, setCompareA] = useState("");
  const [compareB, setCompareB] = useState("");
  const [forkStates, setForkStates] = useState<Record<string, StateView | null>>({});
  const selectedCheckpoint = checkpoints.find((item) => item.checkpoint_id === selected) ?? checkpoints[0];
  const originalFinal = useMemo(
    () => branches.find((branch) => !branch.parent_branch_id)?.final_checkpoint_id,
    [branches],
  );
  const latestForkFinal = useMemo(
    () => [...branches].reverse().find((branch) => branch.parent_branch_id && branch.final_checkpoint_id)?.final_checkpoint_id,
    [branches],
  );
  const availableCheckpointIds = useMemo(
    () => new Set(checkpoints.map((checkpoint) => checkpoint.checkpoint_id)),
    [checkpoints],
  );
  const comparisonGroups = useMemo(() => {
    const groups = branches.map((branch) => ({
      branch,
      checkpoints: checkpoints.filter((checkpoint) => checkpoint.branch_id === branch.branch_id),
    })).filter((group) => group.checkpoints.length > 0);
    const branchIds = new Set(branches.map((branch) => branch.branch_id));
    const ungrouped = checkpoints.filter((checkpoint) => !branchIds.has(checkpoint.branch_id));
    return ungrouped.length > 0 ? [...groups, { branch: null, checkpoints: ungrouped }] : groups;
  }, [branches, checkpoints]);
  const resolvedCompareA = compareA && availableCheckpointIds.has(compareA) ? compareA : originalFinal ?? "";
  const resolvedCompareB = compareB && availableCheckpointIds.has(compareB) ? compareB : latestForkFinal ?? "";

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
    const numericFields = new Set(["search_radius_km", "seasonal_window_radius_months", "target_month_override", "duration_hours", "maximum_walking_distance_km", "selected_related_taxon_key"]);
    const parsed: string | number = numericFields.has(field) ? Number(value) : value;
    await onFork({
      checkpoint_id: selectedCheckpoint.checkpoint_id,
      updates: { [field]: parsed },
      branch_label: label || null,
    });
    setValue("");
  };

  const comparisonFields = comparison
    ? COMPARISON_FIELDS.filter((name) => comparison[name] != null)
      .sort((left, right) => Number(comparison[right]?.changed ?? false) - Number(comparison[left]?.changed ?? false))
    : [];
  const comparedCheckpointA = checkpoints.find((checkpoint) => checkpoint.checkpoint_id === comparison?.checkpoint_a);
  const comparedCheckpointB = checkpoints.find((checkpoint) => checkpoint.checkpoint_id === comparison?.checkpoint_b);
  const comparedBranchA = branches.find((branch) => branch.branch_id === comparedCheckpointA?.branch_id);
  const comparedBranchB = branches.find((branch) => branch.branch_id === comparedCheckpointB?.branch_id);
  const changedFieldCount = comparisonFields.filter((name) => comparison?.[name]?.changed).length;

  const renderCheckpointOptions = () => comparisonGroups.map(({ branch, checkpoints: groupedCheckpoints }) => (
    <optgroup
      key={branch?.branch_id ?? "ungrouped"}
      label={branch ? `${branchComparisonName(branch, branches)} · Branch ${shortId(branch.branch_id)} · ${branchForkSummary(branch)}` : "Other checkpoints"}
    >
      {groupedCheckpoints.map((checkpoint) => (
        <option key={checkpoint.checkpoint_id} value={checkpoint.checkpoint_id}>{checkpointOptionLabel(checkpoint, branch ?? undefined)}</option>
      ))}
    </optgroup>
  ));

  const comparedIdentity = (checkpoint: CheckpointSummary | undefined, branch: Branch | undefined, checkpointId?: string) => {
    if (!checkpoint) return `Checkpoint ${shortId(checkpointId, 12)}`;
    return `${branch ? branchComparisonName(branch, branches) : "Unknown branch"} · ${checkpointOptionLabel(checkpoint, branch)}`;
  };

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
            {checkpoints.map((checkpoint) => {
              const checkpointBranch = branches.find((branch) => branch.branch_id === checkpoint.branch_id);
              const finalLabel = checkpoint.checkpoint_id === checkpointBranch?.final_checkpoint_id
                ? checkpointBranch.parent_branch_id ? "Fork final" : "Original final"
                : null;
              return (
                <li key={checkpoint.checkpoint_id} className={checkpoint.checkpoint_id === selectedCheckpoint?.checkpoint_id ? "selected" : ""}>
                  <button onClick={() => setSelected(checkpoint.checkpoint_id)}>
                    <span className="checkpoint-step">{checkpoint.graph_step}</span>
                    <span><strong>{humanise(checkpoint.node_id)}</strong><small>{compactDate(checkpoint.created_at)} · {shortId(checkpoint.checkpoint_id, 12)}</small></span>
                    <span className="checkpoint-badges">
                      {finalLabel && <em>{finalLabel}</em>}
                      {checkpoint.interrupt_kind && <em className="waiting">HITL</em>}
                    </span>
                  </button>
                </li>
              );
            })}
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
          <p className="comparison-help">Final checkpoints are selected by default. Choose intermediate checkpoints only when you intentionally want to compare an unfinished state.</p>
          <label htmlFor="compare-a">Checkpoint A (left)</label>
          <select id="compare-a" value={resolvedCompareA} onChange={(event) => setCompareA(event.target.value)}><option value="">Select…</option>{renderCheckpointOptions()}</select>
          <label htmlFor="compare-b">Checkpoint B (right)</label>
          <select id="compare-b" value={resolvedCompareB} onChange={(event) => setCompareB(event.target.value)}><option value="">Select…</option>{renderCheckpointOptions()}</select>
          <button disabled={busy || !resolvedCompareA || !resolvedCompareB || resolvedCompareA === resolvedCompareB} onClick={() => onCompare(resolvedCompareA, resolvedCompareB)}>Compare plans</button>
          <p className="microcopy">The original final plan remains immutable when replaying or forking.</p>
        </div>
        <div className="comparison-output" aria-live="polite">
          {!comparison ? <p className="empty-copy">Choose two checkpoints to view server-produced deterministic differences.</p> : (
            <>
              <div className="comparison-key" aria-label="Compared checkpoint identities">
                <div><span>A · left</span><strong>{comparedIdentity(comparedCheckpointA, comparedBranchA, comparison.checkpoint_a)}</strong></div>
                <div><span>B · right</span><strong>{comparedIdentity(comparedCheckpointB, comparedBranchB, comparison.checkpoint_b)}</strong></div>
              </div>
              <p className="comparison-summary"><strong>{changedFieldCount} changed</strong> · {comparisonFields.length - changedFieldCount} unchanged. “Changed” means the stored values differ; it does not mean a check failed.</p>
              <div className="comparison-results">
                {comparisonFields.map((name) => {
                  const compared = comparison[name];
                  if (!compared) return null;
                  return (
                    <article key={name} className={compared.changed ? "changed" : "unchanged"}>
                      <h4>{humanise(name)}<span>{compared.changed ? "Changed" : "Unchanged"}</span></h4>
                      <div className="comparison-values">
                        <div><span className="comparison-side">A</span><pre>{JSON.stringify(compared.checkpoint_a, null, 2)}</pre></div>
                        <div><span className="comparison-side">B</span><pre>{JSON.stringify(compared.checkpoint_b, null, 2)}</pre></div>
                      </div>
                    </article>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
