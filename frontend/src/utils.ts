export function humanise(value?: string | null): string {
  if (!value) return "Not available";
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function shortId(value?: string | null, length = 7): string {
  return value ? value.slice(0, length) : "—";
}

export function duration(value?: number | null): string {
  if (value == null) return "—";
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(2)} s`;
}

export function compactDate(value: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
