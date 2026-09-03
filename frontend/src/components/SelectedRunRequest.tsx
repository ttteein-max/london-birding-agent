import type { RunDetail } from "../api/contracts";
import { compactDate } from "../utils";

interface Props {
  detail: RunDetail | null;
}

export function SelectedRunRequest({ detail }: Props) {
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
    </section>
  );
}
