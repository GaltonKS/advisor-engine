// Operator-only pilot report: usage across every firm, as counts. Every figure comes from
// GET /operator/metrics; this page only lays them out and formats them.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Empty, ErrorNote, Loading, Panel, StatCard } from "../components/ui";
import { useApi } from "../lib/api";
import { date, dateTime, num } from "../lib/format";
import type { AdviserReview, PilotMetrics as Metrics } from "../lib/types";

type Pending = Metrics["pending"][number];

/** FINRA BrokerCheck, where every CRD number is verified. */
export const BROKERCHECK_URL = "https://brokercheck.finra.org/";
export const brokerCheckFor = (crd: string) => `https://brokercheck.finra.org/individual/summary/${crd}`;
type Presets = Metrics["review_presets"];

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

      <Panel title="Approval queue" tag="verify on FINRA BrokerCheck, then review" className="mb-6">
        {d.pending.length === 0 ? <Empty>Nobody is waiting for approval.</Empty> : (
          <div className="space-y-3">
            {d.pending.map((p) => <QueueRow key={p.email} p={p} presets={d.review_presets} />)}
          </div>
        )}
      </Panel>

      <Panel title="Approval audit trail" tag="every decision · can't be edited or deleted">
        <p className="mb-3 text-sm text-muted">
          Every approval is checked against FINRA BrokerCheck:{" "}
          <a className="underline" href={BROKERCHECK_URL} target="_blank" rel="noreferrer">{BROKERCHECK_URL}</a>
        </p>
        {d.reviews.length === 0 ? <Empty>No approvals or rejections yet.</Empty> : (
          <div className="overflow-x-auto">
            <table className="data w-full">
              <thead><tr><th>When</th><th>Adviser</th><th>Decision</th><th>Reviewed by</th><th>Record</th></tr></thead>
              <tbody>
                {d.reviews.map((r, i) => <ReviewRow key={i} r={r} />)}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-3 text-xs text-muted">
          "sql:…" means approved in the Supabase SQL Editor or with the admin command, where no checks are recorded.
          Approve from the queue above to keep the full record.
        </p>
      </Panel>
    </div>
  );
}

function ReviewRow({ r }: { r: AdviserReview }) {
  const approved = r.decision === "approved";
  return (
    <tr className="align-top">
      <td className="whitespace-nowrap">{dateTime(r.reviewed_at)}</td>
      <td>{r.adviser_name ?? "—"}<div className="text-xs text-muted">{r.adviser_email}</div></td>
      <td><span className={`text-sm font-semibold ${approved ? "text-good" : "text-bad"}`}>{approved ? "Approved" : "Rejected"}</span></td>
      <td className="text-sm">{r.reviewed_by}</td>
      <td className="text-sm">
        {r.summary}
        {r.note && <div className="mt-1 text-xs text-muted">Note: {r.note}</div>}
        <div className="mt-1 text-xs">
          <a className="underline" target="_blank" rel="noreferrer"
             href={r.crd ? brokerCheckFor(r.crd) : BROKERCHECK_URL}>
            {r.crd ? `View CRD ${r.crd} on BrokerCheck ↗` : "Search BrokerCheck ↗"}
          </a>
        </div>
      </td>
    </tr>
  );
}

/** One pending sign-up, with the review form (BrokerCheck checks, or a rejection reason). */
function QueueRow({ p, presets }: { p: Pending; presets: Presets }) {
  const api = useApi();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [checks, setChecks] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const review = useMutation({
    mutationFn: (decision: "approved" | "rejected") => api<AdviserReview>("/operator/reviews", {
      json: { email: p.email, decision, checks: decision === "approved" ? checks : [],
              reason: decision === "rejected" ? reason : null, note } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["operator-metrics"] }),
  });
  const allChecked = presets.checks.every((c) => checks.includes(c.key));
  const canReject = Boolean(reason) && (reason !== "other" || note.trim().length > 0);
  const toggle = (key: string) =>
    setChecks((cs) => (cs.includes(key) ? cs.filter((k) => k !== key) : [...cs, key]));

  return (
    <div className="border border-rule bg-paper p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="font-semibold">{p.full_name ?? "Registration not submitted yet"}</div>
          <div className="text-sm text-muted">
            {p.email} · {p.firm_name ?? "no firm given"} ·{" "}
            {p.submitted_at ? `submitted ${dateTime(p.submitted_at)}` : `signed up ${date(p.signed_up_at)}`}
          </div>
        </div>
        <div className="flex items-center gap-3">
          {p.crd && (
            <a className="figure text-sm underline" target="_blank" rel="noreferrer"
               href={brokerCheckFor(p.crd)}>CRD {p.crd} on BrokerCheck ↗</a>
          )}
          {p.submitted_at && (
            <button className="btn" onClick={() => setOpen(!open)}>{open ? "Close" : "Review"}</button>
          )}
        </div>
      </div>

      {open && (
        <div className="mt-4 grid gap-5 border-t border-rule pt-4 md:grid-cols-2">
          <fieldset>
            <legend className="mb-2 text-sm font-semibold">To approve, confirm on BrokerCheck</legend>
            {presets.checks.map((c) => (
              <label key={c.key} className="mb-1.5 flex items-start gap-2 text-sm">
                <input type="checkbox" className="mt-1" checked={checks.includes(c.key)} onChange={() => toggle(c.key)} />
                <span>{c.label}</span>
              </label>
            ))}
            <button className="btn btn-primary mt-2" disabled={!allChecked || review.isPending}
                    onClick={() => review.mutate("approved")}>Approve</button>
          </fieldset>
          <fieldset>
            <legend className="mb-2 text-sm font-semibold">Or reject</legend>
            <select className="input mb-2" value={reason} onChange={(e) => setReason(e.target.value)}>
              <option value="">Choose a reason…</option>
              {presets.reject_reasons.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
            </select>
            <button className="btn btn-danger" disabled={!canReject || review.isPending}
                    onClick={() => review.mutate("rejected")}>Reject</button>
          </fieldset>
          <label className="block text-sm md:col-span-2">
            <span className="mb-1 block font-semibold">Note (optional; required for "Other")</span>
            <input className="input" maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)}
                   placeholder="e.g. IAR at Smith Wealth since 2019; confirmed by phone with the main office" />
          </label>
          <div className="text-xs text-muted md:col-span-2">
            Recorded automatically: your sign-in email as the reviewer, the time, and a summary of the checks.
          </div>
          <div className="md:col-span-2"><ErrorNote error={review.error} /></div>
        </div>
      )}
    </div>
  );
}
