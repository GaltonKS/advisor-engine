// Charts drawn from engine chart specs (src/advisor_engine/charts.py). Every number on a
// chart comes from the spec; code here only positions marks and formats values with
// lib/format. Design rules (sizes, colours, legends) are in CLAUDE.md under "Charts".

import { useRef, useState, type MouseEvent, type ReactNode } from "react";
import { lossSigned, num, pct, sourceLine } from "../lib/format";
import type { ChartSpec, RiskByGroupChart, RiskSource, RiskVsSizeChart, WhatIfChart } from "../lib/types";

/** Drawn width of every chart: the chat column, so 12-unit labels display at 12px. */
export const VW = 700;
const RAMP = ["var(--viz-ramp-1)", "var(--viz-ramp-2)", "var(--viz-ramp-3)", "var(--viz-ramp-4)"];
// Text on the two dark ramp steps is light; on the two light steps it is ink.
const ON_RAMP = ["var(--viz-surface)", "var(--viz-surface)", "var(--viz-ink)", "var(--viz-ink)"];
const FONT = 12;

const ratio = (x: number) => `${num(x, 2)}×`;

/** Axis ticks from 0 to `max` (an engine-chosen range), every `step`. */
function ticks(max: number, step: number): number[] {
  const out: number[] = [];
  for (let i = 0; i * step <= max + 1e-9; i++) out.push(+(i * step).toFixed(6));
  return out;
}
const stepFor = (max: number) => (max > 0.4 ? 0.1 : max > 0.16 ? 0.05 : 0.02);

/** A horizontal bar whose far end is rounded and whose baseline end is square. */
function hbar(x: number, y: number, w: number, h: number, r = 4): string {
  if (w <= 0) return "";
  const rr = Math.min(r, w, h / 2);
  return `M${x},${y}h${w - rr}a${rr},${rr} 0 0 1 ${rr},${rr}v${h - 2 * rr}a${rr},${rr} 0 0 1 -${rr},${rr}h-${w - rr}z`;
}

// --- frame: title, legend, hover tooltip, table view, source line -----------------------

interface Tip { x: number; y: number; body: ReactNode }

function useTip() {
  const box = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip | null>(null);
  const show = (e: MouseEvent, body: ReactNode) => {
    const r = box.current?.getBoundingClientRect();
    if (r) setTip({ x: e.clientX - r.left, y: e.clientY - r.top, body });
  };
  return { box, tip, show, hide: () => setTip(null) };
}

function Frame({ spec, legend, table, children }: {
  spec: ChartSpec; legend?: ReactNode; table: ReactNode; children: (t: ReturnType<typeof useTip>) => ReactNode;
}) {
  const t = useTip();
  const [asTable, setAsTable] = useState(false);
  return (
    <figure className="viz-root my-3 border border-rule p-5" aria-label={spec.title}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-mono text-xs uppercase tracking-[0.12em]" style={{ color: "var(--viz-gold-text)" }}>
            {spec.source.returns_source === "synthetic" ? "Illustrative · synthetic returns" : "Engine output"}
          </div>
          <h4 className="mt-1 font-serif text-[22px] leading-tight">{spec.title}</h4>
          <p className="mt-0.5 text-sm" style={{ color: "var(--viz-text-2)" }}>{spec.subtitle}</p>
        </div>
        <button className="btn !px-2 !py-1 text-xs" onClick={() => setAsTable(!asTable)} aria-pressed={asTable}>
          {asTable ? "Chart" : "Table"}
        </button>
      </div>
      {legend && <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1 text-[13px]">{legend}</div>}
      {asTable ? <div className="mt-3 overflow-x-auto">{table}</div> : (
        <div ref={t.box} className="relative mt-2" onMouseLeave={t.hide}>
          {children(t)}
          {t.tip && (
            <div role="tooltip" className="pointer-events-none absolute z-10 border border-rule bg-card px-2.5 py-1.5 text-[13px] shadow-sm"
                 style={{ left: Math.min(t.tip.x + 12, VW - 220), top: t.tip.y + 12 }}>
              {t.tip.body}
            </div>
          )}
        </div>
      )}
      {"note" in spec && spec.note && <p className="mt-2 text-[13px]" style={{ color: "var(--viz-text-2)" }}>{spec.note}</p>}
      <figcaption className="mt-3 border-t border-rule pt-2 text-xs" style={{ color: "var(--viz-text-2)" }}>
        <span className="viz-num">{sourceLine(spec.source)}</span> · {spec.caveat}
      </figcaption>
    </figure>
  );
}

function Swatch({ fill, label, round = false, outline = false }: { fill: string; label: string; round?: boolean; outline?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <svg width="14" height="14" aria-hidden="true">
        {round ? <circle cx="7" cy="7" r="5.5" fill={fill} stroke="var(--viz-ink)" strokeWidth={outline ? 1.5 : 1} />
               : <rect x="1" y="2" width="12" height="10" rx="2" fill={fill} />}
      </svg>
      {label}
    </span>
  );
}

// --- risk by sector / asset class --------------------------------------------------------

export function RiskByGroup({ spec }: { spec: RiskByGroupChart }) {
  const labelW = 178, ratioW = 72, x0 = labelW + 12, x1 = VW - ratioW - 56;
  const rowH = 46, top = 26, axisH = 30;
  const H = top + spec.rows.length * rowH + axisH;
  const x = (v: number) => x0 + (v / spec.axis_max) * (x1 - x0);
  const what = spec.group === "sector" ? "Sector" : "Asset class";
  const table = (
    <table className="data">
      <thead><tr><th>{what}</th><th className="num">Weight</th><th className="num">Share of risk</th><th className="num">Risk ÷ weight</th></tr></thead>
      <tbody>{spec.rows.map((r) => (
        <tr key={r.label}><td>{r.label}</td><td className="num">{pct(r.weight)}</td><td className="num">{pct(r.risk_share)}</td><td className="num">{ratio(r.risk_to_weight)}</td></tr>
      ))}</tbody>
    </table>
  );
  return (
    <Frame spec={spec} table={table}
           legend={<><Swatch fill="var(--viz-pale)" label="Weight" /><Swatch fill="var(--viz-solid)" label="Share of risk" /></>}>
      {(t) => (
        <svg viewBox={`0 0 ${VW} ${H}`} width="100%" role="img" aria-label={`${spec.title}: weight and share of risk per ${what.toLowerCase()}`}>
          <text x={VW} y={14} textAnchor="end" fontSize={FONT} className="viz-num" fill="var(--viz-text-2)" letterSpacing="0.08em">RISK ÷ WEIGHT</text>
          {ticks(spec.axis_max, stepFor(spec.axis_max)).map((v) => (
            <g key={v}>
              <line x1={x(v)} x2={x(v)} y1={top} y2={H - axisH + 4} stroke="var(--viz-grid)" />
              <text x={x(v)} y={H - 8} textAnchor="middle" fontSize={FONT} className="viz-num" fill="var(--viz-text-2)">{pct(v, 0)}</text>
            </g>
          ))}
          {spec.rows.map((r, i) => {
            const y = top + i * rowH;
            const tip = <><b>{r.label}</b><br />Weight {pct(r.weight)} · Share of risk {pct(r.risk_share)} · Risk ÷ weight {ratio(r.risk_to_weight)}</>;
            return (
              <g key={r.label} onMouseMove={(e) => t.show(e, tip)}>
                <rect x={0} y={y} width={VW} height={rowH} fill="transparent" />
                <text x={labelW} y={y + 27} textAnchor="end" fontSize={14} fill="var(--viz-ink)">
                  {r.label.length > 24 ? `${r.label.slice(0, 23)}…` : r.label}
                  <title>{r.label}</title>
                </text>
                <path d={hbar(x0, y + 9, x(r.weight) - x0, 12)} fill="var(--viz-pale)" />
                <path d={hbar(x0, y + 23, x(r.risk_share) - x0, 12)} fill="var(--viz-solid)" />
                <text x={x(r.weight) + 6} y={y + 19} fontSize={FONT} className="viz-num" fill="var(--viz-text-2)">{pct(r.weight)}</text>
                <text x={x(r.risk_share) + 6} y={y + 33} fontSize={FONT} className="viz-num" fill="var(--viz-ink)">{pct(r.risk_share)}</text>
                <text x={VW} y={y + 27} textAnchor="end" fontSize={14} className="viz-num" fill="var(--viz-ink)"
                      fontWeight={r.risk_to_weight > 1 ? 700 : 400}>{ratio(r.risk_to_weight)}</text>
              </g>
            );
          })}
        </svg>
      )}
    </Frame>
  );
}

// --- risk vs size ------------------------------------------------------------------------

export function RiskVsSize({ spec }: { spec: RiskVsSizeChart }) {
  const H = Math.round(VW / 1.5), l = 60, r = 24, tp = 12, b = 48;
  const x = (v: number) => l + (v / spec.x_max) * (VW - l - r);
  const y = (v: number) => H - b - (v / spec.y_max) * (H - tp - b);
  const m = Math.min(spec.x_max, spec.y_max);
  // The region above the risk = size line, clipped to the plot.
  const region = spec.y_max <= spec.x_max
    ? `${x(0)},${y(0)} ${x(m)},${y(m)} ${x(0)},${y(spec.y_max)}`
    : `${x(0)},${y(0)} ${x(m)},${y(m)} ${x(spec.x_max)},${y(spec.y_max)} ${x(0)},${y(spec.y_max)}`;
  const radius = (w: number) => 5 + 7 * Math.sqrt(w / spec.x_max);
  // Largest dots first so small ones stay visible on top.
  const pts = [...spec.points].sort((a, c) => c.weight - a.weight);
  // Greedy label placement: try right of the dot, then left; skip a label that would
  // overlap a dot or a label already placed, or leave the plot.
  type Box = { x0: number; x1: number; y0: number; y1: number };
  const hit = (a: Box, q: Box) => a.x0 < q.x1 && a.x1 > q.x0 && a.y0 < q.y1 && a.y1 > q.y0;
  // The risk = size line runs from (x(0), y(0)) to (x(m), y(m)); a label box touches it
  // if the line's height across the box's width overlaps the box (pixel y grows downward).
  const slope = (y(m) - y(0)) / (x(m) - x(0));
  const onLine = (a: Box) => {
    const lo = Math.max(a.x0, x(0)), hi = Math.min(a.x1, x(m));
    if (lo > hi) return false;
    const ya = y(0) + slope * (lo - x(0)), yb = y(0) + slope * (hi - x(0));
    return Math.min(ya, yb) - 4 < a.y1 && Math.max(ya, yb) + 4 > a.y0;
  };
  const placed: Box[] = pts.map((p) => {
    const cx = x(p.weight), cy = y(p.risk_share), rr = radius(p.weight);
    return { x0: cx - rr, x1: cx + rr, y0: cy - rr, y1: cy + rr };
  });
  const labels = pts.filter((p) => p.label).sort((a, c) => Number(c.highlight) - Number(a.highlight)).flatMap((p) => {
    const cx = x(p.weight), rr = radius(p.weight), ly = y(p.risk_share) + 4, w = p.ticker.length * 7.5;
    for (const [lx, anchor] of [[cx + rr + 5, "start"], [cx - rr - 5, "end"]] as const) {
      const box = anchor === "start" ? { x0: lx, x1: lx + w, y0: ly - 11, y1: ly + 3 }
                                     : { x0: lx - w, x1: lx, y0: ly - 11, y1: ly + 3 };
      if (box.x0 < l || box.x1 > VW - r) continue;
      if (placed.some((q) => hit(box, q)) || onLine(box)) continue;
      placed.push(box);
      return [{ p, lx, ly, anchor }];
    }
    return [];
  });
  const xs = ticks(spec.x_max, stepFor(spec.x_max)), ys = ticks(spec.y_max, stepFor(spec.y_max));
  const table = (
    <table className="data">
      <thead><tr><th>Holding</th><th className="num">Weight</th><th className="num">Share of risk</th><th className="num">Risk ÷ weight</th></tr></thead>
      <tbody>{spec.points.map((p) => (
        <tr key={p.ticker}><td>{p.ticker}</td><td className="num">{pct(p.weight)}</td><td className="num">{pct(p.risk_share)}</td>
          <td className="num" style={{ fontWeight: p.highlight ? 700 : 400 }}>{ratio(p.risk_to_weight)}</td></tr>
      ))}</tbody>
    </table>
  );
  return (
    <Frame spec={spec} table={table}
           legend={<><Swatch round fill="var(--viz-solid)" label={spec.highlight_rule} /><Swatch round fill="var(--viz-dot-pale)" label="Others · dot size = weight" /></>}>
      {(t) => (
        <svg viewBox={`0 0 ${VW} ${H}`} width="100%" role="img" aria-label={`${spec.title}: weight against share of risk for each holding`}>
          <polygon points={region} fill="var(--viz-region)" />
          <text x={x(0) + 10} y={y(spec.y_max) + 20} fontSize={13} fontWeight={600} fill="var(--viz-gold-text)">More risk than size</text>
          {xs.map((v) => <g key={`x${v}`}>
            <line x1={x(v)} x2={x(v)} y1={tp} y2={H - b} stroke="var(--viz-grid)" />
            <text x={x(v)} y={H - b + 18} textAnchor="middle" fontSize={FONT} className="viz-num" fill="var(--viz-text-2)">{pct(v, 0)}</text>
          </g>)}
          {ys.map((v) => <g key={`y${v}`}>
            <line x1={l} x2={VW - r} y1={y(v)} y2={y(v)} stroke="var(--viz-grid)" />
            <text x={l - 8} y={y(v) + 4} textAnchor="end" fontSize={FONT} className="viz-num" fill="var(--viz-text-2)">{pct(v, 0)}</text>
          </g>)}
          <line x1={x(0)} y1={y(0)} x2={x(m)} y2={y(m)} stroke="var(--viz-ink)" strokeDasharray="4 4" />
          {/* Clear of the dashes: above the line's end when it meets the right edge (the line
              is lower to the left), right of its end when it meets the top. */}
          {spec.y_max > spec.x_max
            ? <text x={x(m) - 4} y={y(m) - 8} textAnchor="end" fontSize={FONT} fill="var(--viz-text-2)">risk = size</text>
            : <text x={x(m) + 6} y={y(m) + 14} fontSize={FONT} fill="var(--viz-text-2)">risk = size</text>}
          <text x={(l + VW - r) / 2} y={H - 8} textAnchor="middle" fontSize={13} fill="var(--viz-ink)">Portfolio weight</text>
          <text transform={`translate(14 ${(tp + H - b) / 2}) rotate(-90)`} textAnchor="middle" fontSize={13} fill="var(--viz-ink)">Share of portfolio risk</text>
          {pts.map((p) => (
            <circle key={p.ticker} cx={x(p.weight)} cy={y(p.risk_share)} r={radius(p.weight)}
                    fill={p.highlight ? "var(--viz-solid)" : "var(--viz-dot-pale)"} stroke="var(--viz-ink)" strokeWidth={1}
                    onMouseMove={(e) => t.show(e, <><b>{p.ticker}</b><br />Weight {pct(p.weight)} · Share of risk {pct(p.risk_share)} · {ratio(p.risk_to_weight)}</>)}>
              <title>{p.ticker}</title>
            </circle>
          ))}
          {labels.map(({ p, lx, ly, anchor }) => (
            <text key={`l${p.ticker}`} x={lx} y={ly} textAnchor={anchor} fontSize={FONT} className="viz-num" fill="var(--viz-ink)" pointerEvents="none">{p.ticker}</text>
          ))}
        </svg>
      )}
    </Frame>
  );
}

// --- what-if: risk mix and headline measures -------------------------------------------

const measureText = (kind: string, v: number) => (kind === "loss" ? lossSigned(v) : kind === "fraction" ? pct(v) : num(v));

export function WhatIf({ spec }: { spec: WhatIfChart }) {
  const labelW = 150, bx = labelW + 12, bw = VW - bx, barH = 34, gap = 18;
  const mixH = spec.mix.length * (barH + gap);
  const names = spec.mix[0]?.sources.map((s) => s.name) ?? [];
  const rowH = 66, tx0 = 280, tx1 = VW - 24;
  const table = (
    <table className="data">
      <thead><tr><th /><th className="num">Current</th><th className="num">{spec.option_label}</th></tr></thead>
      <tbody>
        {names.map((n, i) => (
          <tr key={n}><td>{n}</td>{spec.mix.map((m) => <td key={m.label} className="num">{pct(m.sources[i]?.share ?? 0, 0)}</td>)}</tr>
        ))}
        {spec.measures.map((m) => (
          <tr key={m.key}><td>{m.label}</td><td className="num">{measureText(m.kind, m.current)}</td><td className="num">{measureText(m.kind, m.option)}</td></tr>
        ))}
      </tbody>
    </table>
  );
  return (
    <Frame spec={spec} table={table}>
      {(t) => (
        <>
          <div className="font-mono text-xs uppercase tracking-[0.12em]" style={{ color: "var(--viz-gold-text)" }}>Where the risk comes from</div>
          <svg viewBox={`0 0 ${VW} ${mixH}`} width="100%" className="mt-2" role="img" aria-label="Share of risk by source, current and option">
            <defs>{spec.mix.map((_, i) => (
              <clipPath key={i} id={`mix-${i}`}><rect x={bx} y={i * (barH + gap)} width={bw} height={barH} rx={4} /></clipPath>
            ))}</defs>
            {spec.mix.map((row, i) => {
              const y = i * (barH + gap);
              let cx = bx;
              return (
                <g key={row.label}>
                  <text x={labelW} y={y + barH / 2 + 5} textAnchor="end" fontSize={14} fontWeight={i ? 700 : 400} fill="var(--viz-ink)">
                    {row.label.length > 20 ? `${row.label.slice(0, 19)}…` : row.label}<title>{row.label}</title>
                  </text>
                  <g clipPath={`url(#mix-${i})`}>
                    {row.sources.map((s: RiskSource, k) => {
                      const w = s.share * bw, sx = cx;
                      cx += w;
                      return (
                        <g key={s.name} onMouseMove={(e) => t.show(e, <><b>{row.label}</b><br />{s.name}: {pct(s.share, 0)} of risk</>)}>
                          <rect x={sx} y={y} width={Math.max(0, w)} height={barH} fill={RAMP[k]} stroke="var(--viz-surface)" strokeWidth={2} />
                          {w >= 34 && <text x={sx + 8} y={y + barH / 2 + 4} fontSize={FONT} className="viz-num" fill={ON_RAMP[k]}>{pct(s.share, 0)}</text>}
                        </g>
                      );
                    })}
                  </g>
                </g>
              );
            })}
          </svg>
          <div className="mb-5 flex flex-wrap gap-x-5 gap-y-1 text-[13px]" style={{ paddingLeft: `${(bx / VW) * 100}%` }}>
            {names.map((n, k) => <Swatch key={n} fill={RAMP[k]} label={n} />)}
          </div>
          <div className="flex items-center justify-between">
            <div className="font-mono text-xs uppercase tracking-[0.12em]" style={{ color: "var(--viz-gold-text)" }}>Headline measures</div>
            <div className="flex gap-4 text-[13px]">
              <Swatch round outline fill="var(--viz-surface)" label="Current" />
              <Swatch round fill="var(--viz-ink)" label={spec.option_label} />
            </div>
          </div>
          <svg viewBox={`0 0 ${VW} ${spec.measures.length * rowH}`} width="100%" role="img" aria-label="Headline measures, current and option">
            {spec.measures.map((m, i) => {
              // Each measure has its own zero-based scale (one axis per row, never shared).
              const max = Math.max(m.current, m.option) * 1.15 || 1;
              const px = (v: number) => tx0 + (v / max) * (tx1 - tx0);
              const y = i * rowH + 38, a = px(m.current), o = px(m.option);
              const close = Math.abs(a - o) < 60;
              return (
                <g key={m.key} onMouseMove={(e) => t.show(e, <><b>{m.label}</b><br />Current {measureText(m.kind, m.current)} · {spec.option_label} {measureText(m.kind, m.option)}</>)}>
                  <rect x={0} y={i * rowH} width={VW} height={rowH} fill="transparent" />
                  <text x={0} y={y - 4} fontSize={15} fill="var(--viz-ink)">{m.label}</text>
                  <text x={0} y={y + 14} fontSize={FONT} fill="var(--viz-text-2)">{m.hint}</text>
                  <line x1={tx0} x2={tx1} y1={y} y2={y} stroke="var(--viz-grid)" strokeWidth={2} />
                  <line x1={Math.min(a, o)} x2={Math.max(a, o)} y1={y} y2={y} stroke="var(--viz-ramp-3)" strokeWidth={3} />
                  <circle cx={a} cy={y} r={7} fill="var(--viz-surface)" stroke="var(--viz-ink)" strokeWidth={2} />
                  <circle cx={o} cy={y} r={7} fill="var(--viz-ink)" stroke="var(--viz-surface)" strokeWidth={2} />
                  <text x={o} y={y - 14} textAnchor="middle" fontSize={FONT} fontWeight={700} className="viz-num" fill="var(--viz-ink)">{measureText(m.kind, m.option)}</text>
                  <text x={a} y={close ? y + 24 : y - 14} textAnchor="middle" fontSize={FONT} className="viz-num" fill="var(--viz-text-2)">{measureText(m.kind, m.current)}</text>
                </g>
              );
            })}
          </svg>
        </>
      )}
    </Frame>
  );
}

/** Draws any engine chart spec. */
export function ChartView({ spec }: { spec: ChartSpec }) {
  if (spec.kind === "risk_by_group") return <RiskByGroup spec={spec} />;
  if (spec.kind === "risk_vs_size") return <RiskVsSize spec={spec} />;
  if (spec.kind === "what_if") return <WhatIf spec={spec} />;
  return null;
}

/** The Overview's "Where the risk comes from": the engine's four risk sources, same
 *  names, order and colours as the what-if chart, whole-number percentages. */
export function RiskSourceBars({ sources }: { sources: RiskSource[] }) {
  return (
    <div className="viz-root !bg-transparent">
      {sources.map((s, k) => (
        <div key={s.name} className="my-2.5 text-sm">
          <div className="mb-1 flex justify-between gap-3">
            <span>{s.name}</span>
            <span className="viz-num text-[13px]">{pct(s.share, 0)}</span>
          </div>
          <div className="h-2.5 bg-track">
            <div className="h-2.5 rounded-r" style={{ width: `${Math.max(0, Math.min(1, s.share)) * 100}%`, background: RAMP[k] }} />
          </div>
        </div>
      ))}
    </div>
  );
}
