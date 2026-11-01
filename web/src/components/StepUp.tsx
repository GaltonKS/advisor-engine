// Asks for a fresh authenticator code when the API says a sensitive action needs one
// (deleting a client, removing an authenticator). The API client calls stepUp(), this
// dialog opens, and the request is retried once with the new token. The API makes the
// rule; this only collects the code.

import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useAuth } from "../lib/auth";
import { onStepUp } from "../lib/mfa";
import { CodeInput } from "../pages/MfaSetup";
import { ErrorNote, Field } from "./ui";

export function StepUpProvider({ children }: { children: ReactNode }) {
  const { mfa, verifyCode } = useAuth();
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [factorId, setFactorId] = useState("");
  const resolver = useRef<((ok: boolean) => void) | null>(null);

  useEffect(() => onStepUp(() => new Promise<boolean>((resolve) => {
    resolver.current?.(false); // a second request while open: the first one gives up
    resolver.current = resolve;
    setCode("");
    setError(null);
    setFactorId("");
    setOpen(true);
  })), []);

  function finish(ok: boolean) {
    resolver.current?.(ok);
    resolver.current = null;
    setOpen(false);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    const id = factorId || mfa.factors[0]?.id;
    if (!id) {
      setError("Set up an authenticator first, under Security.");
      return;
    }
    setBusy(true);
    setError(null);
    const err = await verifyCode(id, code);
    setBusy(false);
    if (err) {
      setError(err);
      setCode("");
    } else {
      finish(true);
    }
  }

  return (
    <>
      {children}
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-navy/60 px-4" role="presentation">
          <form onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="step-up-title"
                className="w-full max-w-sm border border-rule border-t-[3px] border-t-gold bg-card p-6">
            <h2 id="step-up-title" className="mb-1 text-2xl">Confirm it's you</h2>
            <p className="mb-4 text-sm text-muted">
              This action needs a fresh code. Enter the six-digit code from your authenticator app.
            </p>
            {mfa.factors.length > 1 && (
              <div className="mb-3">
                <Field label="Authenticator">
                  <select className="input" value={factorId || mfa.factors[0].id}
                          onChange={(e) => setFactorId(e.target.value)}>
                    {mfa.factors.map((f) => <option key={f.id} value={f.id}>{f.friendly_name || "Authenticator"}</option>)}
                  </select>
                </Field>
              </div>
            )}
            <Field label="Six-digit code">
              <CodeInput value={code} onChange={setCode} />
            </Field>
            {error && <ErrorNote error={error} />}
            <div className="mt-4 flex gap-2">
              <button className="btn btn-primary" disabled={busy || code.length !== 6}>
                {busy ? "Checking…" : "Confirm"}
              </button>
              <button type="button" className="btn" onClick={() => finish(false)}>Cancel</button>
            </div>
          </form>
        </div>
      )}
    </>
  );
}
