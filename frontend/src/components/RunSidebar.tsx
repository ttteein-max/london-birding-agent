import type { RunDetail, RunSummary } from "../api/contracts";
import { compactDate, compareCreatedAt, humanise, shortId } from "../utils";

interface Props {
  runs: RunSummary[];
  selectedThread: string | null;
  detail: RunDetail | null;
  onSelect: (threadId: string) => void;
}

type Branch = NonNullable<RunDetail["branches"]>[number];
type Execution = NonNullable<RunDetail["executions"]>[number];

const FORK_FIELD_LABELS: Record<string, { label: string; unit?: string }> = {
  search_radius_km: { label: "Search radius", unit: " km" },
  seasonal_window_radius_months: { label: "Seasonal window", unit: " months" },
  target_month_override: { label: "Target month" },
  target_local_date: { label: "Target date" },
  rain_preference: { label: "Rain preference" },
  duration_hours: { label: "Outing duration", unit: " hours" },
  maximum_walking_distance_km: { label: "Maximum walking distance", unit: " km" },
  selected_related_taxon_key: { label: "Related taxon" },
};

function BranchIcon() {
  return (
    <span className="branch-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24" focusable="false">
        <circle cx="6" cy="5" r="2" />
        <circle cx="18" cy="7" r="2" />
        <circle cx="6" cy="19" r="2" />
        <path d="M6 7v10M8 12h3c4 0 4-5 5-5" />
      </svg>
    </span>
  );
}

function branchName(branch: Branch, branches: Branch[]): string {
  if (!branch.parent_branch_id) return "Original branch";
  const forkIndex = branches.filter((item) => item.parent_branch_id).findIndex((item) => item.branch_id === branch.branch_id);
  return `Fork branch ${forkIndex + 1}`;
}

function branchTitle(branch: Branch, branches: Branch[]): string {
  return branch.branch_label || branchName(branch, branches);
}

function compareBranches(left: Branch, right: Branch): number {
  const originalOrder = Number(Boolean(left.parent_branch_id)) - Number(Boolean(right.parent_branch_id));
  return originalOrder || compareCreatedAt(left, right);
}

function forkSummary(branch: Branch): string {
  const entries = Object.entries(branch.fork_updates ?? {});
  if (entries.length === 0) return "Original constraint trajectory";
  return entries.map(([key, value]) => {
    const field = FORK_FIELD_LABELS[key];
    return `${field?.label ?? humanise(key)} → ${String(value)}${field?.unit ?? ""}`;
  }).join(" · ");
}

function executionNumber(execution: Execution, executions: Execution[]): number {
  return executions.findIndex((item) => item.execution_id === execution.execution_id) + 1;
}

function lineageStatus(status?: string | null, waiting = false): string {
  if (waiting) return "Waiting";
  if (status === "completed_with_deterministic_fallback") return "Completed";
  return humanise(status);
}

export function RunSidebar({ runs, selectedThread, detail, onSelect }: Props) {
  const branches = [...(detail?.branches ?? [])].sort(compareBranches);
  const executions = [...(detail?.executions ?? [])].sort(compareCreatedAt);

  return (
    <aside className="run-sidebar" aria-label="Run and lineage navigator">
      <div className="sidebar-section run-index-section">
        <div className="section-heading compact">
          <div>
            <p className="eyebrow">Notebook index</p>
            <h2>Recent runs</h2>
          </div>
          <span className="count-badge">{runs.length}</span>
        </div>
        {runs.length === 0 ? (
          <p className="empty-copy">No saved runs yet. Start with a fixture example.</p>
        ) : (
          <ul className="run-list">
            {runs.map((run) => (
              <li key={run.thread_id}>
                <button
                  className={run.thread_id === selectedThread ? "selected" : ""}
                  onClick={() => onSelect(run.thread_id)}
                  aria-current={run.thread_id === selectedThread ? "page" : undefined}
                >
                  <span className="run-title">{run.thread_id}</span>
                  <span className="run-summary-line">
                    <span>{humanise(run.status)} · {compactDate(run.updated_at)}</span>
                    <span className="run-mode-label">{run.data_mode}/{run.model_mode}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {detail && (
        <div className="sidebar-section lineage-section" aria-label="Selected run branch lineage">
          <div className="section-heading compact lineage-heading">
            <div>
              <p className="eyebrow">Selected run</p>
              <h2>Branches &amp; executions</h2>
            </div>
            <span className="count-badge">{branches.length}</span>
          </div>
          <p className="lineage-explainer">Each branch is a constraint trajectory. Its executions are nested directly underneath.</p>
          {branches.length === 0 ? (
            <p className="empty-copy">No saved branches yet.</p>
          ) : (
            <ul className="branch-tree">
              {branches.map((branch) => {
                const branchExecutions = executions.filter((execution) => execution.branch_id === branch.branch_id);
                return (
                  <li className="branch-tree-item" key={branch.branch_id}>
                    <div className="branch-row">
                      <BranchIcon />
                      <div className="branch-copy">
                        <div className="branch-title-line">
                          <strong>{branchTitle(branch, branches)}</strong>
                          <span className={branch.parent_branch_id ? "lineage-badge fork" : "lineage-badge original"}>
                            {branch.parent_branch_id ? "Fork" : "Original"}
                          </span>
                        </div>
                        <span className="branch-identity">Branch {shortId(branch.branch_id)} · {lineageStatus(branch.terminal_status)}</span>
                        <span className="branch-change-summary">{forkSummary(branch)}</span>
                      </div>
                    </div>
                    {branchExecutions.length === 0 ? (
                      <p className="execution-empty">No execution saved for this branch.</p>
                    ) : (
                      <ul className="execution-tree" aria-label={`Executions for ${branchTitle(branch, branches)}`}>
                        {branchExecutions.map((execution) => {
                          const number = executionNumber(execution, executions);
                          return (
                            <li key={execution.execution_id}>
                              <span className="execution-icon" aria-hidden="true">{number}</span>
                              <div>
                                <strong>Execution {number}</strong>
                                <span>Execution {shortId(execution.execution_id)} · {lineageStatus(execution.terminal_status, Boolean(execution.interrupt_kind))}</span>
                              </div>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </aside>
  );
}
