import type { RunDetail, RunSummary } from "../api/contracts";
import { compactDate, humanise, shortId } from "../utils";

interface Props {
  runs: RunSummary[];
  selectedThread: string | null;
  detail: RunDetail | null;
  onSelect: (threadId: string) => void;
}

export function RunSidebar({ runs, selectedThread, detail, onSelect }: Props) {
  return (
    <aside className="run-sidebar" aria-label="Run and lineage navigator">
      <div className="sidebar-section">
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
                  <span>{humanise(run.status)} · {compactDate(run.updated_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {detail && (
        <>
          <div className="sidebar-section lineage-section">
            <p className="eyebrow">Branches</p>
            <ul className="identity-list">
              {(detail.branches ?? []).map((branch, index) => (
                <li key={branch.branch_id}>
                  <span className="identity-icon" aria-hidden="true">⑂</span>
                  <div>
                    <strong>{!branch.parent_branch_id ? "Original branch" : `Branch ${index + 1}`}</strong>
                    <span>{shortId(branch.branch_id)} · {humanise(branch.terminal_status)}</span>
                  </div>
                </li>
              ))}
            </ul>
          </div>
          <div className="sidebar-section lineage-section">
            <p className="eyebrow">Executions</p>
            <ul className="identity-list">
              {(detail.executions ?? []).map((execution, index) => (
                <li key={execution.execution_id}>
                  <span className="identity-icon" aria-hidden="true">{index + 1}</span>
                  <div>
                    <strong>Execution {index + 1}</strong>
                    <span>{shortId(execution.execution_id)} · {execution.interrupt_kind ? "Waiting" : humanise(execution.terminal_status)}</span>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </aside>
  );
}
