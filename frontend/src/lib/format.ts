export const fmtUSD = (v: number | null | undefined, digits = 2): string =>
  v == null
    ? "—"
    : v.toLocaleString("en-US", {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });

export const fmtPct = (v: number | null | undefined, digits = 2): string =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;

export const fmtWeight = (v: number | null | undefined): string =>
  v == null ? "—" : `${(v * 100).toFixed(1)}%`;

export const fmtShares = (v: number): string =>
  Number.isInteger(v) ? String(v) : v.toFixed(4);

export const fmtTime = (iso: string): string => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
};

export const plColor = (v: number): string =>
  v > 0 ? "text-emerald-400" : v < 0 ? "text-rose-400" : "text-zinc-400";
