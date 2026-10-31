import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Bar, ErrorNote, Field, Loading, Panel, StatCard } from "../../components/ui";
import { useApi } from "../../lib/api";
import { assetClassLabel, lossPct, money, pct, signed } from "../../lib/format";
import type { Scenario, StressResult } from "../../lib/types";
import type { Workspace } from "../ClientWorkspace";

export default function StressTab({ ws }: { ws: Workspace }) {
  const api = useApi();
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: () => api<Scenario[]>("/stress/scenarios") });
  const run = useMutation({
    mutationFn: (body: { scenario_id?: string; sector_shocks?: Record<string, number>; default_shock?: number;
                         asset_class_shocks?: Record<string, number> }) =>
      api<StressResult>(`/portfolios/${ws.portfolio.id}/stress`, { json: body }),
  });
  // Holdings with no sector take the "all other sectors" shock; "Unclassified" isn't a sector to shock.
  const sectors = Object.keys(ws.profile.sector_exposure).filter((s) => s !== "Unclassified");
  // Custom shocks are typed as percentages and sent as fractions; the engine applies them.
  const [custom, setCustom] = useState<Record<string, string>>({});
  const [other, setOther] = useState("-10");
  // Bonds and preferreds held in this portfolio can be given their own move; cash holds
  // its value unless given one. Blank means "use the sector / all other value".
  const classes = Object.keys(ws.profile.asset_class_exposure ?? {}).filter((c) => ["fixed_income", "preferred", "cash"].includes(c));
  const [classCustom, setClassCustom] = useState<Record<string, string>>({});

  const runCustom = () => run.mutate({
    sector_shocks: Object.fromEntries(Object.entries(custom).filter(([, v]) => v.trim() !== "")
      .map(([k, v]) => [k, Number(v) / 100])),
    default_shock: Number(other) / 100,
    asset_class_shocks: Object.fromEntries(Object.entries(classCustom).filter(([, v]) => v.trim() !== "")
      .map(([k, v]) => [k, Number(v) / 100])),
  });

  if (scenarios.isLoading) return <Loading />;
  return (
    <div className="space-y-5">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {scenarios.data?.map((s) => (
          <button key={s.id} onClick={() => run.mutate({ scenario_id: s.id })}
                  className={`border bg-card p-4 text-left hover:border-gold ${run.data?.scenario.id === s.id ? "border-gold bg-gold-soft" : "border-rule"}`}>
            <div className="font-serif text-lg">{s.label}</div>
            <div className="mt-1 text-xs text-muted">{s.description}</div>
          </button>
        ))}
      </div>

      <details className="border border-rule bg-card p-4">
        <summary className="cursor-pointer font-serif text-lg">Custom scenario</summary>
        <p className="mt-2 text-xs text-muted">Enter a move for each sector in percent (negative for a fall). Blank sectors use the "all other" value. A move for bonds, preferred stock or cash applies to every holding of that kind, whatever its sector; cash holds its value unless you give it a move.</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {sectors.map((s) => (
            <Field key={s} label={s}>
              <input className="input" inputMode="decimal" placeholder="%" value={custom[s] ?? ""}
                     onChange={(e) => setCustom({ ...custom, [s]: e.target.value })} />
            </Field>
          ))}
          {classes.map((c) => (
            <Field key={c} label={`${assetClassLabel(c)} (all)`}>
              <input className="input" inputMode="decimal" placeholder={c === "cash" ? "0 (holds value)" : "%"}
                     value={classCustom[c] ?? ""} onChange={(e) => setClassCustom({ ...classCustom, [c]: e.target.value })} />
            </Field>
          ))}
          <Field label="All other holdings">
            <input className="input" inputMode="decimal" value={other} onChange={(e) => setOther(e.target.value)} />
          </Field>
        </div>
        <button className="btn btn-dark mt-3" onClick={runCustom} disabled={run.isPending}>Run custom scenario</button>
      </details>

      <ErrorNote error={run.error} />
      {run.isPending && <Loading what="Applying the scenario" />}
      {run.data && <Result r={run.data} />}
    </div>
  );
}

function Result({ r }: { r: StressResult }) {
  const worst = Math.max(...r.positions.map((p) => Math.abs(p.contribution)), 1e-12);
  return (
    <div className="space-y-5">
      <div className="grid gap-4 md:grid-cols-2">
        <StatCard eyebrow={`${r.scenario.label}: portfolio move`} value={signed(pct(r.portfolio_return), r.portfolio_return)}
                  note={r.portfolio_return < 0 ? `A ${lossPct(r.portfolio_return)} fall in value.` : "No loss under these shocks."} />
        <StatCard eyebrow="Change in value" value={signed(money(r.value_change), r.value_change)}
                  note={`On holdings worth ${money(r.portfolio_value)} at the prices in the file.`} />
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="By sector">
          <table className="data">
            <thead><tr><th>Sector</th><th className="num">Weight</th><th className="num">Shock</th><th className="num">Effect on portfolio</th></tr></thead>
            <tbody>
              {r.sectors.map((s) => (
                <tr key={s.sector}><td>{s.sector}</td><td className="num">{pct(s.weight)}</td>
                  <td className="num">{signed(pct(s.shock), s.shock)}</td><td className="num">{signed(pct(s.contribution, 2), s.contribution)}</td></tr>
              ))}
            </tbody>
          </table>
        </Panel>
        {r.asset_classes && r.asset_classes.some((a) => a.asset_class !== "equity") && (
          <Panel title="By asset class">
            <table className="data">
              <thead><tr><th>Asset class</th><th className="num">Weight</th><th className="num">Shock</th><th className="num">Effect on portfolio</th></tr></thead>
              <tbody>
                {r.asset_classes.map((a) => (
                  <tr key={a.asset_class}><td>{assetClassLabel(a.asset_class)}</td><td className="num">{pct(a.weight)}</td>
                    <td className="num">{signed(pct(a.shock), a.shock)}</td><td className="num">{signed(pct(a.contribution, 2), a.contribution)}</td></tr>
                ))}
              </tbody>
            </table>
          </Panel>
        )}
        <Panel title="Largest losses by position">
          {r.positions.slice(0, 8).map((p) => (
            <Bar key={p.ticker} tone="bad" label={`${p.ticker} · ${p.sector}`}
                 shown={`${signed(pct(p.contribution, 2), p.contribution)} · ${signed(money(p.value_change), p.value_change)}`}
                 fraction={Math.abs(p.contribution) / worst} />
          ))}
        </Panel>
      </div>
      <p className="text-xs text-muted">{r.basis} Every holding in a sector moves by the same amount, except bonds, preferred stock and cash, which move by their asset class's shock. A sector's shock shown here is the weighted average of its holdings' shocks.</p>
    </div>
  );
}
