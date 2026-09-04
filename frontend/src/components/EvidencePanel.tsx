import type { EvidenceView } from "../api/contracts";
import { humanise } from "../utils";
import { WeatherCard } from "./WeatherCard";

interface Props { evidence: EvidenceView }

const EVIDENCE_REASON: Record<string, string> = {
  strong_gate_met: "The bounded historical sample passes all three deterministic ranking checks.",
  below_strong_gate: "Some historical context was retained, but at least one deterministic ranking check did not pass.",
  isolated_records_only: "Only isolated historical records were retained; they are not sufficient for site ranking.",
  no_retained_records: "No records remained after the deterministic quality filters.",
};

function FunnelStage({
  level,
  label,
  value,
  detail,
}: {
  level: number;
  label: string;
  value: number;
  detail: string;
}) {
  return (
    <li className={`funnel-level-${level}`}>
      <span>{label}</span>
      <strong>{value.toLocaleString("en-GB")}</strong>
      <small>{detail}</small>
    </li>
  );
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
          <span className="gate-seal">{evidence.gate.passed ? "Gate passed" : "Gate not passed"}</span>
        </div>
        <p className="card-intro">{(evidence.reason && EVIDENCE_REASON[evidence.reason]) ?? evidence.reason ?? "Evidence collection has not reached this checkpoint."}</p>
        <div className="funnel-heading">
          <strong>GBIF data funnel</strong>
          <span>Remote result → evidence safe for ranking</span>
        </div>
        <ol className="evidence-funnel" aria-label="GBIF evidence processing funnel">
          <FunnelStage level={0} label="Server matches" value={counts.server_match_count} detail="Remote query matches; not downloaded in full" />
          <FunnelStage level={1} label="Bounded sample" value={counts.sampled_count} detail={`Policy cap selected ${counts.sampled_count.toLocaleString("en-GB")} records`} />
          <FunnelStage level={2} label="Deduplicated" value={counts.deduplicated_count} detail={`${counts.exact_duplicates_removed.toLocaleString("en-GB")} exact duplicates removed`} />
          <FunnelStage level={3} label="Quality retained" value={counts.retained_count} detail={`${counts.rejected_count.toLocaleString("en-GB")} records rejected by quality rules`} />
          <FunnelStage level={4} label="Ranking eligible" value={counts.ranking_eligible_count} detail={`${Math.max(0, counts.retained_count - counts.ranking_eligible_count).toLocaleString("en-GB")} retained for context only`} />
        </ol>
        <p className="count-caveat">{evidence.server_match_note}</p>
        <div className="gate-checklist" aria-label="Strong evidence gate checklist">
          <div className="gate-checklist-title">
            <strong>Strong-evidence gate</strong>
            <span>All three deterministic checks must pass</span>
          </div>
          <ul>
            {evidence.gate.criteria.map((criterion) => (
              <li key={criterion.key} className={criterion.passed ? "passed" : "failed"}>
                <span className="gate-check-mark" aria-hidden="true">{criterion.passed ? "✓" : "×"}</span>
                <span>{criterion.label}</span>
                <strong>{criterion.value.toLocaleString("en-GB")} / {criterion.minimum.toLocaleString("en-GB")} min</strong>
              </li>
            ))}
          </ul>
        </div>
        <dl className="evidence-facts">
          <div><dt>Season</dt><dd>Month {evidence.seasonal_target_month ?? "—"} · window {(evidence.seasonal_months ?? []).join(", ") || "—"}</dd></div>
          <div><dt>Years</dt><dd>{evidence.year_window?.join("–") ?? "—"}</dd></div>
          <div><dt>Dataset diversity</dt><dd>{quality.retained_dataset_count} retained · {quality.ranking_dataset_count} ranking</dd></div>
          <div><dt>Ranking cells</dt><dd>{quality.spatial_cell_count} distinct 1 km cells used by the strong-evidence spread check</dd></div>
          <div><dt>Published safe cells</dt><dd>{quality.safe_map_cell_count} cells with at least 3 eligible records; only these can ground candidate sites</dd></div>
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
