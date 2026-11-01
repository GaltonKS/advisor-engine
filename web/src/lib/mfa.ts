// Two-step sign-in (multi-factor) in the browser. The API decides what a session may do
// (docs/mfa-plan.md); this file only decides which screen to show, and lets the API
// client ask for a fresh code when a sensitive action needs one.

export type MfaMode = "off" | "enrol" | "required";
export type MfaStep = "ok" | "challenge" | "enrol";

export interface MfaInfo {
  mode: MfaMode;
  /** ISO date two-step sign-in becomes required, during the `enrol` grace period. */
  deadline: string | null;
  aal: string;
  fresh_seconds: number;
}

/** Days are compared as ISO date strings ("2026-11-08"), never converted to numbers. */
export function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/** During `enrol`, the set-up screen can be postponed until the deadline, not after. */
export function canPostpone(mfa: MfaInfo | undefined, on = today()): boolean {
  return mfa?.mode === "enrol" && (!mfa.deadline || on < mfa.deadline);
}

/**
 * Which screen a signed-in adviser sees first.
 * - Someone with an authenticator always enters a code, whatever the mode: they chose
 *   two-step sign-in, and a password alone shouldn't open their account.
 * - Without one: set it up when required, or during `enrol` unless postponed.
 */
export function mfaStep(opts: {
  mfa: MfaInfo | undefined;
  currentLevel: string | null;
  hasFactor: boolean;
  postponed: boolean;
}): MfaStep {
  const { mfa, currentLevel, hasFactor, postponed } = opts;
  if (!mfa) return "ok";
  if (hasFactor) return currentLevel === "aal2" ? "ok" : "challenge";
  if (mfa.mode === "required") return "enrol";
  if (mfa.mode === "enrol" && !(postponed && canPostpone(mfa))) return "enrol";
  return "ok";
}

/** Supabase gives the QR code as SVG markup or as a data URL; an <img> needs a URL. */
export function qrSrc(qr: string): string {
  return qr.startsWith("data:") ? qr : `data:image/svg+xml;utf-8,${encodeURIComponent(qr)}`;
}

// --- hooks for the API client ------------------------------------------------------------
// Set by the components that can answer them (StepUpProvider, AuthProvider).

type StepUp = () => Promise<boolean>;
let stepUpHandler: StepUp | null = null;
let mfaRequiredHandler: (() => void) | null = null;

/** Register the dialog that asks for a fresh code; returns an unregister function. */
export function onStepUp(fn: StepUp): () => void {
  stepUpHandler = fn;
  return () => { if (stepUpHandler === fn) stepUpHandler = null; };
}

export function onMfaRequired(fn: () => void): () => void {
  mfaRequiredHandler = fn;
  return () => { if (mfaRequiredHandler === fn) mfaRequiredHandler = null; };
}

/** Ask for a fresh code. False when cancelled or nothing can ask. */
export function stepUp(): Promise<boolean> {
  return stepUpHandler ? stepUpHandler() : Promise.resolve(false);
}

export function mfaRequired(): void {
  mfaRequiredHandler?.();
}

export function errorCode(detail: unknown): string | null {
  return detail && typeof detail === "object" && "code" in detail ? String(detail.code) : null;
}
