import { useState, type FormEvent } from "react";
import type { PendingDecisionView, ResumeRunRequest } from "../api/contracts";
import { humanise } from "../utils";

interface Props {
  decision: PendingDecisionView;
  busy: boolean;
  onResume: (request: ResumeRunRequest) => Promise<void>;
}

const MONTH_NAMES = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

function monthNames(months: number[] | undefined): string {
  return (months ?? []).map((month) => MONTH_NAMES[month - 1]).filter(Boolean).join(", ");
}

type TaxonEvidencePreview = NonNullable<
  NonNullable<PendingDecisionView["candidates"]>[number]["evidence_preview"]
>;

function evidencePreviewSummary(preview: TaxonEvidencePreview): string {
  if (preview.status === "not_evaluated_budget") {
    return "Not evaluated · preview budget exhausted";
  }
  if (preview.status === "source_failure") {
    return "Source failure · evidence unavailable";
  }
  const retained = preview.retained_count == null
    ? "retained count unavailable"
    : `${preview.retained_count.toLocaleString("en-GB")} retained`;
  const datasets = preview.dataset_count == null
    ? "dataset count unavailable"
    : `${preview.dataset_count.toLocaleString("en-GB")} datasets`;
  return `${humanise(preview.status)} · ${retained} · ${datasets}`;
}

export function HitlPanel({ decision, busy, onResume }: Props) {
  const draft = decision.parsed_draft;
  const [postcode, setPostcode] = useState(draft?.postcode ?? "");
  const [locationQuery, setLocationQuery] = useState(draft?.location_query ?? "");
  const [bird, setBird] = useState(draft?.bird_input ?? "");
  const [date, setDate] = useState(draft?.target_local_date ?? "");
  const [duration, setDuration] = useState(
    draft?.duration_hours == null ? "" : String(draft.duration_hours),
  );
  const [radius, setRadius] = useState("8");
  const routeMinimum = Math.max(
    0,
    ...(decision.options ?? []).map((option) => option.minimum_walking_distance_km ?? 0),
  );
  const routeMinimumInput = Math.ceil(routeMinimum * 10) / 10;
  const [walkingLimit, setWalkingLimit] = useState(
    routeMinimumInput ? routeMinimumInput.toFixed(1) : "10",
  );

  const base = { checkpoint_id: decision.checkpoint_id };

  const clarification = async (event: FormEvent) => {
    event.preventDefault();
    const updates: Record<string, string | number | null> = {};
    const nextPostcode = postcode.trim();
    const nextLocationQuery = locationQuery.trim();
    const nextBird = bird.trim();

    if (nextPostcode) {
      if (nextPostcode !== (draft?.postcode ?? "")) updates.postcode = nextPostcode;
      if (draft?.location_query) updates.location_query = null;
    } else if (nextLocationQuery) {
      if (nextLocationQuery !== (draft?.location_query ?? "")) {
        updates.location_query = nextLocationQuery;
      }
      if (draft?.postcode) updates.postcode = null;
    }
    if (nextBird && nextBird !== (draft?.bird_input ?? "")) {
      updates.bird_input = nextBird;
    }
    if (date && date !== (draft?.target_local_date ?? "")) {
      updates.target_local_date = date;
    }
    const initialDuration = draft?.duration_hours;
    if (duration && Number(duration) !== initialDuration) {
      updates.duration_hours = Number(duration);
    }
    await onResume({
      ...base,
      decision: { kind: "request_clarification", updates },
    });
  };

  const options = decision.options ?? [];
  const locationCandidates = decision.location_candidates ?? [];
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
                  <small>{evidencePreviewSummary(candidate.evidence_preview)}</small>
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
                const years = option.year_window?.join("–") ?? "the same multi-year window";
                const currentMonths = monthNames(option.current_seasonal_months);
                const nextMonths = monthNames(option.next_seasonal_months);
                return (
                  <button key={option.option} disabled={busy} onClick={() => onResume({ ...base, decision: { kind: "actionable_tradeoff", option: "widen_seasonal_window", seasonal_window_radius_months: next } })}>
                    <strong>Widen seasonal window</strong>
                    <span>Months: {currentMonths || "current window"} → {nextMonths || "adjacent calendar months"}</span>
                    <span>Re-run at ±{next} months across historical years {years}</span>
                    <small>Current query: {(option.current_server_match_count ?? 0).toLocaleString("en-GB")} server matches · {(option.current_ranking_eligible_count ?? 0).toLocaleString("en-GB")} ranking eligible. Server matches are not abundance.</small>
                  </button>
                );
              }
              return <button key={option.option} disabled={busy} onClick={() => onResume({ ...base, decision: { kind: "actionable_tradeoff", option: option.option as "consider_related_taxa" | "keep_constraints_accept_low_confidence" | "accept_context_only" | "continue_with_weather_acknowledgement" | "accept_uncertain_access" | "revise_rain_preference" } })}><strong>{humanise(option.option)}</strong><span>{option.option === "keep_constraints_accept_low_confidence" ? "Continue with a non-recommendation result" : "Apply this validated decision"}</span></button>;
            })}
          </div>
        )}

        {decision.kind === "route_tradeoff" && (
          <div className="tradeoff-grid" aria-label="Walking route trade-off choices">
            {options.map((option) => option.option === "increase_maximum_walking_distance" ? (
              <form key={option.option} onSubmit={(event) => {
                event.preventDefault();
                void onResume({
                  ...base,
                  decision: {
                    kind: "route_tradeoff",
                    option: "increase_maximum_walking_distance",
                    maximum_walking_distance_km: Number(walkingLimit),
                  },
                });
              }}>
                <label htmlFor="route-walking-limit">Maximum full-excursion walking distance (km)</label>
                <input id="route-walking-limit" type="number" min={Math.ceil((option.minimum_walking_distance_km ?? 0.1) * 10) / 10} max="50" step="0.1" value={walkingLimit} onChange={(event) => setWalkingLimit(event.target.value)} />
                <small>Minimum computed feasible round trip: {option.minimum_walking_distance_km?.toFixed(2)} km.</small>
                <button className="primary-action" disabled={busy}>Recalculate routes</button>
              </form>
            ) : (
              <button key={option.option} disabled={busy} onClick={() => onResume({
                ...base,
                decision: {
                  kind: "route_tradeoff",
                  option: option.option as "accept_uncertain_entrance" | "keep_route_constraints_and_end",
                },
              })}>
                <strong>{humanise(option.option)}</strong>
                <span>{option.option === "accept_uncertain_entrance" ? "Route to a mapped entrance while keeping access uncertainty explicit" : "Keep the supplied constraints and finish without a fabricated route"}</span>
              </button>
            ))}
          </div>
        )}

        {decision.kind === "location_correction" && (
          <>
            {locationCandidates.length > 0 && (
              <div className="candidate-choice-grid location-choice-grid" aria-label="Verified Greater London place matches">
                {locationCandidates.map((candidate) => (
                  <button
                    key={candidate.candidate_id}
                    disabled={busy}
                    aria-label={`Use ${candidate.label}${candidate.postcode ? ` ${candidate.postcode}` : ""}`}
                    onClick={() => onResume({
                      ...base,
                      decision: {
                        kind: "location_correction",
                        candidate_id: candidate.candidate_id,
                      },
                    })}
                  >
                    <strong>{candidate.label}</strong>
                    <span>{[candidate.locality, candidate.administrative_district].filter(Boolean).join(" · ")}</span>
                    <span>{candidate.postcode ?? "No postcode returned"} · {humanise(candidate.place_type ?? candidate.category ?? "place")}</span>
                    <small>Representative planning point — not an entrance or walking route</small>
                  </button>
                ))}
              </div>
            )}
            {locationCandidates.length > 0 && (
              <p className="geocoder-attribution">Place search © OpenStreetMap contributors · Nominatim</p>
            )}
            <form className="hitl-form" onSubmit={(event) => { event.preventDefault(); void onResume({ ...base, decision: { kind: "location_correction", postcode } }); }}>
              <label htmlFor="corrected-postcode">Or enter a London postcode</label>
              <input id="corrected-postcode" value={postcode} onChange={(event) => setPostcode(event.target.value)} required />
              <button className="primary-action" disabled={busy}>Validate postcode</button>
            </form>
          </>
        )}

        {decision.kind === "bird_input_correction" && (
          <form className="hitl-form" onSubmit={(event) => { event.preventDefault(); void onResume({ ...base, decision: { kind: "bird_input_correction", bird_input: bird } }); }}>
            <label htmlFor="corrected-bird">Bird common or scientific name</label>
            <input id="corrected-bird" value={bird} onChange={(event) => setBird(event.target.value)} required />
            <button className="primary-action" disabled={busy}>Validate bird input</button>
          </form>
        )}

        {decision.kind === "request_clarification" && (
          <>
            {draft && (
              <div className="recognised-request" aria-label="Recognised request details">
                <span><strong>Start</strong>{draft.location_query ?? draft.postcode ?? (draft.has_explicit_start_point ? "Explicit map point" : "Needs input")}</span>
                <span><strong>Bird</strong>{draft.bird_input ?? "Needs input"}</span>
                <span><strong>Date</strong>{draft.target_local_date ?? "Needs input"}</span>
                <span><strong>Duration</strong>{draft.duration_hours == null ? "Needs input" : `${draft.duration_hours} hours`}</span>
              </div>
            )}
            <form className="hitl-form clarification-grid" autoComplete="off" onSubmit={clarification}>
              <label htmlFor="clarify-place">Named London place
                <input id="clarify-place" value={locationQuery} onChange={(event) => setLocationQuery(event.target.value)} placeholder={draft?.has_explicit_start_point ? "Explicit map point already retained" : "e.g. Rainham Marshes"} />
                <small>Kept and verified after this decision.</small>
              </label>
              <label htmlFor="clarify-postcode">Postcode override · optional
                <input id="clarify-postcode" value={postcode} onChange={(event) => setPostcode(event.target.value)} placeholder="Only if you prefer a postcode" />
              </label>
              <label htmlFor="clarify-bird">Bird name<input id="clarify-bird" value={bird} onChange={(event) => setBird(event.target.value)} /></label>
              <label htmlFor="clarify-date">Target date<input id="clarify-date" type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label>
              <label htmlFor="clarify-duration">Duration hours<input id="clarify-duration" type="number" min="0.5" max="24" step="0.5" value={duration} onChange={(event) => setDuration(event.target.value)} /></label>
              <button className="primary-action" disabled={busy}>Apply corrections</button>
            </form>
          </>
        )}
      </div>
    </section>
  );
}
