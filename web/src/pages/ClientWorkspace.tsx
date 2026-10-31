import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Empty, ErrorNote, Loading } from "../components/ui";
import { useApi } from "../lib/api";
import { date, shortHash } from "../lib/format";
import type { Client, Portfolio, RiskProfile } from "../lib/types";
import AssistantTab from "./tabs/AssistantTab";
import AuditTab from "./tabs/AuditTab";
import CompareTab from "./tabs/CompareTab";
import HoldingsTab from "./tabs/HoldingsTab";
import OverviewTab from "./tabs/OverviewTab";
import PositionsTab from "./tabs/PositionsTab";
import ProfileTab from "./tabs/ProfileTab";
import ReportTab from "./tabs/ReportTab";
import ReviewTab from "./tabs/ReviewTab";
import StressTab from "./tabs/StressTab";

export interface Workspace {
  client: Client;
  portfolios: Portfolio[];
  portfolio: Portfolio;
  profile: RiskProfile;
  /** Switch tab, optionally sending a question to the assistant. */
  go: (tab: TabId, ask?: string) => void;
}

const TABS = [
  ["overview", "Overview"],
  ["positions", "Positions"],
  ["holdings", "Holdings & what-if"],
  ["compare", "Compare"],
  ["stress", "Stress test"],
  ["profile", "Risk profile"],
  ["assistant", "Assistant"],
  ["report", "Client report"],
  ["review", "Review prep"],
  ["audit", "Audit trail"],
] as const;
export type TabId = (typeof TABS)[number][0];

export function versionName(p: Portfolio, all: Portfolio[]): string {
  const n = all.findIndex((x) => x.id === p.id) + 1;
  const what = p.label || (p.source === "editor" ? "Edited holdings" : p.filename);
  return `v${n} · ${what} · ${date(p.created_at)}`;
}

export default function ClientWorkspace() {
  const { clientId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const api = useApi();
  const qc = useQueryClient();
  const upload = useRef<HTMLInputElement>(null);

  const client = useQuery({ queryKey: ["client", clientId], queryFn: () => api<Client>(`/clients/${clientId}`) });
  const portfolios = client.data?.portfolios ?? [];
  const pid = params.get("p") ?? portfolios.at(-1)?.id ?? null;
  const portfolio = portfolios.find((p) => p.id === pid) ?? portfolios.at(-1);
  const tab = (params.get("tab") as TabId | null) ?? "overview";

  const profile = useQuery({
    queryKey: ["profile", portfolio?.id],
    queryFn: () => api<{ profile: RiskProfile }>(`/portfolios/${portfolio!.id}/profile`).then((r) => r.profile),
    enabled: Boolean(portfolio),
  });

  const addVersion = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api<Portfolio>(`/clients/${clientId}/portfolios`, { form });
    },
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["client", clientId] });
      setParams({ p: p.id, tab });
    },
  });

  function go(next: TabId, ask?: string) {
    const nextParams: Record<string, string> = { tab: next };
    if (portfolio) nextParams.p = portfolio.id;
    if (ask) nextParams.ask = ask;
    setParams(nextParams);
  }

  if (client.isLoading) return <Loading />;
  if (client.error || !client.data) return <ErrorNote error={client.error ?? "Client not found."} />;
  const c = client.data;
  const qr = c.questionnaire_result;

  return (
    <div>
      <div className="no-print mb-2 text-sm"><Link to="/" className="text-muted">← Clients</Link></div>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="eyebrow">
            Client analysis{profile.data ? ` · ${profile.data.returns_source} returns, illustrative` : ""}
          </div>
          <h1 className="text-4xl md:text-5xl">{c.name}</h1>
          <div className="mt-1 text-muted">
            {[c.objective, profile.data && `${profile.data.concentration.n_positions} holdings`,
              qr && `Risk level ${qr.risk_level} / ${qr.risk_level_max}`].filter(Boolean).join(" · ")}
          </div>
        </div>
        <div className="no-print flex flex-wrap items-center gap-2">
          {portfolios.length > 0 && portfolio && (
            <select className="input !w-auto" value={portfolio.id} aria-label="Portfolio version"
                    onChange={(e) => setParams({ p: e.target.value, tab })}>
              {portfolios.map((p) => <option key={p.id} value={p.id}>{versionName(p, portfolios)}</option>)}
            </select>
          )}
          <input ref={upload} type="file" accept=".csv,text/csv" className="hidden"
                 onChange={(e) => e.target.files?.[0] && addVersion.mutate(e.target.files[0])} />
          <button className="btn" onClick={() => upload.current?.click()} disabled={addVersion.isPending}>
            {addVersion.isPending ? "Uploading…" : "Upload holdings"}
          </button>
        </div>
      </div>
      <ErrorNote error={addVersion.error} />

      {!portfolio ? (
        <div className="mt-6"><Empty>Upload a holdings CSV (ticker, shares, price, sector) to analyse this client.{" "}
          <a className="underline" href="/holdings-template.csv" download>Download a template</a></Empty></div>
      ) : (
        <>
          <nav className="no-print mt-6 flex flex-wrap gap-x-5 gap-y-2 border-b border-rule text-sm">
            {TABS.map(([id, label]) => (
              <button key={id} onClick={() => go(id)}
                      className={`-mb-px pb-2 ${tab === id ? "border-b-2 border-gold font-semibold" : "text-muted hover:text-ink"}`}>
                {label}
              </button>
            ))}
          </nav>
          <div className="mt-6">
            {profile.isLoading && <Loading what="Running the engine" />}
            <ErrorNote error={profile.error} />
            {profile.data && (
              <TabBody tab={tab} ws={{ client: c, portfolios, portfolio, profile: profile.data, go }} />
            )}
          </div>
          <div className="no-print mt-8 text-xs text-faint">
            Holdings file {portfolio.filename} · {shortHash(portfolio.sha256)} · engine v{profile.data?.engine_version}
          </div>
        </>
      )}
    </div>
  );
}

function TabBody({ tab, ws }: { tab: TabId; ws: Workspace }) {
  switch (tab) {
    case "positions": return <PositionsTab ws={ws} />;
    case "holdings": return <HoldingsTab ws={ws} />;
    case "compare": return <CompareTab ws={ws} />;
    case "stress": return <StressTab ws={ws} />;
    case "profile": return <ProfileTab ws={ws} />;
    case "assistant": return <AssistantTab ws={ws} />;
    case "report": return <ReportTab ws={ws} />;
    case "review": return <ReviewTab ws={ws} />;
    case "audit": return <AuditTab ws={ws} />;
    default: return <OverviewTab ws={ws} />;
  }
}
