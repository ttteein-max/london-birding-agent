import type { EvidenceView } from "../api/contracts";
import { humanise } from "../utils";
import { WeatherCard } from "./WeatherCard";

interface Props { evidence: EvidenceView }

function Metric({ label, value }: { label: string; value: number }) {
  return <div className="metric"><span>{label}</span><strong>{value.toLocaleString("en-GB")}</strong></div>;
}

export function EvidencePanel({ evidence }: Props) {
  const counts = evidence.counts;
  const quality = evidence.quality;
  const warnings = quality.warnings ?? [];
  return (
    <div className="evidence-stack">
      <section className={`evidence-card evidence-${evidence.status}`} aria-labelledby="evidence-title">
        <div className="card-title-row">
          <div>
            <p className="eyebrow">Historical occurrence evidence</p>
            <h3 id="evidence-title">{humanise(evidence.status)} evidence</h3>
          </div>
          <span className="gate-seal">{evidence.status === "strong" ? "Gate passed" : "Gate not passed"}</span>
        </div>
        <p className="card-intro">{evidence.reason ?? "Evidence collection has not reached this checkpoint."}</p>
        <div className="metrics-grid">
          <Metric label="Server matches" value={counts.server_match_count} />
          <Metric label="Sampled" value={counts.sampled_count} />
          <Metric label="Deduplicated" value={counts.deduplicated_count} />
          <Metric label="Retained" value={counts.retained_count} />
          <Metric label="Ranking eligible" value={counts.ranking_eligible_count} />
          <Metric label="Ranking datasets" value={quality.ranking_dataset_count} />
        </div>
        <p className="count-caveat">{evidence.server_match_note}</p>
        <dl className="evidence-facts">
          <div><dt>Season</dt><dd>Month {evidence.seasonal_target_month ?? "—"} · window {(evidence.seasonal_months ?? []).join(", ") || "—"}</dd></div>
          <div><dt>Years</dt><dd>{evidence.year_window?.join("–") ?? "—"}</dd></div>
          <div><dt>Dataset diversity</dt><dd>{quality.retained_dataset_count} retained · {quality.ranking_dataset_count} ranking</dd></div>
          <div><dt>Spatial cells</dt><dd>{quality.spatial_cell_count} evaluated; only publishable strong-gate cells reach the map</dd></div>
        </dl>
        {warnings.length > 0 && (
          <div className="warning-box"><strong>Quality notes</strong><ul>{warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div>
        )}
      </section>

      <WeatherCard weather={evidence.weather} />

      <section className="evidence-card" aria-labelledby="constraints-title">
        <div className="card-title-row"><h3 id="constraints-title">Constraint ledger</h3><span className="count-badge">{(evidence.constraints ?? []).length}</span></div>
        {(evidence.constraints ?? []).length === 0 ? <p className="empty-copy">No deterministic constraints at this checkpoint.</p> : (
          <ul className="constraint-list">
            {(evidence.constraints ?? []).map((item) => (
              <li key={item.code} className={`constraint-${item.status}`}>
                <span className="constraint-mark" aria-hidden="true">{item.status === "satisfied" ? "✓" : item.status === "violated" ? "!" : "·"}</span>
                <div><strong>{humanise(item.code)}</strong><span>{humanise(item.status)} · {item.message}</span></div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="evidence-card" aria-labelledby="provenance-title">
        <h3 id="provenance-title">Provenance</h3>
        <ul className="provenance-list">
          {(evidence.provenance ?? []).map((item) => (
            <li key={`${item.source}-${item.source_record_type}`}>
              <strong>{item.source}</strong><span>{item.attribution}</span><small>{item.licence}</small>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
