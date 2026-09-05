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

function ElevationProfile({ samples }: { samples: NonNullable<FinalPlanView["walking_plan"]>["elevation_profile"] }) {
  if (!samples || samples.length < 2) {
    return <p className="empty-copy">Elevation samples are unavailable or incomplete.</p>;
  }
  const width = 560;
  const height = 130;
  const maximumDistance = Math.max(...samples.map((sample) => sample.distance_m), 1);
  const elevations = samples.map((sample) => sample.elevation_m);
  const minimumElevation = Math.min(...elevations);
  const maximumElevation = Math.max(...elevations);
  const elevationRange = Math.max(maximumElevation - minimumElevation, 1);
  const points = samples.map((sample) => {
    const x = 12 + (sample.distance_m / maximumDistance) * (width - 24);
    const y = height - 18 - ((sample.elevation_m - minimumElevation) / elevationRange) * (height - 36);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return (
    <figure className="elevation-profile">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="elevation-title elevation-description">
        <title id="elevation-title">Walking route elevation profile</title>
        <desc id="elevation-description">Elevation ranges from {minimumElevation.toFixed(0)} to {maximumElevation.toFixed(0)} metres across {(maximumDistance / 1000).toFixed(2)} kilometres.</desc>
        <polyline points={points} fill="none" stroke="currentColor" strokeWidth="4" strokeLinejoin="round" />
      </svg>
      <figcaption>{minimumElevation.toFixed(0)}–{maximumElevation.toFixed(0)} m elevation · {(maximumDistance / 1000).toFixed(2)} km sampled</figcaption>
    </figure>
  );
}

function isHistoricalRoute(plan: NonNullable<FinalPlanView["walking_plan"]>): boolean {
  return plan.historical_execution || (
    !plan.selection_rationale
    && plan.routing_profile === "foot-walking"
    && plan.total_travel_duration_minutes == null
  );
}

function formatMinutes(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  return Number.isInteger(rounded) ? rounded.toFixed(0) : rounded.toFixed(1);
}

function expeditionOverview(plan: FinalPlanView): string {
  if (plan.itinerary_summary) return plan.itinerary_summary;
  const route = plan.walking_plan;
  if (!route || route.status !== "ready") return plan.explanation;
  const historical = isHistoricalRoute(route);
  const totalTravel = route.total_travel_duration_minutes ?? route.walking_duration_minutes;
  const entrance = route.entrance?.label ?? "the recorded entrance";
  const sentences = [
    `This ${historical ? "historical " : ""}plan uses a ${(plan.duration_hours * 60).toFixed(0)}-minute total-outing budget, including outbound travel, field time and return travel.`,
    `The recorded ${historical ? "walking-only " : ""}round trip reaches ${route.selected_site_name} via ${entrance}.`,
  ];
  if (totalTravel != null) {
    sentences.push(
      `${formatMinutes(totalTravel)} minutes is the total return-journey travel time, not the outbound leg alone; ${route.remaining_field_time_minutes != null ? formatMinutes(route.remaining_field_time_minutes) : "no"} minutes remains for field observation.`,
    );
  }
  if (historical) {
    sentences.push(
      "This saved execution predates the current TfL public-transport-and-walking policy and is not recalculated; create a new live run for a current journey.",
    );
  }
  return sentences.join(" ");
}

function WalkingItinerary({ plan }: { plan: NonNullable<FinalPlanView["walking_plan"]> }) {
  const ready = plan.status === "ready";
  const alternatives = plan.alternative_feasible_routes ?? [];
  const multimodal = plan.journey_type === "public_transport_and_walking";
  const historical = isHistoricalRoute(plan);
  const outboundTravel = plan.outbound_travel_duration_minutes;
  const returnTravel = plan.return_travel_duration_minutes;
  const totalTravel = plan.total_travel_duration_minutes ?? plan.walking_duration_minutes;
  const displayedSegments = (plan.journey_segments ?? []).filter(
    (segment) => !plan.return_route_same_as_outbound || segment.direction === "outbound",
  );
  const historicalRationale = "This immutable historical route passed the constraints recorded by its then-current deterministic routing policy. It has not been re-ranked with the current public-transport provider.";
  return (
    <section className={`walking-itinerary route-${plan.status}`} aria-labelledby="walking-title">
      <div className="list-heading">
        <div><p className="eyebrow">Deterministic routing</p><h3 id="walking-title">Validated journey itinerary</h3></div>
        <span>{humanise(plan.status)}</span>
      </div>
      {ready ? (
        <>
          <p className="route-destination"><strong>{plan.selected_site_name}</strong><span>{plan.entrance?.label} · {humanise(plan.entrance?.access_certainty ?? "unspecified")} access evidence</span></p>
          <p className="route-selection"><strong>Why this site and entrance:</strong> {plan.selection_rationale ?? historicalRationale}</p>
          {historical && (
            <p className="historical-route-note" role="note">
              <strong>Historical walking-only execution.</strong> {plan.routing_policy_note ?? "Current live runs use TfL public transport plus walking. Create a new run to calculate a current journey; this checkpoint remains unchanged."}
            </p>
          )}
          <dl className="route-metrics">
            <div><dt>Journey</dt><dd>{historical ? "Historical walking-only" : multimodal ? "Public transport + walking" : "Walking only"}</dd></div>
            <div><dt>Outbound travel</dt><dd>{outboundTravel != null ? `${formatMinutes(outboundTravel)} min` : "Not separately recorded"}</dd></div>
            <div><dt>Return travel</dt><dd>{returnTravel != null ? `${formatMinutes(returnTravel)} min` : "Not separately recorded"}</dd></div>
            <div><dt>Total travel</dt><dd>{totalTravel != null ? `${formatMinutes(totalTravel)} min` : "—"}</dd></div>
            <div><dt>Total walking</dt><dd>{plan.total_distance_km?.toFixed(2)} km</dd></div>
            <div><dt>Walking time</dt><dd>{plan.walking_duration_minutes != null ? `${formatMinutes(plan.walking_duration_minutes)} min` : "—"}</dd></div>
            <div><dt>Field time left</dt><dd>{plan.remaining_field_time_minutes != null ? `${formatMinutes(plan.remaining_field_time_minutes)} min` : "—"}</dd></div>
            <div><dt>Ascent / descent</dt><dd>{plan.ascent_m?.toFixed(0) ?? "—"} / {plan.descent_m?.toFixed(0) ?? "—"} m</dd></div>
          </dl>
          <p className="route-budget-note">
            The {plan.expedition_duration_minutes.toFixed(0)}-minute request is treated as the complete outing: outbound travel, field time and return travel.
            {totalTravel != null && plan.remaining_field_time_minutes != null ? ` Budget check: ${formatMinutes(plan.expedition_duration_minutes)} total − ${formatMinutes(totalTravel)} travel = ${formatMinutes(plan.remaining_field_time_minutes)} minutes in the field.` : ""}
            {plan.planning_departure_time_local ? ` Planning baseline: ${plan.planning_departure_time_local}.` : ""}
          </p>
          {displayedSegments.length > 0 && (
            <ol className="journey-segments" aria-label="Provider journey steps">
              {displayedSegments.map((segment) => (
                <li key={segment.segment_id}>
                  <strong>{humanise(segment.direction)} · {segment.line_name ? `${segment.mode} ${segment.line_name}` : humanise(segment.mode)}</strong>
                  <span>{segment.instruction} · {segment.duration_minutes.toFixed(0)} min</span>
                </li>
              ))}
            </ol>
          )}
          {plan.return_route_same_as_outbound && <p className="route-budget-note">The return retraces the same route, so its map line and provider directions are shown only once; its separately validated return time remains included above.</p>}
          <div className="route-constraints" aria-label="Walking route constraints">
            {(plan.constraint_results ?? []).map((constraint) => (
              <p key={constraint.code} className={constraint.passed ? "constraint-pass" : "constraint-fail"}>
                <strong>{constraint.passed ? "Pass" : "Fail"} · {humanise(constraint.code)}</strong>
                <span>{constraint.message}</span>
              </p>
            ))}
          </div>
          <ElevationProfile samples={plan.elevation_profile} />
          <p className="route-provider">
            {plan.provider} · {plan.routing_profile} · retrieved {plan.retrieved_at ? new Date(plan.retrieved_at).toLocaleString("en-GB") : "—"} · cache {humanise(plan.cache_status ?? "bypassed")}
          </p>
          {alternatives.length > 0 && (
            <details>
              <summary>Alternative feasible routes · {alternatives.length}</summary>
              <ul>{alternatives.map((option) => <li key={option.option_id}>{option.site_name} via {option.entrance.label} · {option.total_travel_duration_minutes != null ? formatMinutes(option.total_travel_duration_minutes) : "—"} min total travel · {option.total_distance_km?.toFixed(2) ?? "—"} km walking</li>)}</ul>
            </details>
          )}
        </>
      ) : (
        <p className="empty-copy">No journey was fabricated. {humanise(plan.status)}.</p>
      )}
      {[...(plan.warnings ?? []), ...(plan.limitations ?? [])].length > 0 && (
        <details className="route-limitations" open>
          <summary>Route warnings & field checks</summary>
          <ul>{[...(plan.warnings ?? []), ...(plan.limitations ?? [])].map((item) => <li key={item}>{item}</li>)}</ul>
        </details>
      )}
      {plan.attribution && <p className="route-attribution">{plan.attribution} · {plan.licence}</p>}
    </section>
  );
}

export function PlanPanel({ plan }: Props) {
  const overview = expeditionOverview(plan);
  return (
    <section className="plan-panel" aria-labelledby="plan-title">
      <div className="plan-hero">
        <div>
          <p className="eyebrow">Validated field plan</p>
          <h2 id="plan-title">{plan.target_species}</h2>
          <p>{plan.target_date} · {plan.duration_hours} hours total outing budget · {plan.resolved_london_start_context}</p>
        </div>
        <span className={`plan-gate ${plan.evidence_gate_passed ? "passed" : "held"}`}>
          {plan.evidence_gate_passed ? "Evidence gate passed" : "Evidence gate held"}
        </span>
      </div>
      {plan.low_confidence_notice && <div className="low-confidence" role="note">{plan.low_confidence_notice}</div>}
      <section className="plan-overview" aria-labelledby="overview-title">
        <h3 id="overview-title">Expedition overview</h3>
        <p className="plan-explanation">{overview}</p>
      </section>
      {overview !== plan.explanation && (
        <details className="evidence-explanation">
          <summary>Model-written evidence explanation</summary>
          <p>{plan.explanation}</p>
        </details>
      )}
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
      {plan.walking_plan && <WalkingItinerary plan={plan.walking_plan} />}
      {!plan.walking_plan && <p className="legacy-route-note">This historical plan predates validated walking routes and remains readable without a walking plan.</p>}
      <details className="limitations" open>
        <summary>Limitations & field checks</summary>
        <ul>{(plan.unresolved_limitations ?? []).map((item) => <li key={item}>{item}</li>)}</ul>
      </details>
      <div className="attribution-row">{(plan.evidence_attributions ?? []).map((item) => <span key={item}>{item}</span>)}</div>
    </section>
  );
}
