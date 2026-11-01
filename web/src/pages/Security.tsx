// Security: the adviser's own authenticators, and for a firm's owner, which of the
// firm's advisers have set up two-step sign-in. Removing an authenticator goes through
// the API, which asks for a fresh code and never removes the last one while two-step
// sign-in is required. Counts and dates shown here come from the API as they are.

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useMe } from "../components/Layout";
import { Empty, ErrorNote, Loading, Panel } from "../components/ui";
import { useApi } from "../lib/api";
import { useAuth } from "../lib/auth";
import { date } from "../lib/format";
import type { FirmMfa } from "../lib/types";
import { EnrolPanel, LOST_DEVICE } from "./MfaSetup";

function MyAuthenticators() {
  const { mfa, refreshMfa } = useAuth();
  const api = useApi();
  const [adding, setAdding] = useState(false);
  const remove = useMutation({
    mutationFn: (id: string) => api<null>(`/me/mfa/factors/${id}`, { method: "DELETE" }),
    onSuccess: () => refreshMfa(),
  });

  return (
    <Panel title="Your authenticators" tag="Two-step sign-in">
      {mfa.factors.length === 0 ? (
        <Empty>No authenticator yet. Add one to turn on two-step sign-in.</Empty>
      ) : (
        <ul className="divide-y divide-rule border border-rule">
          {mfa.factors.map((f) => (
            <li key={f.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm">
              <div>
                <div className="font-semibold">{f.friendly_name || "Authenticator"}</div>
                <div className="text-xs text-muted">Added {date(f.created_at)}</div>
              </div>
              <button className="text-xs text-muted underline-offset-2 hover:text-bad hover:underline"
                      disabled={remove.isPending} onClick={() => remove.mutate(f.id)}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <ErrorNote error={remove.error} />
      {mfa.factors.length === 1 && (
        <p className="mt-3 text-sm text-muted">
          Add a second authenticator as a backup (another phone, or a password manager that stores
          codes), so losing one device doesn't lock you out.
        </p>
      )}
      <div className="mt-4">
        {adding ? (
          <EnrolPanel title="Add an authenticator" onDone={() => setAdding(false)} />
        ) : (
          <button className="btn btn-primary" onClick={() => setAdding(true)}>Add an authenticator</button>
        )}
      </div>
      <p className="mt-4 text-xs text-muted">{LOST_DEVICE}</p>
    </Panel>
  );
}

function FirmEnrolment() {
  const api = useApi();
  const q = useQuery({ queryKey: ["firm-mfa"], queryFn: () => api<FirmMfa>("/firm/mfa") });
  if (q.isLoading) return <Loading what="Loading your firm" />;
  if (q.error) return <ErrorNote error={q.error} />;
  const data = q.data!;
  return (
    <Panel title="Two-step sign-in at your firm" tag="Firm owner">
      <p className="mb-3 text-sm text-muted">
        Two-step sign-in is required for every adviser at every firm.
        {data.mode === "enrol" && data.deadline ? ` Advisers can postpone it until ${date(data.deadline)}.` : ""}
      </p>
      <table className="data">
        <thead>
          <tr><th>Adviser</th><th>Role</th><th>Two-step sign-in</th><th className="num">Authenticators</th><th>Latest added</th></tr>
        </thead>
        <tbody>
          {data.advisers.map((a) => (
            <tr key={a.adviser_id}>
              <td>
                <div>{a.full_name || a.email}</div>
                {a.full_name && <div className="text-xs text-muted">{a.email}</div>}
              </td>
              <td className="capitalize">{a.role}</td>
              <td className={a.enrolled ? "text-good" : "font-semibold text-bad"}>{a.enrolled ? "Set up" : "Not set up"}</td>
              <td className="num">{a.factor_count}</td>
              <td>{a.latest_factor_at ? date(a.latest_factor_at) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-3 text-xs text-muted">Names, counts and dates only. Codes and set-up keys are never shown to anyone.</p>
    </Panel>
  );
}

export default function Security() {
  const { dev } = useAuth();
  const me = useMe();
  if (dev) {
    return <Empty>Two-step sign-in isn't available with dev sign-in. It applies once Supabase sign-in is configured.</Empty>;
  }
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-3xl">Security</h1>
      <MyAuthenticators />
      {me.data?.adviser.role === "owner" && me.data.adviser.status !== "pending" && <FirmEnrolment />}
    </div>
  );
}
