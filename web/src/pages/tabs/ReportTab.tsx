import { useQuery } from "@tanstack/react-query";
import { Empty, Loading } from "../../components/ui";
import { useMe } from "../../components/Layout";
import { useApi } from "../../lib/api";
import { date, dateTime, lossPct, lossSigned, num, pct, shortHash } from "../../lib/format";
import type { Draft } from "../../lib/types";
import type { Workspace } from "../ClientWorkspace";

// A printable page: the approved explanation plus the engine figures it rests on.
// "Save as PDF" is the browser's print dialog, so the PDF is exactly this page.
export default function ReportTab({ ws }: { ws: Workspace }) {
  const api = useApi();
  const me = useMe();
  const drafts = useQuery({ queryKey: ["drafts", ws.portfolio.id], queryFn: () => api<Draft[]>(`/portfolios/${ws.portfolio.id}/drafts`) });
  if (drafts.isLoading) return <Loading />;
  const approved = drafts.data?.find((d) => d.approved);
  if (!approved) {
    return (
      <Empty>
        The client report uses an approved explanation. Draft one in the Assistant tab, edit it, and approve it.{" "}
        <button className="underline" onClick={() => ws.go("assistant")}>Go to the assistant →</button>
      </Empty>
    );
  }
  const p = ws.profile;
  const qr = ws.client.questionnaire_result;
  return (
    <div>
      <div className="no-print mb-4 flex justify-end">
        <button className="btn btn-primary" onClick={() => window.print()}>Print / save as PDF</button>
      </div>
      <article className="mx-auto max-w-3xl border border-rule bg-white p-10 print:border-0 print:p-0">
        <div className="eyebrow">Portfolio risk review · {date(approved.approved_at ?? approved.created_at)}</div>
        <h1 className="mt-1 text-4xl">{ws.client.name}</h1>
        {ws.client.objective && <p className="text-muted">{ws.client.objective}</p>}

        <div className="my-6 whitespace-pre-wrap font-serif text-[17px] leading-relaxed">{approved.approved_text}</div>

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
          Approved {approved.approved_at ? dateTime(approved.approved_at) : ""}{me.data && approved.approved_by === me.data.adviser.id ? ` by ${me.data.adviser.email}` : ""} ·
          engine v{p.engine_version} · holdings {shortHash(ws.portfolio.sha256)} · draft {approved.id.slice(0, 8)}.
          Every figure was computed by the engine and checked before approval.
        </div>
      </article>
    </div>
  );
}
