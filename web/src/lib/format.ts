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

/** A loss size (positive, as the engine's comparison stores it) shown as a loss: −8.1%. */
export const lossSigned = (size: number, digits = 1): string =>
  size === 0 ? pct(0, digits) : `−${pct(Math.abs(size), digits)}`;

/** The source line under every chart, in one format everywhere. */
export const sourceLine = (s: { label: string; n_periods: number; period_label: string;
  returns_source: string; engine_version: string; holdings_sha256: string | null }): string =>
  `${s.label} · ${s.n_periods} ${s.period_label} ${s.returns_source} returns · engine v${s.engine_version}` +
  (s.holdings_sha256 ? ` · holdings ${s.holdings_sha256.slice(0, 8)}` : "");
