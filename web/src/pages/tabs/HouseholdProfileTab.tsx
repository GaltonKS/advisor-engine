// Household risk profile: people, answers by dimension, and the engine's live profile.
// Every figure on this screen comes from GET /clients/{id}/profile; the screen only
// places marks on the 1 to 10 scale and formats numbers. Draft method: the method status
// line from the engine is shown with the result.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ErrorNote, Loading, Panel } from "../../components/ui";
import { useApi } from "../../lib/api";
import { dateTime, num, pct, shortHash } from "../../lib/format";
import type {
  DimensionEstimate, HouseholdProfile, LossLimits, Person, ProfileAnswerValue, ProfileDimension,
  ProfileQuestion, ProfileQuestionBank, ProfileSnapshot,
} from "../../lib/types";
import type { Workspace } from "../ClientWorkspace";

const DIMENSIONS: { id: ProfileDimension; title: string; about: string }[] = [
  { id: "willingness", title: "Willingness", about: "How this person reacts to losses. Asked of each person." },
  { id: "ability", title: "Ability", about: "How much loss the household can absorb. Asked once for the household." },
  { id: "need", title: "Need", about: "The return the plan requires." },
];

/** A dimension's mean with its band, as the engine gave them: "5.5 ± 2.0", or a dash. */
export const estimateText = (e: DimensionEstimate | null | undefined): string =>
  e ? `${num(e.mean)} ± ${num(e.band)}` : "—";

/** Where `x` sits on the scale, 0..1. Layout only: it places a mark, it is never shown. */
const at = (x: number, scale: { min: number; max: number }) =>
  `${Math.max(0, Math.min(1, (x - scale.min) / (scale.max - scale.min))) * 100}%`;

/** One row on the 1 to 10 scale: the band from low to high, the mean, and the ceiling. */
export function DimensionBar({ label, estimate, scale, ceiling }: {
  label: string; estimate: DimensionEstimate | null | undefined;
  scale: { min: number; max: number }; ceiling?: number | null;
}) {
  return (
    <div className="my-3 text-sm" data-testid={`bar-${label}`}>
      <div className="mb-1 flex justify-between gap-3">
        <span>{label}</span>
        <span className="figure text-[13px]">{estimateText(estimate)}</span>
      </div>
      <div className="relative h-3 bg-track" aria-hidden>
        {estimate && (
          <>
            <div className="absolute top-0 h-3 bg-gold/40" style={{ left: at(estimate.low, scale), right: `calc(100% - ${at(estimate.high, scale)})` }} />
            <div className="absolute -top-0.5 h-4 w-1 -translate-x-1/2 bg-gold-dark" style={{ left: at(estimate.mean, scale) }} />
          </>
        )}
        {ceiling != null && (
          <div className="absolute -top-1 h-5 w-0.5 -translate-x-1/2 bg-navy" style={{ left: at(ceiling, scale) }} title="Ceiling" />
        )}
      </div>
    </div>
  );
}

/** The engine's profile: bars, ceiling, provisional badge, flags and the template summary. */
export function ProfilePanel({ profile }: { profile: HouseholdProfile }) {
  const { scale, ceiling } = profile;
  const c = ceiling?.value ?? null;
  const limiter = ceiling?.limited_by === "ability" ? "ability"
    : profile.people.find((p) => p.id === ceiling?.person_id)?.name ?? "willingness";
  return (
    <Panel title="Profile" tag={profile.provisional ? <span className="border border-gold px-2 py-0.5 text-gold-dark" data-testid="provisional">Provisional</span> : undefined}>
      <div className="border border-rule border-t-[3px] border-t-gold bg-card px-4 py-3">
        <div className="eyebrow">Ceiling</div>
        <div className="my-1 font-serif text-[36px] leading-tight" data-testid="ceiling">
          {ceiling ? num(ceiling.value) : "—"}<span className="ml-1 text-lg text-faint">/ {num(scale.max, 0)}</span>
        </div>
        <div className="text-[13px] text-muted">
          {ceiling ? `Set by ${limiter} (± ${num(ceiling.band)}).` : "Needs at least one willingness answer."}
        </div>
      </div>

      <div className="mt-4">
        {profile.people.map((p) => (
          <DimensionBar key={p.id} label={`${p.name} · willingness`} estimate={p.willingness} scale={scale} ceiling={c} />
        ))}
        {profile.people.length === 2 && (
          <DimensionBar label="Household willingness (the lower)" estimate={profile.household_willingness} scale={scale} ceiling={c} />
        )}
        <DimensionBar label="Ability" estimate={profile.ability} scale={scale} ceiling={c} />
        <DimensionBar label="Need" estimate={profile.need} scale={scale} ceiling={c} />
        <div className="flex justify-between text-[11px] text-faint"><span>{num(scale.min, 0)} cautious</span><span>{num(scale.max, 0)} risk-taking</span></div>
        <div className="mt-1 text-[11px] text-faint">Shaded: the uncertainty band. Dark mark: the estimate. Navy line: the ceiling.</div>
      </div>

      {profile.flags.length > 0 && (
        <ul className="mt-4 space-y-2" aria-label="Flags">
          {profile.flags.map((f) => (
            <li key={f.code} data-testid={`flag-${f.code}`}
                className={`border-l-[3px] px-3 py-1.5 text-sm ${f.severity === "warn" ? "border-l-bad bg-bad/5" : "border-l-gold bg-gold/5"}`}>
              {f.message}
            </li>
          ))}
        </ul>
      )}

      <p className="mt-4 text-sm text-muted" data-testid="summary">{profile.plain_summary}</p>
      <p className="mt-2 text-xs text-faint">Computed by the engine (method {profile.method_version}). {profile.method_status}</p>
    </Panel>
  );
}

function answerFor(profile: HouseholdProfile, q: ProfileQuestion, personId: string | null) {
  return profile.answers.find((a) => a.question_id === q.id && (q.dimension === "willingness" ? a.person_id === personId : true))?.answer;
}

const asPercentInput = (x: number) => String(+(x * 100).toFixed(2));

/** One question. `onAnswer(null)` retracts it. */
export function QuestionCard({ q, answer, onAnswer, busy }: {
  q: ProfileQuestion; answer: ProfileAnswerValue | undefined;
  onAnswer: (a: ProfileAnswerValue | null) => void; busy: boolean;
}) {
  const answered = answer !== undefined;
  return (
    <li className="border border-rule bg-white p-4">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <div className="text-sm font-semibold">{q.prompt}</div>
        {answered && (
          <button className="text-xs text-muted underline" disabled={busy} onClick={() => onAnswer(null)}>Clear answer</button>
        )}
      </div>
      {q.kind === "choice" && (
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={q.prompt}>
          {q.options!.map((o) => (
            <button key={o.key} role="radio" aria-checked={answer === o.key} disabled={busy}
                    className={`border px-3 py-1.5 text-sm ${answer === o.key ? "border-gold bg-gold-soft" : "border-rule bg-white hover:border-gold"}`}
                    onClick={() => onAnswer(o.key)}>
              {o.label}
            </button>
          ))}
        </div>
      )}
      {q.kind === "loss_limits" && <LossLimitsInput q={q} answer={answer as LossLimits | undefined} onAnswer={onAnswer} busy={busy} />}
      {q.kind === "number" && <NumberInput q={q} answer={answer as number | undefined} onAnswer={onAnswer} busy={busy} />}
    </li>
  );
}

function LossLimitsInput({ q, answer, onAnswer, busy }: {
  q: ProfileQuestion; answer: LossLimits | undefined; onAnswer: (a: LossLimits) => void; busy: boolean;
}) {
  const max = (q.max ?? 0.5) * 100;
  const [unc, setUnc] = useState(answer ? asPercentInput(answer.uncomfortable_pct) : "10");
  const [act, setAct] = useState(answer ? asPercentInput(answer.act_pct) : "20");
  useEffect(() => {
    if (answer) { setUnc(asPercentInput(answer.uncomfortable_pct)); setAct(asPercentInput(answer.act_pct)); }
  }, [answer]);
  const slider = (label: string, value: string, set: (v: string) => void) => (
    <label className="block text-sm">
      <span className="flex justify-between"><span>{label}</span><span className="figure">{value}%</span></span>
      <input type="range" min={0} max={max} step={1} value={value} aria-label={label}
             className="w-full accent-[var(--color-gold-dark)]" onChange={(e) => set(e.target.value)} />
    </label>
  );
  return (
    <div className="space-y-2">
      {slider("Fall that would make them uncomfortable", unc, setUnc)}
      {slider("Fall at which they would act (sell)", act, setAct)}
      <div className="flex items-center gap-3">
        <button className="btn" disabled={busy}
                onClick={() => onAnswer({ uncomfortable_pct: Number(unc) / 100, act_pct: Number(act) / 100 })}>
          Save loss limits
        </button>
        {answer && <span className="text-xs text-faint">On file: {pct(answer.uncomfortable_pct, 0)} and {pct(answer.act_pct, 0)}</span>}
      </div>
    </div>
  );
}

function NumberInput({ q, answer, onAnswer, busy }: {
  q: ProfileQuestion; answer: number | undefined; onAnswer: (a: number) => void; busy: boolean;
}) {
  // Required return is a fraction in the API and a percentage on screen.
  const percent = q.unit === "fraction";
  const shown = (x: number) => (percent ? asPercentInput(x) : String(x));
  const [value, setValue] = useState(answer !== undefined ? shown(answer) : "");
  useEffect(() => { if (answer !== undefined) setValue(shown(answer)); }, [answer]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="flex flex-wrap items-center gap-2">
      <input className="input !w-32" inputMode="decimal" value={value} aria-label={q.prompt}
             onChange={(e) => setValue(e.target.value)} />
      <span className="text-sm text-muted">{percent ? "% a year" : q.unit}</span>
      <button className="btn" disabled={busy || value.trim() === "" || Number.isNaN(Number(value))}
              onClick={() => onAnswer(percent ? Number(value) / 100 : Number(value))}>
        Save
      </button>
    </div>
  );
}

/** Add a person (up to two). */
function AddPerson({ onAdd, busy, first }: { onAdd: (name: string) => void; busy: boolean; first: boolean }) {
  const [name, setName] = useState("");
  return (
    <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) { onAdd(name.trim()); setName(""); } }}>
      <input className="input !w-48" placeholder={first ? "Name" : "Partner's name"} value={name}
             aria-label={first ? "Person's name" : "Partner's name"} onChange={(e) => setName(e.target.value)} />
      <button className="btn" type="submit" disabled={busy || !name.trim()}>{first ? "Add person" : "Add partner"}</button>
    </form>
  );
}

export function SnapshotList({ snapshots }: { snapshots: ProfileSnapshot[] }) {
  if (!snapshots.length) return <p className="text-sm text-muted">No saved versions yet.</p>;
  return (
    <ul className="divide-y divide-rule text-sm">
      {snapshots.map((s, i) => (
        <li key={s.id} className="flex flex-wrap items-baseline justify-between gap-2 py-2">
          <span>
            <span className="figure mr-2 text-gold-dark">v{snapshots.length - i}</span>
            {dateTime(s.created_at)}
          </span>
          <span className="text-xs text-muted">
            Ceiling {s.result.ceiling ? num(s.result.ceiling.value) : "—"}
            {s.result.provisional ? " · provisional" : ""} · method {s.method_version} · inputs {shortHash(s.inputs_hash)}
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function HouseholdProfileTab({ ws }: { ws: Workspace }) {
  const api = useApi();
  const qc = useQueryClient();
  const cid = ws.client.id;
  const bank = useQuery({ queryKey: ["profile-questions"], queryFn: () => api<ProfileQuestionBank>("/profile/questions") });
  const profile = useQuery({ queryKey: ["household-profile", cid], queryFn: () => api<HouseholdProfile>(`/clients/${cid}/profile`) });
  const snapshots = useQuery({ queryKey: ["profile-snapshots", cid], queryFn: () => api<ProfileSnapshot[]>(`/clients/${cid}/profile/snapshots`) });
  const [personId, setPersonId] = useState<string | null>(null);

  const refresh = () => qc.invalidateQueries({ queryKey: ["household-profile", cid] });
  const addPerson = useMutation({
    mutationFn: (name: string) => api<Person>(`/clients/${cid}/people`, { json: { name } }),
    onSuccess: (p) => { setPersonId(p.id); refresh(); },
  });
  const answer = useMutation({
    mutationFn: (body: { question_id: string; person_id: string | null; answer: ProfileAnswerValue | null }) =>
      api(`/clients/${cid}/profile/inputs`, { json: body }),
    onSuccess: refresh,
  });
  const save = useMutation({
    mutationFn: () => api<ProfileSnapshot>(`/clients/${cid}/profile/snapshots`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["profile-snapshots", cid] }),
  });

  if (bank.isLoading || profile.isLoading) return <Loading />;
  if (!bank.data || !profile.data) return <ErrorNote error={bank.error ?? profile.error} />;
  const p = profile.data;
  const people = p.people;
  const selected = people.find((x) => x.id === personId) ?? people[0];

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_380px]">
      <div className="space-y-5">
        <Panel title="Household" tag={bank.data.method_status}>
          <div className="flex flex-wrap items-center gap-2" role="tablist" aria-label="People">
            {people.map((x) => (
              <button key={x.id} role="tab" aria-selected={selected?.id === x.id}
                      className={`border px-3 py-1.5 text-sm ${selected?.id === x.id ? "border-navy bg-navy-soft font-semibold text-navy" : "border-rule bg-white hover:border-navy"}`}
                      onClick={() => setPersonId(x.id)}>
                {x.name} <span className="text-xs text-faint">{x.role}</span>
              </button>
            ))}
            {people.length < 2 && <AddPerson first={people.length === 0} busy={addPerson.isPending} onAdd={(n) => addPerson.mutate(n)} />}
          </div>
          {people.length === 0 && <p className="mt-3 text-sm text-muted">Add the people in this household (one or two). Willingness is asked of each person.</p>}
          <ErrorNote error={addPerson.error} />
        </Panel>

        {DIMENSIONS.map((d) => {
          const qs = bank.data.questions.filter((q) => q.dimension === d.id);
          const perPerson = d.id === "willingness";
          if (perPerson && !selected) return null;
          return (
            <Panel key={d.id} title={perPerson ? `${d.title} · ${selected!.name}` : d.title}>
              <p className="mb-3 text-xs text-muted">{d.about}</p>
              <ol className="space-y-3">
                {qs.map((q) => (
                  <QuestionCard key={q.id + (perPerson ? selected!.id : "")} q={q} busy={answer.isPending}
                                answer={answerFor(p, q, perPerson ? selected!.id : null)}
                                onAnswer={(a) => answer.mutate({ question_id: q.id, person_id: perPerson ? selected!.id : null, answer: a })} />
                ))}
              </ol>
            </Panel>
          );
        })}
        <ErrorNote error={answer.error} />
      </div>

      <div className="space-y-4 self-start">
        <ProfilePanel profile={p} />
        <Panel title="Saved versions">
          <button className="btn btn-primary mb-3 w-full justify-center" disabled={save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "Saving…" : "Save profile version"}
          </button>
          <ErrorNote error={save.error} />
          {save.isSuccess && <p className="mb-2 text-xs text-good">Saved and recorded in the audit trail.</p>}
          {snapshots.data ? <SnapshotList snapshots={snapshots.data} /> : <Loading />}
        </Panel>
      </div>
    </div>
  );
}
