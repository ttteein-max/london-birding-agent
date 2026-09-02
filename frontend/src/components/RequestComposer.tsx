import { useState, type FormEvent } from "react";

const EXAMPLES = {
  strong:
    "Plan a two-hour expedition from SW11 4NJ on 15 June 2026 to look for Common woodpigeon.",
  taxonomy:
    "Plan a two-hour expedition from SW11 4NJ on 15 January 2026 to look for robin.",
  low:
    "Plan a two-hour expedition from SW11 4NJ on 15 July 2026 to look for Common swift.",
};

interface Props {
  busy: boolean;
  onSubmit: (request: string) => Promise<void>;
}

export function RequestComposer({ busy, onSubmit }: Props) {
  const [value, setValue] = useState(EXAMPLES.strong);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!value.trim()) return;
    await onSubmit(value.trim());
  };

  return (
    <section className="composer-card" aria-labelledby="composer-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">New field enquiry</p>
          <h2 id="composer-title">Describe the expedition</h2>
        </div>
        <span className="language-tag">English · London only</span>
      </div>
      <form onSubmit={submit}>
        <label htmlFor="expedition-request">Natural-language request</label>
        <textarea
          id="expedition-request"
          rows={3}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          disabled={busy}
        />
        <div className="composer-actions">
          <div className="example-row" aria-label="Fixture examples">
            <button type="button" onClick={() => setValue(EXAMPLES.strong)}>Strong evidence</button>
            <button type="button" onClick={() => setValue(EXAMPLES.taxonomy)}>Taxonomy HITL</button>
            <button type="button" onClick={() => setValue(EXAMPLES.low)}>Low evidence</button>
          </div>
          <button className="primary-action" type="submit" disabled={busy || !value.trim()}>
            {busy ? "Starting…" : "Start expedition"}
          </button>
        </div>
      </form>
      <p className="microcopy">
        Historical records guide evidence-grounded candidates; they do not predict or guarantee a sighting.
      </p>
    </section>
  );
}
