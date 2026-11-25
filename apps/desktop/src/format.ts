const nf = new Intl.NumberFormat();

export const num = (n: number | null | undefined): string => (n == null ? "–" : nf.format(n));

/** "3 min ago", "yesterday", or a date. Accepts the engine's ISO / local timestamps. */
export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "never";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const s = Math.round((now - t) / 1000);
  if (s < 45) return "just now";
  if (s < 90) return "1 min ago";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  if (d === 1) return "yesterday";
  if (d < 14) return `${d} days ago`;
  return new Date(t).toLocaleDateString();
}

export const plural = (n: number, one: string, many = `${one}s`) => `${num(n)} ${n === 1 ? one : many}`;
