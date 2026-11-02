import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { ChartView } from "../../components/charts";
import { Markdown } from "../../lib/markdown";
import { Chip, ErrorNote, GaltonName, Loading, Panel } from "../../components/ui";
import { DraftEditor } from "./DraftEditor";
import { useApi } from "../../lib/api";
import { shortHash } from "../../lib/format";
import type { ChartSpec, ChatMessage, Draft, RiskProfile } from "../../lib/types";
import type { Workspace } from "../ClientWorkspace";

const SUGGESTIONS = [
  "How risky is this portfolio?",
  "What is my sector exposure?",
  "What is the stress loss in a severe period?",
  "Why is the largest risk contributor a concern?",
];

const FOCUS_PARAMS = ["turn", "draft", "question"] as const;

/** The draft the conversation currently ends on: the client explanation stays open in
 *  Galton AI (across tab switches and reloads) until the adviser asks another question. */
export function currentDraftId(messages: ChatMessage[] | undefined): string | null {
  const last = messages?.filter((m) => m.role === "assistant").at(-1);
  return last?.draft_id ?? null;
}

/** Indexes of the messages an audit-trail link points at: the question and its reply.
 *  `turn` and `draft` are exact ids; `question` (turns logged before turn ids existed)
 *  matches the question's text, so a question asked twice marks both exchanges. */
export function focusedIndexes(messages: ChatMessage[], focus: { turn?: string | null; draft?: string | null; question?: string | null }): number[] {
  const out = new Set<number>();
  messages.forEach((m, i) => {
    if (focus.turn && m.turn_id === focus.turn) out.add(i);
    if (focus.draft && m.draft_id === focus.draft) {
      out.add(i);
      if (messages[i - 1]?.role === "user") out.add(i - 1);
    }
    if (focus.question && !m.turn_id && m.role === "user" && m.content === focus.question) {
      out.add(i);
      if (messages[i + 1]?.role === "assistant") out.add(i + 1);
    }
  });
  return [...out].sort((a, b) => a - b);
}

export default function AssistantTab({ ws }: { ws: Workspace }) {
  const api = useApi();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const pid = ws.portfolio.id;
  const messages = useQuery({ queryKey: ["messages", pid], queryFn: () => api<ChatMessage[]>(`/portfolios/${pid}/messages`) });
  const drafts = useQuery({ queryKey: ["drafts", pid], queryFn: () => api<Draft[]>(`/portfolios/${pid}/drafts`) });
  const [text, setText] = useState("");
  const [confirmNew, setConfirmNew] = useState(false);
  // Derived from the saved conversation, not local state, so leaving the tab doesn't lose it.
  const draftId = currentDraftId(messages.data);
  const draft = draftId ? drafts.data?.find((d) => d.id === draftId) : undefined;
  const bottom = useRef<HTMLDivElement>(null);
  const chat = useRef<HTMLDivElement>(null);
  const focus = { turn: params.get("turn"), draft: params.get("draft"), question: params.get("question") };
  const focusing = Boolean(focus.turn || focus.draft || focus.question);
  const focused = focusing && messages.data ? focusedIndexes(messages.data, focus) : [];

  function clearFocus() {
    FOCUS_PARAMS.forEach((k) => params.delete(k));
    setParams(params, { replace: true });
  }

  const ask = useMutation({
    mutationFn: (body: { message?: string; draft?: boolean }) => api<ChatMessage>(`/portfolios/${pid}/turns`, { json: body }),
    onMutate: () => { if (focusing) clearFocus(); },
    onSuccess: (reply) => {
      qc.invalidateQueries({ queryKey: ["messages", pid] });
      if (reply.draft_id) qc.invalidateQueries({ queryKey: ["drafts", pid] });
    },
  });

  // A new chat clears the conversation (and what the model remembers). Every question and
  // reply stays in the audit trail, and drafts stay in the Client report.
  const newChat = useMutation({
    mutationFn: () => api<{ cleared: number }>(`/portfolios/${pid}/messages`, { method: "DELETE" }),
    onSuccess: () => {
      setConfirmNew(false);
      if (focusing) clearFocus();
      qc.setQueryData<ChatMessage[]>(["messages", pid], []);
      qc.invalidateQueries({ queryKey: ["messages", pid] });
      qc.invalidateQueries({ queryKey: ["audit", pid] });
    },
  });

  // A question handed over from another tab (e.g. "Ask Galton AI about BBB").
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
  // An audit-trail link scrolls to its exchange; otherwise the newest message is shown.
  useEffect(() => {
    if (focusing) {
      // Scroll the chat panel only, so the page (and its tabs) stays where it is.
      const el = chat.current?.querySelector<HTMLElement>(`[data-msg="${focused[0]}"]`);
      if (el && chat.current) chat.current.scrollTop = el.offsetTop - 12;
    } else {
      bottom.current?.scrollIntoView({ block: "end" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages.data, ask.isPending, focusing, focused.join(",")]);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    ask.mutate({ message: text.trim() });
    setText("");
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_380px]">
      <div>
        {(messages.data?.length ?? 0) > 0 && (
          confirmNew ? (
            <div className="mb-2 border border-rule bg-card px-3 py-2 text-sm" role="alertdialog" aria-label="Start a new chat">
              <div>Start a new chat? This conversation is cleared from <GaltonName />, which starts again with no memory of it.
                Every question and reply stays in the audit trail, and drafts stay in the Client report.</div>
              <ErrorNote error={newChat.error} />
              <div className="mt-2 flex gap-2">
                <button className="btn btn-dark" disabled={newChat.isPending} onClick={() => newChat.mutate()}>
                  {newChat.isPending ? "Clearing…" : "Start new chat"}
                </button>
                <button className="btn" onClick={() => { newChat.reset(); setConfirmNew(false); }}>Cancel</button>
              </div>
            </div>
          ) : (
            <div className="mb-2 flex justify-end">
              <button className="btn" disabled={ask.isPending} onClick={() => setConfirmNew(true)}>+ New chat</button>
            </div>
          )
        )}
        {focusing && messages.data && (
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2 border-l-[3px] border-navy bg-navy-soft px-3 py-2 text-sm">
            <span>
              {focused.length > 0
                ? "Showing the exchange you opened."
                : "That exchange isn't in this chat (it may have been cleared with New chat). Its record is in the audit trail."}
            </span>
            <button className="text-xs underline" onClick={clearFocus}>Show latest</button>
          </div>
        )}
        <div ref={chat} className="relative max-h-[78vh] min-h-[320px] space-y-5 overflow-y-auto border border-rule bg-card p-5">
          {messages.isLoading && <Loading />}
          {messages.data?.length === 0 && !ask.isPending && (
            <p className="text-sm text-muted">Ask <GaltonName /> about this portfolio. Every figure in a reply is checked against the engine's output before you see it.</p>
          )}
          {messages.data?.map((m, i) => (
            <div key={i} data-msg={i}
                 className={focused.includes(i) ? "-mx-3 border-l-[3px] border-navy bg-navy-soft px-3 py-2" : ""}>
              <Message m={m}
                       onOpenStress={() => ws.go("stress", messages.data?.[i - 1]?.role === "user" ? messages.data[i - 1].content : "")} />
            </div>
          ))}
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
            <DraftEditor key={draft.id} draft={draft} />
            {draft.approved && (
              <button className="btn mt-3" onClick={() => ws.go("report", undefined, { report: draft.id })}>Open the client report →</button>
            )}
          </Panel>
        ) : draftId && drafts.isLoading ? (
          <Panel title="Draft explanation for the client"><Loading /></Panel>
        ) : (
          <Panel title="Client explanation">
            <p className="text-sm text-muted">"Draft the client explanation" writes a plain-language summary from the engine's figures for you to edit and approve.</p>
            {(drafts.data?.length ?? 0) > 0 && (
              <p className="mt-2 text-sm text-muted">
                Earlier explanations stay in the{" "}
                <button className="underline" onClick={() => ws.go("report")}>Client report</button> tab, where you can reopen and edit them.
              </p>
            )}
          </Panel>
        )}
      </div>
    </div>
  );
}

function Message({ m, onOpenStress }: { m: ChatMessage; onOpenStress: () => void }) {
  if (m.role === "user") {
    return <div className="ml-auto max-w-[85%] bg-ink px-4 py-3 text-sm text-card">{m.content}</div>;
  }
  const used = [...new Set((m.tool_calls ?? []).map((c) => c.label))];
  const ok = (m.tool_calls ?? []).filter((c) => !c.is_error && typeof c.output === "object");
  // Charts carry their own source line; the Sources list covers the other tools.
  const charts = ok.filter((c) => c.name === "show_chart").map((c) => (c.output as unknown as { chart: ChartSpec }).chart);
  // The referral is a pointer to the Stress test tab, not an engine run, so it isn't a source.
  const sources = ok.filter((c) => c.name !== "show_chart" && c.name !== "refer_to_stress_test");
  return (
    <div>
      <div className="mb-1 text-sm"><GaltonName /></div>
      {used.length > 0 && (
        <div className="mb-2 flex items-center gap-2"><span className="eyebrow">Used</span>{used.map((u) => <Chip key={u}>{u}</Chip>)}</div>
      )}
      {m.blocked ? (
        <div className="border border-bad/40 bg-bad/5 px-3 py-2 text-sm text-bad"><b>✗ Withheld.</b> {m.content}</div>
      ) : (
        <Markdown text={m.content} />
      )}
      {(m.tool_calls ?? []).some((c) => c.name === "refer_to_stress_test" && !c.is_error) && (
        <button className="btn mt-3" onClick={onOpenStress}>Open in Stress test →</button>
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
