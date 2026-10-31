// Display formatting only. These turn engine figures into text; they never derive a
// new figure (no sums, differences or ratios), so everything on screen stays traceable
// to the engine's output.

export const pct = (x: number, digits = 1): string => `${(x * 100).toFixed(digits)}%`;

/** A loss stored as a negative fraction, shown as a positive percentage ("a 10.9% loss"). */
export const lossPct = (x: number, digits = 1): string => pct(Math.abs(x), digits);

export const num = (x: number, digits = 1): string => x.toFixed(digits);

/** An engine-computed change, with its sign shown (+1.2 / −0.4 / 0.0). */
export const signed = (text: string, x: number): string =>
  x > 0 ? `+${text}` : x < 0 ? `−${text.replace("-", "")}` : text;

export const money = (x: number): string =>
  x.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

export const date = (iso: string): string =>
  new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });

export const dateTime = (iso: string): string =>
  new Date(iso).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });

/** Format a comparison value by its kind. */
export const byKind = (kind: "number" | "fraction" | "count" | "loss", x: number): string =>
  kind === "fraction" || kind === "loss" ? pct(x) : kind === "count" ? x.toFixed(0) : num(x);

export const shortHash = (sha?: string | null): string => (sha ? sha.slice(0, 8) : "—");

/** Display names for the engine's asset classes (holdings.ASSET_CLASSES). */
export const ASSET_CLASS_LABELS: Record<string, string> = {
  equity: "Stocks", fixed_income: "Bonds", preferred: "Preferred stock", cash: "Cash", other: "Other",
};
export const assetClassLabel = (c: string): string => ASSET_CLASS_LABELS[c] ?? c;
