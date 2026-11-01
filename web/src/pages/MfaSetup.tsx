// Two-step sign-in screens: setting up an authenticator, and entering a code after the
// password. Supabase checks the codes; the API refuses sessions that skipped this step,
// so these screens guide the adviser rather than guard anything themselves.

import { useState, type FormEvent } from "react";
import { ErrorNote, Field, Panel } from "../components/ui";
import { useApi } from "../lib/api";
import { useAuth, type Enrolment } from "../lib/auth";
import { date } from "../lib/format";
import { canPostpone, qrSrc } from "../lib/mfa";
import type { Me } from "../lib/types";

export const LOST_DEVICE =
  "Lost your phone or authenticator? Contact GaltonKS support from your registered email. " +
  "We'll confirm who you are with your firm before resetting two-step sign-in; we never " +
  "reset it from an email or in-app request alone.";

/** A six-digit code field: numeric keyboard on phones, one-time-code autofill. */
export function CodeInput({ value, onChange, autoFocus = true }: {
  value: string; onChange: (v: string) => void; autoFocus?: boolean;
}) {
  return (
    <input className="input figure tracking-[0.3em]" inputMode="numeric" autoComplete="one-time-code"
           pattern="[0-9]{6}" maxLength={6} required autoFocus={autoFocus} placeholder="123456"
           value={value} onChange={(e) => onChange(e.target.value.replace(/\D/g, "").slice(0, 6))} />
  );
}

/** Add an authenticator: name it, scan the QR code, enter the first code. */
export function EnrolPanel({ onDone, title = "Set up your authenticator" }: {
  onDone: () => void; title?: string;
}) {
  const { startEnrolment, verifyCode, refreshMfa } = useAuth();
  const api = useApi();
  const [name, setName] = useState("");
  const [enrolment, setEnrolment] = useState<Enrolment | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showSecret, setShowSecret] = useState(false);

  async function start(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const r = await startEnrolment(name.trim());
    setBusy(false);
    if (typeof r === "string") setError(r);
    else setEnrolment(r);
  }

  async function verify(e: FormEvent) {
    e.preventDefault();
    if (!enrolment) return;
    setBusy(true);
    setError(null);
    const err = await verifyCode(enrolment.factorId, code);
    if (err) {
      setBusy(false);
      setError(err);
      return;
    }
    // Recorded in the firm's audit trail once the API confirms it with Supabase. The
    // authenticator works either way, so a failure here doesn't stop the adviser.
    try {
      await api("/me/mfa/enrolled", { json: { factor_id: enrolment.factorId } });
    } catch {
      /* the API logs its own failures */
    }
    await refreshMfa();
    setBusy(false);
    onDone();
  }

  if (!enrolment) {
    return (
      <Panel title={title}>
        <p className="mb-4 text-sm text-muted">
          Use an authenticator app such as Google Authenticator, Microsoft Authenticator, 1Password
          or Authy. You'll enter a six-digit code from it each time you sign in.
        </p>
        <form onSubmit={start} className="space-y-4">
          <Field label="Name this authenticator">
            <input className="input" maxLength={60} value={name} placeholder="e.g. Work phone"
                   onChange={(e) => setName(e.target.value)} />
          </Field>
          {error && <ErrorNote error={error} />}
          <button className="btn btn-primary w-full justify-center" disabled={busy}>
            {busy ? "Please wait…" : "Show QR code"}
          </button>
        </form>
      </Panel>
    );
  }

  return (
    <Panel title={title}>
      <ol className="mb-4 list-decimal space-y-1 pl-5 text-sm text-muted">
        <li>Open your authenticator app and add an account.</li>
        <li>Scan this QR code, or type the key below.</li>
        <li>Enter the six-digit code the app shows.</li>
      </ol>
      <div className="mb-4 flex justify-center border border-rule bg-white p-3">
        <img src={qrSrc(enrolment.qr)} alt="QR code to add GaltonKS to your authenticator app"
             className="h-48 w-48" />
      </div>
      <div className="mb-4 text-xs">
        {showSecret ? (
          <div>
            <div className="mb-1 text-muted">Set-up key (enter it in the app if you can't scan):</div>
            <code className="figure block break-all border border-rule bg-paper px-2 py-1">{enrolment.secret}</code>
          </div>
        ) : (
          <button type="button" className="text-muted underline" onClick={() => setShowSecret(true)}>
            Can't scan? Show the set-up key
          </button>
        )}
      </div>
      <form onSubmit={verify} className="space-y-4">
        <Field label="Six-digit code">
          <CodeInput value={code} onChange={setCode} />
        </Field>
        {error && <ErrorNote error={error} />}
        <button className="btn btn-primary w-full justify-center" disabled={busy || code.length !== 6}>
          {busy ? "Checking…" : "Verify and turn on"}
        </button>
      </form>
    </Panel>
  );
}

/** Shown after the password when the adviser has no authenticator yet. */
export function MfaEnrolScreen({ me, onPostpone, onFinish }: {
  me: Me; onPostpone: () => void; onFinish: () => void;
}) {
  const { mfa } = useAuth();
  const [added, setAdded] = useState(false);
  const [addingBackup, setAddingBackup] = useState(false);
  const postpone = canPostpone(me.mfa);
  const deadline = me.mfa?.deadline;

  if (added && !addingBackup) {
    const hasBackup = mfa.factors.length > 1;
    return (
      <div className="mx-auto max-w-lg">
        <div className="eyebrow mb-2">Two-step sign-in</div>
        <h1 className="mb-3 text-3xl">{hasBackup ? "Backup added" : "Two-step sign-in is on"}</h1>
        <p className="mb-6 text-muted">
          {hasBackup
            ? "You can now sign in with either authenticator."
            : "We strongly recommend a second authenticator as a backup (another phone, or a password " +
              "manager that stores codes). If you lose your only one, getting back in takes an identity check."}
        </p>
        <div className="flex flex-wrap gap-3">
          {!hasBackup && (
            <button className="btn btn-primary" onClick={() => setAddingBackup(true)}>Add a backup authenticator</button>
          )}
          <button className="btn" onClick={onFinish}>Continue to GaltonKS</button>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-lg">
      <div className="eyebrow mb-2">Two-step sign-in</div>
      <h1 className="mb-3 text-3xl">
        {addingBackup ? "Add a backup authenticator" : "Protect your account with a second step"}
      </h1>
      {!addingBackup && (
        <p className="mb-6 text-muted">
          Your account opens client holdings and records, so GaltonKS asks for a code from an
          authenticator app as well as your password.
          {me.mfa?.mode === "required"
            ? " It's required for every adviser."
            : deadline ? ` It becomes required on ${date(deadline)}.` : ""}
        </p>
      )}
      <EnrolPanel title={addingBackup ? "Backup authenticator" : undefined}
                  onDone={() => { setAdded(true); setAddingBackup(false); }} />
      {postpone && !addingBackup && (
        <button className="mt-4 w-full text-center text-xs text-muted underline" onClick={onPostpone}>
          Remind me next time{deadline ? ` (until ${date(deadline)})` : ""}
        </button>
      )}
    </div>
  );
}

/** Shown after the password when the adviser has an authenticator. */
export function MfaChallengeScreen() {
  const { mfa, verifyCode, signOut } = useAuth();
  const [factorId, setFactorId] = useState(mfa.factors[0]?.id ?? "");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const err = await verifyCode(factorId, code);
    setBusy(false);
    if (err) {
      setError(err);
      setCode("");
    }
  }

  return (
    <div className="mx-auto max-w-md">
      <div className="eyebrow mb-2">Two-step sign-in</div>
      <h1 className="mb-3 text-3xl">Enter your code</h1>
      <p className="mb-6 text-muted">Open your authenticator app and enter the six-digit code for GaltonKS.</p>
      <Panel>
        <form onSubmit={submit} className="space-y-4">
          {mfa.factors.length > 1 && (
            <Field label="Authenticator">
              <select className="input" value={factorId} onChange={(e) => setFactorId(e.target.value)}>
                {mfa.factors.map((f) => <option key={f.id} value={f.id}>{f.friendly_name || "Authenticator"}</option>)}
              </select>
            </Field>
          )}
          <Field label="Six-digit code">
            <CodeInput value={code} onChange={setCode} />
          </Field>
          {error && <ErrorNote error={error} />}
          <button className="btn btn-primary w-full justify-center" disabled={busy || code.length !== 6}>
            {busy ? "Checking…" : "Continue"}
          </button>
        </form>
      </Panel>
      <details className="mt-4 text-xs text-muted">
        <summary className="cursor-pointer underline">Lost your device?</summary>
        <p className="mt-2">{LOST_DEVICE}</p>
      </details>
      <button className="mt-3 text-xs text-muted underline" onClick={() => signOut()}>Sign out</button>
    </div>
  );
}
