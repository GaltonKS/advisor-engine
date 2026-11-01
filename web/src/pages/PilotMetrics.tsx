// Operator-only pilot report: usage across every firm, as counts. Every figure comes from
// GET /operator/metrics; this page only lays them out and formats them.

import { useQuery } from "@tanstack/react-query";
import { Empty, ErrorNote, Loading, Panel, StatCard } from "../components/ui";
import { useApi } from "../lib/api";
import { date, dateTime, num } from "../lib/format";
import type { PilotMetrics as Metrics } from "../lib/types";

/** Audit events in plain words. Unknown events show their code. */
const EVENT_LABELS: Record<string, string> = {
  turn: "Galton AI questions",
  what_if: "What-if runs",
  compare: "Version comparisons",
  stress: "Stress tests",
  scenario_suggested: "AI scenario suggestions",
  draft_approved: "Client explanations approved",
  questionnaire_saved: "Risk questionnaires saved",
  portfolio_saved: "Holdings edited and saved",
  client_renamed: "Clients renamed",
  client_deleted: "Clients deleted",
};

const FUNNEL: [keyof Metrics["funnel"], string, string][] = [
  ["signed_up", "Signed up", "Created an account"],
  ["registered", "Registered", "Submitted name, CRD and firm for review"],
  ["approved", "Approved", "Active advisers (includes accounts from before approval existed)"],
  ["added_client", "Added a client", "Created at least one client"],
  ["ran_analysis", "Ran an analysis", "Asked Galton AI, or ran a what-if, comparison or stress test"],
];

export default function PilotMetrics() {
  const api = useApi();
  const m = useQuery({ queryKey: ["operator-metrics"], queryFn: () => api<Metrics>("/operator/metrics") });
  if (m.isLoading) return <Loading />;
  if (m.error || !m.data) return <ErrorNote error={m.error ?? "Couldn't load the pilot metrics."} />;
  const d = m.data;
  return (
    <div>
      <div className="eyebrow">Operator only · every firm · counts, no client data</div>
      <h1 className="mb-1 text-4xl">Pilot metrics</h1>
      <p className="mb-6 text-sm text-muted">
        From the audit log, which records actions (questions, analyses, approvals), not page views.
        An adviser who only looked at screens in a week isn't counted as active.
      </p>

      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard eyebrow="Active advisers" value={d.totals.active_advisers}
                  note={`across ${d.totals.firms} firm${d.totals.firms === 1 ? "" : "s"}`} />
        <StatCard eyebrow="Awaiting approval" value={d.totals.pending_advisers}
                  note={d.avg_approval_hours === null ? "no approvals timed yet"
                    : `past approvals took ${num(d.avg_approval_hours)} hours on average`} />
        <StatCard eyebrow="Clients" value={d.totals.clients} note="not counting deleted clients" />
        <StatCard eyebrow="Galton AI questions" value={d.totals.questions} note={`last ${d.feature_days} days`} />
      </div>

      <div className="mb-6 grid gap-5 lg:grid-cols-2">
        <Panel title="Weekly active advisers" tag={`last ${d.weeks} weeks`}>
          <table className="data w-full">
            <thead><tr><th>Week of</th><th className="text-right">Active advisers</th></tr></thead>
            <tbody>
              {[...d.weekly_active].reverse().map((w) => (
                <tr key={w.week}><td>{date(w.week)}</td><td className="figure text-right">{w.advisers}</td></tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <Panel title="Feature use" tag={`last ${d.feature_days} days`}>
          {d.features.length === 0 ? <Empty>No activity yet.</Empty> : (
            <table className="data w-full">
              <thead><tr><th>Action</th><th className="text-right">Times</th><th className="text-right">Advisers</th></tr></thead>
              <tbody>
                {d.features.map((f) => (
                  <tr key={f.event}>
                    <td>{EVENT_LABELS[f.event] ?? f.event}</td>
                    <td className="figure text-right">{f.uses}</td>
                    <td className="figure text-right">{f.advisers}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>

      <Panel title="Sign-up funnel" tag="all time" className="mb-6">
        <ol className="grid gap-3 sm:grid-cols-5">
          {FUNNEL.map(([key, label, help], i) => (
            <li key={key} className="border border-rule bg-paper px-4 py-3">
              <div className="eyebrow">{i + 1} · {label}</div>
              <div className="figure my-1 text-3xl">{d.funnel[key]}</div>
              <div className="text-xs text-muted">{help}</div>
            </li>
          ))}
        </ol>
      </Panel>

      <Panel title="Approval queue" tag="verify the CRD on FINRA BrokerCheck, then approve">
        {d.pending.length === 0 ? <Empty>Nobody is waiting for approval.</Empty> : (
          <div className="overflow-x-auto">
            <table className="data w-full">
              <thead><tr><th>Email</th><th>Name</th><th>CRD</th><th>Firm</th><th>Submitted</th><th>Signed up</th></tr></thead>
              <tbody>
                {d.pending.map((p) => (
                  <tr key={p.email}>
                    <td>{p.email}</td>
                    <td>{p.full_name ?? "—"}</td>
                    <td className="figure">
                      {p.crd ? <a className="underline" target="_blank" rel="noreferrer"
                                  href={`https://brokercheck.finra.org/individual/summary/${p.crd}`}>{p.crd}</a> : "—"}
                    </td>
                    <td>{p.firm_name ?? "—"}</td>
                    <td>{p.submitted_at ? dateTime(p.submitted_at) : "not yet"}</td>
                    <td>{date(p.signed_up_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-3 text-xs text-muted">
          Approve in the Supabase SQL Editor: <code className="figure">select * from app.approve_adviser('email');</code>
        </p>
      </Panel>
    </div>
  );
}
