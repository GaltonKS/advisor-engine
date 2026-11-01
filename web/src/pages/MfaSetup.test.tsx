import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import type { Me } from "../lib/types";

const verifyCode = vi.fn(async () => null as string | null);
vi.mock("../lib/auth", () => ({
  useAuth: () => ({
    mfa: { loaded: true, currentLevel: "aal1",
           factors: [{ id: "f1", friendly_name: "Work phone", created_at: "2026-11-01T00:00:00Z" }] },
    verifyCode,
    signOut: vi.fn(),
    startEnrolment: vi.fn(),
    refreshMfa: vi.fn(),
    headers: () => ({}),
  }),
}));

const { MfaChallengeScreen, MfaEnrolScreen } = await import("./MfaSetup");

function wrap(children: ReactNode) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

const me = (mode: "enrol" | "required", deadline: string | null): Me => ({
  adviser: { id: "a", email: "a@firm.com", firm_id: "f", role: "owner" },
  firm: { id: "f" }, auth: "supabase", llm: "mock", daily_turn_limit: 200, engine_version: "0",
  mfa: { mode, deadline, aal: "aal1", fresh_seconds: 300 },
});

describe("MfaChallengeScreen", () => {
  it("sends the six-digit code to Supabase for the adviser's authenticator", async () => {
    render(wrap(<MfaChallengeScreen />));
    const button = screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText("123456"), { target: { value: "12 34-56" } });
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    await waitFor(() => expect(verifyCode).toHaveBeenCalledWith("f1", "123456"));
  });
});

describe("MfaEnrolScreen", () => {
  it("offers to postpone during the grace period only", () => {
    const { unmount } = render(wrap(<MfaEnrolScreen me={me("enrol", "2999-01-01")} onPostpone={vi.fn()} onFinish={vi.fn()} />));
    expect(screen.getByText(/Remind me next time/)).toBeTruthy();
    unmount();
    render(wrap(<MfaEnrolScreen me={me("required", null)} onPostpone={vi.fn()} onFinish={vi.fn()} />));
    expect(screen.queryByText(/Remind me next time/)).toBeNull();
    expect(screen.getByText(/required for every adviser/)).toBeTruthy();
  });
});
