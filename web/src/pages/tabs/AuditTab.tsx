import { useQuery } from "@tanstack/react-query";
import { Empty, ErrorNote, Loading, Panel } from "../../components/ui";
import { useApi } from "../../lib/api";
import { dateTime } from "../../lib/format";
import type { AuditRow } from "../../lib/types";
import type { Workspace } from "../ClientWorkspace";
import { describe } from "./ReviewTab";

export default function AuditTab({ ws }: { ws: Workspace }) {
  const api = useApi();
  const rows = useQuery({ queryKey: ["audit", ws.portfolio.id], queryFn: () => api<AuditRow[]>(`/audit?portfolio_id=${ws.portfolio.id}`) });
  if (rows.isLoading) return <Loading />;
  return (
    <Panel title="Audit trail" tag="Append-only · this version">
      <ErrorNote error={rows.error} />
      {rows.data?.length === 0 ? (
        <Empty>No activity on this version yet.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="data">
            <thead><tr><th>When</th><th>What</th><th>Engine</th><th>Model</th><th>Checked</th></tr></thead>
            <tbody>
              {rows.data?.map((r, i) => (
                <tr key={i}>
                  <td className="whitespace-nowrap">{dateTime(r.timestamp)}</td>
                  <td><What r={r} ws={ws} /></td>
                  <td className="figure">v{r.engine_version}</td>
                  <td className="figure">{r.model ?? "—"}</td>
                  <td>{r.verified === null || r.verified === undefined ? "—" : r.verified ? "✓ verified" : "✗ withheld"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-3 text-xs text-muted">Every output is logged with its inputs, so you can show how a recommendation was reached. Entries can't be edited or deleted.</p>
    </Panel>
  );
}

/** Where in the conversation an audit entry happened, as Galton AI tab parameters:
 *  a turn by its id, an approval by its draft, an older turn by its question. */
export function conversationLink(r: AuditRow): Record<string, string> | null {
  if (r.event === "turn") {
    if (r.turn_id) return { turn: r.turn_id };
    return r.user_message ? { question: r.user_message } : null;
  }
  if (r.event === "draft_approved" && r.draft_id) return { draft: r.draft_id };
  return null;
}

function What({ r, ws }: { r: AuditRow; ws: Workspace }) {
  const link = conversationLink(r);
  if (!link) return <>{describe(r)}</>;
  return (
    <button className="text-left underline decoration-rule underline-offset-2 hover:text-navy hover:decoration-navy"
            title="Show this in the Galton AI conversation" onClick={() => ws.go("assistant", undefined, link)}>
      {describe(r)} <span aria-hidden className="text-faint">→</span>
    </button>
  );
}
