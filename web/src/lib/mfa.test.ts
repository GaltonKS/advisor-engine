import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, request } from "./api";
import { canPostpone, mfaStep, onMfaRequired, onStepUp, qrSrc, type MfaInfo } from "./mfa";

const info = (mode: MfaInfo["mode"], deadline: string | null = null): MfaInfo =>
  ({ mode, deadline, aal: "aal1", fresh_seconds: 300 });

describe("mfaStep", () => {
  it("asks anyone with an authenticator for a code until the session reaches aal2", () => {
    for (const mode of ["off", "enrol", "required"] as const) {
      expect(mfaStep({ mfa: info(mode), currentLevel: "aal1", hasFactor: true, postponed: false })).toBe("challenge");
      expect(mfaStep({ mfa: info(mode), currentLevel: "aal2", hasFactor: true, postponed: false })).toBe("ok");
    }
  });

  it("sends advisers without one to set-up when required, with no way past it", () => {
    expect(mfaStep({ mfa: info("required"), currentLevel: "aal1", hasFactor: false, postponed: true })).toBe("enrol");
  });

  it("lets advisers postpone set-up during the grace period, until the deadline", () => {
    const grace = info("enrol", "2999-01-01");
    expect(mfaStep({ mfa: grace, currentLevel: "aal1", hasFactor: false, postponed: false })).toBe("enrol");
    expect(mfaStep({ mfa: grace, currentLevel: "aal1", hasFactor: false, postponed: true })).toBe("ok");
    const over = info("enrol", "2000-01-01");
    expect(mfaStep({ mfa: over, currentLevel: "aal1", hasFactor: false, postponed: true })).toBe("enrol");
  });

  it("asks nothing when two-step sign-in is off or unknown", () => {
    expect(mfaStep({ mfa: info("off"), currentLevel: "aal1", hasFactor: false, postponed: false })).toBe("ok");
    expect(mfaStep({ mfa: undefined, currentLevel: null, hasFactor: false, postponed: false })).toBe("ok");
  });
});

describe("canPostpone", () => {
  it("compares ISO dates: allowed before the deadline day, not on or after it", () => {
    const m = info("enrol", "2026-11-08");
    expect(canPostpone(m, "2026-11-07")).toBe(true);
    expect(canPostpone(m, "2026-11-08")).toBe(false);
    expect(canPostpone(info("required", "2026-11-08"), "2026-11-01")).toBe(false);
  });
});

describe("qrSrc", () => {
  it("keeps a data URL and wraps raw SVG markup", () => {
    expect(qrSrc("data:image/svg+xml;utf-8,<svg/>")).toBe("data:image/svg+xml;utf-8,<svg/>");
    expect(qrSrc("<svg/>")).toBe("data:image/svg+xml;utf-8,%3Csvg%2F%3E");
  });
});

describe("request", () => {
  afterEach(() => vi.unstubAllGlobals());

  const respond = (status: number, body: unknown) =>
    new Response(body === null ? null : JSON.stringify(body), { status });

  it("asks for a fresh code on mfa_fresh_required and retries once with the new token", async () => {
    let token = "old";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(respond(403, { detail: { code: "mfa_fresh_required", message: "Enter your code" } }))
      .mockResolvedValueOnce(respond(204, null));
    vi.stubGlobal("fetch", fetchMock);
    const off = onStepUp(async () => { token = "new"; return true; });
    await expect(request("/clients/1", { method: "DELETE" }, () => ({ Authorization: token }))).resolves.toBeNull();
    off();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe("new");
  });

  it("gives up with the API's message when the code is cancelled", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () =>
      respond(403, { detail: { code: "mfa_fresh_required", message: "Enter your code" } })));
    const off = onStepUp(async () => false);
    const err: unknown = await request("/x", {}, () => ({})).catch((e: unknown) => e);
    off();
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe("Enter your code");
  });

  it("retries only once, even if the API asks again", async () => {
    const fetchMock = vi.fn().mockImplementation(async () =>
      respond(403, { detail: { code: "mfa_fresh_required", message: "Enter your code" } }));
    vi.stubGlobal("fetch", fetchMock);
    const off = onStepUp(async () => true);
    await expect(request("/x", {}, () => ({}))).rejects.toBeInstanceOf(ApiError);
    off();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("tells the sign-in gate when the session needs its second factor", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () =>
      respond(403, { detail: { code: "mfa_required", message: "Two-step sign-in is required." } })));
    const seen = vi.fn();
    const off = onMfaRequired(seen);
    await expect(request("/clients", {}, () => ({}))).rejects.toBeInstanceOf(ApiError);
    off();
    expect(seen).toHaveBeenCalledOnce();
  });
});
