// New sign-ups wait here until the operator verifies their CRD number and approves them.
// The API refuses every client route until then; this page explains why and collects
// what the operator needs to check.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { ErrorNote, Field, Panel } from "../components/ui";
import { useApi } from "../lib/api";
import { dateTime } from "../lib/format";
import type { Adviser, Me } from "../lib/types";

export default function PendingApproval({ me }: { me: Me }) {
  const api = useApi();
  const qc = useQueryClient();
  const a = me.adviser;
  const [fullName, setFullName] = useState(a.full_name ?? "");
  const [crd, setCrd] = useState(a.crd ?? "");
  const [firm, setFirm] = useState("");
  const submit = useMutation({
    mutationFn: () => api<{ adviser: Adviser }>("/me/registration",
      { json: { full_name: fullName.trim(), crd: crd.trim(), firm_name: firm.trim() } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me"] }),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit.mutate();
  }

  const submitted = Boolean(a.registration_submitted_at);
  return (
    <div className="mx-auto max-w-xl py-6">
      <div className="eyebrow mb-2">Account awaiting approval</div>
      <h1 className="mb-3 text-3xl">GaltonKS is for registered advisers only</h1>
      <p className="mb-6 text-muted">
        Before your account can open client data, we verify your registration on FINRA BrokerCheck.
        {submitted
          ? " Thanks: your details are with us. You'll have access as soon as they're verified."
          : " Tell us who you are and we'll review it, usually within one business day."}
      </p>
      {submitted ? (
        <Panel title="Submitted for review" tag={a.registration_submitted_at ? dateTime(a.registration_submitted_at) : undefined}>
          <dl className="grid grid-cols-[120px_1fr] gap-y-2 text-sm">
            <dt className="text-muted">Name</dt><dd>{a.full_name}</dd>
            <dt className="text-muted">CRD number</dt><dd className="figure">{a.crd}</dd>
            <dt className="text-muted">Email</dt><dd>{a.email}</dd>
          </dl>
          <p className="mt-4 text-xs text-muted">Something wrong? Email us from this address and we'll correct it.</p>
        </Panel>
      ) : (
        <Panel title="Your registration">
          <form onSubmit={onSubmit} className="space-y-4">
            <Field label="Full name (as registered)">
              <input className="input" required maxLength={200} value={fullName} autoComplete="name"
                     onChange={(e) => setFullName(e.target.value)} />
            </Field>
            <Field label="CRD number">
              <input className="input figure" required inputMode="numeric" pattern="[0-9]{1,8}" maxLength={8}
                     title="Your FINRA CRD number: digits only, up to 8" value={crd}
                     onChange={(e) => setCrd(e.target.value.replace(/\D/g, ""))} placeholder="e.g. 1234567" />
            </Field>
            <Field label="Firm name">
              <input className="input" required maxLength={200} value={firm} autoComplete="organization"
                     onChange={(e) => setFirm(e.target.value)} />
            </Field>
            <ErrorNote error={submit.error} />
            <button className="btn btn-primary w-full justify-center" disabled={submit.isPending}>
              {submit.isPending ? "Submitting…" : "Submit for review"}
            </button>
          </form>
        </Panel>
      )}
    </div>
  );
}
