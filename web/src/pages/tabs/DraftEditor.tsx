import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ErrorNote } from "../../components/ui";
import { ApiError, useApi } from "../../lib/api";
import { dateTime } from "../../lib/format";
import type { Draft } from "../../lib/types";

/** The text an approved draft prints with, or the model's original for one not yet approved. */
export const savedText = (d: Draft): string => (d.approved ? d.approved_text ?? d.original : d.original);

/** Unsaved edits live in the query cache, keyed by draft, so they survive switching
 *  tabs (Galton AI and Client report share them) but never leave the browser tab. */
const editKey = (id: string) => ["draft-edit", id] as const;

export function useDraftText(d: Draft): [string, (t: string) => void, () => void] {
  const qc = useQueryClient();
  const [text, setLocal] = useState<string>(() => qc.getQueryData<string>(editKey(d.id)) ?? savedText(d));
  const set = (t: string) => { setLocal(t); qc.setQueryData(editKey(d.id), t); };
  const reset = () => { setLocal(savedText(d)); qc.resetQueries({ queryKey: editKey(d.id), exact: true }); };
  return [text, set, reset];
}

/** Whether a draft has edits that aren't approved yet (printing waits for them).
 *  Subscribes to the edit buffer, so it updates as the adviser types. */
export function useUnsavedEdits(d: Draft | undefined): boolean {
  const edit = useQuery({
    queryKey: editKey(d?.id ?? ""), queryFn: () => (d ? savedText(d) : ""),
    enabled: false, staleTime: Infinity,
  });
  return Boolean(d) && edit.data !== undefined && edit.data !== savedText(d!);
}

/** Edit and approve a client explanation. Approved ones stay editable: each saved
 *  change is re-checked against the engine's figures and re-approved (audited as a
 *  revision), so the report only ever prints verified text. Callers key it by draft id. */
export function DraftEditor({ draft }: { draft: Draft }) {
  const api = useApi();
  const qc = useQueryClient();
  const [text, setText, reset] = useDraftText(draft);

  const save = useMutation({
    mutationFn: () => api<Draft>(`/drafts/${draft.id}/approve`, { json: { text } }),
    onSuccess: (d) => {
      qc.setQueryData<Draft[]>(["drafts", d.portfolio_id], (all) => all?.map((x) => (x.id === d.id ? { ...x, ...d } : x)));
      qc.invalidateQueries({ queryKey: ["drafts", d.portfolio_id] });
      qc.resetQueries({ queryKey: editKey(d.id), exact: true });
      qc.invalidateQueries({ queryKey: ["audit"] });
    },
  });
  const unmatched = save.error instanceof ApiError
    ? ((save.error.detail as { unmatched?: string[] })?.unmatched ?? []) : [];
  const dirty = text !== savedText(draft);
  const canSave = draft.approved ? dirty : text.trim().length > 0;

  return (
    <div>
      <textarea className="input min-h-[300px] font-serif text-[15px] leading-relaxed" value={text}
                aria-label="Client explanation"
                onChange={(e) => { setText(e.target.value); save.reset(); }} />
      {unmatched.length > 0 && (
        <div className="mt-2 text-sm text-bad">Not saved: these figures are not from the engine: {unmatched.join(", ")}.</div>
      )}
      {save.error && unmatched.length === 0 && <ErrorNote error={save.error} />}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button className="btn btn-dark" onClick={() => save.mutate()} disabled={save.isPending || !canSave}>
          {draft.approved ? "Save changes" : "Approve"}
        </button>
        {dirty && (
          <button className="text-xs underline" onClick={() => { reset(); save.reset(); }}>
            {draft.approved ? "Discard changes" : "Undo edits"}
          </button>
        )}
      </div>
      {draft.approved && !dirty && (
        <p className="mt-3 text-sm text-good">
          Approved{draft.approved_at ? ` ${dateTime(draft.approved_at)}` : ""} and logged with your name. Nothing was sent. It appears in the client report.
        </p>
      )}
      {draft.approved && dirty && (
        <p className="mt-3 text-sm text-muted">Unsaved changes. The report keeps the approved text until you save.</p>
      )}
      <p className="mt-3 text-xs text-muted">Edits are re-checked: you can reword freely, but a figure the engine didn't produce blocks approval.</p>
    </div>
  );
}
