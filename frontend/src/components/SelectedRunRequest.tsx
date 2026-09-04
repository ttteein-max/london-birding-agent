import type { RunDetail, StateView } from "../api/contracts";
import { compactDate, shortId } from "../utils";
import { DecisionHistory } from "./DecisionHistory";

interface Props {
  detail: RunDetail | null;
  state: StateView | null;
}

export function SelectedRunRequest({ detail, state }: Props) {
  const request = detail?.submitted_request;
  if (!detail || !request) return null;

  return (
    <section className="selected-request-card" aria-labelledby="selected-request-title">
      <div className="section-heading compact">
        <div>
          <p className="eyebrow">Selected run record</p>
          <h2 id="selected-request-title">Natural-language request</h2>
        </div>
        <span className="request-record-mode">
          {detail.run.data_mode}/{detail.run.model_mode}
        </span>
      </div>
      <blockquote>{request.text}</blockquote>
      <div className="request-record-meta" aria-label="Selected run request metadata">
        <span>Thread <code>{detail.run.thread_id}</code></span>
        <span>Submitted {compactDate(detail.run.created_at)}</span>
        <span>{request.language} · local history</span>
      </div>
      {state && (
        <div className="selected-hitl-record" aria-label="HITL decisions for selected execution">
          <div className="selected-hitl-heading">
            <div><strong>HITL decision record</strong><span>Selected execution {shortId(state.execution_id)}</span></div>
            <span className="count-badge">{state.hitl.applied_decisions?.length ?? 0}</span>
          </div>
          <p>The natural-language request above is immutable. These validated choices show how this execution departed from it.</p>
          <DecisionHistory decisions={state.hitl.applied_decisions} emptyMessage="This execution did not apply any HITL choices." />
        </div>
      )}
    </section>
  );
}
