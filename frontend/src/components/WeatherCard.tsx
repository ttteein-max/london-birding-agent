import type { EvidenceView } from "../api/contracts";
import { humanise } from "../utils";

interface Props { weather: EvidenceView["weather"] }

export function WeatherCard({ weather }: Props) {
  if (!weather) {
    return <div className="evidence-card"><h3>Daily weather context</h3><p className="empty-copy">Not collected at this checkpoint.</p></div>;
  }
  const available = weather.status === "available";
  return (
    <section className="evidence-card weather-card" aria-labelledby="weather-title">
      <div className="card-title-row">
        <h3 id="weather-title">Daily weather context</h3>
        <span className={`mini-status ${available ? "ok" : "muted"}`}>{humanise(weather.status)}</span>
      </div>
      <p className="weather-date">{new Date(`${weather.requested_date}T12:00:00`).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })}</p>
      {available ? (
        <div className="weather-strip" aria-label="Exact-date daily weather">
          <div><span>High</span><strong>{weather.maximum_temperature_c ?? "—"}°</strong></div>
          <div><span>Low</span><strong>{weather.minimum_temperature_c ?? "—"}°</strong></div>
          <div><span>Rain</span><strong>{weather.precipitation_probability_percent ?? "—"}%</strong></div>
          <div><span>Total</span><strong>{weather.precipitation_amount_mm ?? "—"} mm</strong></div>
        </div>
      ) : <p className="empty-copy">No different date has been substituted.</p>}
      <p className="microcopy">Single-day context only; weather is not used as sighting probability.</p>
    </section>
  );
}
