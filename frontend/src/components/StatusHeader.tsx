import type { EvidenceView, HealthView, RunDetail } from "../api/contracts";
import { humanise } from "../utils";

interface Props {
  health: HealthView | null;
  run: RunDetail | null;
  evidence: EvidenceView | null;
  connection: "idle" | "connected" | "reconnecting" | "closed";
  operationStatus: RunDetail["run"]["status"] | null;
}

export function StatusHeader({ health, run, evidence, connection, operationStatus }: Props) {
  const status = operationStatus ?? run?.run.status ?? "ready";
  const gate = evidence?.status ?? "pending";
  return (
    <header className="topbar">
      <div className="brand-lockup">
        <div className="brand-mark" aria-hidden="true">LB</div>
        <div>
          <p className="eyebrow">Cartographic field notebook</p>
          <h1>London Birding Agent</h1>
        </div>
      </div>
      <div className="status-cluster" aria-label="Run status overview">
        <span className="mode-chip">
          <span>Data</span> {run?.run.data_mode ?? health?.default_data_mode ?? "fixture"}
        </span>
        <span className="mode-chip">
          <span>Model</span> {run?.run.model_mode ?? health?.default_model_mode ?? "scripted"}
        </span>
        <span className={`status-pill status-${status}`}>
          <i aria-hidden="true" /> {humanise(status)}
        </span>
        <span className={`gate-pill gate-${gate}`}>Evidence · {humanise(gate)}</span>
        {connection === "reconnecting" && (
          <span className="reconnect-note" role="status">Reconnecting live trace…</span>
        )}
      </div>
    </header>
  );
}
