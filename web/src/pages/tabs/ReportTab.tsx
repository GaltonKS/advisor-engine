import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Empty, Loading, Panel } from "../../components/ui";
import { useMe } from "../../components/Layout";
import { useApi } from "../../lib/api";
import { date, dateTime, lossPct, lossSigned, num, pct, shortHash } from "../../lib/format";
import { Markdown } from "../../lib/markdown";
import type { Draft } from "../../lib/types";
import type { Workspace } from "../ClientWorkspace";
import { DraftEditor, useUnsavedEdits } from "./DraftEditor";

/** Which report to show: the one named in the URL (`report=<draft id>`), else the
 *  newest approved one, else the newest draft. Drafts come newest first. */
export function pickReport(drafts: Draft[], wanted: string | null): Draft | undefined {
  return drafts.find((d) => d.id === wanted) ?? drafts.find((d) => d.approved) ?? drafts[0];
}

export function reportLabel(d: Draft, all: Draft[]): string {
  const n = all.length - all.indexOf(d);
  const when = date(d.approved_at ?? d.created_at);
  return `Report ${n} · ${when} · ${d.approved ? "approved" : "not approved yet"}`;
}

// A printable page: the approved explanation plus the engine figures it rests on.
// "Save as PDF" is the browser's print dialog, so the PDF is exactly this page.
// Every explanation drafted for this portfolio stays here, newest first, and can be
// reopened and edited. Edits are re-checked and re-approved before they print.
export default function ReportTab({ ws }: { ws: Workspace }) {
  const api = useApi();
  const me = useMe();
  const [params, setParams] = useSearchParams();
  const [editing, setEditing] = useState(false);
  const drafts = useQuery({ queryKey: ["drafts", ws.portfolio.id], queryFn: () => api<Draft[]>(`/portfolios/${ws.portfolio.id}/drafts`) });
  const all = drafts.data ?? [];
  const report = pickReport(all, params.get("report"));
  const unsaved = useUnsavedEdits(report);
  if (drafts.isLoading) return <Loading />;
  if (!report) {
    return (
      <Empty>
        The client report uses an approved explanation. Draft one in the Galton AI tab, edit it, and approve it.{" "}
        <button className="underline" onClick={() => ws.go("assistant")}>Go to Galton AI →</button>
      </Empty>
    );
  }
  function choose(id: string) {
    params.set("report", id);
    setParams(params, { replace: true });
    setEditing(false);
  }
  const p = ws.profile;
  const qr = ws.client.questionnaire_result;
  const showEditor = editing || !report.approved;
  return (
    <div>
      <div className="no-print mb-4 flex flex-wrap items-center justify-between gap-3">
        {all.length > 1 ? (
          <select className="input !w-auto" value={report.id} aria-label="Report version" onChange={(e) => choose(e.target.value)}>
            {all.map((d) => <option key={d.id} value={d.id}>{reportLabel(d, all)}</option>)}
          </select>
        ) : <span />}
        <div className="flex flex-wrap items-center gap-2">
          {report.approved && (
            <button className="btn" onClick={() => setEditing(!editing)}>{editing ? "Done editing" : "Edit text"}</button>
          )}
          <button className="btn btn-primary" onClick={() => window.print()} disabled={!report.approved || unsaved}
                  title={!report.approved ? "Approve the explanation first" : unsaved ? "Save or discard your changes first" : undefined}>
            Print / save as PDF
          </button>
        </div>
      </div>
      {showEditor && (
        <div className="no-print mx-auto mb-5 max-w-3xl">
          <Panel title={report.approved ? "Edit the explanation" : "Approve this explanation"}
                 tag={report.approved ? "Saved changes are re-checked and re-approved" : "Not approved yet: it won't print until you approve it"}>
            <DraftEditor key={report.id} draft={report} />
          </Panel>
        </div>
      )}
      {report.approved && unsaved && (
        <p className="no-print mx-auto mb-3 max-w-3xl text-sm text-muted">The page below shows the approved text. Save your changes to update it.</p>
      )}
      {report.approved && (
      <article className="mx-auto max-w-3xl border border-rule bg-white p-10 print:border-0 print:p-0">
        <div className="eyebrow">Portfolio risk review · {date(report.approved_at ?? report.created_at)}</div>
        <h1 className="mt-1 text-4xl">{ws.client.name}</h1>
        {ws.client.objective && <p className="text-muted">{ws.client.objective}</p>}

        <Markdown text={report.approved_text ?? ""} className="my-6" />

        <h3 className="mt-8 border-b border-rule pb-1 text-xl">Key figures</h3>
        <table className="data mt-2">
          <tbody>
            <tr><td>Holdings</td><td className="num">{p.concentration.n_positions}</td></tr>
            <tr><td>Behaves like this many independent bets</td><td className="num">{num(p.concentration.effective_bets)}</td></tr>
            <tr><td>Share of risk from the largest common factor</td><td className="num">{pct(p.concentration.top_component_share)}</td></tr>
            <tr><td>Worst historical drop ({p.drawdown.period_label} data)</td><td className="num">{lossPct(p.drawdown.historical_max_drawdown)}</td></tr>
            <tr><td>Loss in a severe 1-in-100 {p.drawdown.period_label} period</td><td className="num">{lossSigned(Math.abs(p.drawdown.stress_loss_1_in_100))}</td></tr>
            {qr && <tr><td>Client risk level (questionnaire)</td><td className="num">{qr.risk_level} / {qr.risk_level_max}</td></tr>}
          </tbody>
        </table>

        <h3 className="mt-8 border-b border-rule pb-1 text-xl">Sector exposure</h3>
        <table className="data mt-2">
          <tbody>
            {Object.entries(p.sector_exposure).map(([s, w]) => <tr key={s}><td>{s}</td><td className="num">{pct(w)}</td></tr>)}
          </tbody>
        </table>

        <p className="mt-8 text-xs text-muted">
          These figures describe the past and a stress scenario. They are not a forecast and not a recommendation.
          Returns used: {p.returns_source}, {p.n_periods} {p.drawdown.period_label} periods.
        </p>
        <div className="mt-6 border-t border-rule pt-3 text-[11px] text-faint">
          Approved {report.approved_at ? dateTime(report.approved_at) : ""}{me.data && report.approved_by === me.data.adviser.id ? ` by ${me.data.adviser.email}` : ""} ·
          engine v{p.engine_version} · holdings {shortHash(ws.portfolio.sha256)} · draft {report.id.slice(0, 8)}.
          Every figure was computed by the engine and checked before approval.
        </div>
      </article>
      )}
    </div>
  );
}
