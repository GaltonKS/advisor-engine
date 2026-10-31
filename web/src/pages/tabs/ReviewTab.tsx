import { useQuery } from "@tanstack/react-query";
import { ASSISTANT_NAME, ComparisonTable, Loading, Panel } from "../../components/ui";
import { useApi } from "../../lib/api";
import { dateTime, lossPct, pct } from "../../lib/format";
import type { AuditRow, CompareResult, Draft, QuestionnaireResult, SuitabilityCheck } from "../../lib/types";
import { versionName, type Workspace } from "../ClientWorkspace";

// One page to prepare a client review: what changed since the previous version, whether
// the portfolio still fits the client's stated tolerance, and what's been done lately.
export default function ReviewTab({ ws }: { ws: Workspace }) {
  const api = useApi();
  const all = ws.portfolios;
  const idx = all.findIndex((p) => p.id === ws.portfolio.id);
  const prev = idx > 0 ? all[idx - 1] : null;

  const changes = useQuery({
    queryKey: ["compare", prev?.id, ws.portfolio.id],
    queryFn: () => api<CompareResult>(`/portfolios/${prev!.id}/compare/${ws.portfolio.id}`),
    enabled: Boolean(prev),
  });
  const fit = useQuery({
    queryKey: ["suitability", ws.portfolio.id, ws.client.questionnaire],
    queryFn: () => api<{ questionnaire: QuestionnaireResult; checks: SuitabilityCheck[] }>(`/portfolios/${ws.portfolio.id}/suitability`),
    enabled: Boolean(ws.client.questionnaire),
  });
  const drafts = useQuery({ queryKey: ["drafts", ws.portfolio.id], queryFn: () => api<Draft[]>(`/portfolios/${ws.portfolio.id}/drafts`) });
  const activity = useQuery({ queryKey: ["audit", ws.portfolio.id], queryFn: () => api<AuditRow[]>(`/audit?portfolio_id=${ws.portfolio.id}`) });

  const approved = drafts.data?.find((d) => d.approved);
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Panel title="What changed" tag={prev ? `${versionName(prev, all)} → now` : "First version"}>
        {!prev && <p className="text-sm text-muted">This is the first version of the holdings, so there's nothing to compare yet.</p>}
        {changes.isLoading && <Loading />}
        {changes.data && <ComparisonTable rows={changes.data.comparison.headline} beforeLabel="Previous" afterLabel="Current" />}
      </Panel>

      <div className="space-y-5">
        <Panel title="Fit with the client's risk profile">
          {!ws.client.questionnaire ? (
            <p className="text-sm text-muted">No questionnaire on file. <button className="underline" onClick={() => ws.go("profile")}>Take it now →</button></p>
          ) : fit.data ? (
            <>
              <p className="text-sm">Risk level <b>{fit.data.questionnaire.risk_level} / {fit.data.questionnaire.risk_level_max}</b>. {fit.data.questionnaire.method_status}</p>
              {fit.data.checks.map((c) => (
                <p key={c.id} className={`mt-2 text-sm ${c.within ? "text-good" : "text-bad"}`}>
                  {c.within ? "Within" : "Outside"} tolerance: worst historical drop {lossPct(c.portfolio_value)} vs {pct(c.client_limit, 0)} the client can accept.
                </p>
              ))}
            </>
          ) : <Loading />}
        </Panel>
        <Panel title="Client explanation">
          {approved ? (
            <p className="text-sm">Approved {approved.approved_at && dateTime(approved.approved_at)}. <button className="underline" onClick={() => ws.go("report")}>Open the client report →</button></p>
          ) : (
            <p className="text-sm text-muted">No approved explanation for this version yet. <button className="underline" onClick={() => ws.go("assistant")}>Draft one →</button></p>
          )}
        </Panel>
        <Panel title="Recent activity">
          {activity.data?.length === 0 && <p className="text-sm text-muted">Nothing recorded yet.</p>}
          <ul className="space-y-1 text-sm">
            {activity.data?.slice(0, 6).map((a, i) => (
              <li key={i} className="flex justify-between gap-3"><span>{describe(a)}</span><span className="text-xs text-faint">{dateTime(a.timestamp)}</span></li>
            ))}
          </ul>
        </Panel>
      </div>
    </div>
  );
}

export function describe(a: AuditRow): string {
  switch (a.event) {
    case "turn": return a.blocked ? `${ASSISTANT_NAME} reply withheld: “${a.user_message}”` : `${ASSISTANT_NAME}: “${a.user_message}”`;
    case "draft_approved": return "Client explanation approved";
    case "what_if": return "What-if run";
    case "compare": return "Versions compared";
    case "stress": return "Stress scenario run";
    case "questionnaire_saved": return "Risk questionnaire saved";
    case "portfolio_saved": return "Edited holdings saved as a new version";
    default: return a.event ?? "Event";
  }
}
