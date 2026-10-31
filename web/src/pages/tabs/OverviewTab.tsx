import { useQuery } from "@tanstack/react-query";
import { Bar, Panel, StatCard } from "../../components/ui";
import { useApi } from "../../lib/api";
import { assetClassLabel, lossPct, num, pct } from "../../lib/format";
import type { QuestionnaireResult, SuitabilityCheck } from "../../lib/types";
import type { Workspace } from "../ClientWorkspace";

export default function OverviewTab({ ws }: { ws: Workspace }) {
  const { profile: p, client, go } = ws;
  const conc = p.concentration;
  const dd = p.drawdown;
  const qr = client.questionnaire_result;

  return (
    <div className="space-y-5">
      <div className="grid gap-4 md:grid-cols-3">
        <StatCard eyebrow="Effective independent bets" value={num(conc.effective_bets)}
                  note={`across ${conc.n_positions} holdings: how many separate bets the portfolio behaves like.`} />
        {qr ? (
          <StatCard eyebrow="Client risk level" value={qr.risk_level} unit={`/ ${qr.risk_level_max}`}
                    note={<>Set from the client's risk questionnaire. <span className="text-faint">{qr.method_status}</span></>} />
        ) : (
          <button className="text-left" onClick={() => go("profile")}>
            <StatCard eyebrow="Client risk level" value="—"
                      note={<span className="underline">Not assessed yet. Take the risk questionnaire →</span>} />
          </button>
        )}
        <StatCard eyebrow="Largest single source of risk" value={pct(conc.top_component_share)}
                  note="of total risk comes from one common factor shared by the holdings." />
      </div>

      {qr && <SuitabilityBanner ws={ws} />}

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Where the risk comes from">
          <p className="mb-2 text-[13px] text-muted">Share of the portfolio's risk by independent source, largest first.</p>
          {conc.component_shares.map((x, i) => (
            <Bar key={i} label={i === 0 ? "Largest common factor" : `Factor ${i + 1}`} shown={pct(x)} fraction={x} />
          ))}
          <p className="mt-4 border-t border-rule pt-3 text-xs text-muted">
            Method: principal component analysis on a shrunk covariance of {p.n_periods} {dd.period_label} returns
            ({p.returns_source}). Factors are statistical, not named markets or styles.
          </p>
        </Panel>
        <Panel title="Sector exposure">
          {Object.entries(p.sector_exposure).map(([s, w]) => (
            <Bar key={s} label={s} shown={pct(w)} fraction={w} />
          ))}
          <p className="mt-3 text-xs text-muted">
            {conc.sector_level.sector_effective_bets !== undefined
              ? <>Across {conc.sector_level.n_sectors} sectors the portfolio behaves like {num(conc.sector_level.sector_effective_bets)} independent sector bets.</>
              : <>Sector-level diversification is not computed until every holding has a sector.</>}
          </p>
        </Panel>
      </div>

      {p.asset_class_exposure && Object.keys(p.asset_class_exposure).some((c) => c !== "equity") && (
        <Panel title="Asset classes">
          {Object.entries(p.asset_class_exposure).map(([c, w]) => (
            <Bar key={c} label={assetClassLabel(c)} shown={pct(w)} fraction={w} />
          ))}
          <p className="mt-3 text-xs text-muted">
            Bonds, preferred stock and cash are modelled with their own (illustrative) return behaviour, so they
            lower the portfolio's risk rather than moving like stocks.
          </p>
        </Panel>
      )}

      <Panel title="Downside" tag={`${dd.period_label} basis`}>
        <div className="grid gap-4 sm:grid-cols-3">
          <Figure label="Worst historical drop" value={lossPct(dd.historical_max_drawdown)} note="Largest peak-to-trough loss in the history analysed." />
          <Figure label="1-in-100 stress loss" value={lossPct(dd.stress_loss_1_in_100)} note={`Estimated loss over one ${dd.period_label} period in a severe scenario.`} />
          <Figure label={`Typical ${dd.period_label} volatility`} value={pct(dd.period_volatility)} note="How much the value usually moves in one period." />
        </div>
        <p className="mt-3 text-xs text-muted">These describe history and a stress scenario. They are not forecasts.</p>
      </Panel>
    </div>
  );
}

function Figure({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div>
      <div className="eyebrow !text-muted">{label}</div>
      <div className="font-serif text-3xl">{value}</div>
      <div className="text-xs text-muted">{note}</div>
    </div>
  );
}

function SuitabilityBanner({ ws }: { ws: Workspace }) {
  const api = useApi();
  const s = useQuery({
    queryKey: ["suitability", ws.portfolio.id, ws.client.questionnaire],
    queryFn: () => api<{ questionnaire: QuestionnaireResult; checks: SuitabilityCheck[] }>(`/portfolios/${ws.portfolio.id}/suitability`),
  });
  if (!s.data) return null;
  return (
    <div className="space-y-2">
      {s.data.checks.map((c) => (
        <div key={c.id} className={`border-l-4 px-4 py-3 text-sm ${c.within ? "border-good bg-good/5" : "border-bad bg-bad/5"}`}>
          <b>{c.within ? "Within the client's stated tolerance." : "Outside the client's stated tolerance."}</b>{" "}
          {c.label}: the portfolio's worst historical drop was {lossPct(c.portfolio_value)}; the client said they could accept {pct(c.client_limit, 0)}.
        </div>
      ))}
    </div>
  );
}
