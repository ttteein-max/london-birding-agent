import type { StateView } from "../api/contracts";
import { humanise, shortId } from "../utils";
import { DecisionHistory } from "./DecisionHistory";

interface Props { state: StateView | null; loading: boolean; onClose: () => void }

function Row({ label, value }: { label: string; value: unknown }) {
  const display = value == null ? "—" : typeof value === "boolean" ? (value ? "Yes" : "No") : String(value);
  return <div className="inspector-row"><dt>{label}</dt><dd>{display}</dd></div>;
}

export function StateInspector({ state, loading, onClose }: Props) {
  if (!loading && !state) return null;
  return (
    <aside className="drawer state-drawer" aria-labelledby="state-title" aria-live="polite">
      <div className="drawer-header">
        <div><p className="eyebrow">Exact checkpoint</p><h2 id="state-title">Safe state inspector</h2></div>
        <button className="icon-button" onClick={onClose} aria-label="Close state inspector">×</button>
      </div>
      {loading || !state ? <p className="empty-copy">Loading allow-listed checkpoint state…</p> : (
        <div className="inspector-content">
          <div className="identity-strip"><span>Thread {shortId(state.thread_id)}</span><span>Branch {shortId(state.branch_id)}</span><span>Execution {shortId(state.execution_id)}</span><span>Checkpoint {shortId(state.checkpoint_id)}</span></div>
          <p className="checkpoint-explainer"><strong>CP means checkpoint.</strong> It is an immutable saved graph state after a step, so it shows everything accumulated up to this point—not just the event on the node you clicked.</p>
          <section><h3>{humanise(state.node_id)} · step {state.graph_step}</h3><dl><Row label="Source" value={state.source} /><Row label="Terminal status" value={state.terminal_status} /><Row label="Next nodes" value={(state.next_nodes ?? []).join(", ") || "None"} /></dl></section>
          <section><h3>Request constraints</h3><dl><Row label="Target date" value={state.request?.target_local_date} /><Row label="Total outing budget" value={state.request?.duration_hours ? `${state.request.duration_hours} hours including return travel` : null} /><Row label="Search radius" value={state.request?.search_radius_km ? `${state.request.search_radius_km} km straight-line candidate search` : null} /><Row label="Seasonal window" value={state.request?.seasonal_window_radius_months} /><Row label="Walking constraint" value={state.request?.maximum_walking_distance_km ? `${state.request.maximum_walking_distance_km} km full excursion` : "No explicit limit"} /></dl></section>
          <section><h3>Location & taxon</h3><dl><Row label="London status" value={state.location?.status} /><Row label="District" value={state.location?.administrative_district} /><Row label="Taxon" value={state.taxon?.canonical_name} /><Row label="Taxon status" value={state.taxon?.status} /></dl></section>
          <section><h3>Evidence & plan gate</h3><dl><Row label="Evidence outcome" value={state.evidence.occurrence_outcome} /><Row label="Sampled / retained / ranking" value={`${state.evidence.sampled_count ?? 0} / ${state.evidence.retained_count ?? 0} / ${state.evidence.ranking_eligible_count ?? 0}`} /><Row label="Safe aggregate cells" value={state.evidence.safe_map_cell_count} /><Row label="Candidate sites" value={state.evidence.candidate_site_count} /><Row label="Audited entrances" value={state.evidence.public_entrance_count} /><Row label="Route options" value={state.evidence.route_option_count} /><Row label="Plan status" value={state.plan.final_status ?? state.plan.deterministic_status} /><Row label="Evidence gate passed" value={state.plan.evidence_gate_passed} /></dl></section>
          <section><h3>Validated route state</h3><dl><Row label="Route status" value={state.plan.route_status} /><Row label="Selected public site" value={state.plan.selected_route_site_id} /><Row label="Total travel" value={state.plan.total_travel_duration_minutes == null ? null : `${state.plan.total_travel_duration_minutes} minutes`} /><Row label="Total walking" value={state.plan.total_walking_distance_km == null ? null : `${state.plan.total_walking_distance_km} km`} /><Row label="Remaining field time" value={state.plan.remaining_field_time_minutes == null ? null : `${state.plan.remaining_field_time_minutes} minutes`} /><Row label="Provider" value={state.plan.routing_provider} /><Row label="Cache" value={state.plan.route_cache_status} /></dl></section>
          <section className="inspector-decision-section">
            <h3>HITL decisions at this checkpoint</h3>
            <p className="section-note">A waiting HITL checkpoint is saved before the answer. The selected answer first appears in the following Apply validated choice checkpoint.</p>
            <DecisionHistory
              decisions={state.hitl.applied_decisions}
              emptyMessage={state.hitl.waiting ? "Waiting for a human answer; no new choice has been applied yet." : undefined}
            />
            <dl><Row label="Waiting for input" value={state.hitl.waiting} /><Row label="Interrupt" value={state.hitl.interrupt_kind} /></dl>
          </section>
          <section><h3>Execution counters</h3><dl><Row label="Evidence rounds" value={state.counters.evidence_loop_count} /><Row label="Plan revisions" value={state.counters.plan_revision_count} /><Row label="Recorded tools" value={state.counters.recorded_tool_call_count} /></dl></section>
        </div>
      )}
    </aside>
  );
}
