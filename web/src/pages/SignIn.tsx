import { useState, type FormEvent } from "react";
import { Brand } from "../components/Layout";
import { Field } from "../components/ui";
import { useAuth } from "../lib/auth";

export default function SignIn() {
  const { signIn, signUp } = useAuth();
  const [mode, setMode] = useState<"in" | "up">("in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    const err = mode === "in" ? await signIn(email, password) : await signUp(email, password);
    setBusy(false);
    if (err) setError(err);
    else if (mode === "up") setNotice("Check your inbox: confirm your email address, then sign in.");
  }

  return (
    <div className="min-h-screen">
      <header className="navy-band">
        <div className="mx-auto max-w-[1240px] px-4 py-3"><Brand /></div>
      </header>
      <main className="mx-auto grid max-w-[1240px] gap-10 px-4 py-12 md:grid-cols-[1.2fr_1fr]">
        <div>
          <div className="eyebrow mb-3">A private workspace</div>
          <h1 className="text-4xl leading-tight md:text-5xl">
            Analysis your clients can follow, behind a door only advisers can open.
          </h1>
          <p className="mt-5 max-w-md text-muted">
            GaltonKS turns a client's holdings and goals into scored, explained options. You make every
            decision. Every figure comes from the engine, never from the language model.
          </p>
          <div className="my-6 h-px w-14 bg-gold" />
          <ol className="space-y-3 text-sm">
            <li><span className="figure mr-3 text-gold-dark">01</span><b>Each firm sees only its own clients.</b> Enforced in the database, not just the app.</li>
            <li><span className="figure mr-3 text-gold-dark">02</span><b>Every figure is checked.</b> An explanation with a number the engine didn't produce is withheld.</li>
            <li><span className="figure mr-3 text-gold-dark">03</span><b>Every output is logged</b> with its inputs, so you can show how a recommendation was reached.</li>
          </ol>
        </div>
        <form onSubmit={submit} className="self-start border border-rule border-t-[3px] border-t-gold bg-card p-6">
          <h2 className="mb-1 text-2xl">{mode === "in" ? "Sign in" : "Request access"}</h2>
          <p className="mb-5 text-xs text-muted">
            {mode === "in" ? "Use the email you registered with." : "Create an account. We'll email you to confirm the address."}
          </p>
          <div className="space-y-4">
            <Field label="Work email">
              <input className="input" type="email" required autoComplete="email" value={email}
                     onChange={(e) => setEmail(e.target.value)} placeholder="name@yourfirm.com" />
            </Field>
            <Field label="Password">
              <input className="input" type="password" required minLength={8} value={password}
                     autoComplete={mode === "in" ? "current-password" : "new-password"}
                     onChange={(e) => setPassword(e.target.value)} />
            </Field>
          </div>
          {error && <div className="mt-4 text-sm text-bad">{error}</div>}
          {notice && <div className="mt-4 text-sm text-good">{notice}</div>}
          <button className="btn btn-primary mt-5 w-full justify-center" disabled={busy}>
            {busy ? "Please wait…" : mode === "in" ? "Sign in" : "Create account"}
          </button>
          <button type="button" className="mt-3 w-full text-center text-xs text-muted underline"
                  onClick={() => setMode(mode === "in" ? "up" : "in")}>
            {mode === "in" ? "New here? Request access" : "Already registered? Sign in"}
          </button>
          <p className="mt-5 text-[11px] text-faint">
            GaltonKS is decision support for registered advisers. It is not investment advice and does not hold or move client money.
          </p>
        </form>
      </main>
    </div>
  );
}
