// The firm's financial forecast: the home page. Every figure comes from the forecast
// engine service (through the API's /forecast routes); this page only lays it out.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Chip, Empty, ErrorNote, Field, FilePicker, Loading, Panel, StatCard } from "../components/ui";
import { useApi } from "../lib/api";
import { dateTime, money, num, pct } from "../lib/format";
import type { ForecastResult, ForecastStatus, ForecastSummary, ForecastUpload } from "../lib/types";

const month = (iso: string): string =>
  new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString("en-US", { year: "numeric", month: "short" });

const METHODS: [string, string][] = [
  ["auto", "Best by backtest (recommended)"], ["driver", "Driver formula"],
  ["seasonal_naive", "Seasonal naive"], ["ets", "Exponential smoothing (ETS)"],
  ["arima", "ARIMA"], ["prophet", "Prophet"],
];

const DRIVER_LABELS: Record<string, string> = {
  revenue_base: "Starting monthly revenue", revenue_growth: "Monthly revenue growth",
  gross_margin: "Gross margin", opex_base: "Starting monthly operating costs",
  opex_growth: "Monthly operating cost growth", other_net: "Other income (net, monthly)",
};
const FRACTION_DRIVERS = new Set(["revenue_growth", "gross_margin", "opex_growth"]);
const driverValue = (k: string, v: number) => (FRACTION_DRIVERS.has(k) ? pct(v, 2) : money(v));

/** Scenario fields are entered as percentages; blank means "let the engine fit it". */
const asFraction = (s: string): number | undefined => (s.trim() === "" ? undefined : Number(s) / 100);

function UploadPanel({ onDone }: { onDone: () => void }) {
  const api = useApi();
  const [file, setFile] = useState<File | null>(null);
  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.append("file", file!);
      return api<ForecastUpload>("/forecast/actuals", { form });
    },
    onSuccess: () => { setFile(null); onDone(); },
  });
  const s = upload.data;
  return (
    <Panel title="Monthly actuals" tag="Step 1">
      <p className="mb-3 text-sm text-muted">
        A CSV with columns <code>period, account, category, amount</code>; category is revenue, cogs,
        opex or other. Re-uploading a month replaces that account&apos;s amount.
      </p>
      <form onSubmit={(e) => { e.preventDefault(); if (file) upload.mutate(); }}>
        <FilePicker label="Financials CSV" file={file} onChange={setFile} accept=".csv,text/csv" />
        <ErrorNote error={upload.error} />
        <button className="btn btn-primary mt-3" disabled={!file || upload.isPending}>
          {upload.isPending ? "Uploading…" : "Upload"}
        </button>
      </form>
      {s && (
        <div className="mt-3 text-sm">
          Loaded {s.rows_received} rows across {s.accounts} accounts: {s.months_in_history} months,{" "}
          {month(s.history_start)} to {month(s.history_end)}.
          {s.gaps.length > 0 && <div className="text-bad">Missing months: {s.gaps.join(", ")}</div>}
        </div>
      )}
    </Panel>
  );
}

function ScenarioPanel({ onResult }: { onResult: (r: ForecastResult) => void }) {
  const api = useApi();
  const [horizon, setHorizon] = useState("12");
  const [method, setMethod] = useState("auto");
  const [growth, setGrowth] = useState("");
  const [margin, setMargin] = useState("");
  const [opexGrowth, setOpexGrowth] = useState("");
  const run = useMutation({
    mutationFn: () => api<ForecastResult>("/forecast/runs", { json: {
      horizon_months: Number(horizon), method,
      overrides: { revenue_growth: asFraction(growth), gross_margin: asFraction(margin),
                   opex_growth: asFraction(opexGrowth) },
    } }),
    onSuccess: onResult,
  });
  function submit(e: FormEvent) { e.preventDefault(); run.mutate(); }
  const pctInput = (value: string, set: (v: string) => void, label: string) => (
    <Field label={label}>
      <input className="input" type="number" step="0.01" inputMode="decimal" placeholder="Fitted by the engine"
             value={value} onChange={(e) => set(e.target.value)} />
    </Field>
  );
  return (
    <Panel title="Run a forecast" tag="Step 2">
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
        <Field label="Months ahead">
          <input className="input" type="number" min={1} max={120} required value={horizon}
                 onChange={(e) => setHorizon(e.target.value)} />
        </Field>
        <Field label="Model">
          <select className="input" value={method} onChange={(e) => setMethod(e.target.value)}>
            {METHODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </Field>
        {pctInput(growth, setGrowth, "Revenue growth, % a month (scenario)")}
        {pctInput(margin, setMargin, "Gross margin, % (scenario)")}
        {pctInput(opexGrowth, setOpexGrowth, "Operating cost growth, % a month (scenario)")}
        <div className="flex items-end">
          <button className="btn btn-primary" disabled={run.isPending}>
            {run.isPending ? "Forecasting…" : "Run forecast"}
          </button>
        </div>
      </form>
      <ErrorNote error={run.error} />
    </Panel>
  );
}

function Result({ r }: { r: ForecastResult }) {
  const last = r.months[r.months.length - 1];
  return (
    <div className="mt-6 space-y-5">
      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard eyebrow={`Revenue, ${month(last.period)}`} value={money(last.revenue)} />
        <StatCard eyebrow={`Net income, ${month(last.period)}`} value={money(last.net_income)} />
        <StatCard eyebrow="History used" value={r.history_months} unit="months"
                  note={`Actuals to ${month(r.last_actual_period)}`} />
      </div>
      {r.warnings.length > 0 && (
        <div className="border border-bad/40 bg-bad/5 px-3 py-2 text-sm text-bad">
          {r.warnings.map((w) => <div key={w}>{w}</div>)}
        </div>
      )}
      <Panel title="Projected income statement" tag={`Forecast engine v${r.engine_version}`}>
        <div className="overflow-x-auto">
          <table className="data">
            <thead><tr>
              <th>Month</th><th className="num">Revenue</th><th className="num">Cost of sales</th>
              <th className="num">Gross profit</th><th className="num">Operating costs</th>
              <th className="num">Operating income</th><th className="num">Net income</th>
            </tr></thead>
            <tbody>
              {r.months.map((m) => (
                <tr key={m.period}>
                  <td>{month(m.period)}</td><td className="num">{money(m.revenue)}</td>
                  <td className="num">{money(m.cogs)}</td><td className="num">{money(m.gross_profit)}</td>
                  <td className="num">{money(m.opex)}</td><td className="num">{money(m.operating_income)}</td>
                  <td className="num">{money(m.net_income)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Assumptions" tag="How each was set">
          <table className="data">
            <tbody>
              {Object.entries(r.drivers).map(([k, d]) => (
                <tr key={k}>
                  <td>
                    <div className="font-semibold">{DRIVER_LABELS[k] ?? k} {d.overridden && <Chip>scenario</Chip>}</div>
                    <div className="text-xs text-muted">{d.method}</div>
                  </td>
                  <td className="num">{driverValue(k, d.value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
        <Panel title="Models" tag="Chosen by backtest">
          {Object.entries(r.models).map(([line, m]) => (
            <div key={line} className="mb-4 text-sm">
              <div className="font-semibold capitalize">{line}: {m.label}</div>
              <div className="text-muted">{m.reason}</div>
              <div className="text-xs text-faint">{m.seasonality.note}</div>
              {m.scores.length > 0 && (
                <table className="data mt-2">
                  <thead><tr><th>Model</th><th className="num">Error (WAPE)</th><th className="num">Bias</th></tr></thead>
                  <tbody>
                    {m.scores.map((s) => (
                      <tr key={s.method}>
                        <td>{s.method}{s.method === m.method && " ✓"}</td>
                        <td className="num">{s.wape === null ? s.status : pct(s.wape)}</td>
                        <td className="num">{s.bias === null ? "—" : pct(s.bias)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          ))}
          {Object.keys(r.models).length === 0 && <div className="text-sm text-muted">Driver formulas only.</div>}
        </Panel>
      </div>
      <Panel title="Formulas">
        <ul className="space-y-1 font-mono text-[13px]">
          {Object.entries(r.formulas).map(([k, f]) => <li key={k}>{f}</li>)}
        </ul>
      </Panel>
    </div>
  );
}

function SavedRuns({ onOpen }: { onOpen: (id: string) => void }) {
  const api = useApi();
  const runs = useQuery({ queryKey: ["forecast-runs"], queryFn: () => api<ForecastSummary[]>("/forecast/runs") });
  if (runs.isLoading) return null;
  if (runs.error) return <ErrorNote error={runs.error} />;
  if (!runs.data?.length) return null;
  return (
    <Panel title="Saved forecasts" className="mt-6">
      <table className="data">
        <thead><tr><th>Run</th><th>Months ahead</th><th>Models</th><th>Scenario</th><th /></tr></thead>
        <tbody>
          {runs.data.map((s) => (
            <tr key={s.id}>
              <td>{dateTime(s.created_at)}</td>
              <td>{s.horizon_months}</td>
              <td className="text-xs">{Object.entries(s.methods).map(([k, v]) => `${k}: ${v}`).join(", ") || "—"}</td>
              <td className="text-xs">{Object.entries(s.overrides).map(([k, v]) =>
                `${DRIVER_LABELS[k] ?? k} ${FRACTION_DRIVERS.has(k) ? pct(v, 2) : num(v, 0)}`).join(", ") || "Fitted"}</td>
              <td><button className="text-sm underline" onClick={() => onOpen(s.id)}>Open</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

export default function Forecast() {
  const api = useApi();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["forecast-status"], queryFn: () => api<ForecastStatus>("/forecast") });
  const [result, setResult] = useState<ForecastResult | null>(null);
  const open = useMutation({
    mutationFn: (id: string) => api<ForecastResult>(`/forecast/runs/${id}`),
    onSuccess: setResult,
  });
  const header = (
    <div className="mb-5">
      <div className="eyebrow">Financial forecast</div>
      <h1 className="text-4xl">Forecast</h1>
      <p className="mt-1 max-w-2xl text-sm text-muted">
        Upload the business&apos;s monthly financials and project them forward. The forecast engine fits
        every assumption and picks each line&apos;s model by backtest; set a scenario to override any of them.
      </p>
    </div>
  );
  if (status.isLoading) return <>{header}<Loading what="Connecting to the forecast engine" /></>;
  if (status.error) return <>{header}<ErrorNote error={status.error} /></>;
  if (!status.data?.configured || !status.data.reachable) {
    return (
      <>{header}<Empty>
        {status.data?.configured
          ? "The forecast engine isn't answering right now. Please try again in a moment."
          : "The forecast engine isn't connected yet. Set FORECAST_API_URL (and FORECAST_API_KEY) on the API."}
      </Empty></>
    );
  }
  const done = (r: ForecastResult) => { setResult(r); qc.invalidateQueries({ queryKey: ["forecast-runs"] }); };
  return (
    <>
      {header}
      <div className="grid gap-5 lg:grid-cols-[2fr_3fr]">
        <UploadPanel onDone={() => qc.invalidateQueries({ queryKey: ["forecast-runs"] })} />
        <ScenarioPanel onResult={done} />
      </div>
      <ErrorNote error={open.error} />
      {result && <Result r={result} />}
      <SavedRuns onOpen={(id) => open.mutate(id)} />
    </>
  );
}
