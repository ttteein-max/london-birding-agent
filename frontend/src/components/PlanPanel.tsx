import type { FinalPlanView } from "../api/contracts";
import { humanise } from "../utils";

interface Props { plan: FinalPlanView }

function SiteList({ sites, contextual = false }: { sites: FinalPlanView["recommended_sites"]; contextual?: boolean }) {
  const values = sites ?? [];
  if (values.length === 0) return <p className="empty-copy">None at this evidence gate.</p>;
  return (
    <ol className="site-list">
      {values.map((site) => (
        <li key={site.site_id}>
          <span className={`site-index ${contextual ? "contextual" : "candidate"}`} aria-hidden="true">{contextual ? "C" : "E"}</span>
          <div>
            <strong>{site.name}</strong>
            <span>{site.approximate_straight_line_distance_km.toFixed(2)} km straight-line · {humanise(site.access_certainty)}</span>
            <small>{contextual ? "Context only — not a recommendation" : "Evidence-grounded candidate site"}</small>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function PlanPanel({ plan }: Props) {
  return (
    <section className="plan-panel" aria-labelledby="plan-title">
      <div className="plan-hero">
        <div>
          <p className="eyebrow">Validated field plan</p>
          <h2 id="plan-title">{plan.target_species}</h2>
          <p>{plan.target_date} · {plan.duration_hours} hours · {plan.resolved_london_start_context}</p>
        </div>
        <span className={`plan-gate ${plan.evidence_gate_passed ? "passed" : "held"}`}>
          {plan.evidence_gate_passed ? "Evidence gate passed" : "Evidence gate held"}
        </span>
      </div>
      {plan.low_confidence_notice && <div className="low-confidence" role="note">{plan.low_confidence_notice}</div>}
      <p className="plan-explanation">{plan.explanation}</p>
      <p className={`generated-note ${plan.generated_by === "deterministic_fallback" ? "fallback" : ""}`}>
        {plan.generated_by === "deterministic_fallback"
          ? "Deterministic fallback plan"
          : `Structured plan · ${humanise(plan.generated_by)}`}
      </p>
      <div className="plan-columns">
        <div>
          <div className="list-heading"><h3>Evidence-grounded candidate sites</h3><span>{(plan.recommended_sites ?? []).length}</span></div>
          <SiteList sites={plan.recommended_sites} />
        </div>
        <div>
          <div className="list-heading"><h3>Contextual sites</h3><span>{(plan.contextual_sites ?? []).length}</span></div>
          <SiteList sites={plan.contextual_sites} contextual />
        </div>
      </div>
      <details className="limitations" open>
        <summary>Limitations & field checks</summary>
        <ul>{(plan.unresolved_limitations ?? []).map((item) => <li key={item}>{item}</li>)}</ul>
      </details>
      <div className="attribution-row">{(plan.evidence_attributions ?? []).map((item) => <span key={item}>{item}</span>)}</div>
    </section>
  );
}
