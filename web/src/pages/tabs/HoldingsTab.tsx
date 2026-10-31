import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ComparisonTable, ErrorNote, Field, Loading, Panel } from "../../components/ui";
import { useApi } from "../../lib/api";
import { ASSET_CLASS_LABELS, pct, signed } from "../../lib/format";
import type { CompareResult, HoldingRow, Portfolio } from "../../lib/types";
import type { Workspace } from "../ClientWorkspace";

// Rows are edited as text so a half-typed number doesn't fight the input; the API
// validates them (long-only, positive prices, unique tickers) when the engine runs.
interface EditRow { ticker: string; shares: string; price: string; sector: string; asset_class: string }

// "Unclassified" is the engine's label for no sector; edit it as a blank to fill in.
const UNCLASSIFIED = "Unclassified";
const toEdit = (r: HoldingRow): EditRow => ({ ticker: r.ticker, shares: String(r.shares), price: String(r.price),
                                               sector: r.sector === UNCLASSIFIED ? "" : r.sector,
                                               asset_class: r.asset_class ?? "equity" });
const toApi = (rows: EditRow[]) => rows
  .filter((r) => r.ticker.trim())
  .map((r) => ({ ticker: r.ticker.trim(), shares: Number(r.shares), price: Number(r.price), sector: r.sector.trim(),
                 asset_class: r.asset_class }));

export default function HoldingsTab({ ws }: { ws: Workspace }) {
  const api = useApi();
  const qc = useQueryClient();
  const source = useQuery({
    queryKey: ["holdings", ws.portfolio.id],
    queryFn: () => api<{ holdings: HoldingRow[] }>(`/portfolios/${ws.portfolio.id}/holdings`),
  });
  const [rows, setRows] = useState<EditRow[]>([]);
  const [label, setLabel] = useState("");
  useEffect(() => { if (source.data) setRows(source.data.holdings.map(toEdit)); }, [source.data]);

  const whatIf = useMutation({
    mutationFn: () => api<CompareResult>(`/portfolios/${ws.portfolio.id}/what-if`, { json: { holdings: toApi(rows) } }),
  });
  const save = useMutation({
    mutationFn: () => api<Portfolio>(`/clients/${ws.client.id}/portfolios/holdings`,
                                     { json: { holdings: toApi(rows), label } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["client", ws.client.id] }),
  });

  const set = (i: number, k: keyof EditRow, v: string) => {
    setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
    whatIf.reset();
  };

  if (source.isLoading) return <Loading />;
  const sectors = [...new Set(rows.map((r) => r.sector).filter((s) => s && s !== UNCLASSIFIED))];

  return (
    <div className="grid gap-5 xl:grid-cols-[1fr_1fr]">
      <Panel title="Holdings" tag="Edit, then run a what-if">
        <ErrorNote error={source.error} />
        <datalist id="sectors">{sectors.map((s) => <option key={s} value={s} />)}</datalist>
        <div className="overflow-x-auto">
          <table className="data">
            <thead><tr><th>Ticker</th><th className="num">Shares</th><th className="num">Price</th><th>Sector</th><th>Asset class</th><th /></tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td><input className="input !w-24 uppercase" value={r.ticker} aria-label={`Ticker ${i + 1}`}
                             onChange={(e) => set(i, "ticker", e.target.value.toUpperCase())} /></td>
                  <td><input className="input !w-24 text-right" inputMode="decimal" value={r.shares} aria-label={`Shares ${i + 1}`}
                             onChange={(e) => set(i, "shares", e.target.value)} /></td>
                  <td><input className="input !w-24 text-right" inputMode="decimal" value={r.price} aria-label={`Price ${i + 1}`}
                             onChange={(e) => set(i, "price", e.target.value)} /></td>
                  <td><input className={`input min-w-44 ${r.sector.trim() ? "" : "border-gold"}`} list="sectors" value={r.sector}
                             placeholder="Add sector" aria-label={`Sector ${i + 1}`}
                             onChange={(e) => set(i, "sector", e.target.value)} /></td>
                  <td><select className="input !w-auto" value={r.asset_class} aria-label={`Asset class ${i + 1}`}
                              onChange={(e) => set(i, "asset_class", e.target.value)}>
                    {Object.entries(ASSET_CLASS_LABELS).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
                  </select></td>
                  <td><button className="text-faint hover:text-bad" title="Remove" aria-label={`Remove ${r.ticker}`}
                              onClick={() => { setRows(rows.filter((_, j) => j !== i)); whatIf.reset(); }}>✕</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <button className="btn" onClick={() => setRows([...rows, { ticker: "", shares: "", price: "", sector: "", asset_class: "equity" }])}>+ Add position</button>
          <button className="btn" onClick={() => { setRows(source.data!.holdings.map(toEdit)); whatIf.reset(); }}>Reset</button>
          <button className="btn btn-dark ml-auto" onClick={() => whatIf.mutate()} disabled={whatIf.isPending}>
            {whatIf.isPending ? "Running…" : "Run what-if"}
          </button>
        </div>
        <ErrorNote error={whatIf.error} />
      </Panel>

      <div className="space-y-5">
        {!whatIf.data ? (
          <Panel title="What-if result">
            <p className="text-sm text-muted">
              Change shares, prices or positions on the left and run a what-if. The engine reruns the full risk profile on the
              edited holdings and compares it with the current version. Nothing is saved until you choose to.
            </p>
          </Panel>
        ) : (
          <>
            <Panel title="What-if result" tag="Current vs edited">
              <ComparisonTable rows={whatIf.data.comparison.headline} beforeLabel="Current" afterLabel="Edited" />
              {(whatIf.data.comparison.added.length > 0 || whatIf.data.comparison.removed.length > 0) && (
                <p className="mt-3 text-sm text-muted">
                  {whatIf.data.comparison.added.length > 0 && <>Added: {whatIf.data.comparison.added.join(", ")}. </>}
                  {whatIf.data.comparison.removed.length > 0 && <>Removed: {whatIf.data.comparison.removed.join(", ")}.</>}
                </p>
              )}
            </Panel>
            <Panel title="Sector exposure">
              <table className="data">
                <thead><tr><th>Sector</th><th className="num">Current</th><th className="num">Edited</th><th className="num">Change</th></tr></thead>
                <tbody>
                  {whatIf.data.comparison.sectors.map((s) => (
                    <tr key={s.sector}><td>{s.sector}</td><td className="num">{pct(s.before)}</td>
                      <td className="num">{pct(s.after)}</td><td className="num">{signed(pct(s.change), s.change)}</td></tr>
                  ))}
                </tbody>
              </table>
            </Panel>
            <Panel title="Keep these holdings?">
              <p className="mb-3 text-sm text-muted">Saving creates a new version. The current version stays exactly as it is.</p>
              <div className="flex flex-wrap items-end gap-2">
                <div className="min-w-48 flex-1">
                  <Field label="Version label (optional)">
                    <input className="input" value={label} maxLength={120} placeholder="e.g. Trim technology"
                           onChange={(e) => setLabel(e.target.value)} />
                  </Field>
                </div>
                <button className="btn btn-primary" onClick={() => save.mutate()} disabled={save.isPending || save.isSuccess}>
                  {save.isSuccess ? "Saved" : save.isPending ? "Saving…" : "Save as new version"}
                </button>
              </div>
              <ErrorNote error={save.error} />
              {save.isSuccess && <p className="mt-2 text-sm text-good">Saved. Pick it from the version list at the top to analyse it.</p>}
            </Panel>
          </>
        )}
      </div>
    </div>
  );
}
