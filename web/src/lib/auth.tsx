// Sign-in. With VITE_DEV_USER set the app skips Supabase and identifies itself with the
// X-Dev-User header, which only an API running dev auth accepts (and that API always
// uses the mock assistant). Otherwise it signs in with Supabase and sends the session's
// access token, which the API verifies.
//
// Two-step sign-in: enrolling an authenticator and entering its codes happen between the
// browser and Supabase, which then issues a token at assurance level "aal2". The API
// checks that level; this file only tracks it so the right screen is shown.

import { createClient, type Factor, type Session, type SupabaseClient } from "@supabase/supabase-js";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { onMfaRequired } from "./mfa";

const DEV_USER = import.meta.env.VITE_DEV_USER as string | undefined;
const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const SUPABASE_KEY = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string | undefined;

export const supabase: SupabaseClient | null =
  !DEV_USER && SUPABASE_URL && SUPABASE_KEY ? createClient(SUPABASE_URL, SUPABASE_KEY) : null;

export interface Authenticator {
  id: string;
  friendly_name: string;
  created_at: string;
}

export interface MfaState {
  /** False until the level and factors have been read for this session. */
  loaded: boolean;
  /** "aal1" after a password alone, "aal2" once a code has been entered. */
  currentLevel: string | null;
  /** Verified authenticators. */
  factors: Authenticator[];
}

export interface Enrolment {
  factorId: string;
  qr: string;
  secret: string;
}

export interface AuthState {
  ready: boolean;
  email: string | null;
  dev: boolean;
  headers: () => Record<string, string>;
  signIn: (email: string, password: string) => Promise<string | null>;
  signUp: (email: string, password: string) => Promise<string | null>;
  signOut: () => Promise<void>;
  mfa: MfaState;
  refreshMfa: () => Promise<void>;
  /** Start adding an authenticator: a QR code and secret to show once, never stored. */
  startEnrolment: (friendlyName: string) => Promise<Enrolment | string>;
  /** Enter a code for a factor. Returns an error message, or null once verified. */
  verifyCode: (factorId: string, code: string) => Promise<string | null>;
}

const NO_MFA: MfaState = { loaded: false, currentLevel: null, factors: [] };
const AuthContext = createContext<AuthState | null>(null);

function authenticators(factors: Factor[]): Authenticator[] {
  return factors
    .filter((f) => f.status === "verified")
    .map((f) => ({ id: f.id, friendly_name: f.friendly_name ?? "", created_at: f.created_at }));
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(Boolean(DEV_USER));
  const [mfa, setMfa] = useState<MfaState>(DEV_USER ? { ...NO_MFA, loaded: true } : NO_MFA);
  // headers() reads the latest token, so a request retried right after a fresh code
  // carries the new one rather than the token from the last render.
  const sessionRef = useRef<Session | null>(null);

  const refreshMfa = useCallback(async () => {
    if (!supabase) return;
    const [level, factors] = await Promise.all([
      supabase.auth.mfa.getAuthenticatorAssuranceLevel(),
      supabase.auth.mfa.listFactors(),
    ]);
    setMfa({
      loaded: true,
      currentLevel: level.data?.currentLevel ?? null,
      factors: authenticators(factors.data?.all ?? []),
    });
  }, []);

  useEffect(() => {
    if (!supabase) {
      setReady(true);
      return;
    }
    supabase.auth.getSession().then(({ data }) => {
      sessionRef.current = data.session;
      setSession(data.session);
      setReady(true);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, s) => {
      sessionRef.current = s;
      setSession(s);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  useEffect(() => {
    if (session) void refreshMfa();
    else setMfa(DEV_USER ? { ...NO_MFA, loaded: true } : NO_MFA);
  }, [session?.access_token, refreshMfa]); // eslint-disable-line react-hooks/exhaustive-deps

  // The API said this session needs its second factor: re-read the level so the gate
  // shows the code screen.
  useEffect(() => onMfaRequired(() => void refreshMfa()), [refreshMfa]);

  const state: AuthState = {
    ready,
    dev: Boolean(DEV_USER),
    email: DEV_USER ? `${DEV_USER}@dev.local` : (session?.user.email ?? null),
    headers: (): Record<string, string> => {
      if (DEV_USER) return { "X-Dev-User": DEV_USER };
      const s = sessionRef.current;
      return s ? { Authorization: `Bearer ${s.access_token}` } : {};
    },
    signIn: async (email, password) => {
      if (!supabase) return "Sign-in is not configured (VITE_SUPABASE_URL / key).";
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      return error?.message ?? null;
    },
    signUp: async (email, password) => {
      if (!supabase) return "Sign-up is not configured (VITE_SUPABASE_URL / key).";
      const { error } = await supabase.auth.signUp({
        email,
        password,
        options: { emailRedirectTo: window.location.origin },
      });
      return error?.message ?? null;
    },
    signOut: async () => {
      await supabase?.auth.signOut();
    },
    mfa,
    refreshMfa,
    startEnrolment: async (friendlyName) => {
      if (!supabase) return "Two-step sign-in needs Supabase sign-in.";
      // An abandoned set-up leaves an unverified factor behind; clear those first so the
      // name can be reused.
      const existing = await supabase.auth.mfa.listFactors();
      for (const f of existing.data?.all ?? []) {
        if (f.status !== "verified") await supabase.auth.mfa.unenroll({ factorId: f.id });
      }
      const { data, error } = await supabase.auth.mfa.enroll({
        factorType: "totp",
        friendlyName: friendlyName || `Authenticator ${new Date().toISOString().slice(0, 10)}`,
      });
      if (error || !data) return error?.message ?? "Couldn't start set-up. Please try again.";
      return { factorId: data.id, qr: data.totp.qr_code, secret: data.totp.secret };
    },
    verifyCode: async (factorId, code) => {
      if (!supabase) return "Two-step sign-in needs Supabase sign-in.";
      const { error } = await supabase.auth.mfa.challengeAndVerify({ factorId, code: code.trim() });
      if (error) {
        return /invalid|expired/i.test(error.message)
          ? "That code didn't work. Check your authenticator app and enter the current code."
          : error.message;
      }
      const { data } = await supabase.auth.getSession();
      sessionRef.current = data.session;
      setSession(data.session);
      await refreshMfa();
      return null;
    },
  };
  return <AuthContext.Provider value={state}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
