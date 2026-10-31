// Shared building blocks. They lay out values they are given; none computes a figure.

import { useId, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../lib/api";
import { byKind, signed } from "../lib/format";
import type { ComparisonRow } from "../lib/types";

export function Panel({ title, tag, children, className = "" }: {
  title?: ReactNode; tag?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`border border-rule bg-card p-5 ${className}`}>
      {(title || tag) && (
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          {title && <h3 className="text-xl">{title}</h3>}
          {tag && <span className="eyebrow">{tag}</span>}
        </div>
      )}
      {children}
    </section>
  );
}

export function StatCard({ eyebrow, value, unit, note }: {
  eyebrow: string; value: ReactNode; unit?: string; note?: ReactNode;
}) {
  return (
    <div className="border border-rule border-t-[3px] border-t-gold bg-card px-5 py-4">
      <div className="eyebrow">{eyebrow}</div>
      <div className="my-1.5 font-serif text-[42px] leading-tight">
        {value}
        {unit && <span className="ml-1 text-xl text-faint">{unit}</span>}
      </div>
      {note && <div className="text-[13px] text-muted">{note}</div>}
    </div>
  );
}

/** A labelled horizontal bar. `fraction` (0..1) only sets the bar's drawn length. */
export function Bar({ label, shown, fraction, tone = "gold" }: {
  label: ReactNode; shown: string; fraction: number; tone?: "gold" | "bad";
}) {
  const width = `${Math.max(0, Math.min(1, fraction)) * 100}%`;
  return (
    <div className="my-2.5 text-sm">
      <div className="mb-1 flex justify-between gap-3">
        <span>{label}</span>
        <span className="figure text-[13px]">{shown}</span>
      </div>
      <div className="h-2 bg-track">
        <div className={`h-2 ${tone === "bad" ? "bg-bad/70" : "bg-gold"}`} style={{ width }} />
      </div>
    </div>
  );
}

/** The assistant's name. One place, so every screen says the same thing. */
export const ASSISTANT_NAME = "Galton AI";

/** The assistant's name in the brand navy, for paper and card grounds (not on navy). */
export function GaltonName({ className = "" }: { className?: string }) {
  return <span className={`font-semibold text-navy ${className}`}>{ASSISTANT_NAME}</span>;
}

export function Chip({ children }: { children: ReactNode }) {
  return <span className="bg-gold-soft px-2 py-0.5 text-xs">{children}</span>;
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="py-8 text-sm text-faint">{what}…</div>;
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof ApiError || error instanceof Error ? error.message : String(error);
  return <div className="my-3 border border-bad/40 bg-bad/5 px-3 py-2 text-sm text-bad">{msg}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="border border-dashed border-rule px-4 py-6 text-center text-sm text-muted">{children}</div>;
}

/** Engine comparison rows: before, after and the engine's own change figure. */
export function ComparisonTable({ rows, beforeLabel = "Before", afterLabel = "After" }: {
  rows: ComparisonRow[]; beforeLabel?: string; afterLabel?: string;
}) {
  return (
    <table className="data">
      <thead>
        <tr><th>Measure</th><th className="num">{beforeLabel}</th><th className="num">{afterLabel}</th><th className="num">Change</th></tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key}>
            <td>{r.label}</td>
            <td className="num">{byKind(r.kind, r.before)}</td>
            <td className="num">{byKind(r.kind, r.after)}</td>
            <td className="num">{signed(byKind(r.kind, r.change), r.change)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-semibold">{label}</span>
      {children}
    </label>
  );
}

/** A file picker that reads as a step to complete: an empty dashed box saying no file
 *  is chosen yet, then the chosen file's name with a way to change or remove it. */
export function FilePicker({ label, file, onChange, accept, hint, error, required = false }: {
  label: string; file: File | null; onChange: (f: File | null) => void;
  accept?: string; hint?: ReactNode; error?: string | null; required?: boolean;
}) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  function pick(f: File | null) {
    onChange(f);
    // Clear the native value so choosing the same file again still fires onChange.
    if (input.current) input.current.value = "";
  }

  const border = error ? "border-bad bg-bad/5" : file ? "border-gold border-solid bg-gold-soft/40"
    : dragging ? "border-gold bg-gold-soft/40" : "border-ink/40 hover:border-gold hover:bg-gold-soft/30";

  return (
    <div className="text-sm">
      <label htmlFor={id} className="mb-1 block font-semibold">
        {label}{required && <span className="ml-1 font-normal text-bad">(required)</span>}
      </label>
      <input ref={input} id={id} type="file" accept={accept} className="peer sr-only"
             aria-invalid={error ? true : undefined}
             onChange={(e) => pick(e.target.files?.[0] ?? null)} />
      <div
        className={`flex items-center gap-3 border-2 border-dashed px-3 py-3 transition-colors
                    peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-gold ${border}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); pick(e.dataTransfer.files?.[0] ?? null); }}
      >
        <span aria-hidden className={`flex h-9 w-9 shrink-0 items-center justify-center text-lg
                                      ${file ? "bg-gold text-ink" : "bg-track text-muted"}`}>
          {file ? "✓" : "↑"}
        </span>
        <div className="min-w-0 flex-1">
          {file ? (
            <>
              <div className="eyebrow">File chosen</div>
              <div className="break-all font-semibold" data-testid="chosen-file">{file.name}</div>
              <div className="mt-1 flex gap-3 text-xs">
                <label htmlFor={id} className="cursor-pointer underline hover:text-gold-dark">Change</label>
                <button type="button" className="underline hover:text-bad" aria-label={`Remove ${file.name}`}
                        onClick={() => pick(null)}>
                  Remove
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="font-semibold">No file chosen</div>
              <div className="text-xs text-muted">Choose a file, or drag one here</div>
            </>
          )}
        </div>
        {!file && (
          <label htmlFor={id} className="btn shrink-0 cursor-pointer !px-3 !py-1.5 text-xs">Choose file</label>
        )}
      </div>
      {error && <div role="alert" className="mt-1 text-xs text-bad">{error}</div>}
      {hint && <div className="mt-1 text-xs text-faint">{hint}</div>}
    </div>
  );
}
