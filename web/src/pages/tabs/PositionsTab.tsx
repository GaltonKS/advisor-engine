import { useState } from "react";
import { Panel } from "../../components/ui";
import { num, pct } from "../../lib/format";
import type { Position } from "../../lib/types";
import type { Workspace } from "../ClientWorkspace";

type SortKey = "risk_share" | "weight" | "risk_to_weight" | "avg_corr_to_rest" | "ticker";

function closeness(corr: number): string {
  return corr >= 0.6 ? "closely" : corr >= 0.3 ? "moderately" : "only loosely";
}

export default function PositionsTab({ ws }: { ws: Workspace }) {
  const positions = ws.profile.positions;
  const [sort, setSort] = useState<SortKey>("risk_share");
  const [selected, setSelected] = useState<Position | null>(positions[0] ?? null);
  // Sorting reorders engine rows for display; it computes nothing.
  const rows = [...positions].sort((a, b) =>
    sort === "ticker" ? a.ticker.localeCompare(b.ticker) : (b[sort] as number) - (a[sort] as number));

  const head = (key: SortKey, label: string, numeric = true) => (
    <th className={numeric ? "num" : ""}>
      <button className={sort === key ? "text-ink underline" : ""} onClick={() => setSort(key)}>{label}</button>
    </th>
  );

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_340px]">
      <Panel title="Positions" tag="Click a row for detail">
        <div className="overflow-x-auto">
          <table className="data">
            <thead>
              <tr>
                {head("ticker", "Ticker", false)}<th>Sector</th>
                {head("weight", "Weight")}{head("risk_share", "Share of risk")}
                {head("risk_to_weight", "Risk vs size")}{head("avg_corr_to_rest", "Correlation")}
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.ticker} onClick={() => setSelected(p)}
                    className={`cursor-pointer ${selected?.ticker === p.ticker ? "bg-gold-soft" : "hover:bg-paper"}`}>
                  <td className="font-semibold">{p.ticker}</td>
                  <td>{p.sector}</td>
                  <td className="num">{pct(p.weight)}</td>
                  <td className="num">{pct(p.risk_share)}</td>
                  <td className={`num ${p.risk_to_weight > 1 ? "text-bad" : ""}`}>{num(p.risk_to_weight, 2)}x</td>
                  <td className="num">{num(p.avg_corr_to_rest, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-xs text-muted">Risk vs size above 1.00x means a position adds more risk than its weight suggests.</p>
      </Panel>

      {selected && (
        <Panel title={selected.ticker} tag={selected.sector} className="self-start">
          <dl className="space-y-3 text-sm">
            <Row k="Weight in the portfolio" v={pct(selected.weight)} />
            <Row k="Share of the portfolio's risk" v={pct(selected.risk_share)} />
            <Row k="Risk relative to its size" v={`${num(selected.risk_to_weight, 2)}x`} />
            <Row k={`Own volatility (${ws.profile.drawdown.period_label})`} v={pct(selected.volatility)} />
            <Row k="Average correlation with the rest" v={num(selected.avg_corr_to_rest, 2)} />
          </dl>
          <p className="mt-4 text-sm text-muted">
            It moves {closeness(selected.avg_corr_to_rest)} with the rest of the portfolio and adds{" "}
            {selected.risk_to_weight > 1 ? "more" : "no more"} risk than its size would suggest.
          </p>
          <div className="mt-4 flex flex-col gap-2">
            <button className="btn btn-dark justify-center" onClick={() => ws.go("assistant", `Why is ${selected.ticker} a concern?`)}>
              Ask Galton AI about {selected.ticker}
            </button>
            <button className="btn justify-center" onClick={() => ws.go("holdings")}>
              Try changing it in a what-if
            </button>
          </div>
        </Panel>
      )}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-3 border-b border-rule pb-2">
      <dt className="text-muted">{k}</dt>
      <dd className="figure">{v}</dd>
    </div>
  );
}
