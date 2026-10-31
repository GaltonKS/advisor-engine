import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { ChartView } from "../../components/charts";
import { Chip, ErrorNote, Loading, Panel } from "../../components/ui";
import { ApiError, useApi } from "../../lib/api";
import { shortHash } from "../../lib/format";
import type { ChartSpec, ChatMessage, Draft, RiskProfile } from "../../lib/types";
import type { Workspace } from "../ClientWorkspace";

const SUGGESTIONS = [
  "How risky is this portfolio?",
  "What is my sector exposure?",
  "What is the stress loss in a severe period?",
  "Why is the largest risk contributor a concern?",
];

export default function AssistantTab({ ws }: { ws: Workspace }) {
  const api = useApi();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const pid = ws.portfolio.id;
  const messages = useQuery({ queryKey: ["messages", pid], queryFn: () => api<ChatMessage[]>(`/portfolios/${pid}/messages`) });
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<{ id: string; text: string; original: string } | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const ask = useMutation({
    mutationFn: (body: { message?: string; draft?: boolean }) => api<ChatMessage>(`/portfolios/${pid}/turns`, { json: body }),
    onSuccess: (reply) => {
      qc.invalidateQueries({ queryKey: ["messages", pid] });
      if (reply.draft_id) setDraft({ id: reply.draft_id, text: reply.content, original: reply.content });
    },
  });
  const approve = useMutation({
    mutationFn: () => api<Draft>(`/drafts/${draft!.id}/approve`, { json: { text: draft!.text } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["drafts", pid] }),
  });

  // A question handed over from another tab (e.g. "Ask the assistant about BBB").
  const handed = params.get("ask");
  useEffect(() => {
    if (handed && !ask.isPending) {
      ask.mutate({ message: handed });
      params.delete("ask");
      setParams(params, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handed]);
  // Braces matter: newer browsers return a Promise from scrollIntoView, and an effect may
  // only return a clean-up function.
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [messages.data, ask.isPending]);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    ask.mutate({ message: text.trim() });
    setText("");
  }

  const unmatched = approve.error instanceof ApiError
    ? ((approve.error.detail as { unmatched?: string[] })?.unmatched ?? []) : [];

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_380px]">
      <div>
        <div className="max-h-[78vh] min-h-[320px] space-y-5 overflow-y-auto border border-rule bg-card p-5">
          {messages.isLoading && <Loading />}
          {messages.data?.length === 0 && !ask.isPending && (
            <p className="text-sm text-muted">Ask about this portfolio. Every figure in a reply is checked against the engine's output before you see it.</p>
          )}
          {messages.data?.map((m, i) => <Message key={i} m={m} />)}
          {ask.isPending && <div className="text-sm text-faint">Checking with the engine…</div>}
          <div ref={bottom} />
        </div>
        <ErrorNote error={ask.error} />
        <div className="mt-3 flex flex-wrap gap-2">
          <button className="btn btn-primary" disabled={ask.isPending} onClick={() => ask.mutate({ draft: true })}>Draft the client explanation</button>
          {SUGGESTIONS.map((s) => (
            <button key={s} className="btn" disabled={ask.isPending} onClick={() => ask.mutate({ message: s })}>{s}</button>
          ))}
        </div>
        <form onSubmit={submit} className="mt-3 flex border border-rule bg-card">
          <input className="flex-1 bg-transparent px-4 py-3 text-sm outline-none" value={text} maxLength={4000}
                 onChange={(e) => setText(e.target.value)} placeholder="Describe the client or ask a follow-up" />
          <button className="m-1.5 bg-gold px-4 text-ink" disabled={ask.isPending} aria-label="Send">↑</button>
        </form>
        <p className="mt-2 text-xs text-faint">These are explanations to weigh, not recommendations. Which option to use is your call.</p>
      </div>

      <div className="self-start">
        {draft ? (
          <Panel title="Draft explanation for the client" tag="Editable · yours to approve">
            <textarea className="input min-h-[300px] font-serif text-[15px] leading-relaxed" value={draft.text}
                      disabled={approve.isSuccess}
                      onChange={(e) => { setDraft({ ...draft, text: e.target.value }); approve.reset(); }} />
            {unmatched.length > 0 && (
              <div className="mt-2 text-sm text-bad">Not approved: these figures are not from the engine: {unmatched.join(", ")}.</div>
            )}
            {approve.error && unmatched.length === 0 && <ErrorNote error={approve.error} />}
            {approve.isSuccess ? (
              <p className="mt-3 text-sm text-good">Approved and logged with your name. Nothing was sent. It now appears in the client report.</p>
            ) : (
              <button className="btn btn-dark mt-3" onClick={() => approve.mutate()} disabled={approve.isPending}>Approve</button>
            )}
            <p className="mt-3 text-xs text-muted">Edits are re-checked: you can reword freely, but a figure the engine didn't produce blocks approval.</p>
          </Panel>
        ) : (
          <Panel title="Client explanation">
            <p className="text-sm text-muted">"Draft the client explanation" writes a plain-language summary from the engine's figures for you to edit and approve.</p>
          </Panel>
        )}
      </div>
    </div>
  );
}

function Message({ m }: { m: ChatMessage }) {
  if (m.role === "user") {
    return <div className="ml-auto max-w-[85%] bg-ink px-4 py-3 text-sm text-card">{m.content}</div>;
  }
  const used = [...new Set((m.tool_calls ?? []).map((c) => c.label))];
  const ok = (m.tool_calls ?? []).filter((c) => !c.is_error && typeof c.output === "object");
  // Charts carry their own source line; the Sources list covers the other tools.
  const charts = ok.filter((c) => c.name === "show_chart").map((c) => (c.output as unknown as { chart: ChartSpec }).chart);
  const sources = ok.filter((c) => c.name !== "show_chart");
  return (
    <div>
      {used.length > 0 && (
        <div className="mb-2 flex items-center gap-2"><span className="eyebrow">Used</span>{used.map((u) => <Chip key={u}>{u}</Chip>)}</div>
      )}
      {m.blocked ? (
        <div className="border border-bad/40 bg-bad/5 px-3 py-2 text-sm text-bad"><b>✗ Withheld.</b> {m.content}</div>
      ) : (
        <div className="whitespace-pre-wrap font-serif text-[17px] leading-relaxed">{m.content}</div>
      )}
      {/* Charts are built by the engine, never by the model, so they stay visible even
          when the reply's text was withheld by the number check. */}
      {m.blocked && charts.length > 0 && (
        <p className="mt-2 text-xs text-muted">Only the text above was withheld. The chart below is engine output.</p>
      )}
      {charts.map((c, i) => <ChartView key={i} spec={c} />)}
      {!m.blocked && m.verification && (
        <div className="mt-2 text-xs text-good">✓ Verified · {m.verification.checked ?? 0} figures matched engine output</div>
      )}
      {!m.blocked && sources.length > 0 && (
        <div className="mt-2 border-t border-rule pt-2 text-xs text-muted">
          <div className="eyebrow mb-1">Sources</div>
          {sources.map((c, i) => {
            // A what-if carries the profile after the change; stress and holdings carry no profile.
            const out = c.output as { after?: RiskProfile } & Partial<RiskProfile>;
            const o = out.after ?? (out.n_periods !== undefined ? (out as RiskProfile) : undefined);
            return (
              <div key={i}><span className="figure text-gold-dark">[{i + 1}]</span> {c.label}
                {o && <> · {o.n_periods} {o.drawdown.period_label} {o.returns_source} returns · engine v{o.engine_version}</>}
                {" "}· holdings {shortHash(c.holdings_sha256)}</div>
            );
          })}
        </div>
      )}
    </div>
  );
}
