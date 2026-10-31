import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Bar, ErrorNote, Field, Loading, Panel, StatCard } from "../../components/ui";
import { useApi } from "../../lib/api";
import { assetClassLabel, lossPct, money, pct, signed } from "../../lib/format";
import type { Scenario, ScenarioSuggestion, StressResult } from "../../lib/types";
import type { Workspace } from "../ClientWorkspace";

export default function StressTab({ ws }: { ws: Workspace }) {
  const api = useApi();
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: () => api<Scenario[]>("/stress/scenarios") });
  const run = useMutation({
    mutationFn: (body: { scenario_id?: string; sector_shocks?: Record<string, number>; default_shock?: number;
                         asset_class_shocks?: Record<string, number>; description?: string;
                         suggested?: ScenarioSuggestion | null }) =>
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

  // A described scenario: the AI suggests shocks into the fields below, the adviser edits
  // them, and the engine computes the result from whatever is in the fields at Run.
  const [params, setParams] = useSearchParams();
  const [description, setDescription] = useState("");
  const [open, setOpen] = useState(false);
  const [suggestion, setSuggestion] = useState<ScenarioSuggestion | null>(null);
  useEffect(() => {
    // A question handed over from the Assistant ("Open in Stress test").
    const handed = params.get("ask");
    if (handed) {
      setDescription(handed);
      setOpen(true);
      params.delete("ask");
      setParams(params, { replace: true });
    }
  }, [params, setParams]);
  // Fractions from the API shown as editable percentages (display only).
  const asInput = (v: number) => String(+(v * 100).toFixed(2));
  const suggest = useMutation({
    mutationFn: () => api<ScenarioSuggestion>(`/portfolios/${ws.portfolio.id}/stress/suggest`, { json: { description } }),
    onSuccess: (s) => {
      setSuggestion(s);
      setCustom(Object.fromEntries(Object.entries(s.sector_shocks).map(([k, v]) => [k, asInput(v)])));
      setClassCustom(Object.fromEntries(Object.entries(s.asset_class_shocks).map(([k, v]) => [k, asInput(v)])));
      setOther(asInput(s.default_shock));
    },
  });

  const runCustom = () => run.mutate({
    sector_shocks: Object.fromEntries(Object.entries(custom).filter(([, v]) => v.trim() !== "")
      .map(([k, v]) => [k, Number(v) / 100])),
    default_shock: Number(other) / 100,
    asset_class_shocks: Object.fromEntries(Object.entries(classCustom).filter(([, v]) => v.trim() !== "")
      .map(([k, v]) => [k, Number(v) / 100])),
    description,
    suggested: suggestion,
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

      <details className="border border-rule bg-card p-4" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
        <summary className="cursor-pointer font-serif text-lg">Custom scenario</summary>
        <Field label="Describe a scenario (optional)">
          <textarea className="input min-h-[80px]" value={description} maxLength={1000}
                    placeholder="e.g. China blockades Taiwan; chip supply shock and a flight to safety."
                    onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <button className="btn mt-2" disabled={!description.trim() || suggest.isPending} onClick={() => suggest.mutate()}>
          {suggest.isPending ? "Suggesting…" : "Suggest shocks with AI"}
        </button>
        <ErrorNote error={suggest.error} />
        {suggestion && <SuggestionNote s={suggestion} />}
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

function SuggestionNote({ s }: { s: ScenarioSuggestion }) {
  return (
    <div className="mt-3 border border-gold/50 bg-gold/5 p-3 text-sm">
      <div className="eyebrow">{s.source === "mock" ? "Offline keyword suggestion" : "Suggested by the AI"} · assumptions to review · not a forecast</div>
      <div className="mt-1 font-serif text-lg">{s.label}</div>
      <p className="mt-1 text-muted">{s.rationale}</p>
      {!s.explanation_withheld && (
        <ul className="mt-2 space-y-0.5">
          {s.reasons.map((r) => (
            <li key={`${r.kind}-${r.target}`}><span className="figure">{signed(pct(r.shock, 0), r.shock)}</span> · {r.kind === "asset_class" ? assetClassLabel(r.target) : r.target}{r.reason && <> · <span className="text-muted">{r.reason}</span></>}</li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-xs text-muted">These shocks are now in the fields below. Edit them as you see fit; the engine computes the result from whatever the fields hold when you run the scenario.</p>
    </div>
  );
}

const ASSUMPTIONS: Record<string, string> = {
  preset: "Assumptions: an illustrative preset, not calibrated to a historical period.",
  adviser: "Assumptions: entered by you.",
  ai_suggested: "Assumptions: suggested by the AI from your description, not a forecast.",
  ai_suggested_edited: "Assumptions: suggested by the AI from your description and edited by you; not a forecast.",
};

function Result({ r }: { r: StressResult }) {
  const worst = Math.max(...r.positions.map((p) => Math.abs(p.contribution)), 1e-12);
  const source = r.assumptions?.source;
  return (
    <div className="space-y-5">
      {source && (
        <div className={`border px-3 py-2 text-sm ${source.startsWith("ai") ? "border-gold/50 bg-gold/5" : "border-rule bg-card"}`}>
          <div>{source.startsWith("ai") && r.assumptions?.model === "mock"
                  ? ASSUMPTIONS[source].replace("by the AI", "by the offline keyword helper") : ASSUMPTIONS[source]}
               {r.assumptions?.description ? <> “{r.assumptions.description}”</> : null}</div>
          <div className="mt-0.5 font-semibold">Results: computed by the engine from these assumptions.</div>
        </div>
      )}
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
