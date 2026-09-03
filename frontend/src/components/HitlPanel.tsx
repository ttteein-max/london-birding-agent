import { useState, type FormEvent } from "react";
import type { PendingDecisionView, ResumeRunRequest } from "../api/contracts";
import { humanise } from "../utils";

interface Props {
  decision: PendingDecisionView;
  busy: boolean;
  onResume: (request: ResumeRunRequest) => Promise<void>;
}

export function HitlPanel({ decision, busy, onResume }: Props) {
  const [postcode, setPostcode] = useState("");
  const [bird, setBird] = useState("");
  const [date, setDate] = useState("");
  const [duration, setDuration] = useState("");
  const [radius, setRadius] = useState("8");

  const base = { checkpoint_id: decision.checkpoint_id };

  const clarification = async (event: FormEvent) => {
    event.preventDefault();
    const updates: Record<string, string | number> = {};
    if (postcode.trim()) updates.postcode = postcode.trim();
    if (bird.trim()) updates.bird_input = bird.trim();
    if (date) updates.target_local_date = date;
    if (duration) updates.duration_hours = Number(duration);
    await onResume({
      ...base,
      decision: { kind: "request_clarification", updates },
    });
  };

  const options = decision.options ?? [];
  const taxonomyDecisionKind =
    decision.kind === "taxon_selection" || decision.kind === "related_taxon_selection"
      ? decision.kind
      : null;
  return (
    <section className="hitl-panel" aria-labelledby="hitl-title">
      <div className="hitl-signal" aria-hidden="true"><span>Human</span><strong>Decision</strong></div>
      <div className="hitl-content">
        <p className="eyebrow">Execution paused safely</p>
        <h2 id="hitl-title">{humanise(decision.kind)}</h2>
        <p>{decision.question}</p>
        {decision.status && <p className="hitl-reason">Status · {humanise(decision.status)}</p>}
        {decision.rationale && <p className="hitl-reason">{decision.rationale}</p>}
        {(decision.validation_errors ?? []).length > 0 && (
          <ul className="validation-list">{decision.validation_errors?.map((item) => <li key={item}>{item}</li>)}</ul>
        )}

        {taxonomyDecisionKind && (
          <div className="candidate-choice-grid">
            {(decision.candidates ?? []).map((candidate) => (
              <button
                key={candidate.accepted_taxon_key}
                disabled={busy}
                onClick={() => onResume({
                  ...base,
                  decision: {
                    kind: taxonomyDecisionKind,
                    accepted_taxon_key: candidate.accepted_taxon_key,
                  },
                })}
              >
                <strong>{candidate.common_name ?? candidate.canonical_name}</strong>
                <em>{candidate.scientific_name}</em>
                <span>{candidate.rank} · GBIF {candidate.accepted_taxon_key}</span>
                {candidate.relation_level && <span>{humanise(candidate.relation_level)} · not an ecological substitute</span>}
                {candidate.evidence_preview && (
                  <small>
                    {humanise(candidate.evidence_preview.status)} · {candidate.evidence_preview.retained_count ?? 0} retained · {candidate.evidence_preview.dataset_count ?? 0} datasets
                  </small>
                )}
              </button>
            ))}
          </div>
        )}

        {decision.kind === "actionable_tradeoff" && (
          <div className="tradeoff-grid">
            {options.map((option) => {
              if (option.option === "expand_search_radius") {
                return (
                  <form key={option.option} onSubmit={(event) => { event.preventDefault(); void onResume({ ...base, decision: { kind: "actionable_tradeoff", option: "expand_search_radius", search_radius_km: Number(radius) } }); }}>
                    <label htmlFor="hitl-radius">Expanded search radius (km)</label>
                    <input id="hitl-radius" type="number" min={(option.current_radius_km ?? 0) + 0.1} max={option.maximum_radius_km ?? 25} step="0.1" value={radius} onChange={(event) => setRadius(event.target.value)} />
                    <button className="primary-action" disabled={busy}>Apply radius</button>
                  </form>
                );
              }
              if (option.option === "widen_seasonal_window") {
                const next = Math.min(option.maximum_radius_months ?? 3, (option.current_radius_months ?? 1) + 1);
                return <button key={option.option} disabled={busy} onClick={() => onResume({ ...base, decision: { kind: "actionable_tradeoff", option: "widen_seasonal_window", seasonal_window_radius_months: next } })}><strong>Widen seasonal window</strong><span>Re-run bounded occurrence evidence at ±{next} months</span></button>;
              }
              return <button key={option.option} disabled={busy} onClick={() => onResume({ ...base, decision: { kind: "actionable_tradeoff", option: option.option as "consider_related_taxa" | "keep_constraints_accept_low_confidence" | "accept_context_only" | "continue_with_weather_acknowledgement" | "accept_uncertain_access" | "revise_rain_preference" } })}><strong>{humanise(option.option)}</strong><span>{option.option === "keep_constraints_accept_low_confidence" ? "Continue with a non-recommendation result" : "Apply this validated decision"}</span></button>;
            })}
          </div>
        )}

        {decision.kind === "location_correction" && (
          <form className="hitl-form" onSubmit={(event) => { event.preventDefault(); void onResume({ ...base, decision: { kind: "location_correction", postcode } }); }}>
            <label htmlFor="corrected-postcode">London postcode</label>
            <input id="corrected-postcode" value={postcode} onChange={(event) => setPostcode(event.target.value)} required />
            <button className="primary-action" disabled={busy}>Validate location</button>
          </form>
        )}

        {decision.kind === "bird_input_correction" && (
          <form className="hitl-form" onSubmit={(event) => { event.preventDefault(); void onResume({ ...base, decision: { kind: "bird_input_correction", bird_input: bird } }); }}>
            <label htmlFor="corrected-bird">Bird common or scientific name</label>
            <input id="corrected-bird" value={bird} onChange={(event) => setBird(event.target.value)} required />
            <button className="primary-action" disabled={busy}>Validate bird input</button>
          </form>
        )}

        {decision.kind === "request_clarification" && (
          <form className="hitl-form clarification-grid" onSubmit={clarification}>
            <label htmlFor="clarify-postcode">London postcode<input id="clarify-postcode" value={postcode} onChange={(event) => setPostcode(event.target.value)} /></label>
            <label htmlFor="clarify-bird">Bird name<input id="clarify-bird" value={bird} onChange={(event) => setBird(event.target.value)} /></label>
            <label htmlFor="clarify-date">Target date<input id="clarify-date" type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label>
            <label htmlFor="clarify-duration">Duration hours<input id="clarify-duration" type="number" min="0.5" max="24" step="0.5" value={duration} onChange={(event) => setDuration(event.target.value)} /></label>
            <button className="primary-action" disabled={busy}>Apply corrections</button>
          </form>
        )}
      </div>
    </section>
  );
}
